from __future__ import annotations

import math
import random
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from .domain import (
    Candidate,
    ComparisonOutcome,
    EvaluationReport,
    canonical_hash,
    derive_seed,
)
from .owner_study import OwnerStudyKey, build_owner_study


SCALAR_PUBLIC_SCHEMA_VERSION = "pairwise-scalar-study-public/v1"
SCALAR_KEY_SCHEMA_VERSION = "pairwise-scalar-study-key/v1"
SCALAR_SCORE_SCHEMA_VERSION = "pairwise-scalar-study-scores/v1"
SCALAR_BASELINE_SCHEMA_VERSION = "pairwise-scalar-baseline/v1"


@dataclass(frozen=True)
class ScalarStudyItem:
    item_id: str
    prompt_id: str
    prompt_text: str
    response: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.item_id,
                self.prompt_id,
                self.prompt_text,
                self.response,
            )
        ):
            raise ValueError("scalar-study public items require complete text and IDs")


@dataclass(frozen=True)
class ScalarStudyKeyItem:
    item_id: str
    prompt_id: str
    candidate_id: str
    system_id: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.item_id,
                self.prompt_id,
                self.candidate_id,
                self.system_id,
            )
        ):
            raise ValueError("scalar-study key items require complete IDs")


@dataclass(frozen=True)
class ScalarStudyCohort:
    schema_version: str
    cohort_id: str
    source_owner_cohort_id: str
    instructions: tuple[str, ...]
    items: tuple[ScalarStudyItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "instructions", tuple(self.instructions))
        object.__setattr__(self, "items", tuple(self.items))
        if self.schema_version != SCALAR_PUBLIC_SCHEMA_VERSION:
            raise ValueError("unsupported scalar-study public schema")
        item_ids = [item.item_id for item in self.items]
        if (
            not self.cohort_id.strip()
            or not item_ids
            or len(item_ids) != len(set(item_ids))
        ):
            raise ValueError("scalar-study cohort requires unique items")


