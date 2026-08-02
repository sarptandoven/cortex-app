from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping, Protocol
import unicodedata

from .domain import (
    Candidate,
    ComparisonOutcome,
    EvaluationPrompt,
    HeldOutProfile,
    JudgeDecision,
    canonical_hash,
)


class CitationValidationPolicy(Protocol):
    policy_id: str

    def reproducibility_config(self) -> Mapping[str, object]: ...

    def invalid_reason(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        decision: JudgeDecision,
    ) -> str | None: ...


@dataclass(frozen=True)
class EligibleCitationPolicy:
    """Syntactic provenance floor shared by every pairwise judge."""

    policy_id: str = "eligible_owner_evidence_v1"

    def reproducibility_config(self) -> Mapping[str, object]:
        return {}

    def invalid_reason(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        decision: JudgeDecision,
    ) -> str | None:
        del prompt, left, right
        allowed = {
            item.memory_id
            for item in profile.items
            if item.author_class == "user"
            and item.status == "active"
            and item.trust_score > 0
        }
        cited = set(decision.cited_memory_ids)
        if len(cited) != len(decision.cited_memory_ids):
            return "judge citations must be unique"
        if not cited.issubset(allowed):
            return (
                "judge cited evidence that is missing, inactive, non-owner-authored, "
                "or zero-trust"
            )
        if (
            decision.outcome in {ComparisonOutcome.LEFT, ComparisonOutcome.RIGHT}
            and not cited
        ):
            return "decisive judgments require cited profile evidence"
        return None


@dataclass(frozen=True)
class PromptScopedCitationPolicy:
    """Require citations to come from a preregistered prompt-specific evidence set.

    The mapping is the trusted boundary where a profile builder resolves
    relevance and contradictions. The runner then makes that decision
    enforceable and reproducible rather than accepting any eligible memory.
    """

    allowed_memory_ids: Mapping[str, tuple[str, ...]]
    require_scope_for_decisive: bool = True
    policy_id: str = "prompt_scoped_owner_evidence_v1"

    def __post_init__(self) -> None:
        normalized: dict[str, tuple[str, ...]] = {}
        for prompt_id, memory_ids in self.allowed_memory_ids.items():
            if not isinstance(prompt_id, str) or not prompt_id.strip():
                raise ValueError("citation scope prompt IDs must be non-empty strings")
            values = tuple(memory_ids)
            if any(
                not isinstance(memory_id, str) or not memory_id.strip()
                for memory_id in values
            ):
                raise ValueError("citation scopes require non-empty memory IDs")
            if len(values) != len(set(values)):
                raise ValueError("citation scope memory IDs must be unique")
            normalized[prompt_id] = tuple(sorted(values))
        object.__setattr__(
            self,
            "allowed_memory_ids",
            MappingProxyType(dict(sorted(normalized.items()))),
        )
        if not isinstance(self.require_scope_for_decisive, bool):
            raise ValueError("require_scope_for_decisive must be boolean")

    def reproducibility_config(self) -> Mapping[str, object]:
        return {
            "allowed_memory_ids": self.allowed_memory_ids,
            "require_scope_for_decisive": self.require_scope_for_decisive,
            "profile_scope_mode": "exact_prompt_scope_v1",
        }

    def scope_profile(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
    ) -> HeldOutProfile:
        """Return only evidence preregistered for this prompt.

        Citation scope is also a disclosure boundary: generators and remote
        judges must never receive evidence selected for another prompt.
        """

        scoped_ids = self.allowed_memory_ids.get(prompt.prompt_id)
        if not scoped_ids:
            raise ValueError(
                f"prompt {prompt.prompt_id!r} has no eligible profile evidence"
            )
        by_id = {item.memory_id: item for item in profile.items}
        unknown = sorted(set(scoped_ids) - set(by_id))
        if unknown:
            raise ValueError(
                "prompt profile scope references unknown memory IDs: "
                + ", ".join(unknown)
            )
        items = tuple(by_id[memory_id] for memory_id in scoped_ids)
        ineligible = [
            item.memory_id
            for item in items
            if item.author_class != "user"
            or item.status != "active"
            or item.trust_score <= 0
        ]
        if ineligible:
            raise ValueError(
                "prompt profile scope contains ineligible memory IDs: "
                + ", ".join(sorted(ineligible))
            )
        source_fingerprint = profile.fingerprint
        return HeldOutProfile(
            profile_id=canonical_hash(
                {
                    "source_profile_fingerprint": source_fingerprint,
                    "prompt_id": prompt.prompt_id,
                    "memory_ids": scoped_ids,
                },
                prefix="prompt_profile_",
            ),
            items=items,
            metadata={
                "prompt_id": prompt.prompt_id,
                "scope_policy_id": self.policy_id,
                "source_profile_fingerprint": source_fingerprint,
            },
        )

    def invalid_reason(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        decision: JudgeDecision,
    ) -> str | None:
        base_reason = EligibleCitationPolicy().invalid_reason(
            prompt,
            profile,
            left,
            right,
            decision,
        )
        if base_reason:
            return base_reason
        scoped = self.allowed_memory_ids.get(prompt.prompt_id)
        decisive = decision.outcome in {
            ComparisonOutcome.LEFT,
            ComparisonOutcome.RIGHT,
        }
        if scoped is None:
            if decisive and self.require_scope_for_decisive:
                return "decisive judgment has no preregistered prompt evidence scope"
            return None
        if not set(decision.cited_memory_ids).issubset(scoped):
            return "judge cited eligible but out-of-scope profile evidence"
        return None


