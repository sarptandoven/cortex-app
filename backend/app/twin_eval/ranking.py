from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Mapping, Sequence

from .domain import (
    ComparisonOutcome,
    RankingDiagnostics,
    RankingResult,
    SystemRating,
)


def _components(nodes: tuple[str, ...], edges: set[tuple[str, str]]) -> tuple[tuple[str, ...], ...]:
    neighbors = {node: set() for node in nodes}
    for left, right in edges:
        neighbors[left].add(right)
        neighbors[right].add(left)
    remaining = set(nodes)
    groups: list[tuple[str, ...]] = []
    while remaining:
        root = min(remaining)
        stack = [root]
        found: set[str] = set()
        while stack:
            node = stack.pop()
            if node in found:
                continue
            found.add(node)
            stack.extend(neighbors[node] - found)
        remaining -= found
        groups.append(tuple(sorted(found)))
    return tuple(sorted(groups))


@dataclass(frozen=True)
class BradleyTerryRanker:
    """Pure-Python MM fit for a Bradley–Terry model.

    Ties contribute half a win to each system. ``both_bad`` is intentionally
    excluded: it says both candidates failed an absolute floor, not that they
    have equal preference strength. Abstentions and invalid judgments are also
    excluded and surfaced in diagnostics.
    """

    tolerance: float = 1e-10
    max_iterations: int = 10_000
    prior: float = 0.5
    scale: float = 400.0
    ranking_id: str = "bradley_terry_mm_v1"

    def __post_init__(self) -> None:
        numeric = (self.tolerance, self.prior, self.scale)
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not math.isfinite(float(value))
            or float(value) <= 0
            for value in numeric
        ):
            raise ValueError("tolerance, prior, and scale must be finite and positive")
        if (
            isinstance(self.max_iterations, bool)
            or not isinstance(self.max_iterations, int)
            or self.max_iterations < 1
        ):
            raise ValueError("max_iterations must be a positive integer")

    def reproducibility_config(self) -> Mapping[str, object]:
        return {
            "tolerance": self.tolerance,
            "max_iterations": self.max_iterations,
            "prior": self.prior,
            "scale": self.scale,
        }

    def rank(
        self,
        system_ids: Sequence[str],
        comparisons: Sequence[tuple[str, str, ComparisonOutcome]],
    ) -> RankingResult:
        systems = tuple(dict.fromkeys(str(item).strip() for item in system_ids))
        if len(systems) < 2 or any(not item for item in systems):
            raise ValueError("ranking requires at least two unique systems")
        index = {system: position for position, system in enumerate(systems)}
        n = len(systems)
        wins = [0.0] * n
        totals = [[0.0] * n for _ in range(n)]
        counts = [0] * n
        edges: set[tuple[str, str]] = set()
        ignored_both_bad = 0
        ignored_abstain = 0
        ignored_invalid = 0

        for left, right, raw_outcome in comparisons:
            if left == right or left not in index or right not in index:
                raise ValueError(f"invalid ranking comparison: {left!r} vs {right!r}")
            outcome = ComparisonOutcome.normalize(raw_outcome)
            if outcome is ComparisonOutcome.BOTH_BAD:
                ignored_both_bad += 1
                continue
            if outcome is ComparisonOutcome.ABSTAIN:
                ignored_abstain += 1
                continue
            if outcome is ComparisonOutcome.INVALID:
                ignored_invalid += 1
                continue
            i, j = index[left], index[right]
            totals[i][j] += 1.0
            totals[j][i] += 1.0
            counts[i] += 1
            counts[j] += 1
            edges.add(tuple(sorted((left, right))))
            if outcome is ComparisonOutcome.LEFT:
                wins[i] += 1.0
            elif outcome is ComparisonOutcome.RIGHT:
                wins[j] += 1.0
            else:
                wins[i] += 0.5
                wins[j] += 0.5

        components = _components(systems, edges)
        # Symmetric pseudo-observations are added between every system pair:
        # ``prior`` wins in each direction. Unlike a fixed external reference,
        # this regularization is scale-invariant and therefore compatible with
        # the geometric-mean identifiability constraint below.
        fit_wins = [wins[i] + self.prior * (n - 1) for i in range(n)]
        fit_totals = [
            [
                0.0 if i == j else totals[i][j] + 2.0 * self.prior
                for j in range(n)
            ]
            for i in range(n)
        ]

        abilities = [1.0] * n
        converged = False
        max_delta = float("inf")
        iterations = 0
        for iterations in range(1, self.max_iterations + 1):
            updated: list[float] = []
            for i in range(n):
                denominator = 0.0
                for j in range(n):
                    if i != j and fit_totals[i][j]:
                        denominator += fit_totals[i][j] / (abilities[i] + abilities[j])
                updated.append(fit_wins[i] / denominator)
            geometric_mean = math.exp(sum(math.log(max(value, 1e-300)) for value in updated) / n)
            updated = [value / geometric_mean for value in updated]
            max_delta = max(abs(math.log(updated[i]) - math.log(abilities[i])) for i in range(n))
            abilities = updated
            if max_delta < self.tolerance:
                converged = True
                break

        log_likelihood = 0.0
        for left, right, raw_outcome in comparisons:
            outcome = ComparisonOutcome.normalize(raw_outcome)
            if outcome in {
                ComparisonOutcome.BOTH_BAD,
                ComparisonOutcome.ABSTAIN,
                ComparisonOutcome.INVALID,
            }:
                continue
            i, j = index[left], index[right]
            probability = abilities[i] / (abilities[i] + abilities[j])
            probability = min(max(probability, 1e-15), 1.0 - 1e-15)
            if outcome is ComparisonOutcome.LEFT:
                log_likelihood += math.log(probability)
            elif outcome is ComparisonOutcome.RIGHT:
                log_likelihood += math.log(1.0 - probability)
            else:
                log_likelihood += 0.5 * (math.log(probability) + math.log(1.0 - probability))

        raw_scores = {system: self.scale * math.log(abilities[index[system]]) for system in systems}
        centered = sum(raw_scores.values()) / n
        component_by_system = {
            system: component_index
            for component_index, component in enumerate(components)
            for system in component
        }
        if len(components) == 1:
            ordered = sorted(systems, key=lambda item: (-raw_scores[item], item))
        else:
            ordered = sorted(
                systems,
                key=lambda item: (component_by_system[item], -raw_scores[item], item),
            )

        global_ranks: dict[str, int | None] = {}
        if len(components) == 1:
            previous_score: float | None = None
            dense_rank = 0
            for system in ordered:
                score = raw_scores[system]
                if previous_score is None or abs(score - previous_score) > self.tolerance:
                    dense_rank += 1
                    previous_score = score
                global_ranks[system] = dense_rank
        else:
            global_ranks = {system: None for system in systems}

        component_ranks: dict[str, int] = {}
        for component in components:
            local_order = sorted(component, key=lambda item: (-raw_scores[item], item))
            previous_score = None
            dense_rank = 0
            for system in local_order:
                score = raw_scores[system]
                if previous_score is None or abs(score - previous_score) > self.tolerance:
                    dense_rank += 1
                    previous_score = score
                component_ranks[system] = dense_rank

        ratings = tuple(
            SystemRating(
                system_id=system,
                score=round(raw_scores[system] - centered, 6),
                rank=global_ranks[system],
                component_rank=component_ranks[system],
                comparisons=counts[index[system]],
                wins=round(wins[index[system]], 3),
            )
            for system in ordered
        )
        return RankingResult(
            ratings=ratings,
            diagnostics=RankingDiagnostics(
                connected=len(components) == 1,
                components=components,
                converged=converged,
                iterations=iterations,
                max_delta=max_delta,
                log_likelihood=round(log_likelihood, 8),
                ignored_both_bad=ignored_both_bad,
                ignored_abstain=ignored_abstain,
                ignored_invalid=ignored_invalid,
            ),
        )


