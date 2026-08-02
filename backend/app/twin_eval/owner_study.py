from __future__ import annotations

import itertools
import math
import random
from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence

from .domain import (
    Candidate,
    ComparisonOutcome,
    EvaluationReport,
    canonical_hash,
    derive_seed,
)
from .metrics import clustered_bootstrap_mean, paired_clustered_bootstrap_delta


PUBLIC_SCHEMA_VERSION = "pairwise-owner-study-public/v1"
KEY_SCHEMA_VERSION = "pairwise-owner-study-key/v1"
LABEL_SCHEMA_VERSION = "pairwise-owner-study-labels/v1"
ANALYSIS_SCHEMA_VERSION = "pairwise-owner-study-analysis/v1"


class OwnerLabelOutcome(str, Enum):
    A = "a"
    B = "b"
    TIE = "tie"
    BOTH_BAD = "both_bad"
    ABSTAIN = "abstain"

    @classmethod
    def normalize(cls, value: OwnerLabelOutcome | str) -> OwnerLabelOutcome:
        if isinstance(value, cls):
            return value
        normalized = str(value).strip().lower().replace("-", "_").replace(" ", "_")
        aliases = {
            "a": cls.A,
            "left": cls.A,
            "b": cls.B,
            "right": cls.B,
            "tie": cls.TIE,
            "equal": cls.TIE,
            "both_bad": cls.BOTH_BAD,
            "neither": cls.BOTH_BAD,
            "abstain": cls.ABSTAIN,
            "skip": cls.ABSTAIN,
        }
        if normalized not in aliases:
            raise ValueError(f"unsupported owner label outcome: {value!r}")
        return aliases[normalized]


@dataclass(frozen=True)
class OwnerStudyItem:
    item_id: str
    prompt_id: str
    prompt_text: str
    response_a: str
    response_b: str

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.item_id,
                self.prompt_id,
                self.prompt_text,
                self.response_a,
                self.response_b,
            )
        ):
            raise ValueError("owner-study public items require complete text and IDs")


@dataclass(frozen=True)
class OwnerStudyKeyItem:
    item_id: str
    pair_group_id: str
    logical_comparison_id: str
    prompt_id: str
    canonical_system_a_id: str
    canonical_system_b_id: str
    displayed_a_system_id: str
    displayed_b_system_id: str
    displayed_a_candidate_id: str
    displayed_b_candidate_id: str
    is_reversed_repeat: bool

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.item_id,
                self.pair_group_id,
                self.logical_comparison_id,
                self.prompt_id,
                self.canonical_system_a_id,
                self.canonical_system_b_id,
                self.displayed_a_system_id,
                self.displayed_b_system_id,
                self.displayed_a_candidate_id,
                self.displayed_b_candidate_id,
            )
        ):
            raise ValueError("owner-study key items require complete IDs")
        if self.canonical_system_a_id >= self.canonical_system_b_id:
            raise ValueError("owner-study key systems must be in canonical order")
        if {
            self.displayed_a_system_id,
            self.displayed_b_system_id,
        } != {
            self.canonical_system_a_id,
            self.canonical_system_b_id,
        }:
            raise ValueError("displayed owner-study systems must match canonical systems")
        if not isinstance(self.is_reversed_repeat, bool):
            raise ValueError("is_reversed_repeat must be boolean")


@dataclass(frozen=True)
class OwnerStudyCohort:
    schema_version: str
    cohort_id: str
    source_spec_id: str
    instructions: tuple[str, ...]
    items: tuple[OwnerStudyItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "instructions", tuple(self.instructions))
        object.__setattr__(self, "items", tuple(self.items))
        if self.schema_version != PUBLIC_SCHEMA_VERSION:
            raise ValueError("unsupported owner-study public schema")
        if not self.cohort_id.strip():
            raise ValueError("owner-study cohort_id is required")
        item_ids = [item.item_id for item in self.items]
        if not item_ids or len(item_ids) != len(set(item_ids)):
            raise ValueError("owner-study public item IDs must be non-empty and unique")


