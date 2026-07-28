from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

from .admission import (
    PairwiseAdmissionPolicy,
    create_pairwise_admission_receipt,
    pairwise_admission_signing_key_is_valid,
    verify_pairwise_admission_receipt,
)
from .domain import (
    CitedProfileItem,
    EvaluationPrompt,
    HeldOutProfile,
    canonical_hash,
    canonical_json,
)
from .preflight import (
    EstimateRange,
    PairwisePreflightEstimate,
    PreflightAssumptions,
    PreflightBudget,
    PreflightPricing,
    estimate_pairwise_workload,
)
from .strategies import AllPairsStrategy, AnchorStrategy, RepeatedSwappedStrategy


MAX_PROFILE_ITEMS = 10_000
MAX_STRING_ID_CHARS = 200
MAX_ITEM_CONTENT_CHARS = 200_000
MAX_SOURCE_URL_CHARS = 2_000
MAX_METADATA_CHARS = 200_000
MAX_REPETITIONS = 10_000
MAX_CONCURRENCY = 10_000


def _mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return value


def _reject_unknown(
    value: Mapping[str, Any],
    allowed: Sequence[str],
    name: str,
) -> None:
    unknown = sorted(set(value) - set(allowed))
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(unknown)}")


def _string(
    value: Any,
    name: str,
    *,
    maximum: int,
    optional: bool = False,
    preserve_whitespace: bool = False,
) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    if len(value) > maximum:
        raise ValueError(f"{name} must not exceed {maximum} characters")
    return value if preserve_whitespace else value.strip()