@dataclass(frozen=True)
class WinRateRanker:
    """Empirical fractional-win baseline with explicit graph diagnostics.

    Ties contribute half a win to each side. Outcomes that do not express a
    relative preference (both-bad, abstain, invalid) are excluded from both
    the comparison graph and score denominators, and counted in diagnostics.
    """

    tolerance: float = 1e-12
    ranking_id: str = "win_rate_v1"

    def __post_init__(self) -> None:
        if (
            isinstance(self.tolerance, bool)
            or not isinstance(self.tolerance, (int, float))
            or not math.isfinite(float(self.tolerance))
            or self.tolerance <= 0
        ):
            raise ValueError("tolerance must be finite and positive")

    def reproducibility_config(self) -> Mapping[str, object]:
        return {"tolerance": self.tolerance}

    def rank(
        self,
        system_ids: Sequence[str],
        comparisons: Sequence[tuple[str, str, ComparisonOutcome]],
    ) -> RankingResult:
        systems = tuple(dict.fromkeys(str(item).strip() for item in system_ids))
        if len(systems) < 2 or any(not item for item in systems):
            raise ValueError("ranking requires at least two unique systems")
        index = {system: position for position, system in enumerate(systems)}
        wins = [0.0] * len(systems)
        counts = [0] * len(systems)
        edges: set[tuple[str, str]] = set()
        ignored_both_bad = 0
        ignored_abstain = 0
        ignored_invalid = 0

        for left, right, raw_outcome in comparisons:
            if left == right or left not in index or right not in index:
                raise ValueError(f"invalid ranking comparison: {left!r} vs {right!r}")
            try:
                outcome = ComparisonOutcome.normalize(raw_outcome)
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    f"invalid ranking outcome for {left!r} vs {right!r}"
                ) from exc
            if outcome is ComparisonOutcome.BOTH_BAD:
                ignored_both_bad += 1
                continue
            if outcome is ComparisonOutcome.ABSTAIN:
                ignored_abstain += 1
                continue
            if outcome is ComparisonOutcome.INVALID:
                ignored_invalid += 1
                continue

            i, j = index[left], index[right]
            counts[i] += 1
            counts[j] += 1
            edges.add(tuple(sorted((left, right))))
            if outcome is ComparisonOutcome.LEFT:
                wins[i] += 1.0
            elif outcome is ComparisonOutcome.RIGHT:
                wins[j] += 1.0
            else:
                wins[i] += 0.5
                wins[j] += 0.5

        scores = [
            wins[position] / counts[position] if counts[position] else 0.0
            for position in range(len(systems))
        ]
        components = _components(systems, edges)
        connected = len(components) == 1
        component_by_system = {
            system: component_index
            for component_index, component in enumerate(components)
            for system in component
        }
        ordered = sorted(
            systems,
            key=lambda system: (
                0 if connected else component_by_system[system],
                -scores[index[system]],
                system,
            ),
        )

        global_ranks: dict[str, int | None] = {system: None for system in systems}
        if connected:
            previous: float | None = None
            dense_rank = 0
            for system in ordered:
                score = scores[index[system]]
                if previous is None or abs(score - previous) > self.tolerance:
                    dense_rank += 1
                    previous = score
                global_ranks[system] = dense_rank

        component_ranks: dict[str, int] = {}
        for component in components:
            previous = None
            dense_rank = 0
            for system in sorted(component, key=lambda item: (-scores[index[item]], item)):
                score = scores[index[system]]
                if previous is None or abs(score - previous) > self.tolerance:
                    dense_rank += 1
                    previous = score
                component_ranks[system] = dense_rank

        return RankingResult(
            ratings=tuple(
                SystemRating(
                    system_id=system,
                    score=round(scores[index[system]], 6),
                    rank=global_ranks[system],
                    component_rank=component_ranks[system],
                    comparisons=counts[index[system]],
                    wins=round(wins[index[system]], 3),
                )
                for system in ordered
            ),
            diagnostics=RankingDiagnostics(
                connected=connected,
                components=components,
                converged=True,
                iterations=0,
                max_delta=0.0,
                log_likelihood=0.0,
                ignored_both_bad=ignored_both_bad,
                ignored_abstain=ignored_abstain,
                ignored_invalid=ignored_invalid,
            ),
        )
