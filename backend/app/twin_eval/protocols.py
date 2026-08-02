from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping, Protocol, Sequence

from .domain import (
    Candidate,
    ComparisonOutcome,
    ComparisonPlan,
    EvaluationPrompt,
    HeldOutProfile,
    JudgeDecision,
    RankingResult,
)


class CandidateGenerator(Protocol):
    system_id: str

    def generate(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        *,
        seed: int,
    ) -> Candidate: ...


class PairwiseJudge(Protocol):
    judge_id: str

    def reproducibility_config(self) -> Mapping[str, object]: ...

    def judge(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        *,
        seed: int,
    ) -> JudgeDecision: ...


class ComparisonStrategy(Protocol):
    strategy_id: str

    def reproducibility_config(self) -> Mapping[str, object]: ...

    def plan(
        self,
        prompts: Sequence[EvaluationPrompt],
        system_ids: Sequence[str],
        *,
        seed: int,
    ) -> tuple[ComparisonPlan, ...]: ...


class RankingBackend(Protocol):
    ranking_id: str

    def reproducibility_config(self) -> Mapping[str, object]: ...

    def rank(
        self,
        system_ids: Sequence[str],
        comparisons: Sequence[tuple[str, str, ComparisonOutcome]],
    ) -> RankingResult: ...


@dataclass(frozen=True)
class DeterministicGenerator:
    """Offline adapter useful for tests, demos, and reproducible benchmarks."""

    system_id: str
    render: Callable[[EvaluationPrompt, HeldOutProfile, int], str]

    def generate(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        *,
        seed: int,
    ) -> Candidate:
        text = str(self.render(prompt, profile, seed))
        if not text.strip():
            raise ValueError(f"generator {self.system_id!r} returned empty text")
        return Candidate(
            candidate_id=f"{self.system_id}:{prompt.prompt_id}:{seed:016x}",
            system_id=self.system_id,
            text=text,
            prompt_id=prompt.prompt_id,
            seed=seed,
        )


@dataclass(frozen=True)
class OracleJudge:
    """Deterministic judge driven by expected winners keyed by prompt ID.

    Expected values are system IDs, ``tie``, or ``both_bad``. Decisions remain
    correct when a strategy swaps presentation order.
    """

    expected: Mapping[str, str | Mapping[str, float]]
    judge_id: str = "oracle"
    requires_candidate_identity: bool = True

    def reproducibility_config(self) -> Mapping[str, object]:
        return {
            "expected": self.expected,
            "requires_candidate_identity": self.requires_candidate_identity,
        }

    def judge(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        *,
        seed: int,
    ) -> JudgeDecision:
        del seed
        expected = self.expected.get(prompt.prompt_id)
        if expected is None:
            raise ValueError(f"no oracle label for prompt {prompt.prompt_id!r}")
        eligible_citations = tuple(
            item.memory_id
            for item in profile.items
            if item.author_class == "user"
            and item.status == "active"
            and item.trust_score > 0
        )
        if isinstance(expected, Mapping):
            if left.system_id not in expected or right.system_id not in expected:
                return JudgeDecision(
                    outcome=ComparisonOutcome.ABSTAIN,
                    rationale="oracle has no utility for one or both systems",
                    cited_memory_ids=eligible_citations,
                )
            left_score = float(expected[left.system_id])
            right_score = float(expected[right.system_id])
            if left_score > right_score:
                outcome = ComparisonOutcome.LEFT
            elif right_score > left_score:
                outcome = ComparisonOutcome.RIGHT
            else:
                outcome = ComparisonOutcome.TIE
        else:
            normalized = expected.strip().lower().replace("-", "_")
            if expected == left.system_id:
                outcome = ComparisonOutcome.LEFT
            elif expected == right.system_id:
                outcome = ComparisonOutcome.RIGHT
            elif normalized == "tie":
                outcome = ComparisonOutcome.TIE
            elif normalized in {"abstain", "insufficient_evidence"}:
                outcome = ComparisonOutcome.ABSTAIN
            elif normalized == "both_bad":
                outcome = ComparisonOutcome.BOTH_BAD
            else:
                raise ValueError(
                    f"oracle winner {expected!r} is not present in comparison "
                    f"{left.system_id!r} vs {right.system_id!r}"
                )
        if outcome in {ComparisonOutcome.LEFT, ComparisonOutcome.RIGHT} and not eligible_citations:
            return JudgeDecision(
                outcome=ComparisonOutcome.ABSTAIN,
                rationale="oracle lacks eligible owner-authored evidence",
            )
        return JudgeDecision(
            outcome=outcome,
            rationale=f"offline oracle label: {expected}",
            cited_memory_ids=eligible_citations,
        )