def normalize_evidence_text(value: str) -> str:
    """Canonical form shared by quote validation and retention redaction."""

    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class QuotedEvidenceCitationPolicy:
    """Require auditable quotes that actually occur in every cited memory.

    This verifies provenance and quote fidelity. It intentionally does not
    claim that substring matching proves semantic entailment.
    """

    base_policy: CitationValidationPolicy = EligibleCitationPolicy()
    min_quote_chars: int = 4
    max_quote_chars: int = 500
    policy_id: str = "quoted_owner_evidence_v1"

    def __post_init__(self) -> None:
        if (
            isinstance(self.min_quote_chars, bool)
            or not isinstance(self.min_quote_chars, int)
            or self.min_quote_chars < 1
        ):
            raise ValueError("min_quote_chars must be a positive integer")
        if (
            isinstance(self.max_quote_chars, bool)
            or not isinstance(self.max_quote_chars, int)
            or self.max_quote_chars < self.min_quote_chars
        ):
            raise ValueError("max_quote_chars must be at least min_quote_chars")

    def reproducibility_config(self) -> Mapping[str, object]:
        snapshot = self.base_policy.reproducibility_config()
        if not isinstance(snapshot, Mapping):
            raise TypeError("base citation reproducibility_config must be a mapping")
        return {
            "base_policy": {
                "id": self.base_policy.policy_id,
                "config": snapshot,
            },
            "min_quote_chars": self.min_quote_chars,
            "max_quote_chars": self.max_quote_chars,
        }

    def scope_profile(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
    ) -> HeldOutProfile:
        scope = getattr(self.base_policy, "scope_profile", None)
        if not callable(scope):
            return profile
        scoped = scope(prompt, profile)
        if not isinstance(scoped, HeldOutProfile):
            raise TypeError("scope_profile() must return HeldOutProfile")
        return scoped

    def invalid_reason(
        self,
        prompt: EvaluationPrompt,
        profile: HeldOutProfile,
        left: Candidate,
        right: Candidate,
        decision: JudgeDecision,
    ) -> str | None:
        base_reason = self.base_policy.invalid_reason(
            prompt,
            profile,
            left,
            right,
            decision,
        )
        if base_reason:
            return base_reason
        if not decision.cited_memory_ids:
            return None
        raw_quotes: Any = decision.metadata.get("evidence_quotes")
        if not isinstance(raw_quotes, Mapping):
            return "judge citations require auditable evidence_quotes metadata"
        profile_by_id = {item.memory_id: item for item in profile.items}
        if set(raw_quotes) != set(decision.cited_memory_ids):
            return "evidence_quotes must exactly cover cited_memory_ids"
        for memory_id in decision.cited_memory_ids:
            quotes = raw_quotes[memory_id]
            if not isinstance(quotes, (tuple, list)) or not quotes:
                return "every cited memory requires at least one evidence quote"
            content = normalize_evidence_text(
                profile_by_id[memory_id].content
            )
            for quote in quotes:
                if not isinstance(quote, str):
                    return "evidence quotes must be strings"
                normalized_quote = normalize_evidence_text(quote)
                if not self.min_quote_chars <= len(normalized_quote) <= self.max_quote_chars:
                    return "evidence quote length is outside configured bounds"
                if normalized_quote not in content:
                    return "evidence quote does not occur in the cited memory"
        return None
