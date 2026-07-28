from __future__ import annotations

import math
import json
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .domain import EvaluationPrompt, HeldOutProfile, canonical_hash, canonical_json
from .protocols import ComparisonStrategy
from .scheduling import build_evaluation_schedule


@dataclass(frozen=True)
class EstimateRange:
    lower: float
    expected: float
    upper: float

    def __post_init__(self) -> None:
        values = (self.lower, self.expected, self.upper)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) < 0
            for value in values
        ):
            raise ValueError("estimate ranges require finite, non-negative numbers")
        if not self.lower <= self.expected <= self.upper:
            raise ValueError("estimate ranges must satisfy lower <= expected <= upper")

    def scaled(self, factor: float) -> EstimateRange:
        return EstimateRange(
            self.lower * factor,
            self.expected * factor,
            self.upper * factor,
        )

    def plus(self, other: EstimateRange) -> EstimateRange:
        return EstimateRange(
            self.lower + other.lower,
            self.expected + other.expected,
            self.upper + other.upper,
        )

    def to_dict(self, *, integral: bool = False) -> dict[str, int | float]:
        if integral:
            return {
                "lower": math.ceil(self.lower),
                "expected": math.ceil(self.expected),
                "upper": math.ceil(self.upper),
            }
        return {
            "lower": self.lower,
            "expected": self.expected,
            "upper": self.upper,
        }


@dataclass(frozen=True)
class PreflightPricing:
    generator_input_per_million_tokens: float
    generator_output_per_million_tokens: float
    judge_input_per_million_tokens: float
    judge_output_per_million_tokens: float

    def __post_init__(self) -> None:
        for value in (
            self.generator_input_per_million_tokens,
            self.generator_output_per_million_tokens,
            self.judge_input_per_million_tokens,
            self.judge_output_per_million_tokens,
        ):
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError("pricing values must be finite and non-negative")


