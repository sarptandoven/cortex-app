from __future__ import annotations

import itertools
import random
from dataclasses import dataclass
from typing import Mapping, Sequence

from .domain import ComparisonPlan, EvaluationPrompt, canonical_hash, derive_seed


def _validate_systems(system_ids: Sequence[str]) -> tuple[str, ...]:
    systems = tuple(sorted(set(str(item).strip() for item in system_ids)))
    if len(systems) < 2 or any(not item for item in systems):
        raise ValueError("comparison strategies require at least two unique system IDs")
    return systems


def _make_plan(
    prompt_id: str,
    left: str,
    right: str,
    repetition: int,
    swapped: bool,
) -> ComparisonPlan:
    system_a, system_b = sorted((left, right))
    logical_identity = {
        "prompt_id": prompt_id,
        "system_a": system_a,
        "system_b": system_b,
        "repetition": repetition,
    }
    identity = {
        "logical_comparison_id": canonical_hash(logical_identity, prefix="pair_")[:37],
        "left": left,
        "right": right,
        "swapped": swapped,
    }
    return ComparisonPlan(
        comparison_id=canonical_hash(identity, prefix="cmp_")[:36],
        logical_comparison_id=identity["logical_comparison_id"],
        prompt_id=prompt_id,
        left_system_id=left,
        right_system_id=right,
        repetition=repetition,
        swapped=swapped,
    )


@dataclass(frozen=True)
class AllPairsStrategy:
    repetitions: int = 1
    swap_sides: bool = False
    shuffle: bool = True
    strategy_id: str = "all_pairs"

    def reproducibility_config(self) -> Mapping[str, object]:
        return {
            "repetitions": self.repetitions,
            "swap_sides": self.swap_sides,
            "shuffle": self.shuffle,
        }

    def plan(
        self,
        prompts: Sequence[EvaluationPrompt],
        system_ids: Sequence[str],
        *,
        seed: int,
    ) -> tuple[ComparisonPlan, ...]:
        systems = _validate_systems(system_ids)
        if self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        plans: list[ComparisonPlan] = []
        for prompt in sorted(prompts, key=lambda item: item.prompt_id):
            for first, second in itertools.combinations(systems, 2):
                for repetition in range(self.repetitions):
                    swapped = bool(derive_seed(seed, prompt.prompt_id, first, second, repetition) & 1)
                    left, right = (second, first) if swapped else (first, second)
                    plans.append(_make_plan(prompt.prompt_id, left, right, repetition, swapped))
                    if self.swap_sides:
                        plans.append(_make_plan(prompt.prompt_id, right, left, repetition, not swapped))
        if self.shuffle:
            random.Random(derive_seed(seed, self.strategy_id)).shuffle(plans)
        return tuple(plans)


@dataclass(frozen=True)
class AnchorStrategy:
    anchor_system_id: str
    repetitions: int = 1
    swap_sides: bool = False
    shuffle: bool = True
    strategy_id: str = "anchor"

    def reproducibility_config(self) -> Mapping[str, object]:
        return {
            "anchor_system_id": self.anchor_system_id,
            "repetitions": self.repetitions,
            "swap_sides": self.swap_sides,
            "shuffle": self.shuffle,
        }

    def plan(
        self,
        prompts: Sequence[EvaluationPrompt],
        system_ids: Sequence[str],
        *,
        seed: int,
    ) -> tuple[ComparisonPlan, ...]:
        systems = _validate_systems(system_ids)
        if self.anchor_system_id not in systems:
            raise ValueError("anchor_system_id must be one of the evaluated systems")
        others = tuple(item for item in systems if item != self.anchor_system_id)
        return self._anchor_plans(prompts, others, seed)

    def _anchor_plans(
        self,
        prompts: Sequence[EvaluationPrompt],
        others: Sequence[str],
        seed: int,
    ) -> tuple[ComparisonPlan, ...]:
        if self.repetitions < 1:
            raise ValueError("repetitions must be positive")
        plans: list[ComparisonPlan] = []
        for prompt in sorted(prompts, key=lambda item: item.prompt_id):
            for other in others:
                for repetition in range(self.repetitions):
                    swapped = bool(derive_seed(seed, prompt.prompt_id, self.anchor_system_id, other, repetition) & 1)
                    left, right = (
                        (other, self.anchor_system_id)
                        if swapped
                        else (self.anchor_system_id, other)
                    )
                    plans.append(_make_plan(prompt.prompt_id, left, right, repetition, swapped))
                    if self.swap_sides:
                        plans.append(_make_plan(prompt.prompt_id, right, left, repetition, not swapped))
        if self.shuffle:
            random.Random(derive_seed(seed, self.strategy_id)).shuffle(plans)
        return tuple(plans)


@dataclass(frozen=True)
class RepeatedSwappedStrategy:
    """Balanced all-pairs trials: every repetition is judged in both orders."""

    repetitions: int = 1
    shuffle: bool = True
    strategy_id: str = "repeated_swapped"

    def reproducibility_config(self) -> Mapping[str, object]:
        return {
            "repetitions": self.repetitions,
            "shuffle": self.shuffle,
        }

    def plan(
        self,
        prompts: Sequence[EvaluationPrompt],
        system_ids: Sequence[str],
        *,
        seed: int,
    ) -> tuple[ComparisonPlan, ...]:
        return AllPairsStrategy(
            repetitions=self.repetitions,
            swap_sides=True,
            shuffle=self.shuffle,
            strategy_id=self.strategy_id,
        ).plan(prompts, system_ids, seed=seed)
