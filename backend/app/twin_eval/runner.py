from __future__ import annotations

import json
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from .domain import (
    Candidate,
    ComparisonOutcome,
    ComparisonRecord,
    EvaluationPrompt,
    EvaluationReport,
    HeldOutProfile,
    JudgeDecision,
    ResolvedComparison,
    canonical_hash,
    canonical_json,
    derive_seed,
)
from .policies import CitationValidationPolicy, EligibleCitationPolicy
from .protocols import CandidateGenerator, ComparisonStrategy, PairwiseJudge, RankingBackend


def _reproducibility_config(component: Any) -> Mapping[str, Any]:
    snapshot = getattr(component, "reproducibility_config", None)
    if callable(snapshot):
        value = snapshot()
        if not isinstance(value, Mapping):
            raise TypeError("reproducibility_config() must return a mapping")
        return value
    return {
        "adapter_type": (
            f"{component.__class__.__module__}.{component.__class__.__qualname__}"
        )
    }


@dataclass(frozen=True)
class PairwiseEvaluationRunner:
    generators: Sequence[CandidateGenerator]
    judge: PairwiseJudge
    strategy: ComparisonStrategy
    ranker: RankingBackend
    metadata: Mapping[str, Any] = field(default_factory=dict)
    citation_policy: CitationValidationPolicy = field(
        default_factory=EligibleCitationPolicy
    )
    blind_judge_inputs: bool = True
    max_input_chars: int = 2_000_000
    max_candidate_chars: int = 200_000
    max_judge_rationale_chars: int = 100_000
    max_report_chars: int = 50_000_000
    max_prompts: int = 1_000
    max_systems: int = 100
    max_plans: int = 100_000

    @staticmethod
    def _validate_plan_groups(plans: Sequence[Any]) -> None:
        """Reject schedules that could manufacture swap reliability."""
        if not plans:
            raise ValueError("comparison strategy emitted no plans")
        grouped: dict[str, list[Any]] = {}
        trial_ids: dict[tuple[str, tuple[str, str], int], str] = {}
        for plan in plans:
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

    def _validated_decision(
        self,
        decision: JudgeDecision,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
    ) -> JudgeDecision:
        invalid_reason = self.citation_policy.invalid_reason(
            prompt,
            profile,
            left,
            right,
            decision,
        )
        if not invalid_reason:
            return decision
        metadata = dict(decision.metadata)
        metadata["invalid_reason"] = invalid_reason
        metadata["original_outcome"] = decision.outcome.value
        return JudgeDecision(
            outcome=ComparisonOutcome.INVALID,
            rationale=invalid_reason,
            cited_memory_ids=decision.cited_memory_ids,
            confidence=decision.confidence,
            metadata=metadata,
        )

    @staticmethod
    def _resolve_comparisons(
        records: Sequence[ComparisonRecord],
    ) -> tuple[ResolvedComparison, ...]:
        grouped: dict[str, list[ComparisonRecord]] = {}
        for record in records:
            grouped.setdefault(record.plan.logical_comparison_id, []).append(record)

        resolved: list[ResolvedComparison] = []
        for logical_id in sorted(grouped):
            sources = grouped[logical_id]
            first = sources[0]
            system_a, system_b = sorted((first.left.system_id, first.right.system_id))
            canonical_outcomes: list[ComparisonOutcome] = []
            for record in sources:
                if {record.left.system_id, record.right.system_id} != {system_a, system_b}:
                    raise ValueError("logical comparison grouped different system pairs")
                if record.plan.prompt_id != first.plan.prompt_id:
                    raise ValueError("logical comparison grouped different prompts")
                if record.plan.repetition != first.plan.repetition:
                    raise ValueError("logical comparison grouped different repetitions")
                outcome = record.decision.outcome
                if record.left.system_id != system_a:
                    outcome = outcome.swapped()
                canonical_outcomes.append(outcome)

            unanimous = len(set(canonical_outcomes)) == 1
            outcome = canonical_outcomes[0] if unanimous else ComparisonOutcome.INVALID
            resolved.append(
                ResolvedComparison(
                    logical_comparison_id=logical_id,
                    prompt_id=first.plan.prompt_id,
                    repetition=first.plan.repetition,
                    system_a_id=system_a,
                    system_b_id=system_b,
                    outcome=outcome,
                    source_comparison_ids=tuple(sorted(record.plan.comparison_id for record in sources)),
                    swap_consistent=unanimous if len(sources) > 1 else None,
                )
            )
        return tuple(resolved)

    def run(
        self,
        profile: HeldOutProfile,
        prompts: Sequence[EvaluationPrompt],
        *,
        seed: int | str = 0,
        trial_id: str | None = None,
    ) -> EvaluationReport:
        normalized_trial_id: str | None = None
        if trial_id is not None:
            if not isinstance(trial_id, str) or not trial_id.strip():
                raise ValueError("trial_id must be a non-empty string when provided")
            normalized_trial_id = trial_id.strip()
            if len(normalized_trial_id) > 200:
                raise ValueError("trial_id must not exceed 200 characters")
        limits = (
            self.max_input_chars,
            self.max_candidate_chars,
            self.max_judge_rationale_chars,
            self.max_report_chars,
            self.max_prompts,
            self.max_systems,
            self.max_plans,
        )
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 1
            for value in limits
        ):
            raise ValueError("runner limits must be positive integers")
        if not isinstance(self.blind_judge_inputs, bool):
            raise ValueError("blind_judge_inputs must be boolean")
        if self.blind_judge_inputs and getattr(
            self.judge, "requires_candidate_identity", False
        ):
            raise ValueError(
                "judge requires candidate identity; set blind_judge_inputs=False "
                "only for trusted fixture evaluation"
            )
        # Snapshot all caller-owned mappings before invoking extensibility
        # hooks, which may otherwise mutate configuration during a run.
        metadata_snapshot = json.loads(canonical_json(self.metadata))
        reserved_metadata = {
            "spec_id",
            "trial_id",
            "reproducibility_manifest",
        }.intersection(metadata_snapshot)
        if reserved_metadata:
            names = ", ".join(sorted(reserved_metadata))
            raise ValueError(f"runner metadata uses reserved keys: {names}")
        strategy_config = json.loads(
            canonical_json(_reproducibility_config(self.strategy))
        )
        judge_config = json.loads(
            canonical_json(_reproducibility_config(self.judge))
        )
        ranking_config = json.loads(
            canonical_json(_reproducibility_config(self.ranker))
        )
        citation_policy_config = json.loads(
            canonical_json(_reproducibility_config(self.citation_policy))
        )
        config_chars = sum(
            len(canonical_json(value))
            for value in (
                metadata_snapshot,
                strategy_config,
                judge_config,
                ranking_config,
                citation_policy_config,
            )
        )
        if config_chars > self.max_report_chars:
            raise ValueError(
                f"configuration exceeds max_report_chars={self.max_report_chars}"
            )
        prompt_tuple = tuple(sorted(prompts, key=lambda item: item.prompt_id))
        if not prompt_tuple:
            raise ValueError("evaluation requires at least one prompt")
        if len(prompt_tuple) > self.max_prompts:
            raise ValueError(f"evaluation exceeds max_prompts={self.max_prompts}")
        input_chars = len(canonical_json(profile)) + sum(
            len(canonical_json(prompt)) for prompt in prompt_tuple
        )
        if input_chars > self.max_input_chars:
            raise ValueError(
                f"evaluation input exceeds max_input_chars={self.max_input_chars}"
            )
        prompt_by_id = {prompt.prompt_id: prompt for prompt in prompt_tuple}
        if len(prompt_by_id) != len(prompt_tuple):
            raise ValueError("prompt_id values must be unique")
        generator_by_id = {generator.system_id: generator for generator in self.generators}
        if len(generator_by_id) != len(self.generators) or len(generator_by_id) < 2:
            raise ValueError("evaluation requires at least two uniquely named generators")
        if len(generator_by_id) > self.max_systems:
            raise ValueError(f"evaluation exceeds max_systems={self.max_systems}")
        systems = tuple(sorted(generator_by_id))
        root_seed = derive_seed(seed, profile.fingerprint, tuple(prompt_by_id), systems)
        plans = self.strategy.plan(prompt_tuple, systems, seed=root_seed)
        if len(plans) > self.max_plans:
            raise ValueError(f"evaluation exceeds max_plans={self.max_plans}")
        self._validate_plan_groups(plans)
        comparison_ids = [plan.comparison_id for plan in plans]
        if len(comparison_ids) != len(set(comparison_ids)):
            raise ValueError("strategy emitted duplicate comparison_id values")
        for plan in plans:
            if plan.prompt_id not in prompt_by_id:
                raise ValueError(f"strategy emitted unknown prompt_id {plan.prompt_id!r}")
            if plan.left_system_id not in generator_by_id or plan.right_system_id not in generator_by_id:
                raise ValueError("strategy emitted an unknown system_id")
        covered_prompts = {plan.prompt_id for plan in plans}
        covered_systems = {
            system_id
            for plan in plans
            for system_id in (plan.left_system_id, plan.right_system_id)
        }
        if covered_prompts != set(prompt_by_id):
            raise ValueError("strategy did not schedule every evaluation prompt")
        if covered_systems != set(systems):
            raise ValueError("strategy did not schedule every evaluated system")

        candidates: dict[tuple[str, str], Candidate] = {}
        candidate_ids: set[str] = set()
        serialized_candidate_chars = 0
        for prompt in prompt_tuple:
            for system_id in systems:
                candidate_seed = derive_seed(root_seed, "candidate", prompt.prompt_id, system_id)
                candidate = generator_by_id[system_id].generate(
                    prompt,
                    profile,
                    seed=candidate_seed,
                )
                if candidate.system_id != system_id or candidate.prompt_id != prompt.prompt_id:
                    raise ValueError("generator returned a candidate with mismatched identity")
                if candidate.seed != candidate_seed:
                    raise ValueError("generator returned a candidate with a mismatched seed")
                if candidate.candidate_id in candidate_ids:
                    raise ValueError(
                        f"generator returned duplicate candidate_id {candidate.candidate_id!r}"
                    )
                candidate_chars = len(canonical_json(candidate))
                if candidate_chars > self.max_candidate_chars:
                    raise ValueError(
                        "serialized candidate exceeds "
                        f"max_candidate_chars={self.max_candidate_chars}"
                    )
                serialized_candidate_chars += candidate_chars
                if serialized_candidate_chars > self.max_report_chars:
                    raise ValueError(
                        "cumulative candidates exceed "
                        f"max_report_chars={self.max_report_chars}"
                    )
                candidate_ids.add(candidate.candidate_id)
                candidates[(prompt.prompt_id, system_id)] = candidate

        records: list[ComparisonRecord] = []
        for plan in plans:
            left = candidates[(plan.prompt_id, plan.left_system_id)]
            right = candidates[(plan.prompt_id, plan.right_system_id)]
            judge_seed = derive_seed(root_seed, "judge", plan.comparison_id)
            judge_left = left
            judge_right = right
            if self.blind_judge_inputs:
                judge_left = Candidate(
                    candidate_id="candidate_a",
                    system_id="candidate_a",
                    text=left.text,
                    prompt_id=left.prompt_id,
                    seed=0,
                )
                judge_right = Candidate(
                    candidate_id="candidate_b",
                    system_id="candidate_b",
                    text=right.text,
                    prompt_id=right.prompt_id,
                    seed=0,
                )
            decision = self._validated_decision(
                self.judge.judge(
                    prompt_by_id[plan.prompt_id],
                    profile,
                    judge_left,
                    judge_right,
                    seed=judge_seed,
                ),
                prompt_by_id[plan.prompt_id],
                profile,
                left,
                right,
            )
            if (
                unicodedata.normalize("NFKC", left.text)
                == unicodedata.normalize("NFKC", right.text)
                and decision.outcome
                in {ComparisonOutcome.LEFT, ComparisonOutcome.RIGHT}
            ):
                metadata = dict(decision.metadata)
                metadata["invalid_reason"] = (
                    "decisive judgment cannot distinguish identical candidate content"
                )
                metadata["original_outcome"] = decision.outcome.value
                decision = JudgeDecision(
                    outcome=ComparisonOutcome.INVALID,
                    rationale=metadata["invalid_reason"],
                    cited_memory_ids=decision.cited_memory_ids,
                    confidence=decision.confidence,
                    metadata=metadata,
                )
            if len(decision.rationale) > self.max_judge_rationale_chars:
                raise ValueError(
                    "judge rationale exceeds "
                    f"max_judge_rationale_chars={self.max_judge_rationale_chars}"
                )
            records.append(
                ComparisonRecord(
                    plan=plan,
                    left=left,
                    right=right,
                    decision=decision,
                    judge_seed=judge_seed,
                )
            )
        serialized_record_chars = sum(len(canonical_json(record)) for record in records)
        if serialized_record_chars > self.max_report_chars:
            raise ValueError(
                f"comparison records exceed max_report_chars={self.max_report_chars}"
            )

        resolved = self._resolve_comparisons(records)
        ranking = self.ranker.rank(
            systems,
            tuple(
                (
                    record.system_a_id,
                    record.system_b_id,
                    record.outcome,
                )
                for record in resolved
            ),
        )
        ranked_systems = [rating.system_id for rating in ranking.ratings]
        if len(ranked_systems) != len(set(ranked_systems)) or set(
            ranked_systems
        ) != set(systems):
            raise ValueError("ranking backend must return each evaluated system exactly once")
        candidate_fingerprints = tuple(
            canonical_hash(candidate)
            for _, candidate in sorted(candidates.items())
        )
        # Execution order is part of the frozen specification: stateful or
        # remote judges can observe ordering even with per-comparison seeds.
        plan_manifest = tuple(plans)
        spec_identity = {
            "seed": seed,
            "profile": profile.fingerprint,
            "prompts": prompt_tuple,
            "systems": systems,
            "candidate_fingerprints": candidate_fingerprints,
            "plans": plan_manifest,
            "metadata": metadata_snapshot,
            "runner": {
                "max_input_chars": self.max_input_chars,
                "max_candidate_chars": self.max_candidate_chars,
                "max_judge_rationale_chars": self.max_judge_rationale_chars,
                "max_report_chars": self.max_report_chars,
                "max_prompts": self.max_prompts,
                "max_systems": self.max_systems,
                "max_plans": self.max_plans,
                "blind_judge_inputs": self.blind_judge_inputs,
            },
            "strategy": {
                "id": self.strategy.strategy_id,
                "config": strategy_config,
            },
            "judge": {
                "id": self.judge.judge_id,
                "config": judge_config,
            },
            "ranking": {
                "id": self.ranker.ranking_id,
                "config": ranking_config,
            },
            "citation_policy": {
                "id": self.citation_policy.policy_id,
                "config": citation_policy_config,
            },
        }
        spec_id = canonical_hash(spec_identity, prefix="twin_eval_spec_")
        run_identity = {
            "spec_id": spec_id,
            "judgments": tuple(
                (record.plan.comparison_id, canonical_hash(record.decision))
                for record in sorted(records, key=lambda item: item.plan.comparison_id)
            ),
            "ranking": canonical_hash(ranking),
        }
        if normalized_trial_id is not None:
            # A caller-supplied replicate identity keeps byte-identical stochastic
            # executions independently addressable without changing the frozen spec.
            run_identity["trial_id"] = normalized_trial_id
        report_metadata = dict(metadata_snapshot)
        report_metadata["spec_id"] = spec_id
        if normalized_trial_id is not None:
            report_metadata["trial_id"] = normalized_trial_id
        report_metadata["reproducibility_manifest"] = {
            "candidate_fingerprints": candidate_fingerprints,
            "plan_digest": canonical_hash(plan_manifest),
            "runner": spec_identity["runner"],
            "strategy": spec_identity["strategy"],
            "judge": spec_identity["judge"],
            "ranking": spec_identity["ranking"],
            "citation_policy": spec_identity["citation_policy"],
        }
        report = EvaluationReport(
            run_id=canonical_hash(run_identity, prefix="twin_eval_"),
            seed=seed,
            profile_fingerprint=profile.fingerprint,
            prompts=prompt_tuple,
            systems=systems,
            comparisons=tuple(records),
            resolved_comparisons=resolved,
            ranking=ranking,
            metadata=report_metadata,
        )
        if len(canonical_json(report)) > self.max_report_chars:
            raise ValueError(
                f"evaluation report exceeds max_report_chars={self.max_report_chars}"
            )
        return report