@dataclass(frozen=True)
class OwnerStudyKey:
    schema_version: str
    cohort_id: str
    source_run_id: str
    source_artifact_digest: str
    seed: int | str
    reversed_repeat_fraction: float
    items: tuple[OwnerStudyKeyItem, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "items", tuple(self.items))
        if self.schema_version != KEY_SCHEMA_VERSION:
            raise ValueError("unsupported owner-study key schema")
        if not all(
            value.strip()
            for value in (
                self.cohort_id,
                self.source_run_id,
                self.source_artifact_digest,
            )
        ):
            raise ValueError("owner-study key requires source identities")
        if (
            isinstance(self.reversed_repeat_fraction, bool)
            or not isinstance(self.reversed_repeat_fraction, (int, float))
            or not math.isfinite(float(self.reversed_repeat_fraction))
            or not 0 <= self.reversed_repeat_fraction <= 1
        ):
            raise ValueError("owner-study repeat fraction must be between 0 and 1")
        item_ids = [item.item_id for item in self.items]
        if not item_ids or len(item_ids) != len(set(item_ids)):
            raise ValueError("owner-study key item IDs must be non-empty and unique")


@dataclass(frozen=True)
class OwnerLabel:
    item_id: str
    outcome: OwnerLabelOutcome | str

    def __post_init__(self) -> None:
        if not self.item_id.strip():
            raise ValueError("owner labels require item_id")
        object.__setattr__(self, "outcome", OwnerLabelOutcome.normalize(self.outcome))


def _candidate_index(report: EvaluationReport) -> dict[tuple[str, str], Candidate]:
    candidates: dict[tuple[str, str], Candidate] = {}
    for record in report.comparisons:
        for candidate in (record.left, record.right):
            key = (candidate.prompt_id, candidate.system_id)
            existing = candidates.get(key)
            if existing is not None and existing != candidate:
                raise ValueError("report contains conflicting candidates for one prompt/system")
            candidates[key] = candidate
    return candidates