@dataclass(frozen=True)
class PreflightAssumptions:
    candidate_output_chars: EstimateRange = field(
        default_factory=lambda: EstimateRange(1_000, 4_000, 16_000)
    )
    judge_output_tokens_per_call: EstimateRange = field(
        default_factory=lambda: EstimateRange(64, 256, 1_024)
    )
    generator_latency_seconds: EstimateRange = field(
        default_factory=lambda: EstimateRange(1, 5, 30)
    )
    judge_latency_seconds: EstimateRange = field(
        default_factory=lambda: EstimateRange(1, 5, 60)
    )
    chars_per_token: float = 4.0
    generator_request_overhead_chars: int = 1_000
    judge_request_overhead_chars: int = 2_000
    max_parallel_generations: int = 1
    max_parallel_judgments: int = 1
    pricing: PreflightPricing | None = None

    def __post_init__(self) -> None:
        if (
            isinstance(self.chars_per_token, bool)
            or not isinstance(self.chars_per_token, (int, float))
            or not math.isfinite(float(self.chars_per_token))
            or self.chars_per_token <= 0
        ):
            raise ValueError("chars_per_token must be finite and positive")
        for name in (
            "generator_request_overhead_chars",
            "judge_request_overhead_chars",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_parallel_generations", "max_parallel_judgments"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True)
class PreflightBudget:
    max_provider_calls: int | None = None
    max_total_tokens: int | None = None
    max_cost_usd: float | None = None
    max_duration_seconds: float | None = None

    def __post_init__(self) -> None:
        for name in ("max_provider_calls", "max_total_tokens"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int) or value < 0
            ):
                raise ValueError(f"{name} must be a non-negative integer")
        for name in ("max_cost_usd", "max_duration_seconds"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(float(value))
                or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")


@dataclass(frozen=True, init=False)
class PairwisePreflightEstimate:
    _payload_json: str

    def __init__(self, payload: Mapping[str, Any]) -> None:
        object.__setattr__(self, "_payload_json", canonical_json(payload))

    @property
    def payload(self) -> Mapping[str, Any]:
        """Return an isolated copy so callers cannot mutate the estimate."""
        return self.to_dict()

    @property
    def within_budget(self) -> bool:
        return bool(self.to_dict()["budget"]["within_budget"])

    def to_dict(self) -> dict[str, Any]:
        return json.loads(self._payload_json)


def _token_range(chars: EstimateRange, chars_per_token: float) -> EstimateRange:
    return EstimateRange(
        math.ceil(chars.lower / chars_per_token),
        math.ceil(chars.expected / chars_per_token),
        math.ceil(chars.upper / chars_per_token),
    )


def _cost_range(
    generator_input: EstimateRange,
    generator_output: EstimateRange,
    judge_input: EstimateRange,
    judge_output: EstimateRange,
    pricing: PreflightPricing,
) -> EstimateRange:
    return (
        generator_input.scaled(pricing.generator_input_per_million_tokens / 1_000_000)
        .plus(
            generator_output.scaled(
                pricing.generator_output_per_million_tokens / 1_000_000
            )
        )
        .plus(judge_input.scaled(pricing.judge_input_per_million_tokens / 1_000_000))
        .plus(
            judge_output.scaled(
                pricing.judge_output_per_million_tokens / 1_000_000
            )
        )
    )


def estimate_pairwise_workload(
    profile: HeldOutProfile,
    prompts: Sequence[EvaluationPrompt],
    system_ids: Sequence[str],
    strategy: ComparisonStrategy,
    *,
    seed: int | str = 0,
    assumptions: PreflightAssumptions | None = None,
    budget: PreflightBudget | None = None,
    max_prompts: int = 1_000,
    max_systems: int = 100,
    max_plans: int = 100_000,
    max_input_chars: int = 2_000_000,
) -> PairwisePreflightEstimate:
    """Estimate a run without invoking candidate generators or judge providers."""

    assumptions = assumptions or PreflightAssumptions()
    budget = budget or PreflightBudget()
    for value, name in (
        (max_prompts, "max_prompts"),
        (max_systems, "max_systems"),
        (max_plans, "max_plans"),
        (max_input_chars, "max_input_chars"),
    ):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f"{name} must be a positive integer")
    if len(prompts) > max_prompts:
        raise ValueError(f"evaluation exceeds max_prompts={max_prompts}")
    if len(system_ids) > max_systems:
        raise ValueError(f"evaluation exceeds max_systems={max_systems}")
    input_chars = len(canonical_json(profile)) + sum(
        len(canonical_json(prompt)) for prompt in prompts
    )
    if input_chars > max_input_chars:
        raise ValueError(
            f"evaluation input exceeds max_input_chars={max_input_chars}"
        )

    schedule = build_evaluation_schedule(
        profile,
        prompts,
        system_ids,
        strategy,
        seed=seed,
        max_plans=max_plans,
    )
    prompt_chars = {
        prompt.prompt_id: len(canonical_json(prompt)) for prompt in schedule.prompts
    }
    profile_chars = len(canonical_json(profile))
    generator_calls = len(schedule.prompts) * len(schedule.systems)
    judge_calls = len(schedule.plans)
    logical_comparisons = len(
        {plan.logical_comparison_id for plan in schedule.plans}
    )

    generator_input_chars_exact = sum(
        profile_chars
        + prompt_chars[prompt.prompt_id]
        + assumptions.generator_request_overhead_chars
        for prompt in schedule.prompts
        for _ in schedule.systems
    )
    generator_input_tokens = _token_range(
        EstimateRange(
            generator_input_chars_exact,
            generator_input_chars_exact,
            generator_input_chars_exact,
        ),
        assumptions.chars_per_token,
    )
    generator_output_tokens = _token_range(
        assumptions.candidate_output_chars.scaled(generator_calls),
        assumptions.chars_per_token,
    )

    judge_base_chars = sum(
        profile_chars
        + prompt_chars[plan.prompt_id]
        + assumptions.judge_request_overhead_chars
        for plan in schedule.plans
    )
    judge_candidate_chars = assumptions.candidate_output_chars.scaled(2 * judge_calls)
    judge_input_tokens = _token_range(
        EstimateRange(judge_base_chars, judge_base_chars, judge_base_chars).plus(
            judge_candidate_chars
        ),
        assumptions.chars_per_token,
    )
    judge_output_tokens = assumptions.judge_output_tokens_per_call.scaled(judge_calls)
    total_tokens = (
        generator_input_tokens.plus(generator_output_tokens)
        .plus(judge_input_tokens)
        .plus(judge_output_tokens)
    )

    generation_batches = math.ceil(
        generator_calls / assumptions.max_parallel_generations
    )
    judgment_batches = math.ceil(judge_calls / assumptions.max_parallel_judgments)
    duration = assumptions.generator_latency_seconds.scaled(
        generation_batches
    ).plus(assumptions.judge_latency_seconds.scaled(judgment_batches))
    provider_calls = generator_calls + judge_calls
    cost = (
        _cost_range(
            generator_input_tokens,
            generator_output_tokens,
            judge_input_tokens,
            judge_output_tokens,
            assumptions.pricing,
        )
        if assumptions.pricing is not None
        else None
    )
    if budget.max_cost_usd is not None and cost is None:
        raise ValueError("max_cost_usd requires pricing assumptions")

    violations: list[dict[str, int | float | str]] = []

    def check(metric: str, estimated: int | float, limit: int | float | None) -> None:
        if limit is not None and estimated > limit:
            violations.append(
                {"metric": metric, "estimated_upper": estimated, "limit": limit}
            )

    check("provider_calls", provider_calls, budget.max_provider_calls)
    check("total_tokens", math.ceil(total_tokens.upper), budget.max_total_tokens)
    check(
        "cost_usd",
        cost.upper if cost is not None else 0,
        budget.max_cost_usd,
    )
    check("duration_seconds", duration.upper, budget.max_duration_seconds)

    payload = {
        "schema_version": "pairwise-twin-preflight/v1",
        "status": "estimated",
        "provider_calls_made": 0,
        "schedule": {
            "seed": seed,
            "root_seed": schedule.root_seed,
            "schedule_digest": canonical_hash(schedule.plans),
            "strategy_id": strategy.strategy_id,
            "prompts": len(schedule.prompts),
            "systems": len(schedule.systems),
            "candidate_generations": generator_calls,
            "raw_judgments": judge_calls,
            "logical_comparisons": logical_comparisons,
            "swapped_presentations": sum(plan.swapped for plan in schedule.plans),
            "total_provider_calls": provider_calls,
        },
        "tokens": {
            "generator_input": generator_input_tokens.to_dict(integral=True),
            "generator_output": generator_output_tokens.to_dict(integral=True),
            "judge_input": judge_input_tokens.to_dict(integral=True),
            "judge_output": judge_output_tokens.to_dict(integral=True),
            "total": total_tokens.to_dict(integral=True),
        },
        "cost_usd": cost.to_dict() if cost is not None else None,
        "duration_seconds": duration.to_dict(),
        "concurrency": {
            "generation": assumptions.max_parallel_generations,
            "judgment": assumptions.max_parallel_judgments,
            "generation_batches": generation_batches,
            "judgment_batches": judgment_batches,
        },
        "assumptions": {
            "candidate_output_chars": assumptions.candidate_output_chars.to_dict(
                integral=True
            ),
            "judge_output_tokens_per_call": (
                assumptions.judge_output_tokens_per_call.to_dict(integral=True)
            ),
            "generator_latency_seconds": (
                assumptions.generator_latency_seconds.to_dict()
            ),
            "judge_latency_seconds": assumptions.judge_latency_seconds.to_dict(),
            "chars_per_token": assumptions.chars_per_token,
            "generator_request_overhead_chars": (
                assumptions.generator_request_overhead_chars
            ),
            "judge_request_overhead_chars": assumptions.judge_request_overhead_chars,
            "pricing": (
                {
                    "generator_input_per_million_tokens": (
                        assumptions.pricing.generator_input_per_million_tokens
                    ),
                    "generator_output_per_million_tokens": (
                        assumptions.pricing.generator_output_per_million_tokens
                    ),
                    "judge_input_per_million_tokens": (
                        assumptions.pricing.judge_input_per_million_tokens
                    ),
                    "judge_output_per_million_tokens": (
                        assumptions.pricing.judge_output_per_million_tokens
                    ),
                }
                if assumptions.pricing is not None
                else None
            ),
        },
        "budget": {
            "within_budget": not violations,
            "limits": {
                "max_provider_calls": budget.max_provider_calls,
                "max_total_tokens": budget.max_total_tokens,
                "max_cost_usd": budget.max_cost_usd,
                "max_duration_seconds": budget.max_duration_seconds,
            },
            "violations": violations,
        },
        "limitations": (
            "Token, cost, and duration ranges are forecasts from caller-controlled "
            "assumptions; schedule and call counts are exact.",
            "Provider-call totals assume one request per candidate generation and "
            "one request per raw judgment.",
        ),
    }
    return PairwisePreflightEstimate(payload)
