from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .domain import ComparisonPlan, EvaluationPrompt, HeldOutProfile, derive_seed
from .protocols import ComparisonStrategy


@dataclass(frozen=True)
class EvaluationSchedule:
    """Validated, deterministic work shared by execution and preflight."""

    root_seed: int
    prompts: tuple[EvaluationPrompt, ...]
    systems: tuple[str, ...]
    plans: tuple[ComparisonPlan, ...]


def _positive_integer(value: int, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def validate_comparison_schedule(
    plans: Sequence[ComparisonPlan],
    *,
    prompt_ids: Sequence[str],
    system_ids: Sequence[str],
    max_plans: int,
) -> tuple[ComparisonPlan, ...]:
    """Validate strategy output without invoking a generator or judge."""

    _positive_integer(max_plans, "max_plans")
    plan_tuple = tuple(plans)
    if not plan_tuple:
        raise ValueError("comparison strategy emitted no plans")
    if len(plan_tuple) > max_plans:
        raise ValueError(f"evaluation exceeds max_plans={max_plans}")
    if any(not isinstance(plan, ComparisonPlan) for plan in plan_tuple):
        raise TypeError("comparison strategy must emit ComparisonPlan values")

    expected_prompts = set(prompt_ids)
    expected_systems = set(system_ids)
    comparison_ids: set[str] = set()
    grouped: dict[str, list[ComparisonPlan]] = {}
    trial_ids: dict[tuple[str, tuple[str, str], int], str] = {}
    covered_prompts: set[str] = set()
    covered_systems: set[str] = set()

    for plan in plan_tuple:
        if plan.comparison_id in comparison_ids:
            raise ValueError("strategy emitted duplicate comparison_id values")
        comparison_ids.add(plan.comparison_id)
        if plan.prompt_id not in expected_prompts:
            raise ValueError(f"strategy emitted unknown prompt_id {plan.prompt_id!r}")
        if (
            plan.left_system_id not in expected_systems
            or plan.right_system_id not in expected_systems
        ):
            raise ValueError("strategy emitted an unknown system_id")

        covered_prompts.add(plan.prompt_id)
        covered_systems.update((plan.left_system_id, plan.right_system_id))
        grouped.setdefault(plan.logical_comparison_id, []).append(plan)
        trial_key = (
            plan.prompt_id,
            tuple(sorted((plan.left_system_id, plan.right_system_id))),
            plan.repetition,
        )
        existing_logical_id = trial_ids.setdefault(
            trial_key, plan.logical_comparison_id
        )
        if existing_logical_id != plan.logical_comparison_id:
            raise ValueError(
                "one prompt/system/repetition trial cannot use multiple "
                "logical_comparison_id values"
            )

    if covered_prompts != expected_prompts:
        raise ValueError("strategy did not schedule every evaluation prompt")
    if covered_systems != expected_systems:
        raise ValueError("strategy did not schedule every evaluated system")

    for logical_id, sources in grouped.items():
        if len(sources) > 2:
            raise ValueError(
                f"logical comparison {logical_id!r} has more than two presentations"
            )
        first = sources[0]
        expected_pair = {first.left_system_id, first.right_system_id}
        for plan in sources[1:]:
            if plan.prompt_id != first.prompt_id or plan.repetition != first.repetition:
                raise ValueError("logical comparison grouped different prompt trials")
            if {plan.left_system_id, plan.right_system_id} != expected_pair:
                raise ValueError("logical comparison grouped different system pairs")
        if len(sources) == 2:
            orientations = {
                (plan.left_system_id, plan.right_system_id) for plan in sources
            }
            if len(orientations) != 2:
                raise ValueError(
                    "two-presentation logical comparisons must use opposite orientations"
                )
            if len({plan.swapped for plan in sources}) != 2:
                raise ValueError(
                    "opposite presentations must have complementary swapped flags"
                )
    return plan_tuple


def build_evaluation_schedule(
    profile: HeldOutProfile,
    prompts: Sequence[EvaluationPrompt],
    system_ids: Sequence[str],
    strategy: ComparisonStrategy,
    *,
    seed: int | str = 0,
    max_plans: int = 100_000,
) -> EvaluationSchedule:
    """Build the exact schedule used by both estimation and execution."""

    _positive_integer(max_plans, "max_plans")
    prompt_tuple = tuple(sorted(prompts, key=lambda item: item.prompt_id))
    if not prompt_tuple:
        raise ValueError("evaluation requires at least one prompt")
    prompt_ids = tuple(prompt.prompt_id for prompt in prompt_tuple)
    if len(set(prompt_ids)) != len(prompt_ids):
        raise ValueError("prompt_id values must be unique")

    raw_systems = tuple(system_ids)
    if any(
        not isinstance(system_id, str) or not system_id.strip()
        for system_id in raw_systems
    ):
        raise ValueError("system IDs must be non-empty strings")
    if len(set(raw_systems)) != len(raw_systems) or len(raw_systems) < 2:
        raise ValueError("evaluation requires at least two uniquely named generators")
    systems = tuple(sorted(raw_systems))

    root_seed = derive_seed(seed, profile.fingerprint, prompt_ids, systems)
    projected_count = getattr(strategy, "planned_comparison_count", None)
    if callable(projected_count):
        projected_plans = projected_count(len(prompt_tuple), len(systems))
        if (
            isinstance(projected_plans, bool)
            or not isinstance(projected_plans, int)
            or projected_plans < 0
        ):
            raise ValueError(
                "planned_comparison_count() must return a non-negative integer"
            )
        if projected_plans > max_plans:
            raise ValueError(f"evaluation exceeds max_plans={max_plans}")
    plans = validate_comparison_schedule(
        strategy.plan(prompt_tuple, systems, seed=root_seed),
        prompt_ids=prompt_ids,
        system_ids=systems,
        max_plans=max_plans,
    )
    return EvaluationSchedule(root_seed, prompt_tuple, systems, plans)