def build_owner_study(
    report: EvaluationReport,
    *,
    seed: int | str = 0,
    reversed_repeat_fraction: float = 0.2,
) -> tuple[OwnerStudyCohort, OwnerStudyKey]:
    """Build a blinded cohort plus a separately held private decoding key."""
    if (
        isinstance(reversed_repeat_fraction, bool)
        or not isinstance(reversed_repeat_fraction, (int, float))
        or not math.isfinite(float(reversed_repeat_fraction))
        or not 0 <= float(reversed_repeat_fraction) <= 1
    ):
        raise ValueError("reversed_repeat_fraction must be finite and between 0 and 1")

    prompts = {prompt.prompt_id: prompt for prompt in report.prompts}
    candidates = _candidate_index(report)
    selected: dict[tuple[str, str, str], Any] = {}
    for resolved in report.resolved_comparisons:
        group = (
            resolved.prompt_id,
            resolved.system_a_id,
            resolved.system_b_id,
        )
        current = selected.get(group)
        if current is None or (
            resolved.repetition,
            resolved.logical_comparison_id,
        ) < (
            current.repetition,
            current.logical_comparison_id,
        ):
            selected[group] = resolved
    if not selected:
        raise ValueError("owner study requires at least one resolved comparison")

    source_spec_id = str(report.metadata.get("spec_id") or "")
    reproducibility_manifest = report.metadata.get("reproducibility_manifest", {})
    if not isinstance(reproducibility_manifest, Mapping):
        reproducibility_manifest = {}
    cohort_identity = {
        "source_spec_id": source_spec_id,
        "candidate_fingerprints": reproducibility_manifest.get(
            "candidate_fingerprints", ()
        ),
        "seed": seed,
        "reversed_repeat_fraction": float(reversed_repeat_fraction),
        "groups": tuple(sorted(selected)),
    }
    cohort_id = canonical_hash(cohort_identity, prefix="owner_cohort_")

    base_rows: list[tuple[OwnerStudyItem, OwnerStudyKeyItem]] = []
    for prompt_id, system_a, system_b in sorted(selected):
        resolved = selected[(prompt_id, system_a, system_b)]
        candidate_a = candidates[(prompt_id, system_a)]
        candidate_b = candidates[(prompt_id, system_b)]
        swap = bool(
            derive_seed(seed, cohort_id, prompt_id, system_a, system_b, "display")
            & 1
        )
        displayed_a, displayed_b = (
            (candidate_b, candidate_a) if swap else (candidate_a, candidate_b)
        )
        pair_group_id = canonical_hash(
            {
                "cohort_id": cohort_id,
                "prompt_id": prompt_id,
                "system_a": system_a,
                "system_b": system_b,
            },
            prefix="owner_pair_",
        )
        item_id = canonical_hash(
            {"pair_group_id": pair_group_id, "presentation": 0},
            prefix="owner_item_",
        )
        base_rows.append(
            (
                OwnerStudyItem(
                    item_id,
                    prompt_id,
                    prompts[prompt_id].text,
                    displayed_a.text,
                    displayed_b.text,
                ),
                OwnerStudyKeyItem(
                    item_id,
                    pair_group_id,
                    resolved.logical_comparison_id,
                    prompt_id,
                    system_a,
                    system_b,
                    displayed_a.system_id,
                    displayed_b.system_id,
                    displayed_a.candidate_id,
                    displayed_b.candidate_id,
                    False,
                ),
            )
        )

    repeat_count = round(len(base_rows) * float(reversed_repeat_fraction))
    repeat_order = sorted(
        range(len(base_rows)),
        key=lambda index: derive_seed(
            seed,
            cohort_id,
            base_rows[index][1].pair_group_id,
            "repeat-selection",
        ),
    )
    repeated_indexes = set(repeat_order[:repeat_count])
    rows = list(base_rows)
    for index in sorted(repeated_indexes):
        public, key = base_rows[index]
        item_id = canonical_hash(
            {"pair_group_id": key.pair_group_id, "presentation": 1},
            prefix="owner_item_",
        )
        rows.append(
            (
                OwnerStudyItem(
                    item_id,
                    public.prompt_id,
                    public.prompt_text,
                    public.response_b,
                    public.response_a,
                ),
                OwnerStudyKeyItem(
                    item_id,
                    key.pair_group_id,
                    key.logical_comparison_id,
                    key.prompt_id,
                    key.canonical_system_a_id,
                    key.canonical_system_b_id,
                    key.displayed_b_system_id,
                    key.displayed_a_system_id,
                    key.displayed_b_candidate_id,
                    key.displayed_a_candidate_id,
                    True,
                ),
            )
        )

    random.Random(derive_seed(seed, cohort_id, "item-order")).shuffle(rows)
    cohort = OwnerStudyCohort(
        PUBLIC_SCHEMA_VERSION,
        cohort_id,
        source_spec_id,
        (
            "Judge only the two displayed responses; generator identity is hidden.",
            "Choose a, b, tie, both_bad (neither), or abstain (cannot decide).",
            "Do not try to infer whether an item is a repeated presentation.",
        ),
        tuple(public for public, _ in rows),
    )
    key = OwnerStudyKey(
        KEY_SCHEMA_VERSION,
        cohort_id,
        report.run_id,
        report.artifact_digest,
        seed,
        float(reversed_repeat_fraction),
        tuple(private for _, private in rows),
    )
    return cohort, key


def labels_template(cohort: OwnerStudyCohort) -> dict[str, Any]:
    return {
        "schema_version": LABEL_SCHEMA_VERSION,
        "cohort_id": cohort.cohort_id,
        "labels": [
            {"item_id": item.item_id, "outcome": None}
            for item in cohort.items
        ],
    }


def cohort_from_dict(value: Mapping[str, Any]) -> OwnerStudyCohort:
    if value.get("schema_version") != PUBLIC_SCHEMA_VERSION:
        raise ValueError("unsupported owner-study public schema")
    return OwnerStudyCohort(
        PUBLIC_SCHEMA_VERSION,
        str(value["cohort_id"]),
        str(value.get("source_spec_id", "")),
        tuple(str(item) for item in value.get("instructions", [])),
        tuple(
            OwnerStudyItem(
                str(item["item_id"]),
                str(item["prompt_id"]),
                str(item["prompt_text"]),
                str(item["response_a"]),
                str(item["response_b"]),
            )
            for item in value["items"]
        ),
    )