@dataclass(frozen=True)
class ScalarStudyKey:
    schema_version: str
    cohort_id: str
    source_owner_cohort_id: str
    source_run_id: str
    source_artifact_digest: str
    items: tuple[ScalarStudyKeyItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        if self.schema_version != SCALAR_KEY_SCHEMA_VERSION:
            raise ValueError("unsupported scalar-study key schema")
        item_ids = [item.item_id for item in self.items]
        candidate_ids = [item.candidate_id for item in self.items]
        if (
            not all(
                value.strip()
                for value in (
                    self.cohort_id,
                    self.source_owner_cohort_id,
                    self.source_run_id,
                    self.source_artifact_digest,
                )
            )
            or not item_ids
            or len(item_ids) != len(set(item_ids))
            or len(candidate_ids) != len(set(candidate_ids))
        ):
            raise ValueError("scalar-study key requires unique, complete items")


def _candidate_by_id(report: EvaluationReport) -> dict[str, Candidate]:
    candidates: dict[str, Candidate] = {}
    for comparison in report.comparisons:
        for candidate in (comparison.left, comparison.right):
            existing = candidates.get(candidate.candidate_id)
            if existing is not None and existing != candidate:
                raise ValueError("candidate ID aliases conflicting report candidates")
            candidates[candidate.candidate_id] = candidate
    return candidates


def build_scalar_study(
    report: EvaluationReport,
    owner_key: OwnerStudyKey,
) -> tuple[ScalarStudyCohort, ScalarStudyKey]:
    """Export every frozen candidate once for identity-blind pointwise scoring."""
    if (
        owner_key.source_run_id != report.run_id
        or owner_key.source_artifact_digest != report.artifact_digest
    ):
        raise ValueError("owner key does not match the evaluation report")
    _, expected_owner_key = build_owner_study(
        report,
        seed=owner_key.seed,
        reversed_repeat_fraction=owner_key.reversed_repeat_fraction,
    )
    if owner_key != expected_owner_key:
        raise ValueError("owner key does not match its deterministic source")
    candidates = _candidate_by_id(report)
    prompts = {prompt.prompt_id: prompt for prompt in report.prompts}
    selected_ids = {
        candidate_id
        for item in owner_key.items
        for candidate_id in (
            item.displayed_a_candidate_id,
            item.displayed_b_candidate_id,
        )
    }
    if not selected_ids or not selected_ids.issubset(candidates):
        raise ValueError("owner key references unknown or empty candidate set")
    cohort_id = canonical_hash(
        {
            "owner_cohort_id": owner_key.cohort_id,
            "source_artifact_digest": report.artifact_digest,
            "candidate_fingerprints": tuple(
                canonical_hash(candidates[candidate_id])
                for candidate_id in sorted(selected_ids)
            ),
        },
        prefix="scalar_cohort_",
    )
    rows: list[tuple[ScalarStudyItem, ScalarStudyKeyItem]] = []
    for candidate_id in sorted(selected_ids):
        candidate = candidates[candidate_id]
        item_id = canonical_hash(
            {
                "scalar_cohort_id": cohort_id,
                "candidate_fingerprint": canonical_hash(candidate),
            },
            prefix="scalar_item_",
        )
        rows.append(
            (
                ScalarStudyItem(
                    item_id,
                    candidate.prompt_id,
                    prompts[candidate.prompt_id].text,
                    candidate.text,
                ),
                ScalarStudyKeyItem(
                    item_id,
                    candidate.prompt_id,
                    candidate.candidate_id,
                    candidate.system_id,
                ),
            )
        )
    random.Random(
        derive_seed(owner_key.seed, cohort_id, "scalar-item-order")
    ).shuffle(rows)
    public = ScalarStudyCohort(
        SCALAR_PUBLIC_SCHEMA_VERSION,
        cohort_id,
        owner_key.cohort_id,
        (
            "Score each response independently; no competing response is shown.",
            "Use a 0-100 scale for how well the response represents the owner.",
            "Apply the same rubric and thresholds to every item.",
        ),
        tuple(item for item, _ in rows),
    )
    private = ScalarStudyKey(
        SCALAR_KEY_SCHEMA_VERSION,
        cohort_id,
        owner_key.cohort_id,
        report.run_id,
        report.artifact_digest,
        tuple(item for _, item in rows),
    )
    return public, private


def scalar_scores_template(cohort: ScalarStudyCohort) -> dict[str, Any]:
    return {
        "schema_version": SCALAR_SCORE_SCHEMA_VERSION,
        "cohort_id": cohort.cohort_id,
        "scores": [
            {"item_id": item.item_id, "score": None} for item in cohort.items
        ],
    }


def scalar_cohort_from_dict(value: Mapping[str, Any]) -> ScalarStudyCohort:
    if value.get("schema_version") != SCALAR_PUBLIC_SCHEMA_VERSION:
        raise ValueError("unsupported scalar-study public schema")
    return ScalarStudyCohort(
        SCALAR_PUBLIC_SCHEMA_VERSION,
        str(value["cohort_id"]),
        str(value["source_owner_cohort_id"]),
        tuple(str(item) for item in value.get("instructions", [])),
        tuple(
            ScalarStudyItem(
                str(item["item_id"]),
                str(item["prompt_id"]),
                str(item["prompt_text"]),
                str(item["response"]),
            )
            for item in value["items"]
        ),
    )


def scalar_key_from_dict(value: Mapping[str, Any]) -> ScalarStudyKey:
    if value.get("schema_version") != SCALAR_KEY_SCHEMA_VERSION:
        raise ValueError("unsupported scalar-study key schema")
    return ScalarStudyKey(
        SCALAR_KEY_SCHEMA_VERSION,
        str(value["cohort_id"]),
        str(value["source_owner_cohort_id"]),
        str(value["source_run_id"]),
        str(value["source_artifact_digest"]),
        tuple(
            ScalarStudyKeyItem(
                str(item["item_id"]),
                str(item["prompt_id"]),
                str(item["candidate_id"]),
                str(item["system_id"]),
            )
            for item in value["items"]
        ),
    )


def scalar_scores_from_dict(
    value: Mapping[str, Any],
    key: ScalarStudyKey,
) -> dict[str, float]:
    if value.get("schema_version") != SCALAR_SCORE_SCHEMA_VERSION:
        raise ValueError("unsupported scalar-study score schema")
    if value.get("cohort_id") != key.cohort_id:
        raise ValueError("scalar scores do not match the private key")
    scores: dict[str, float] = {}
    for item in value["scores"]:
        item_id = str(item["item_id"])
        score = item.get("score")
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 100
        ):
            raise ValueError("scalar scores must be finite numbers from 0 to 100")
        if item_id in scores:
            raise ValueError("scalar scores contain duplicate item IDs")
        scores[item_id] = float(score)
    expected = {item.item_id for item in key.items}
    if set(scores) != expected:
        raise ValueError("scalar scores must exactly cover the private key")
    return scores