def _boolean(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _integer(
    value: Any,
    name: str,
    *,
    minimum: int = 0,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    if maximum is not None and value > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return value


def _number(
    value: Any,
    name: str,
    *,
    minimum: float = 0,
    maximum: float | None = None,
) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
        or float(value) < minimum
    ):
        raise ValueError(f"{name} must be a finite number >= {minimum}")
    normalized = float(value)
    if maximum is not None and normalized > maximum:
        raise ValueError(f"{name} must be <= {maximum}")
    return normalized


def _metadata(value: Any, name: str) -> Mapping[str, Any]:
    metadata = _mapping(value, name)
    if len(canonical_json(metadata)) > MAX_METADATA_CHARS:
        raise ValueError(f"{name} exceeds {MAX_METADATA_CHARS} serialized characters")
    return metadata


def _parse_profile(value: Any) -> HeldOutProfile:
    profile = _mapping(value, "profile")
    _reject_unknown(profile, ("profile_id", "items", "metadata"), "profile")
    raw_items = profile.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        raise ValueError("profile.items must be a non-empty array")
    if len(raw_items) > MAX_PROFILE_ITEMS:
        raise ValueError(f"profile.items must contain at most {MAX_PROFILE_ITEMS} items")

    items: list[CitedProfileItem] = []
    allowed_item_fields = (
        "memory_id",
        "content",
        "source_url",
        "layer",
        "author_class",
        "status",
        "trust_score",
    )
    for index, raw_item in enumerate(raw_items):
        name = f"profile.items[{index}]"
        item = _mapping(raw_item, name)
        _reject_unknown(item, allowed_item_fields, name)
        items.append(
            CitedProfileItem(
                memory_id=_string(
                    item.get("memory_id"),
                    f"{name}.memory_id",
                    maximum=MAX_STRING_ID_CHARS,
                ),
                content=_string(
                    item.get("content"),
                    f"{name}.content",
                    maximum=MAX_ITEM_CONTENT_CHARS,
                    preserve_whitespace=True,
                ),
                source_url=_string(
                    item.get("source_url"),
                    f"{name}.source_url",
                    maximum=MAX_SOURCE_URL_CHARS,
                    optional=True,
                ),
                layer=_string(
                    item.get("layer"),
                    f"{name}.layer",
                    maximum=100,
                    optional=True,
                ),
                author_class=_string(
                    item.get("author_class", "user"),
                    f"{name}.author_class",
                    maximum=100,
                ),
                status=_string(
                    item.get("status", "active"),
                    f"{name}.status",
                    maximum=100,
                ),
                trust_score=_number(
                    item.get("trust_score", 1.0),
                    f"{name}.trust_score",
                    maximum=1,
                ),
            )
        )
    return HeldOutProfile(
        profile_id=_string(
            profile.get("profile_id"),
            "profile.profile_id",
            maximum=MAX_STRING_ID_CHARS,
        ),
        items=tuple(items),
        metadata=_metadata(profile.get("metadata", {}), "profile.metadata"),
    )


def _parse_prompts(value: Any) -> tuple[EvaluationPrompt, ...]:
    if not isinstance(value, list) or not value:
        raise ValueError("prompts must be a non-empty array")
    if len(value) > 1_000:
        raise ValueError("prompts must contain at most 1000 items")
    prompts: list[EvaluationPrompt] = []
    for index, raw_prompt in enumerate(value):
        name = f"prompts[{index}]"
        prompt = _mapping(raw_prompt, name)
        _reject_unknown(prompt, ("prompt_id", "text", "metadata"), name)
        prompts.append(
            EvaluationPrompt(
                prompt_id=_string(
                    prompt.get("prompt_id"),
                    f"{name}.prompt_id",
                    maximum=MAX_STRING_ID_CHARS,
                ),
                text=_string(
                    prompt.get("text"),
                    f"{name}.text",
                    maximum=MAX_ITEM_CONTENT_CHARS,
                    preserve_whitespace=True,
                ),
                metadata=_metadata(prompt.get("metadata", {}), f"{name}.metadata"),
            )
        )
    return tuple(prompts)


def _parse_system_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise ValueError("system_ids must be an array")
    if not 2 <= len(value) <= 100:
        raise ValueError("system_ids must contain between 2 and 100 items")
    return tuple(
        _string(
            system_id,
            f"system_ids[{index}]",
            maximum=MAX_STRING_ID_CHARS,
        )
        for index, system_id in enumerate(value)
    )


def _parse_strategy(value: Any):
    strategy = _mapping(value or {}, "strategy")
    strategy_type = _string(
        strategy.get("type", "repeated_swapped"),
        "strategy.type",
        maximum=40,
    )
    repetitions = _integer(
        strategy.get("repetitions", 1),
        "strategy.repetitions",
        minimum=1,
        maximum=MAX_REPETITIONS,
    )
    shuffle = _boolean(strategy.get("shuffle", True), "strategy.shuffle")
    if strategy_type == "repeated_swapped":
        _reject_unknown(strategy, ("type", "repetitions", "shuffle"), "strategy")
        return RepeatedSwappedStrategy(repetitions=repetitions, shuffle=shuffle)
    if strategy_type == "all_pairs":
        _reject_unknown(
            strategy,
            ("type", "repetitions", "shuffle", "swap_sides"),
            "strategy",
        )
        return AllPairsStrategy(
            repetitions=repetitions,
            swap_sides=_boolean(
                strategy.get("swap_sides", False),
                "strategy.swap_sides",
            ),
            shuffle=shuffle,
        )
    if strategy_type == "anchor":
        _reject_unknown(
            strategy,
            (
                "type",
                "repetitions",
                "shuffle",
                "swap_sides",
                "anchor_system_id",
            ),
            "strategy",
        )
        return AnchorStrategy(
            anchor_system_id=_string(
                strategy.get("anchor_system_id"),
                "strategy.anchor_system_id",
                maximum=MAX_STRING_ID_CHARS,
            ),
            repetitions=repetitions,
            swap_sides=_boolean(
                strategy.get("swap_sides", False),
                "strategy.swap_sides",
            ),
            shuffle=shuffle,
        )
    raise ValueError(
        "strategy.type must be repeated_swapped, all_pairs, or anchor"
    )


def _parse_range(
    value: Any,
    name: str,
    default: EstimateRange,
) -> EstimateRange:
    if value is None:
        return default
    raw_range = _mapping(value, name)
    _reject_unknown(raw_range, ("lower", "expected", "upper"), name)
    missing = {"lower", "expected", "upper"} - set(raw_range)
    if missing:
        raise ValueError(f"{name} is missing fields: {', '.join(sorted(missing))}")
    return EstimateRange(
        _number(raw_range["lower"], f"{name}.lower"),
        _number(raw_range["expected"], f"{name}.expected"),
        _number(raw_range["upper"], f"{name}.upper"),
    )


def _parse_pricing(value: Any) -> PreflightPricing | None:
    if value is None:
        return None
    pricing = _mapping(value, "assumptions.pricing")
    names = (
        "generator_input_per_million_tokens",
        "generator_output_per_million_tokens",
        "judge_input_per_million_tokens",
        "judge_output_per_million_tokens",
    )
    _reject_unknown(pricing, names, "assumptions.pricing")
    missing = set(names) - set(pricing)
    if missing:
        raise ValueError(
            "assumptions.pricing is missing fields: "
            + ", ".join(sorted(missing))
        )
    return PreflightPricing(
        *(
            _number(pricing[name], f"assumptions.pricing.{name}")
            for name in names
        )
    )


def _parse_assumptions(value: Any) -> PreflightAssumptions:
    defaults = PreflightAssumptions()
    assumptions = _mapping(value or {}, "assumptions")
    allowed = (
        "candidate_output_chars",
        "judge_output_tokens_per_call",
        "generator_latency_seconds",
        "judge_latency_seconds",
        "chars_per_token",
        "generator_request_overhead_chars",
        "judge_request_overhead_chars",
        "max_parallel_generations",
        "max_parallel_judgments",
        "pricing",
    )
    _reject_unknown(assumptions, allowed, "assumptions")
    return PreflightAssumptions(
        candidate_output_chars=_parse_range(
            assumptions.get("candidate_output_chars"),
            "assumptions.candidate_output_chars",
            defaults.candidate_output_chars,
        ),
        judge_output_tokens_per_call=_parse_range(
            assumptions.get("judge_output_tokens_per_call"),
            "assumptions.judge_output_tokens_per_call",
            defaults.judge_output_tokens_per_call,
        ),
        generator_latency_seconds=_parse_range(
            assumptions.get("generator_latency_seconds"),
            "assumptions.generator_latency_seconds",
            defaults.generator_latency_seconds,
        ),
        judge_latency_seconds=_parse_range(
            assumptions.get("judge_latency_seconds"),
            "assumptions.judge_latency_seconds",
            defaults.judge_latency_seconds,
        ),
        chars_per_token=_number(
            assumptions.get("chars_per_token", defaults.chars_per_token),
            "assumptions.chars_per_token",
            minimum=0.000_001,
        ),
        generator_request_overhead_chars=_integer(
            assumptions.get(
                "generator_request_overhead_chars",
                defaults.generator_request_overhead_chars,
            ),
            "assumptions.generator_request_overhead_chars",
            maximum=2_000_000,
        ),
        judge_request_overhead_chars=_integer(
            assumptions.get(
                "judge_request_overhead_chars",
                defaults.judge_request_overhead_chars,
            ),
            "assumptions.judge_request_overhead_chars",
            maximum=2_000_000,
        ),
        max_parallel_generations=_integer(
            assumptions.get(
                "max_parallel_generations",
                defaults.max_parallel_generations,
            ),
            "assumptions.max_parallel_generations",
            minimum=1,
            maximum=MAX_CONCURRENCY,
        ),
        max_parallel_judgments=_integer(
            assumptions.get(
                "max_parallel_judgments",
                defaults.max_parallel_judgments,
            ),
            "assumptions.max_parallel_judgments",
            minimum=1,
            maximum=MAX_CONCURRENCY,
        ),
        pricing=_parse_pricing(assumptions.get("pricing")),
    )


def _parse_optional_integer(value: Any, name: str) -> int | None:
    return None if value is None else _integer(value, name)


def _parse_optional_number(value: Any, name: str) -> float | None:
    return None if value is None else _number(value, name)


def _parse_budget(value: Any) -> PreflightBudget:
    budget = _mapping(value or {}, "budget")
    allowed = (
        "max_provider_calls",
        "max_total_tokens",
        "max_cost_usd",
        "max_duration_seconds",
    )
    _reject_unknown(budget, allowed, "budget")
    return PreflightBudget(
        max_provider_calls=_parse_optional_integer(
            budget.get("max_provider_calls"),
            "budget.max_provider_calls",
        ),
        max_total_tokens=_parse_optional_integer(
            budget.get("max_total_tokens"),
            "budget.max_total_tokens",
        ),
        max_cost_usd=_parse_optional_number(
            budget.get("max_cost_usd"),
            "budget.max_cost_usd",
        ),
        max_duration_seconds=_parse_optional_number(
            budget.get("max_duration_seconds"),
            "budget.max_duration_seconds",
        ),
    )


def estimate_pairwise_preflight_request(
    payload: Mapping[str, Any],
    *,
    policy: PairwiseAdmissionPolicy | None = None,
) -> PairwisePreflightEstimate:
    """Validate a product-boundary request and perform a zero-call estimate."""

    request = _mapping(payload, "request")
    _reject_unknown(
        request,
        (
            "profile",
            "prompts",
            "system_ids",
            "strategy",
            "seed",
            "assumptions",
            "budget",
            "profile_bundle_digest",
            "execution_config_digest",
        ),
        "request",
    )
    for name in (
        "profile_bundle_digest",
        "execution_config_digest",
    ):
        if name in request:
            _string(
                request[name],
                name,
                maximum=200,
            )
    seed = request.get("seed", 0)
    if isinstance(seed, bool) or not isinstance(seed, (int, str)):
        raise ValueError("seed must be an integer or string")
    if isinstance(seed, str) and (not seed.strip() or len(seed) > 200):
        raise ValueError("string seed must contain 1 to 200 characters")
    assumptions = _parse_assumptions(request.get("assumptions"))
    budget = _parse_budget(request.get("budget"))
    policy_payload: dict[str, Any] | None = None
    if policy is not None:
        assumptions, budget, policy_payload = policy.apply(assumptions, budget)
    estimate = estimate_pairwise_workload(
        _parse_profile(request.get("profile")),
        _parse_prompts(request.get("prompts")),
        _parse_system_ids(request.get("system_ids")),
        _parse_strategy(request.get("strategy")),
        seed=seed,
        assumptions=assumptions,
        budget=budget,
    )
    if policy_payload is None:
        return estimate
    policy_identity = {
        key: value
        for key, value in policy_payload.items()
        if key != "assumptions_hardened"
    }
    policy_payload["policy_digest"] = canonical_hash(
        policy_identity,
        prefix="pairwise_policy_",
    )
    result = estimate.to_dict()
    result["admission_policy"] = policy_payload
    result["limitations"].append(
        "Cost is not a server-enforced admission limit until provider pricing "
        "is selected by a trusted execution configuration."
    )
    return PairwisePreflightEstimate(result)


def require_pairwise_preflight_budget(
    payload: Mapping[str, Any],
    *,
    policy: PairwiseAdmissionPolicy | None = None,
) -> PairwisePreflightEstimate:
    """Reusable application gate for future execution and queue boundaries."""

    estimate = estimate_pairwise_preflight_request(payload, policy=policy)
    if not estimate.within_budget:
        violations = estimate.to_dict()["budget"]["violations"]
        summary = ", ".join(str(item["metric"]) for item in violations)
        raise ValueError(f"pairwise preflight budget exceeded: {summary}")
    return estimate


def build_pairwise_preflight_response(
    payload: Mapping[str, Any],
    *,
    policy: PairwiseAdmissionPolicy,
    subject: str,
    signing_key: str = "",
    receipt_ttl_seconds: int = 15 * 60,
    now_unix: int | None = None,
) -> dict[str, Any]:
    """Estimate a request and attach a short-lived execution-boundary receipt."""

    estimate = estimate_pairwise_preflight_request(payload, policy=policy)
    result = estimate.to_dict()
    if not estimate.within_budget:
        result["admission_receipt"] = {
            "available": False,
            "reason": "budget_exceeded",
        }
    elif not pairwise_admission_signing_key_is_valid(signing_key):
        result["admission_receipt"] = {
            "available": False,
            "reason": "server_signing_key_unavailable",
        }
    else:
        result["admission_receipt"] = create_pairwise_admission_receipt(
            payload,
            estimate,
            subject=subject,
            signing_key=signing_key,
            ttl_seconds=receipt_ttl_seconds,
            now_unix=now_unix,
        )
    return result


def require_pairwise_admission_receipt(
    payload: Mapping[str, Any],
    receipt: Mapping[str, Any],
    *,
    policy: PairwiseAdmissionPolicy,
    subject: str,
    signing_key: str,
    now_unix: int | None = None,
) -> PairwisePreflightEstimate:
    """Future execution gate: rerun current admission, then verify its receipt."""

    estimate = require_pairwise_preflight_budget(payload, policy=policy)
    verify_pairwise_admission_receipt(
        receipt,
        payload,
        estimate,
        subject=subject,
        signing_key=signing_key,
        now_unix=now_unix,
    )
    return estimate
