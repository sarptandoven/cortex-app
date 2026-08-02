from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from .domain import ComparisonOutcome, EvaluationReport, derive_seed


@dataclass(frozen=True)
class ReliabilityMetrics:
    raw_judgments: int
    logical_comparisons: int
    decisive: int
    ties: int
    abstentions: int
    both_bad: int
    invalid: int
    swapped_pairs: int
    swap_consistent_pairs: int
    invalid_swap_pairs: int
    swap_agreement: float | None
    repeat_pairs: int
    invalid_repeat_pairs: int
    repeat_agreement: float | None
    displayed_left_wins: int
    displayed_right_wins: int
    position_bias: float | None


@dataclass(frozen=True)
class BootstrapInterval:
    estimate: float
    low: float | None
    high: float | None
    clusters: int
    resamples: int


def reliability_metrics(report: EvaluationReport) -> ReliabilityMetrics:
    outcome_counts = {outcome: 0 for outcome in ComparisonOutcome}
    for resolved in report.resolved_comparisons:
        outcome_counts[resolved.outcome] += 1

    swapped = [
        resolved
        for resolved in report.resolved_comparisons
        if resolved.swap_consistent is not None
    ]
    invalid_swapped = [
        resolved
        for resolved in swapped
        if resolved.outcome is ComparisonOutcome.INVALID and resolved.swap_consistent is True
    ]
    swap_evaluable = [
        resolved
        for resolved in swapped
        if not (
            resolved.outcome is ComparisonOutcome.INVALID
            and resolved.swap_consistent is True
        )
    ]
    swap_consistent = sum(
        1 for resolved in swap_evaluable if resolved.swap_consistent
    )

    repeat_groups: dict[tuple[str, str, str], list[ComparisonOutcome]] = {}
    for resolved in report.resolved_comparisons:
        key = (resolved.prompt_id, resolved.system_a_id, resolved.system_b_id)
        repeat_groups.setdefault(key, []).append(resolved.outcome)
    repeat_pairs = 0
    repeat_matches = 0
    invalid_repeat_pairs = 0
    for outcomes in repeat_groups.values():
        for first, second in itertools.combinations(outcomes, 2):
            if ComparisonOutcome.INVALID in {first, second}:
                invalid_repeat_pairs += 1
                continue
            repeat_pairs += 1
            repeat_matches += int(first is second)

    left_wins = sum(
        1 for record in report.comparisons if record.decision.outcome is ComparisonOutcome.LEFT
    )
    right_wins = sum(
        1 for record in report.comparisons if record.decision.outcome is ComparisonOutcome.RIGHT
    )
    displayed_decisive = left_wins + right_wins

    return ReliabilityMetrics(
        raw_judgments=len(report.comparisons),
        logical_comparisons=len(report.resolved_comparisons),
        decisive=outcome_counts[ComparisonOutcome.LEFT] + outcome_counts[ComparisonOutcome.RIGHT],
        ties=outcome_counts[ComparisonOutcome.TIE],
        abstentions=outcome_counts[ComparisonOutcome.ABSTAIN],
        both_bad=outcome_counts[ComparisonOutcome.BOTH_BAD],
        invalid=outcome_counts[ComparisonOutcome.INVALID],
        swapped_pairs=len(swapped),
        swap_consistent_pairs=swap_consistent,
        invalid_swap_pairs=len(invalid_swapped),
        swap_agreement=(
            swap_consistent / len(swap_evaluable) if swap_evaluable else None
        ),
        repeat_pairs=repeat_pairs,
        invalid_repeat_pairs=invalid_repeat_pairs,
        repeat_agreement=(repeat_matches / repeat_pairs) if repeat_pairs else None,
        displayed_left_wins=left_wins,
        displayed_right_wins=right_wins,
        position_bias=((left_wins - right_wins) / displayed_decisive) if displayed_decisive else None,
    )


def _cluster_means(values: Mapping[str, Sequence[float]]) -> tuple[tuple[str, float], ...]:
    if not values:
        raise ValueError("at least one cluster is required")
    means: list[tuple[str, float]] = []
    for cluster_id in sorted(values):
        cluster = tuple(float(value) for value in values[cluster_id])
        if not cluster:
            raise ValueError(f"cluster {cluster_id!r} has no observations")
        if any(not math.isfinite(value) for value in cluster):
            raise ValueError(f"cluster {cluster_id!r} contains a non-finite observation")
        means.append((cluster_id, sum(cluster) / len(cluster)))
    return tuple(means)


def _percentile(sorted_values: Sequence[float], probability: float) -> float:
    if not sorted_values:
        raise ValueError("percentile requires at least one value")
    position = probability * (len(sorted_values) - 1)
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    fraction = position - lower
    return sorted_values[lower] * (1.0 - fraction) + sorted_values[upper] * fraction


def clustered_bootstrap_mean(
    values: Mapping[str, Sequence[float]],
    *,
    seed: int | str,
    resamples: int = 2_000,
) -> BootstrapInterval:
    """Case-cluster bootstrap for a macro-average.

    All observations within a case/profile cluster remain together. The point
    estimate and resamples weight independent clusters equally, preventing
    repeated swapped judgments from manufacturing false precision.
    """

    if (
        isinstance(resamples, bool)
        or not isinstance(resamples, int)
        or resamples < 0
    ):
        raise ValueError("resamples must be a non-negative integer")
    means = _cluster_means(values)
    estimate = sum(mean for _, mean in means) / len(means)
    if len(means) < 2 or resamples < 1:
        return BootstrapInterval(estimate, None, None, len(means), max(0, resamples))

    # Sampling depends on the cohort identity, not observed values. This keeps
    # paired challenger-baseline and baseline-challenger intervals exact
    # reflections while preserving deterministic cluster draws.
    rng = random.Random(
        derive_seed(
            seed,
            "clustered_bootstrap_mean",
            tuple(cluster_id for cluster_id, _ in means),
            resamples,
        )
    )
    samples: list[float] = []
    for _ in range(resamples):
        drawn = [means[rng.randrange(len(means))][1] for _ in range(len(means))]
        samples.append(sum(drawn) / len(drawn))
    samples.sort()
    return BootstrapInterval(
        estimate=estimate,
        low=_percentile(samples, 0.025),
        high=_percentile(samples, 0.975),
        clusters=len(means),
        resamples=resamples,
    )


def paired_clustered_bootstrap_delta(
    baseline: Mapping[str, Sequence[float]],
    challenger: Mapping[str, Sequence[float]],
    *,
    seed: int | str,
    resamples: int = 2_000,
) -> BootstrapInterval:
    """Paired cluster bootstrap of challenger minus baseline."""

    baseline_means = dict(_cluster_means(baseline))
    challenger_means = dict(_cluster_means(challenger))
    if baseline_means.keys() != challenger_means.keys():
        raise ValueError("baseline and challenger must contain identical cluster IDs")
    differences = {
        cluster_id: (challenger_means[cluster_id] - baseline_means[cluster_id],)
        for cluster_id in baseline_means
    }
    return clustered_bootstrap_mean(differences, seed=seed, resamples=resamples)