def scalar_baseline_from_scores(
    owner_key: OwnerStudyKey,
    scalar_key: ScalarStudyKey,
    scores: Mapping[str, float],
    *,
    tie_margin: float = 0.0,
    both_bad_at_or_below: float | None = None,
) -> dict[str, Any]:
    """Convert frozen pointwise scores into pair outcomes without re-judging pairs."""
    if scalar_key.source_owner_cohort_id != owner_key.cohort_id:
        raise ValueError("scalar key and owner key do not match")
    if (
        scalar_key.source_run_id != owner_key.source_run_id
        or scalar_key.source_artifact_digest != owner_key.source_artifact_digest
    ):
        raise ValueError("scalar key and owner key reference different artifacts")
    expected_candidate_mapping: dict[str, tuple[str, str]] = {}
    for item in owner_key.items:
        for system_id, candidate_id in (
            (item.displayed_a_system_id, item.displayed_a_candidate_id),
            (item.displayed_b_system_id, item.displayed_b_candidate_id),
        ):
            identity = (item.prompt_id, system_id)
            existing = expected_candidate_mapping.setdefault(candidate_id, identity)
            if existing != identity:
                raise ValueError("owner key aliases one candidate across identities")
    actual_candidate_mapping = {
        item.candidate_id: (item.prompt_id, item.system_id)
        for item in scalar_key.items
    }
    if actual_candidate_mapping != expected_candidate_mapping:
        raise ValueError("scalar key candidate mapping does not match the owner key")
    if (
        isinstance(tie_margin, bool)
        or not isinstance(tie_margin, (int, float))
        or not math.isfinite(float(tie_margin))
        or float(tie_margin) < 0
    ):
        raise ValueError("tie_margin must be a non-negative finite number")
    if both_bad_at_or_below is not None and (
        isinstance(both_bad_at_or_below, bool)
        or not isinstance(both_bad_at_or_below, (int, float))
        or not math.isfinite(float(both_bad_at_or_below))
        or not 0 <= float(both_bad_at_or_below) <= 100
    ):
        raise ValueError("both_bad_at_or_below must be between 0 and 100")
    key_by_candidate = {
        item.candidate_id: item for item in scalar_key.items
    }
    expected_score_ids = {item.item_id for item in scalar_key.items}
    if set(scores) != expected_score_ids:
        raise ValueError("scores must exactly cover the scalar key")
    score_by_candidate: dict[str, float] = {}
    for candidate_id, item in key_by_candidate.items():
        if item.item_id not in scores:
            raise ValueError("scores do not cover every scalar item")
        score = scores[item.item_id]
        if (
            isinstance(score, bool)
            or not isinstance(score, (int, float))
            or not math.isfinite(float(score))
            or not 0 <= float(score) <= 100
        ):
            raise ValueError("scalar scores must be finite numbers from 0 to 100")
        score_by_candidate[candidate_id] = float(score)
    groups: dict[str, dict[str, str]] = {}
    for item in owner_key.items:
        mapping = groups.setdefault(item.pair_group_id, {})
        for system_id, candidate_id in (
            (item.displayed_a_system_id, item.displayed_a_candidate_id),
            (item.displayed_b_system_id, item.displayed_b_candidate_id),
        ):
            existing = mapping.setdefault(system_id, candidate_id)
            if existing != candidate_id:
                raise ValueError("owner study pair group changed frozen candidate")
    outcomes: dict[str, str] = {}
    for pair_group_id, candidates_by_system in sorted(groups.items()):
        owner_item = next(
            item for item in owner_key.items if item.pair_group_id == pair_group_id
        )
        candidate_a = candidates_by_system[owner_item.canonical_system_a_id]
        candidate_b = candidates_by_system[owner_item.canonical_system_b_id]
        score_a = score_by_candidate[candidate_a]
        score_b = score_by_candidate[candidate_b]
        if (
            both_bad_at_or_below is not None
            and score_a <= both_bad_at_or_below
            and score_b <= both_bad_at_or_below
        ):
            outcome = ComparisonOutcome.BOTH_BAD
        elif abs(score_a - score_b) <= float(tie_margin):
            outcome = ComparisonOutcome.TIE
        elif score_a > score_b:
            outcome = ComparisonOutcome.LEFT
        else:
            outcome = ComparisonOutcome.RIGHT
        outcomes[pair_group_id] = outcome.value
    return {
        "schema_version": SCALAR_BASELINE_SCHEMA_VERSION,
        "owner_cohort_id": owner_key.cohort_id,
        "scalar_cohort_id": scalar_key.cohort_id,
        "source_run_id": owner_key.source_run_id,
        "source_artifact_digest": owner_key.source_artifact_digest,
        "method": "independent_pointwise_0_100",
        "tie_margin": float(tie_margin),
        "both_bad_at_or_below": both_bad_at_or_below,
        "scores_digest": canonical_hash(dict(sorted(scores.items()))),
        "outcomes": outcomes,
    }


def baseline_outcomes_from_dict(
    value: Mapping[str, Any],
    owner_key: OwnerStudyKey | None = None,
) -> Mapping[str, ComparisonOutcome | str]:
    if value.get("schema_version") == SCALAR_BASELINE_SCHEMA_VERSION:
        if owner_key is not None and (
            value.get("owner_cohort_id") != owner_key.cohort_id
            or value.get("source_run_id") != owner_key.source_run_id
            or value.get("source_artifact_digest")
            != owner_key.source_artifact_digest
        ):
            raise ValueError("scalar baseline does not match the owner study")
        outcomes = value.get("outcomes")
        if not isinstance(outcomes, Mapping):
            raise ValueError("scalar baseline outcomes must be an object")
        return {
            str(pair_group_id): ComparisonOutcome.normalize(str(outcome))
            for pair_group_id, outcome in outcomes.items()
        }
    return value