def key_from_dict(value: Mapping[str, Any]) -> OwnerStudyKey:
    if value.get("schema_version") != KEY_SCHEMA_VERSION:
        raise ValueError("unsupported owner-study key schema")
    raw_items = []
    for item in value["items"]:
        reversed_repeat = item["is_reversed_repeat"]
        if not isinstance(reversed_repeat, bool):
            raise ValueError("is_reversed_repeat must be boolean")
        raw_items.append(
            OwnerStudyKeyItem(
                str(item["item_id"]),
                str(item["pair_group_id"]),
                str(item["logical_comparison_id"]),
                str(item["prompt_id"]),
                str(item["canonical_system_a_id"]),
                str(item["canonical_system_b_id"]),
                str(item["displayed_a_system_id"]),
                str(item["displayed_b_system_id"]),
                str(item["displayed_a_candidate_id"]),
                str(item["displayed_b_candidate_id"]),
                reversed_repeat,
            )
        )
    return OwnerStudyKey(
        KEY_SCHEMA_VERSION,
        str(value["cohort_id"]),
        str(value["source_run_id"]),
        str(value["source_artifact_digest"]),
        value["seed"],
        float(value["reversed_repeat_fraction"]),
        tuple(raw_items),
    )


def labels_from_dict(value: Mapping[str, Any]) -> tuple[OwnerLabel, ...]:
    if value.get("schema_version") != LABEL_SCHEMA_VERSION:
        raise ValueError("unsupported owner-study label schema")
    labels: list[OwnerLabel] = []
    for item in value["labels"]:
        if item.get("outcome") is None:
            raise ValueError("owner-study label template is incomplete")
        labels.append(OwnerLabel(str(item["item_id"]), item["outcome"]))
    return tuple(labels)


def _canonical_owner_outcome(
    label: OwnerLabel,
    key: OwnerStudyKeyItem,
) -> ComparisonOutcome:
    outcome = OwnerLabelOutcome.normalize(label.outcome)
    if outcome is OwnerLabelOutcome.TIE:
        return ComparisonOutcome.TIE
    if outcome is OwnerLabelOutcome.BOTH_BAD:
        return ComparisonOutcome.BOTH_BAD
    if outcome is OwnerLabelOutcome.ABSTAIN:
        return ComparisonOutcome.ABSTAIN
    chosen_system = (
        key.displayed_a_system_id
        if outcome is OwnerLabelOutcome.A
        else key.displayed_b_system_id
    )
    return (
        ComparisonOutcome.LEFT
        if chosen_system == key.canonical_system_a_id
        else ComparisonOutcome.RIGHT
    )


