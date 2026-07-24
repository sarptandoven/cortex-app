from __future__ import annotations

import itertools
import math
import statistics
from collections import Counter
from typing import Any, Mapping, Sequence

from .domain import ComparisonOutcome, EvaluationReport


STABILITY_SCHEMA_VERSION = "pairwise-twin-stability/v1"


def _percentile(values: Sequence[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return ordered[low]
    weight = position - low
    return ordered[low] * (1 - weight) + ordered[high] * weight


def _finite_number(value: Any) -> float | None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(float(value))
    ):
        return None
    return float(value)


def analyze_stability_reports(
    reports: Sequence[EvaluationReport],
) -> dict[str, Any]:
    """Analyze repeated stochastic executions of one frozen evaluation spec."""
    report_tuple = tuple(reports)
    if len(report_tuple) < 2:
        raise ValueError("stability analysis requires at least two reports")
    run_ids = [report.run_id for report in report_tuple]
    if len(run_ids) != len(set(run_ids)):
        raise ValueError(
            "stability analysis requires distinct run_id values; pass a unique "
            "trial_id to repeated executions that may produce identical results"
        )
    spec_ids = {str(report.metadata.get("spec_id") or "") for report in report_tuple}
    if "" in spec_ids or len(spec_ids) != 1:
        raise ValueError("stability reports must share one non-empty spec_id")
    logical_ids = {
        tuple(sorted(item.logical_comparison_id for item in report.resolved_comparisons))
        for report in report_tuple
    }
    if len(logical_ids) != 1:
        raise ValueError("stability reports must share the same logical comparisons")
    outcomes_by_run = [
        {
            item.logical_comparison_id: item.outcome
            for item in report.resolved_comparisons
        }
        for report in report_tuple
    ]
    logical_id_tuple = next(iter(logical_ids))
    per_comparison: dict[str, Any] = {}
    modal_agreements: list[float] = []
    entropies: list[float] = []
    for logical_id in logical_id_tuple:
        counts = Counter(run[logical_id].value for run in outcomes_by_run)
        modal = max(counts.values()) / len(report_tuple)
        entropy = -sum(
            (count / len(report_tuple)) * math.log2(count / len(report_tuple))
            for count in counts.values()
        )
        normalized_entropy = (
            entropy / math.log2(min(len(ComparisonOutcome), len(report_tuple)))
            if len(report_tuple) > 1
            else 0.0
        )
        modal_agreements.append(modal)
        entropies.append(normalized_entropy)
        per_comparison[logical_id] = {
            "outcome_counts": dict(sorted(counts.items())),
            "modal_agreement": modal,
            "normalized_outcome_entropy": normalized_entropy,
        }

    inter_run: list[float] = []
    for first, second in itertools.combinations(outcomes_by_run, 2):
        inter_run.append(
            sum(first[key] is second[key] for key in logical_id_tuple)
            / len(logical_id_tuple)
        )

    top_sets: list[tuple[str, ...]] = []
    missing_top_rank_runs = 0
    for report in report_tuple:
        ranked = [
            rating.system_id
            for rating in report.ranking.ratings
            if rating.rank == 1
        ]
        if ranked:
            top_sets.append(tuple(sorted(ranked)))
        else:
            missing_top_rank_runs += 1
    top_counts = Counter(top_sets)
    top_mode = (
        max(top_counts.values()) / len(top_sets)
        if len(top_sets) >= 2 and missing_top_rank_runs == 0
        else None
    )

    latencies: list[float] = []
    confidences: list[float] = []
    usage_totals = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
    }
    usage_observations = Counter()
    failure_types: Counter[str] = Counter()
    for report in report_tuple:
        for comparison in report.comparisons:
            metadata = comparison.decision.metadata
            latency = _finite_number(metadata.get("latency_seconds"))
            if latency is not None and latency >= 0:
                latencies.append(latency)
            confidence = _finite_number(comparison.decision.confidence)
            if confidence is not None:
                confidences.append(confidence)
            usage = metadata.get("usage")
            if isinstance(usage, Mapping):
                for field in usage_totals:
                    value = _finite_number(usage.get(field))
                    if value is not None and value >= 0:
                        usage_totals[field] += value
                        usage_observations[field] += 1
            failure_type = metadata.get("failure_type")
            if isinstance(failure_type, str) and failure_type:
                failure_types[failure_type] += 1

    latency_payload = {
        "observations": len(latencies),
        "mean_seconds": statistics.fmean(latencies) if latencies else None,
        "p50_seconds": _percentile(latencies, 0.5),
        "p95_seconds": _percentile(latencies, 0.95),
        "max_seconds": max(latencies) if latencies else None,
    }
    confidence_payload = {
        "observations": len(confidences),
        "mean": statistics.fmean(confidences) if confidences else None,
        "population_variance": (
            statistics.pvariance(confidences) if len(confidences) > 1 else None
        ),
    }
    return {
        "schema_version": STABILITY_SCHEMA_VERSION,
        "spec_id": next(iter(spec_ids)),
        "runs": len(report_tuple),
        "unique_artifacts": len(
            {report.artifact_digest for report in report_tuple}
        ),
        "logical_comparisons": len(logical_id_tuple),
        "mean_modal_outcome_agreement": statistics.fmean(modal_agreements),
        "worst_modal_outcome_agreement": min(modal_agreements),
        "mean_normalized_outcome_entropy": statistics.fmean(entropies),
        "mean_pairwise_inter_run_agreement": statistics.fmean(inter_run),
        "min_pairwise_inter_run_agreement": min(inter_run),
        "top_rank_set_stability": top_mode,
        "top_rank_available_runs": len(top_sets),
        "top_rank_missing_runs": missing_top_rank_runs,
        "top_rank_set_counts": {
            ",".join(key): value
            for key, value in sorted(top_counts.items())
        },
        "confidence": confidence_payload,
        "latency": latency_payload,
        "usage_totals": {
            field: value
            for field, value in usage_totals.items()
            if usage_observations[field]
        },
        "usage_observations": dict(sorted(usage_observations.items())),
        "provider_failures": dict(sorted(failure_types.items())),
        "per_comparison": per_comparison,
    }
