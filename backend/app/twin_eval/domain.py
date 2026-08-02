from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field, is_dataclass
from enum import Enum
from typing import Any, Iterator, Mapping


class _FrozenMapping(Mapping[str, Any]):
    """Small immutable JSON mapping used at artifact boundaries."""

    __slots__ = ("_values",)

    def __init__(self, values: Mapping[str, Any]) -> None:
        self._values = dict(values)

    def __getitem__(self, key: str) -> Any:
        return self._values[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __deepcopy__(self, memo: dict[int, Any]) -> _FrozenMapping:
        del memo
        return self

    def __repr__(self) -> str:
        return repr(self._values)


def _freeze_json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical mapping keys must be strings")
        return _FrozenMapping(
            {key: _freeze_json_value(item) for key, item in value.items()}
        )
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_json_value(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("canonical values must contain only finite floats")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def _freeze_metadata(value: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen = _freeze_json_value(value)
    if not isinstance(frozen, Mapping):
        raise TypeError("metadata must be a mapping")
    return frozen


def _json_value(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _json_value(item) for key, item in asdict(value).items()}
    if isinstance(value, Mapping):
        if any(not isinstance(key, str) for key in value):
            raise TypeError("canonical mapping keys must be strings")
        return {key: _json_value(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, float):
        if not (-float("inf") < value < float("inf")):
            raise ValueError("canonical values must contain only finite floats")
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"unsupported canonical value: {type(value).__name__}")


def canonical_json(value: Any) -> str:
    """Stable, compact JSON used for run IDs and reproducible seed derivation."""
    return json.dumps(
        _json_value(value),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_hash(value: Any, *, prefix: str = "") -> str:
    digest = hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    return f"{prefix}{digest}"


def derive_seed(root_seed: int | str, *parts: Any) -> int:
    """Derive a platform-stable 64-bit child seed without relying on hash()."""
    payload = {"root_seed": root_seed, "parts": list(parts)}
    return int(canonical_hash(payload)[:16], 16)


class ComparisonOutcome(str, Enum):
    LEFT = "left"
    RIGHT = "right"
    TIE = "tie"
    ABSTAIN = "abstain"
    INVALID = "invalid"
    BOTH_BAD = "both_bad"

    @classmethod
    def normalize(cls, value: ComparisonOutcome | str) -> ComparisonOutcome:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "a": cls.LEFT,
            "candidate_a": cls.LEFT,
            "left": cls.LEFT,
            "b": cls.RIGHT,
            "candidate_b": cls.RIGHT,
            "right": cls.RIGHT,
            "draw": cls.TIE,
            "equal": cls.TIE,
            "tie": cls.TIE,
            "abstain": cls.ABSTAIN,
            "insufficient_evidence": cls.ABSTAIN,
            "invalid": cls.INVALID,
            "malformed": cls.INVALID,
            "neither": cls.BOTH_BAD,
            "both_bad": cls.BOTH_BAD,
        }
        if normalized not in aliases:
            raise ValueError(f"unsupported comparison outcome: {value!r}")
        return aliases[normalized]

    def swapped(self) -> ComparisonOutcome:
        if self is ComparisonOutcome.LEFT:
            return ComparisonOutcome.RIGHT
        if self is ComparisonOutcome.RIGHT:
            return ComparisonOutcome.LEFT
        return self


@dataclass(frozen=True)
class CitedProfileItem:
    memory_id: str
    content: str
    source_url: str | None = None
    layer: str | None = None
    author_class: str = "user"
    status: str = "active"
    trust_score: float = 1.0

    def __post_init__(self) -> None:
        if not self.memory_id.strip() or not self.content.strip():
            raise ValueError("profile items require memory_id and content")
        if (
            isinstance(self.trust_score, bool)
            or not isinstance(self.trust_score, (int, float))
            or not math.isfinite(float(self.trust_score))
            or not 0 <= self.trust_score <= 1
        ):
            raise ValueError("profile trust_score must be between 0 and 1")


@dataclass(frozen=True)
class HeldOutProfile:
    profile_id: str
    items: tuple[CitedProfileItem, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
        if not self.profile_id.strip():
            raise ValueError("profile_id is required")
        if not self.items:
            raise ValueError("a held-out profile needs at least one cited item")
        ids = [item.memory_id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("profile memory_id values must be unique")

    @property
    def fingerprint(self) -> str:
        return canonical_hash(self, prefix="profile_")


@dataclass(frozen=True)
class EvaluationPrompt:
    prompt_id: str
    text: str
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
        if not self.prompt_id.strip() or not self.text.strip():
            raise ValueError("evaluation prompts require prompt_id and text")


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    system_id: str
    text: str
    prompt_id: str
    seed: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
        if not all((self.candidate_id.strip(), self.system_id.strip(), self.text.strip(), self.prompt_id.strip())):
            raise ValueError("candidates require non-empty identity, system, prompt, and text")
        if isinstance(self.seed, bool) or not isinstance(self.seed, int):
            raise ValueError("candidate seed must be an integer")


@dataclass(frozen=True)
class ComparisonPlan:
    comparison_id: str
    logical_comparison_id: str
    prompt_id: str
    left_system_id: str
    right_system_id: str
    repetition: int = 0
    swapped: bool = False

    def __post_init__(self) -> None:
        if not all(
            (
                self.comparison_id.strip(),
                self.logical_comparison_id.strip(),
                self.prompt_id.strip(),
                self.left_system_id.strip(),
                self.right_system_id.strip(),
            )
        ):
            raise ValueError("comparison plans require complete identities")
        if self.left_system_id == self.right_system_id:
            raise ValueError("a system cannot be compared with itself")
        if (
            isinstance(self.repetition, bool)
            or not isinstance(self.repetition, int)
            or self.repetition < 0
        ):
            raise ValueError("repetition must be a non-negative integer")
        if not isinstance(self.swapped, bool):
            raise ValueError("swapped must be boolean")


@dataclass(frozen=True)
class JudgeDecision:
    outcome: ComparisonOutcome
    rationale: str = ""
    cited_memory_ids: tuple[str, ...] = ()
    confidence: float | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", ComparisonOutcome.normalize(self.outcome))
        object.__setattr__(self, "cited_memory_ids", tuple(self.cited_memory_ids))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))
        if any(
            not isinstance(memory_id, str) or not memory_id.strip()
            for memory_id in self.cited_memory_ids
        ):
            raise ValueError("cited_memory_ids must contain non-empty strings")
        if self.confidence is not None:
            confidence = self.confidence
            if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
                raise ValueError("confidence must be a finite number between 0 and 1")
            if not (-float("inf") < float(confidence) < float("inf")) or not 0 <= float(confidence) <= 1:
                raise ValueError("confidence must be a finite number between 0 and 1")


@dataclass(frozen=True)
class ComparisonRecord:
    plan: ComparisonPlan
    left: Candidate
    right: Candidate
    decision: JudgeDecision
    judge_seed: int


@dataclass(frozen=True)
class ResolvedComparison:
    """One canonical outcome per logical pair, after presentation swaps agree."""

    logical_comparison_id: str
    prompt_id: str
    repetition: int
    system_a_id: str
    system_b_id: str
    outcome: ComparisonOutcome
    source_comparison_ids: tuple[str, ...]
    swap_consistent: bool | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "outcome", ComparisonOutcome.normalize(self.outcome))
        object.__setattr__(self, "source_comparison_ids", tuple(self.source_comparison_ids))
        if self.system_a_id >= self.system_b_id:
            raise ValueError("resolved comparison systems must be in canonical order")
        if not self.source_comparison_ids:
            raise ValueError("resolved comparisons require at least one source judgment")
        if (
            isinstance(self.repetition, bool)
            or not isinstance(self.repetition, int)
            or self.repetition < 0
        ):
            raise ValueError("repetition must be a non-negative integer")
        if self.swap_consistent is not None and not isinstance(
            self.swap_consistent, bool
        ):
            raise ValueError("swap_consistent must be boolean or None")


@dataclass(frozen=True)
class SystemRating:
    system_id: str
    score: float
    rank: int | None
    component_rank: int
    comparisons: int
    wins: float

    def __post_init__(self) -> None:
        if not self.system_id.strip():
            raise ValueError("rating system_id is required")
        if not isinstance(self.score, (int, float)) or not math.isfinite(
            float(self.score)
        ):
            raise ValueError("rating score must be finite")
        if self.rank is not None and (
            isinstance(self.rank, bool)
            or not isinstance(self.rank, int)
            or self.rank < 1
        ):
            raise ValueError("rating rank must be a positive integer or None")
        if (
            isinstance(self.component_rank, bool)
            or not isinstance(self.component_rank, int)
            or self.component_rank < 1
        ):
            raise ValueError("component_rank must be a positive integer")
        if (
            isinstance(self.comparisons, bool)
            or not isinstance(self.comparisons, int)
            or self.comparisons < 0
        ):
            raise ValueError("rating comparisons must be a non-negative integer")
        if (
            isinstance(self.wins, bool)
            or not isinstance(self.wins, (int, float))
            or not math.isfinite(float(self.wins))
            or not 0 <= self.wins <= self.comparisons
        ):
            raise ValueError("rating wins must be finite and within comparisons")


@dataclass(frozen=True)
class RankingDiagnostics:
    connected: bool
    components: tuple[tuple[str, ...], ...]
    converged: bool
    iterations: int
    max_delta: float
    log_likelihood: float
    ignored_both_bad: int = 0
    ignored_abstain: int = 0
    ignored_invalid: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "components",
            tuple(tuple(component) for component in self.components),
        )
        if not isinstance(self.connected, bool) or not isinstance(self.converged, bool):
            raise ValueError("ranking connected/converged flags must be boolean")
        if self.connected != (len(self.components) == 1):
            raise ValueError("ranking connected flag must match graph components")
        if (
            isinstance(self.iterations, bool)
            or not isinstance(self.iterations, int)
            or self.iterations < 0
        ):
            raise ValueError("ranking iterations must be a non-negative integer")
        if any(
            not isinstance(value, (int, float)) or not math.isfinite(float(value))
            for value in (self.max_delta, self.log_likelihood)
        ):
            raise ValueError("ranking diagnostics must contain finite values")
        for value in (
            self.ignored_both_bad,
            self.ignored_abstain,
            self.ignored_invalid,
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError("ignored outcome counts must be non-negative integers")


@dataclass(frozen=True)
class RankingResult:
    ratings: tuple[SystemRating, ...]
    diagnostics: RankingDiagnostics

    def __post_init__(self) -> None:
        object.__setattr__(self, "ratings", tuple(self.ratings))
        systems = [rating.system_id for rating in self.ratings]
        if len(systems) != len(set(systems)):
            raise ValueError("ranking ratings must contain unique system IDs")
        component_systems = [
            system for component in self.diagnostics.components for system in component
        ]
        if (
            len(component_systems) != len(set(component_systems))
            or set(component_systems) != set(systems)
        ):
            raise ValueError("ranking components must partition rated systems")
        if self.diagnostics.connected and any(
            rating.rank is None for rating in self.ratings
        ):
            raise ValueError("connected rankings require global ranks")
        if not self.diagnostics.connected and any(
            rating.rank is not None for rating in self.ratings
        ):
            raise ValueError("disconnected rankings cannot assign global ranks")


@dataclass(frozen=True)
class EvaluationReport:
    run_id: str
    seed: int | str
    profile_fingerprint: str
    prompts: tuple[EvaluationPrompt, ...]
    systems: tuple[str, ...]
    comparisons: tuple[ComparisonRecord, ...]
    resolved_comparisons: tuple[ResolvedComparison, ...]
    ranking: RankingResult
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "prompts", tuple(self.prompts))
        object.__setattr__(self, "systems", tuple(self.systems))
        object.__setattr__(self, "comparisons", tuple(self.comparisons))
        object.__setattr__(self, "resolved_comparisons", tuple(self.resolved_comparisons))
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def artifact_digest(self) -> str:
        """Content digest for exact replay verification, separate from spec run_id."""
        return canonical_hash(self, prefix="artifact_")