def analyze_owner_study(
    report: EvaluationReport,
    cohort: OwnerStudyCohort,
    key: OwnerStudyKey,
    labels: Sequence[OwnerLabel],
    *,
    bootstrap_seed: int | str = 0,
    bootstrap_resamples: int = 2_000,
    baseline_outcomes: Mapping[str, ComparisonOutcome | str] | None = None,
) -> dict[str, Any]:
    if cohort.cohort_id != key.cohort_id:
        raise ValueError("public cohort and private key do not match")
    if key.source_run_id != report.run_id or key.source_artifact_digest != report.artifact_digest:
        raise ValueError("owner-study key does not match the evaluation report")
    expected_cohort, expected_key = build_owner_study(
        report,
        seed=key.seed,
        reversed_repeat_fraction=key.reversed_repeat_fraction,
    )
    if cohort != expected_cohort:
        raise ValueError(
            "owner-study public cohort does not match its deterministic source"
        )
    if key != expected_key:
        raise ValueError(
            "owner-study private key does not match its deterministic source"
        )
    public_ids = {item.item_id for item in cohort.items}
    key_by_id = {item.item_id: item for item in key.items}
    label_by_id = {label.item_id: label for label in labels}
    if len(key_by_id) != len(key.items) or set(key_by_id) != public_ids:
        raise ValueError("owner-study key does not exactly cover the public cohort")
    if len(label_by_id) != len(labels) or set(label_by_id) != public_ids:
        raise ValueError("labels must cover every public item exactly once")

    resolved_by_id = {
        resolved.logical_comparison_id: resolved
        for resolved in report.resolved_comparisons
    }
    owner_outcomes = {
        item_id: _canonical_owner_outcome(label_by_id[item_id], key_by_id[item_id])
        for item_id in sorted(public_ids)
    }

    repeat_groups: dict[str, list[ComparisonOutcome]] = {}
    for item_id, outcome in owner_outcomes.items():
        repeat_groups.setdefault(key_by_id[item_id].pair_group_id, []).append(outcome)
    repeat_pairs = 0
    repeat_matches = 0
    for outcomes in repeat_groups.values():
        for first, second in itertools.combinations(outcomes, 2):
            repeat_pairs += 1
            repeat_matches += int(first is second)

    pairwise_clusters: dict[str, list[float]] = {}
    baseline_clusters: dict[str, list[float]] = {}
    displayed_a = 0
    displayed_b = 0
    agreement_items = 0
    reversed_repeat_items = 0
    expected_pair_groups = {item.pair_group_id for item in key.items}
    if baseline_outcomes is not None and set(baseline_outcomes) != expected_pair_groups:
        raise ValueError("baseline outcomes must exactly cover every pair group")
    for item_id in sorted(public_ids):
        private = key_by_id[item_id]
        owner = owner_outcomes[item_id]
        raw_owner = OwnerLabelOutcome.normalize(label_by_id[item_id].outcome)
        displayed_a += int(raw_owner is OwnerLabelOutcome.A)
        displayed_b += int(raw_owner is OwnerLabelOutcome.B)
        if private.is_reversed_repeat:
            reversed_repeat_items += 1
            continue
        agreement_items += 1
        predicted = resolved_by_id[private.logical_comparison_id].outcome
        pairwise_clusters.setdefault(private.prompt_id, []).append(
            float(owner is predicted)
        )
        if baseline_outcomes is not None:
            baseline = ComparisonOutcome.normalize(
                baseline_outcomes[private.pair_group_id]
            )
            baseline_clusters.setdefault(private.prompt_id, []).append(
                float(owner is baseline)
            )

    pairwise_interval = clustered_bootstrap_mean(
        pairwise_clusters,
        seed=bootstrap_seed,
        resamples=bootstrap_resamples,
    )
    payload: dict[str, Any] = {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "cohort_id": cohort.cohort_id,
        "items": len(public_ids),
        "independent_pair_groups": len(repeat_groups),
        "agreement_items": agreement_items,
        "reversed_repeat_items": reversed_repeat_items,
        "owner_repeat_pairs": repeat_pairs,
        "owner_repeat_agreement": (
            repeat_matches / repeat_pairs if repeat_pairs else None
        ),
        "owner_displayed_a_choices": displayed_a,
        "owner_displayed_b_choices": displayed_b,
        "owner_position_bias": (
            (displayed_a - displayed_b) / (displayed_a + displayed_b)
            if displayed_a + displayed_b
            else None
        ),
        "pairwise_owner_agreement": pairwise_interval.estimate,
        "pairwise_owner_agreement_ci95": {
            "low": pairwise_interval.low,
            "high": pairwise_interval.high,
            "clusters": pairwise_interval.clusters,
            "resamples": pairwise_interval.resamples,
        },
    }
    if baseline_outcomes is not None:
        baseline_interval = clustered_bootstrap_mean(
            baseline_clusters,
            seed=bootstrap_seed,
            resamples=bootstrap_resamples,
        )
        delta = paired_clustered_bootstrap_delta(
            baseline_clusters,
            pairwise_clusters,
            seed=bootstrap_seed,
            resamples=bootstrap_resamples,
        )
        payload.update(
            {
                "baseline_owner_agreement": baseline_interval.estimate,
                "paired_pairwise_minus_baseline": delta.estimate,
                "paired_pairwise_minus_baseline_ci95": {
                    "low": delta.low,
                    "high": delta.high,
                    "clusters": delta.clusters,
                    "resamples": delta.resamples,
                },
            }
        )
    return payload
