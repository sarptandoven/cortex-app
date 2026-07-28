from __future__ import annotations

import json
import re
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
from .policies import (
    CitationValidationPolicy,
    EligibleCitationPolicy,
    normalize_evidence_text,
)
from .protocols import CandidateGenerator, ComparisonStrategy, PairwiseJudge, RankingBackend
from .scheduling import build_evaluation_schedule


_CORTEX_PROFILE_MANIFEST_KEYS = frozenset(
    {
        "schema_version",
        "builder_id",
        "as_of",
        "config_digest",
        "selection_digest",
        "prompt_scope_digests",
    }
)
_DIGEST_RE = re.compile(r"^[a-z0-9_]+_[0-9a-f]{64}$")
_AS_OF_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _validated_profile_manifest(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("profile_manifest must be a mapping")
    if set(value) != _CORTEX_PROFILE_MANIFEST_KEYS:
        raise ValueError(
            "profile_manifest must use the exact Cortex digest-only schema"
        )
    if value.get("schema_version") != (
        "cortex-pairwise-profile-manifest/v1"
    ):
        raise ValueError("profile_manifest schema_version is unsupported")
    if value.get("builder_id") != "cortex_context_profile_v1":
        raise ValueError("profile_manifest builder_id is unsupported")
    as_of = value.get("as_of")
    if not isinstance(as_of, str) or not _AS_OF_RE.fullmatch(as_of):
        raise ValueError("profile_manifest as_of must be normalized UTC")
    for name in ("config_digest", "selection_digest"):
        digest = value.get(name)
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise ValueError(f"profile_manifest {name} is invalid")
    raw_scopes = value.get("prompt_scope_digests")
    if not isinstance(raw_scopes, (list, tuple)):
        raise ValueError(
            "profile_manifest prompt_scope_digests must be an array"
        )
    scopes: list[list[str]] = []
    seen_prompt_ids: set[str] = set()
    for raw_scope in raw_scopes:
        if not isinstance(raw_scope, (list, tuple)) or len(raw_scope) != 2:
            raise ValueError("profile_manifest prompt scope is invalid")
        prompt_id, digest = raw_scope
        if (
            not isinstance(prompt_id, str)
            or not prompt_id
            or len(prompt_id) > 200
            or prompt_id in seen_prompt_ids
        ):
            raise ValueError("profile_manifest prompt_id is invalid")
        if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
            raise ValueError("profile_manifest prompt scope digest is invalid")
        seen_prompt_ids.add(prompt_id)
        scopes.append([prompt_id, digest])
    if scopes != sorted(scopes, key=lambda item: item[0]):
        raise ValueError(
            "profile_manifest prompt scopes must be uniquely sorted"
        )
    return {
        "schema_version": value["schema_version"],
        "builder_id": value["builder_id"],
        "as_of": as_of,
        "config_digest": value["config_digest"],
        "selection_digest": value["selection_digest"],
        "prompt_scope_digests": scopes,
    }


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
    def _retention_safe_decision(
        decision: JudgeDecision,
    ) -> JudgeDecision:
        """Replace verbatim evidence quotes with content-addressed proofs.

        Citation policies need the quotes briefly to verify occurrence against
        the frozen profile. Reports need only the cited IDs and quote digests;
        retaining the raw excerpts would duplicate private profile evidence in
        plaintext comparison rows.
        """

        metadata = dict(decision.metadata)
        raw_quotes = metadata.pop("evidence_quotes", None)
        if raw_quotes is None:
            return decision
        rationale = decision.rationale
        if isinstance(raw_quotes, Mapping):
            quote_digests: dict[str, tuple[str, ...]] = {}
            for memory_id, quotes in raw_quotes.items():
                if not isinstance(memory_id, str) or not isinstance(
                    quotes,
                    (list, tuple),
                ):
                    continue
                quote_digests[memory_id] = tuple(
                    canonical_hash(
                        {
                            "memory_id": memory_id,
                            "quote": quote,
                        },
                        prefix="evidence_quote_",
                    )
                    for quote in quotes
                    if isinstance(quote, str) and quote
                )
                for quote in quotes:
                    if isinstance(quote, str) and quote:
                        normalized_quote = normalize_evidence_text(quote)
                        if (
                            normalized_quote
                            and normalized_quote
                            in normalize_evidence_text(rationale)
                        ):
                            rationale = "[EVIDENCE_QUOTE_REDACTED]"
            metadata["evidence_quote_digests"] = quote_digests
        return JudgeDecision(
            outcome=decision.outcome,
            rationale=rationale,
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
        profile_manifest_snapshot = _validated_profile_manifest(
            profile.metadata.get("profile_manifest")
        )
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
        scope_profile = getattr(self.citation_policy, "scope_profile", None)
        profile_by_prompt: dict[str, HeldOutProfile] = {}
        for prompt in prompt_tuple:
            scoped_profile = (
                scope_profile(prompt, profile)
                if callable(scope_profile)
                else profile
            )
            if not isinstance(scoped_profile, HeldOutProfile):
                raise TypeError("scope_profile() must return HeldOutProfile")
            profile_by_prompt[prompt.prompt_id] = scoped_profile
        generator_by_id = {generator.system_id: generator for generator in self.generators}
        if len(generator_by_id) != len(self.generators) or len(generator_by_id) < 2:
            raise ValueError("evaluation requires at least two uniquely named generators")
        if len(generator_by_id) > self.max_systems:
            raise ValueError(f"evaluation exceeds max_systems={self.max_systems}")
        schedule = build_evaluation_schedule(
            profile,
            prompt_tuple,
            tuple(generator_by_id),
            self.strategy,
            seed=seed,
            max_plans=self.max_plans,
        )
        systems = schedule.systems
        root_seed = schedule.root_seed
        plans = schedule.plans

        candidates: dict[tuple[str, str], Candidate] = {}
        candidate_ids: set[str] = set()
        serialized_candidate_chars = 0
        for prompt in prompt_tuple:
            for system_id in systems:
                candidate_seed = derive_seed(root_seed, "candidate", prompt.prompt_id, system_id)
                candidate = generator_by_id[system_id].generate(
                    prompt,
                    profile_by_prompt[prompt.prompt_id],
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
                    profile_by_prompt[plan.prompt_id],
                    judge_left,
                    judge_right,
                    seed=judge_seed,
                ),
                prompt_by_id[plan.prompt_id],
                profile_by_prompt[plan.prompt_id],
                left,
                right,
            )
            decision = self._retention_safe_decision(decision)
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
        reproducibility_manifest = {
            "candidate_fingerprints": candidate_fingerprints,
            "plan_digest": canonical_hash(plan_manifest),
            "runner": spec_identity["runner"],
            "strategy": spec_identity["strategy"],
            "judge": spec_identity["judge"],
            "ranking": spec_identity["ranking"],
            "citation_policy": spec_identity["citation_policy"],
        }
        if profile_manifest_snapshot is not None:
            # Cortex-built profiles expose a digest-only audit manifest. Copy
            # it automatically so repository persistence cannot depend on a
            # future execution route remembering to thread optional metadata.
            reproducibility_manifest["profile_manifest"] = (
                profile_manifest_snapshot
            )
        report_metadata["reproducibility_manifest"] = (
            reproducibility_manifest
        )
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
