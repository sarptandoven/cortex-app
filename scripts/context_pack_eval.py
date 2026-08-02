from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.storage import (
    CONTEXT_INTENT_WEIGHTS,
    CortexStore,
    _CONTEXT_INSTRUCTIONS,
    resolve_profile,
)
from backend.app.smp import SMP_LEGEND
from scripts.retrieval_eval import seed_representative_memories


USER_ID = "context-pack-quality"
METRIC_K = 5

# Model-profile matrix (build-plan #7/#9). The SAME quality gate is proven across three
# consuming-model profiles so the profile plumbing can never silently regress pack quality:
#   * generic (model=None) — the byte-identical legacy per-intent greedy packer.
#   * claude  ("large")    — wide window, global marginal-utility knapsack/MMR packer.
#   * cursor  ("small")    — tight window, knapsack packer that leans hardest on relevance.
# Every ranking/coverage floor must hold (or rise) for EACH profile.
PROFILES: tuple[tuple[str, str | None], ...] = (
    ("generic", None),
    ("claude", "claude"),
    ("cursor", "cursor"),
)

# Budget-efficiency probe budget. The primary metrics run at each case's own (roomy) budget so
# ranking/coverage is measured without starvation; utilization + regret are measured at a
# deliberately BINDING budget where the packer must actually choose what to keep — that is the
# only regime where "did you spend the budget well?" is a meaningful question. At this budget
# every profile's evidence exceeds the budget for at least some cases, so the floor bites.
BUDGET_PROBE_TOKENS = 1200

# Universe budget for the offline-optimal knapsack baseline: large enough that the packer emits
# every cited candidate it would ever consider (the seeded corpus is far smaller than any
# profile's pack_token_budget cap), so the eval sees the full candidate pool to optimize over.
UNIVERSE_BUDGET_TOKENS = 200_000

# Context Assembly Engine (assemble_context / get_context) quality gate. No existing gate
# exercises the pack builder end-to-end: this seeds the retrieval_eval multi-source,
# multi-project corpus, drives assemble_context over a battery of intent-labeled tasks, and
# scores six deterministic properties:
#
#   * layer_population     — for each intent, the layers that intent weights heavily are
#                            actually populated with cited items (graded, per-case).
#   * ndcg@k / mrr         — ranking quality over graded-relevance expected_memory_ids.
#   * citation_coverage    — every packed item resolves to a source_url (floor 1.0).
#   * no_leak              — zero forbidden-sector ids appear in a sector-scoped pack
#                            (floor 1.0).
#   * budget_adherence     — the pack's token estimate stays inside the requested budget.
#   * intent_derivation    — assemble_context's resolved `intent` matches the labeled
#                            expectation on a labeled subset.
#
# Floors sit ~0.05 below the first measured value so the gate catches a real regression
# without being flaky; citation_coverage and no_leak floors are 1.0 (a hard invariant).
# main() prints a JSON summary and exits non-zero on any breach, so CI blocks merges.
# Measured on the current corpus (10 cases): layer_population=1.0, ndcg@k=0.8875, mrr=1.0,
# citation_coverage=1.0, no_leak=1.0, budget_adherence=1.0, intent_derivation=1.0. Floors sit
# ~0.05 below the first measured value; citation_coverage / no_leak / budget_adherence /
# intent_derivation are hard invariants pinned at 1.0.
#
# Budget-efficiency gates (build-plan #5/#9), measured at the BINDING BUDGET_PROBE_TOKENS:
#   * budget_utilization  — fraction of the *achievable* budget the pack actually spends
#                           (used_content / min(packable_budget, total_candidate_tokens)).
#                           Floor 0.8 whenever evidence exists: a packer that leaves budget on
#                           the table while dropping cited items is wasting the window.
#   * budget_regret       — 1 - captured_value/optimal_value vs an offline-optimal 0/1 knapsack
#                           the eval computes over the full candidate pool (value = the intent's
#                           per-layer weight; cost = the profile's own token estimate). Ceiling
#                           0.30: the online packer must stay within 30% of the offline optimum.
#                           (The knapsack profiles measure ~0.0; the legacy per-layer allocator
#                           is the binding case at ~0.21.)
#
# SMP-contract gates (build-plan #4/#9): every response_format="smp" envelope validates against
# its own inline legend (all declared fields present, NO undeclared keys), and its projected
# items are byte-parity with the underlying text-format pack items:
#   * smp_valid           — envelope + every item carry EXACTLY their legend-declared fields.
#   * smp_citation_coverage / smp_no_leak — stay 1.0 (invariants carry through the projection).
#   * smp_byte_parity     — each SMP item's facts equal the text pack's item (same ref → same
#                           content/source_url/layer/relevance): a pure projection, no drift.
CONTEXT_PACK_THRESHOLDS: dict[str, Any] = {
    "min_case_count": 10,
    "layer_population": 0.95,
    "ndcg@k": 0.83,
    "mrr": 0.95,
    "citation_coverage": 1.0,
    "no_leak": 1.0,
    "budget_adherence": 1.0,
    "intent_derivation": 1.0,
    "budget_utilization": 0.8,
    "smp_valid": 1.0,
    "smp_citation_coverage": 1.0,
    "smp_no_leak": 1.0,
    "smp_byte_parity": 1.0,
}
# budget_regret is a CEILING (lower is better), so it lives apart from the floor thresholds above.
CONTEXT_PACK_REGRET_CEILING = 0.30


# --------------------------------------------------------------------------------------------
# Session-replay (build-plan #14): a multi-turn agent working the SAME session_id issues a
# sequence of related queries; each response rides a working_memory DELTA (new / evicted /
# superseded / cursor) instead of re-sending the whole pack. This suite proves the delta is
# SOUND and CHEAP across turns, and that it is inert on the no-session default path.
#
# HARD invariants (pinned at ==1.0 — a single violation on any turn fails the gate):
#   * delta_no_leak            — no id in the delta (new / evicted / superseded / replacement)
#                                belongs to a sector outside the session's scope.
#   * delta_no_resend          — a `new` list never re-sends an id the session already holds
#                                (new ∩ known_before == ∅) and carries no duplicate ref.
#   * eviction_soundness       — every evicted id WAS held and is genuinely absent from this
#                                pack; every superseded id WAS held and maps to a real active
#                                head; and the delta, replayed by the caller, reconstructs the
#                                backend's persisted known-set exactly (held_after ==
#                                (held_before − evicted − superseded) ∪ served).
#   * resume_rehydration_exact — a FRESH store instance on the same db reproduces the exact
#                                known-set byte-for-byte, and a resumed turn re-sends nothing
#                                already held (state lives in the durable row, not in RAM).
#   * no_session_clean         — the byte-identical no-session default path carries NO
#                                working_memory delta (JSON warnings channel empty of it AND
#                                the SMP envelope's working_memory is null).
#
# Efficiency / quality floors + the suite's first latency SLO:
#   * token_savings_ratio      — cumulative delta tokens / cumulative naive full-resend tokens,
#                                summed over every turn. CEILING 0.60: the delta must cost less
#                                than 60% of blindly re-sending each pack (measured ~0.28).
#   * recall@3                  — the top-3 graded-relevant ids each turn stay inside the agent's
#                                working-set (known_before ∪ served): saving tokens must not
#                                drop what matters. FLOOR 0.97.
#   * prefetch_hit_rate        — for turns ≥ 2, the fraction of a pack already warm in the
#                                working-set (served ∩ known_before / served): the working-set
#                                acts as the prefetch cache. FLOOR 0.60 (min-over-turns ~0.75).
#   * latency_slo_ok           — turn-2 assembly is not pathologically slower than turn-1 given
#                                the warmed working-set: t2 ≤ max(t1 · 2.0, t1 + 40 ms). A
#                                sanity SLO with an additive floor so sub-100ms jitter is inert.
SESSION_REPLAY_THRESHOLDS: dict[str, Any] = {
    "min_turn_count": 9,
    "delta_no_leak": 1.0,
    "delta_no_resend": 1.0,
    "eviction_soundness": 1.0,
    "resume_rehydration_exact": 1.0,
    "no_session_clean": 1.0,
    "smp_channel_parity": 1.0,
    "recall@3": 0.97,
    "prefetch_hit_rate": 0.60,
    "latency_slo_ok": 1.0,
}
# token_savings_ratio is a CEILING (lower is better): the delta must transmit < 60% of a naive
# full-resend while recall@3 holds — the whole economic argument for a working-set delta.
SESSION_TOKEN_SAVINGS_CEILING = 0.60
# Turn-2 assembly may be at most this multiple of turn-1 (warmed working-set); the additive floor
# absorbs sub-100ms wall-clock jitter so the SLO sanity check is not flaky.
SESSION_LATENCY_SLO_TOLERANCE = 2.0
SESSION_LATENCY_SLO_FLOOR_MS = 40.0
# A bare id line (evicted / superseded) the caller must be told about — far cheaper than re-sending
# the item's content, but not free; counted honestly against the delta's token budget.
SESSION_REF_LINE_TOKEN_COST = 4


@dataclass(frozen=True)
class ReplayTurn:
    task: str
    intent: str | None
    # Up to three graded-relevant ids the working-set must still hold this turn (recall@3).
    relevant_top: tuple[str, ...] = ()


@dataclass(frozen=True)
class ReplaySession:
    name: str
    sector: str
    token_budget: int
    turns: tuple[ReplayTurn, ...]


# Related-query trajectories over the seeded corpus. Each stays in ONE sector (so delta_no_leak
# and eviction have a well-defined scope) and drifts intent turn-to-turn so the working-set both
# accretes (savings) and goes cold (a real eviction fires on Granite turn 5, TTL=4).
SESSION_REPLAYS: tuple[ReplaySession, ...] = (
    ReplaySession(
        "granite_migration",
        "Project Granite",
        2500,
        (
            ReplayTurn(
                "Plan the next steps for the Project Granite migration",
                "plan",
                ("rq_x_granite_neg_dashboards", "rq_x_granite_neg_backfill", "rq_x_granite_dec_dualwrite"),
            ),
            ReplayTurn(
                "Run the Project Granite warehouse cutover and migrate the shards",
                "act",
                ("rq_x_granite_neg_backfill", "rq_x_granite_neg_dashboards", "rq_x_granite_proc_cutover"),
            ),
            ReplayTurn(
                "Draft a reply about the Granite cutover window",
                "draft",
                ("rq_x_granite_neg_backfill", "rq_x_validity_granite_cutover_current", "rq_x_granite_style_runbook"),
            ),
            ReplayTurn(
                "What is the current Granite cutover window and retention policy?",
                "answer",
                ("rq_x_validity_granite_retention_current", "rq_x_validity_granite_cutover_current", "rq_x_granite_neg_dashboards"),
            ),
            ReplayTurn(
                "Schedule the Granite dry-run rehearsal and freeze window",
                "act",
                ("rq_x_granite_neg_backfill", "rq_x_granite_neg_dashboards", "rq_x_granite_proc_cutover"),
            ),
        ),
    ),
    ReplaySession(
        "health_training",
        "Personal Health",
        2500,
        (
            ReplayTurn(
                "What is my resting heart rate and hydration target?",
                "answer",
                ("rq_x_health_neg_hiit", "rq_x_health_resting_hr", "rq_x_health_sleep"),
            ),
            ReplayTurn(
                "Build the weekend long run plan and configure the recovery block",
                "act",
                ("rq_x_health_neg_hiit", "rq_x_validity_health_hydration_current", "rq_x_health_proc_longrun"),
            ),
            ReplayTurn(
                "Plan my training week around sleep and recovery",
                "plan",
                ("rq_x_health_neg_hiit", "rq_x_validity_health_hydration_current", "rq_x_health_resting_hr"),
            ),
            ReplayTurn(
                "Draft a note about my hydration and resting heart rate baseline",
                "draft",
                ("rq_x_health_neg_hiit", "rq_x_health_resting_hr", "rq_x_health_pref_metric"),
            ),
        ),
    ),
)


@dataclass(frozen=True)
class ContextCase:
    name: str
    task: str
    # Sector scope passed to assemble_context; also the ONLY sector allowed to appear.
    sector: str | None = None
    project: str | None = None
    intent: str | None = None
    token_budget: int = 2500
    # Layers (assemble_context CONTEXT_LAYER_ORDER names) that MUST be populated with cited
    # items for this task/intent.
    expected_layers: frozenset[str] = field(default_factory=frozenset)
    # Graded relevance: {memory_id: gain}. Used for nDCG@k / MRR over the pack's cited order.
    expected_memory_ids: dict[str, int] = field(default_factory=dict)
    # Sectors that must NOT appear anywhere in the pack (project isolation / no-leak).
    forbidden_sectors: frozenset[str] = field(default_factory=frozenset)
    # When set, assemble_context's resolved `intent` must equal this (intent-derivation check).
    expected_intent: str | None = None


# Every ContextCase is sector-scoped, so the pack must contain only that sector's memories.
# forbidden_sectors is the full set of OTHER seeded sectors, so no-leak catches any bleed.
ALL_SECTORS: tuple[str, ...] = (
    "Project Granite",
    "Project Foundry",
    "Personal Health",
    "Project Kestrel",
    "Project Larkspur",
)


def _others(sector: str) -> frozenset[str]:
    return frozenset(other for other in ALL_SECTORS if other != sector)


CONTEXT_CASES: tuple[ContextCase, ...] = (
    # Graded relevance ids are the memories that genuinely belong to each task AND land in a
    # layer this intent weights, so a correct pack surfaces them near the top. The pack orders
    # items by layer (constraints -> decisions -> facts -> ... -> recency), so top gains go to
    # the item that legitimately leads the intent's dominant layer.
    ContextCase(
        name="granite_plan_next_steps",
        task="Plan the next steps for the Project Granite migration",
        sector="Project Granite",
        expected_intent="plan",
        # plan weights constraints/decisions/open_loops/recency; decisions must carry cited items.
        expected_layers=frozenset({"constraints", "decisions", "recency"}),
        expected_memory_ids={
            "rq_x_granite_neg_dashboards": 3,
            "rq_x_granite_neg_backfill": 3,
            "rq_x_granite_dec_dualwrite": 2,
            "rq_x_granite_dec_differ": 1,
        },
        forbidden_sectors=_others("Project Granite"),
    ),
    ContextCase(
        name="granite_act_cutover",
        task="Run the Project Granite warehouse cutover and migrate the shards",
        sector="Project Granite",
        expected_intent="act",
        # act weights constraints/facts/procedures; the cutover procedure must be present.
        expected_layers=frozenset({"constraints", "procedures", "recency"}),
        expected_memory_ids={
            "rq_x_granite_neg_backfill": 3,
            "rq_x_granite_neg_dashboards": 2,
            "rq_x_granite_proc_cutover": 1,
        },
        forbidden_sectors=_others("Project Granite"),
    ),
    ContextCase(
        name="granite_draft_reply",
        task="Draft a reply about the Granite cutover window",
        sector="Project Granite",
        expected_intent="draft",
        # draft weights constraints/identity (preference/style); style + current window lead.
        expected_layers=frozenset({"constraints", "identity", "recency"}),
        expected_memory_ids={
            "rq_x_granite_neg_backfill": 3,
            "rq_x_validity_granite_cutover_current": 2,
            "rq_x_granite_style_runbook": 1,
        },
        forbidden_sectors=_others("Project Granite"),
    ),
    ContextCase(
        name="foundry_act_panel",
        task="Schedule the Foundry interview panel and debrief",
        sector="Project Foundry",
        expected_intent="act",
        expected_layers=frozenset({"constraints", "procedures", "identity", "recency"}),
        expected_memory_ids={
            "rq_x_foundry_neg_comp": 3,
            "rq_x_foundry_event_panel": 2,
            "rq_x_foundry_proc_debrief": 1,
        },
        forbidden_sectors=_others("Project Foundry"),
    ),
    ContextCase(
        name="foundry_plan_pipeline",
        task="Plan and prioritise the Foundry hiring pipeline for next quarter",
        sector="Project Foundry",
        expected_intent="plan",
        expected_layers=frozenset({"constraints", "decisions", "recency"}),
        expected_memory_ids={
            "rq_x_foundry_neg_comp": 3,
            "rq_x_foundry_dec_ats": 2,
            "rq_x_foundry_dec_takehome": 1,
        },
        forbidden_sectors=_others("Project Foundry"),
    ),
    ContextCase(
        name="health_answer_baseline",
        task="What is my resting heart rate and hydration target?",
        sector="Personal Health",
        expected_intent="answer",
        # answer weights facts heavily; the resting-HR fact must lead the facts layer.
        expected_layers=frozenset({"facts", "recency"}),
        expected_memory_ids={
            "rq_x_health_neg_hiit": 3,
            "rq_x_health_resting_hr": 2,
            "rq_x_health_sleep": 1,
        },
        forbidden_sectors=_others("Personal Health"),
    ),
    ContextCase(
        name="health_act_longrun",
        task="Build the weekend long run plan and configure the recovery block",
        sector="Personal Health",
        expected_intent="act",
        expected_layers=frozenset({"constraints", "procedures", "recency"}),
        expected_memory_ids={
            "rq_x_health_neg_hiit": 3,
            "rq_x_health_neg_freeform": 2,
            "rq_x_health_proc_longrun": 1,
        },
        forbidden_sectors=_others("Personal Health"),
    ),
    ContextCase(
        name="kestrel_act_tilecache",
        task="Fix the Project Kestrel offline tile cache and ship the build",
        sector="Project Kestrel",
        expected_intent="act",
        expected_layers=frozenset({"constraints", "procedures", "recency"}),
        expected_memory_ids={
            "rq_x_kestrel_neg_regression": 3,
            "rq_x_kestrel_tiles": 2,
            "rq_x_kestrel_dec_log": 1,
        },
        forbidden_sectors=_others("Project Kestrel"),
    ),
    ContextCase(
        name="kestrel_draft_changelog",
        task="Write the Project Kestrel changelog entry for the offline fixes",
        sector="Project Kestrel",
        expected_intent="draft",
        expected_layers=frozenset({"constraints", "identity", "recency"}),
        expected_memory_ids={
            "rq_x_kestrel_neg_regression": 3,
            "rq_x_kestrel_style_changelog": 2,
        },
        forbidden_sectors=_others("Project Kestrel"),
    ),
    ContextCase(
        name="larkspur_plan_backlog",
        task="Plan and prioritise the Project Larkspur design-system backlog",
        sector="Project Larkspur",
        expected_intent="plan",
        expected_layers=frozenset({"constraints", "decisions", "recency"}),
        expected_memory_ids={
            "rq_x_larkspur_neg_hex": 3,
            "rq_x_larkspur_dec_font": 2,
            "rq_x_larkspur_dec_darkmode": 1,
        },
        forbidden_sectors=_others("Project Larkspur"),
    ),
)


def _iter_pack_items(pack: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """Yield (layer_name, item) for every cited item in the pack, in pack order."""
    out: list[tuple[str, dict[str, Any]]] = []
    for entry in pack.get("layers") or []:
        if entry.get("omitted"):
            continue
        layer = str(entry.get("layer") or "")
        for item in entry.get("items") or []:
            out.append((layer, item))
    return out


def _cited_order(pack: dict[str, Any]) -> list[str]:
    """Ranked list of packed memory ids (pack order == relevance order)."""
    ids: list[str] = []
    for _layer, item in _iter_pack_items(pack):
        memory_id = str(item.get("memory_id") or item.get("task_id") or "")
        if memory_id:
            ids.append(memory_id)
    return ids


def _populated_layers(pack: dict[str, Any]) -> set[str]:
    populated: set[str] = set()
    for entry in pack.get("layers") or []:
        if entry.get("omitted"):
            continue
        if entry.get("items"):
            populated.add(str(entry.get("layer") or ""))
    return populated


def _dcg(gains: list[int]) -> float:
    return sum(gain / math.log2(index + 2) for index, gain in enumerate(gains))


def _ndcg_at_k(ranked_ids: list[str], relevance: dict[str, int], k: int) -> float:
    if not relevance:
        return 1.0
    ranked_gains = [int(relevance.get(memory_id, 0)) for memory_id in ranked_ids[:k]]
    ideal_gains = sorted((int(v) for v in relevance.values()), reverse=True)[:k]
    ideal = _dcg(ideal_gains)
    if ideal <= 0.0:
        return 1.0
    return round(_dcg(ranked_gains) / ideal, 6)


def _mrr(ranked_ids: list[str], relevance: dict[str, int]) -> float:
    if not relevance:
        return 1.0
    relevant = {memory_id for memory_id, gain in relevance.items() if gain > 0}
    for index, memory_id in enumerate(ranked_ids, start=1):
        if memory_id in relevant:
            return round(1.0 / index, 6)
    return 0.0


# --------------------------------------------------------------------------------------------
# SMP-contract helpers (build-plan #4/#9): validate a response_format="smp" envelope against
# its own inline legend and prove it is a byte-parity projection of the text-format pack.
# --------------------------------------------------------------------------------------------

def _smp_declared_envelope_fields() -> set[str]:
    return set(SMP_LEGEND.get("envelope") or {})


def _smp_declared_item_fields() -> set[str]:
    return set(SMP_LEGEND.get("item") or {})


def _validate_smp_envelope(envelope: dict[str, Any]) -> list[str]:
    """Every declared field present, NO undeclared keys — for the envelope and every item.
    Returns a list of human-readable violations (empty == valid)."""
    problems: list[str] = []
    declared_env = _smp_declared_envelope_fields()
    keys = set(envelope.keys())
    for missing in sorted(declared_env - keys):
        problems.append(f"envelope missing declared field '{missing}'")
    for extra in sorted(keys - declared_env):
        problems.append(f"envelope has undeclared key '{extra}'")
    # The embedded legend must itself be the contract we validated against.
    if envelope.get("legend") is not SMP_LEGEND and envelope.get("legend") != SMP_LEGEND:
        problems.append("envelope legend does not match SMP_LEGEND")
    if str(envelope.get("smp") or "") != "1":
        problems.append(f"envelope smp version != '1' (got {envelope.get('smp')!r})")
    declared_item = _smp_declared_item_fields()
    for index, item in enumerate(envelope.get("items") or []):
        if not isinstance(item, dict):
            problems.append(f"item[{index}] is not an object")
            continue
        item_keys = set(item.keys())
        for missing in sorted(declared_item - item_keys):
            problems.append(f"item[{index}] missing declared field '{missing}'")
        for extra in sorted(item_keys - declared_item):
            problems.append(f"item[{index}] has undeclared key '{extra}'")
    return problems


def _relevance_value(raw: Any) -> float | None:
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _smp_byte_parity(envelope: dict[str, Any], text_pack: dict[str, Any]) -> list[str]:
    """Each SMP item projects an underlying text-pack item without inventing or dropping facts:
    the ref sets match, and for every shared ref the content/source_url/layer/relevance are equal.
    Returns a list of parity violations (empty == byte-parity)."""
    problems: list[str] = []
    text_by_ref: dict[str, dict[str, Any]] = {}
    for _layer, item in _iter_pack_items(text_pack):
        ref = str(item.get("memory_id") or item.get("task_id") or "")
        if ref:
            text_by_ref[ref] = item
    smp_refs = [str(item.get("ref") or "") for item in envelope.get("items") or []]
    if set(smp_refs) != set(text_by_ref):
        problems.append(
            f"ref-set drift: smp_only={sorted(set(smp_refs) - set(text_by_ref))} "
            f"text_only={sorted(set(text_by_ref) - set(smp_refs))}"
        )
    for item in envelope.get("items") or []:
        ref = str(item.get("ref") or "")
        source = text_by_ref.get(ref)
        if source is None:
            continue
        if str(item.get("content") or "") != str(source.get("content") or ""):
            problems.append(f"content drift for ref={ref}")
        if item.get("source_url") != (source.get("source_url") or None):
            problems.append(f"source_url drift for ref={ref}")
        if str(item.get("layer") or "") != str(source.get("layer") or ""):
            problems.append(f"layer drift for ref={ref}")
        if item.get("relevance") != _relevance_value(source.get("relevance")):
            problems.append(f"relevance drift for ref={ref}")
    return problems


# --------------------------------------------------------------------------------------------
# Budget-efficiency helpers (build-plan #5/#9): utilization + regret vs an offline-optimal
# knapsack the eval computes itself over the packer's full candidate pool.
# --------------------------------------------------------------------------------------------

def _instructions_cost(chars_per_token: float) -> int:
    """The header-token reservation the packer subtracts before packing, using the profile's
    own char→token divisor (mirrors _pack_context_*'s instructions_cost exactly)."""
    return sum(max(1, math.ceil(len(line) / max(chars_per_token, 1e-6))) for line in _CONTEXT_INSTRUCTIONS)


def _knapsack_optimal(value_cost: list[tuple[int, int]], capacity: int) -> int:
    """Offline-optimal 0/1 knapsack value. Deterministic DP over integer token capacity."""
    if capacity <= 0:
        return 0
    dp = [0] * (capacity + 1)
    for value, cost in value_cost:
        if cost <= 0 or cost > capacity or value <= 0:
            continue
        for cap in range(capacity, cost - 1, -1):
            candidate = dp[cap - cost] + value
            if candidate > dp[cap]:
                dp[cap] = candidate
    return max(dp)


def _evaluate_budget(
    store: CortexStore,
    user_id: str,
    case: ContextCase,
    *,
    model: str | None,
) -> dict[str, Any]:
    """Utilization + regret for one case under one profile, at the binding probe budget.

    utilization = used_content / min(packable_budget, total_candidate_tokens).
    regret      = 1 - captured_value / optimal_value, where value(item) = the intent's per-layer
                  weight and the optimum is a 0/1 knapsack over the full candidate pool (obtained
                  by re-packing at a huge budget, so nothing is dropped) under packable_budget.
    Both use the PROFILE's own token estimate, so the baseline is apples-to-apples with the packer.
    """
    profile = resolve_profile(model, "agent")
    cpt = profile.chars_per_token
    item_overhead = 12  # mirrors assemble_context's per-item metadata cost
    instructions_cost = _instructions_cost(cpt)

    packed = store.assemble_context(
        user_id,
        case.task,
        token_budget=BUDGET_PROBE_TOKENS,
        sector=case.sector,
        project=case.project,
        intent=case.intent,
        model=model,
    )
    universe = store.assemble_context(
        user_id,
        case.task,
        token_budget=UNIVERSE_BUDGET_TOKENS,
        sector=case.sector,
        project=case.project,
        intent=case.intent,
        model=model,
    )
    assert isinstance(packed, dict) and isinstance(universe, dict)

    intent = str(packed.get("intent") or "")
    weights = CONTEXT_INTENT_WEIGHTS.get(intent, {})

    def _cost(item: dict[str, Any]) -> int:
        return max(1, math.ceil(len(str(item.get("content") or "")) / max(cpt, 1e-6))) + item_overhead

    packable_budget = max(int(packed.get("budget", {}).get("token_budget") or BUDGET_PROBE_TOKENS) - instructions_cost, 120)

    universe_items = _iter_pack_items(universe)
    total_candidate_tokens = sum(_cost(item) for _layer, item in universe_items)
    value_cost = [(int(weights.get(layer, 0)), _cost(item)) for layer, item in universe_items]
    optimal_value = _knapsack_optimal(value_cost, packable_budget)

    packed_items = _iter_pack_items(packed)
    has_evidence = bool(packed_items)
    used_total = int(packed.get("budget", {}).get("used_tokens") or 0)
    used_content = max(used_total - instructions_cost, 0)
    achievable = min(packable_budget, total_candidate_tokens)
    utilization = round(used_content / achievable, 6) if achievable > 0 else 1.0
    # A pack that already spends more than the achievable floor (protected constraint force-include)
    # is fully utilized, not >100% — clamp so the floor reads cleanly.
    utilization = min(utilization, 1.0)

    captured_value = sum(int(weights.get(layer, 0)) for layer, _item in packed_items)
    regret = round(max(0.0, (optimal_value - captured_value) / optimal_value), 6) if optimal_value > 0 else 0.0

    return {
        "case": case.name,
        "model": model,
        "profile": profile.name,
        "has_evidence": has_evidence,
        "packable_budget": packable_budget,
        "total_candidate_tokens": total_candidate_tokens,
        "used_content_tokens": used_content,
        "budget_utilization": utilization,
        "optimal_value": optimal_value,
        "captured_value": captured_value,
        "budget_regret": regret,
    }


def _evaluate_case(
    store: CortexStore,
    user_id: str,
    case: ContextCase,
    id_sector: dict[str, str],
    *,
    model: str | None = None,
) -> dict[str, Any]:
    pack = store.assemble_context(
        user_id,
        case.task,
        token_budget=case.token_budget,
        sector=case.sector,
        project=case.project,
        intent=case.intent,
        model=model,
    )
    assert isinstance(pack, dict)  # json format returns a dict

    populated = _populated_layers(pack)
    ranked_ids = _cited_order(pack)
    items = _iter_pack_items(pack)

    # Layer population: every expected layer for this intent carries cited items.
    missing_layers = sorted(case.expected_layers - populated)
    layer_population_ok = not missing_layers

    # Ranking quality over graded-relevance ids.
    ndcg = _ndcg_at_k(ranked_ids, case.expected_memory_ids, METRIC_K)
    mrr = _mrr(ranked_ids, case.expected_memory_ids)

    # Citation coverage: every packed item resolves to a source_url.
    uncited = [
        (layer, str(item.get("memory_id") or item.get("task_id") or ""))
        for layer, item in items
        if not str(item.get("source_url") or "").strip()
    ]
    citation_coverage = round((len(items) - len(uncited)) / len(items), 6) if items else 1.0

    # No-leak: no packed id belongs to a forbidden sector; and if the case is sector-scoped,
    # every packed id's sector is either the target sector or the sector-less baseline seed.
    leaked: list[dict[str, str]] = []
    for _layer, item in items:
        memory_id = str(item.get("memory_id") or item.get("task_id") or "")
        # Prefer the pack-reported sector; fall back to the seed map for tasks/loops.
        item_sector = str(item.get("sector") or id_sector.get(memory_id) or "")
        if item_sector and item_sector in case.forbidden_sectors:
            leaked.append({"memory_id": memory_id, "sector": item_sector})
    no_leak_ok = not leaked

    # Budget adherence: reported used tokens stay within the requested budget.
    budget = pack.get("budget") or {}
    used_tokens = int(budget.get("used_tokens") or 0)
    token_budget = int(budget.get("token_budget") or case.token_budget)
    budget_ok = used_tokens <= token_budget

    # Intent derivation: only checked when the case is labeled (subset).
    resolved_intent = str(pack.get("intent") or "")
    if case.expected_intent is None:
        intent_ok = True
    else:
        intent_ok = resolved_intent == case.expected_intent

    # SMP contract: the response_format="smp" envelope is a self-describing, byte-parity
    # projection of THIS pack (same inputs), validated against its own inline legend.
    smp_envelope = store.assemble_context(
        user_id,
        case.task,
        token_budget=case.token_budget,
        sector=case.sector,
        project=case.project,
        intent=case.intent,
        model=model,
        response_format="smp",
    )
    assert isinstance(smp_envelope, dict)
    smp_validation = _validate_smp_envelope(smp_envelope)
    smp_valid_ok = not smp_validation
    smp_items = smp_envelope.get("items") or []
    smp_uncited = [str(it.get("ref") or "") for it in smp_items if not str(it.get("source_url") or "").strip()]
    smp_citation_coverage = round((len(smp_items) - len(smp_uncited)) / len(smp_items), 6) if smp_items else 1.0
    smp_leaked = [
        str(it.get("ref") or "")
        for it in smp_items
        if str(id_sector.get(str(it.get("ref") or "")) or "") in case.forbidden_sectors
    ]
    # SMP items carry no sector field; cross-check ref→sector against the seed map AND the
    # text pack's own reported sector so a leak cannot hide behind the projection.
    text_sector_by_ref = {
        str(item.get("memory_id") or item.get("task_id") or ""): str(item.get("sector") or "")
        for _layer, item in items
    }
    for it in smp_items:
        ref = str(it.get("ref") or "")
        sector = text_sector_by_ref.get(ref) or id_sector.get(ref) or ""
        if sector and sector in case.forbidden_sectors and ref not in smp_leaked:
            smp_leaked.append(ref)
    smp_no_leak_ok = not smp_leaked
    smp_parity_problems = _smp_byte_parity(smp_envelope, pack)
    smp_byte_parity_ok = not smp_parity_problems

    ok = (
        layer_population_ok
        and no_leak_ok
        and citation_coverage == 1.0
        and budget_ok
        and intent_ok
        and smp_valid_ok
        and smp_citation_coverage == 1.0
        and smp_no_leak_ok
        and smp_byte_parity_ok
    )
    return {
        "check": "context_pack",
        "case": case.name,
        "model": model,
        "profile": resolve_profile(model, "agent").name,
        "task": case.task,
        "sector": case.sector,
        "resolved_intent": resolved_intent,
        "expected_intent": case.expected_intent,
        "packed_item_count": len(items),
        "populated_layers": sorted(populated),
        "expected_layers": sorted(case.expected_layers),
        "missing_layers": missing_layers,
        "layer_population_ok": layer_population_ok,
        "ndcg@k": ndcg,
        "mrr": mrr,
        "citation_coverage": citation_coverage,
        "uncited_items": uncited,
        "no_leak_ok": no_leak_ok,
        "leaked": leaked,
        "budget_used_tokens": used_tokens,
        "token_budget": token_budget,
        "budget_ok": budget_ok,
        "intent_ok": intent_ok,
        "ranked_ids": ranked_ids,
        "smp_item_count": len(smp_items),
        "smp_valid_ok": smp_valid_ok,
        "smp_validation": smp_validation,
        "smp_citation_coverage": smp_citation_coverage,
        "smp_no_leak_ok": smp_no_leak_ok,
        "smp_leaked": smp_leaked,
        "smp_byte_parity_ok": smp_byte_parity_ok,
        "smp_parity_problems": smp_parity_problems,
        "ok": ok,
    }


def _summarize(checks: list[dict[str, Any]]) -> dict[str, Any]:
    def mean(key: str, *, boolean: bool = False) -> float:
        if not checks:
            return 1.0
        if boolean:
            total = sum(1.0 for c in checks if c.get(key))
        else:
            total = sum(float(c.get(key) or 0.0) for c in checks)
        return round(total / len(checks), 6)

    intent_checks = [c for c in checks if c.get("expected_intent")]
    intent_rate = (
        round(sum(1.0 for c in intent_checks if c.get("intent_ok")) / len(intent_checks), 6)
        if intent_checks
        else 1.0
    )
    return {
        "case_count": len(checks),
        "layer_population": mean("layer_population_ok", boolean=True),
        "ndcg@k": mean("ndcg@k"),
        "mrr": mean("mrr"),
        "citation_coverage": mean("citation_coverage"),
        "no_leak": mean("no_leak_ok", boolean=True),
        "budget_adherence": mean("budget_ok", boolean=True),
        "intent_derivation": intent_rate,
        "labeled_intent_cases": len(intent_checks),
        "smp_valid": mean("smp_valid_ok", boolean=True),
        "smp_citation_coverage": mean("smp_citation_coverage"),
        "smp_no_leak": mean("smp_no_leak_ok", boolean=True),
        "smp_byte_parity": mean("smp_byte_parity_ok", boolean=True),
    }


def _summarize_budget(budget_checks: list[dict[str, Any]]) -> dict[str, Any]:
    """Budget-efficiency rollup. Utilization is floored only over evidence-bearing cases (the
    "when evidence exists" qualifier); regret is a ceiling, so the worst case is what bites."""
    evidence = [c for c in budget_checks if c.get("has_evidence")]
    utils = [float(c.get("budget_utilization") or 0.0) for c in evidence]
    regrets = [float(c.get("budget_regret") or 0.0) for c in budget_checks]
    return {
        "probe_budget_tokens": BUDGET_PROBE_TOKENS,
        "evidence_case_count": len(evidence),
        "budget_utilization": round(min(utils), 6) if utils else 1.0,
        "budget_utilization_mean": round(sum(utils) / len(utils), 6) if utils else 1.0,
        "budget_regret": round(max(regrets), 6) if regrets else 0.0,
        "budget_regret_mean": round(sum(regrets) / len(regrets), 6) if regrets else 0.0,
    }


def _run_profile(store: CortexStore, user_id: str, id_sector: dict[str, str], profile_name: str, model: str | None) -> dict[str, Any]:
    """Evaluate the full case battery under one model profile: primary quality metrics at each
    case's own budget, plus the binding-budget utilization/regret probe."""
    checks = [_evaluate_case(store, user_id, case, id_sector, model=model) for case in CONTEXT_CASES]
    budget_checks = [_evaluate_budget(store, user_id, case, model=model) for case in CONTEXT_CASES]
    metrics = {**_summarize(checks), **_summarize_budget(budget_checks)}
    failures = [c for c in checks if not c.get("ok")]
    return {
        "profile": profile_name,
        "model": model,
        "metrics": metrics,
        "checks": checks,
        "budget_checks": budget_checks,
        "failures": failures,
    }


# --------------------------------------------------------------------------------------------
# Session-replay helpers (build-plan #14): read the working_memory delta off a response and
# drive the multi-turn soundness / savings / latency battery.
# --------------------------------------------------------------------------------------------

def _working_memory_from_pack(pack: dict[str, Any]) -> dict[str, Any] | None:
    """The session delta rides a NON-SMP pack on the existing warnings[] channel
    (kind=='working_memory'); returns None when the call carried no session (default path)."""
    for warning in pack.get("warnings") or []:
        if isinstance(warning, dict) and warning.get("kind") == "working_memory":
            wm = warning.get("working_memory")
            if isinstance(wm, dict):
                return wm
    return None


def _new_refs(delta: dict[str, Any]) -> list[str]:
    """Ordered ids in the delta's `new` list, tolerant of both the {ref,delta_relevance} object
    shape and a bare-string shape."""
    out: list[str] = []
    for entry in delta.get("new") or []:
        if isinstance(entry, dict) and entry.get("ref"):
            out.append(str(entry["ref"]))
        elif isinstance(entry, str) and entry:
            out.append(entry)
    return out


def _superseded_pairs(delta: dict[str, Any]) -> list[tuple[str, str | None]]:
    pairs: list[tuple[str, str | None]] = []
    for entry in delta.get("superseded") or []:
        if isinstance(entry, dict) and entry.get("ref"):
            rep = entry.get("replacement")
            pairs.append((str(entry["ref"]), str(rep) if rep else None))
    return pairs


def _delta_all_refs(delta: dict[str, Any]) -> set[str]:
    """Every id the delta references — the leak surface."""
    refs = set(_new_refs(delta))
    refs.update(str(r) for r in (delta.get("evicted") or []))
    for ref, rep in _superseded_pairs(delta):
        refs.add(ref)
        if rep:
            refs.add(rep)
    return {r for r in refs if r}


def _item_token_cost(content: str, cpt: float, overhead: int = 12) -> int:
    """Mirror _evaluate_budget's per-item estimate so savings are measured in the packer's units."""
    return max(1, math.ceil(len(str(content or "")) / max(cpt, 1e-6))) + overhead


def _evaluate_one_replay(
    store: CortexStore,
    db_path: Path,
    vault_path: Path | None,
    user_id: str,
    id_sector: dict[str, str],
    session: ReplaySession,
) -> dict[str, Any]:
    forbidden = _others(session.sector) if session.sector in ALL_SECTORS else frozenset()
    session_id = f"cxr_{session.name}"
    profile = resolve_profile(None, "agent")
    cpt = profile.chars_per_token

    turn_checks: list[dict[str, Any]] = []
    held_recon: set[str] = set()
    naive_tokens = 0
    delta_tokens = 0
    recall_vals: list[float] = []
    prefetch_vals: list[float] = []
    latencies_ms: list[float] = []

    for turn_index, turn in enumerate(session.turns, start=1):
        known_before = set(store._load_session_working_set(user_id, session_id)["known"])
        # held_recon (the caller's own replay of prior deltas) must track the backend's state.
        recon_before_ok = held_recon == known_before

        started = time.perf_counter()
        pack = store.assemble_context(
            user_id,
            turn.task,
            token_budget=session.token_budget,
            sector=session.sector,
            intent=turn.intent,
            session_id=session_id,
            record_reuse=False,  # deterministic: skip the best-effort background prefetch warm loop
        )
        latencies_ms.append((time.perf_counter() - started) * 1000.0)
        assert isinstance(pack, dict)

        delta = _working_memory_from_pack(pack)
        delta_present = isinstance(delta, dict)
        delta = delta or {}

        items = _iter_pack_items(pack)
        served_order: list[str] = []
        content_by_ref: dict[str, str] = {}
        for _layer, item in items:
            ref = str(item.get("memory_id") or item.get("task_id") or "")
            if ref:
                served_order.append(ref)
                content_by_ref[ref] = str(item.get("content") or "")
        served_set = set(served_order)

        new_refs = _new_refs(delta)
        evicted = [str(r) for r in (delta.get("evicted") or [])]
        superseded = _superseded_pairs(delta)
        superseded_refs = [ref for ref, _rep in superseded]

        # ---- HARD: delta_no_resend (never re-send a held id; no duplicate refs) ----
        resent = sorted(set(new_refs) & known_before)
        no_resend_ok = delta_present and not resent and len(new_refs) == len(set(new_refs))

        # ---- HARD: eviction_soundness (evicted/superseded were held & cold; caller's replay
        #            of the delta reconstructs the backend's persisted known-set exactly) ----
        evict_bad = [e for e in evicted if e not in known_before or e in served_set]
        sup_bad: list[str] = []
        for ref, rep in superseded:
            if ref not in known_before:
                sup_bad.append(ref)  # never told about supersession of an id we do not hold
            if rep is not None and rep == ref:
                sup_bad.append(ref)  # a replacement must be a DIFFERENT id than the stale ref
        after = set(store._load_session_working_set(user_id, session_id)["known"])
        expected_after = (known_before - set(evicted) - set(superseded_refs)) | served_set
        recon_after_ok = expected_after == after
        eviction_ok = delta_present and not evict_bad and not sup_bad and recon_before_ok and recon_after_ok
        held_recon = after  # advance to the ground truth for the next turn

        # ---- HARD: delta_no_leak (no out-of-sector id in the delta) ----
        leaked = sorted(r for r in _delta_all_refs(delta) if (id_sector.get(r) or "") in forbidden)
        no_leak_ok = delta_present and not leaked

        # ---- token savings (content is only paid for `new` items; drops are id-only lines) ----
        turn_naive = sum(_item_token_cost(content_by_ref[r], cpt) for r in served_order)
        turn_delta = sum(_item_token_cost(content_by_ref.get(r, ""), cpt) for r in new_refs) + (
            SESSION_REF_LINE_TOKEN_COST * (len(evicted) + len(superseded))
        )
        naive_tokens += turn_naive
        delta_tokens += turn_delta

        # ---- recall@3 over the agent's working-set (what it holds, not just this pack) ----
        working_set = known_before | served_set
        rel = list(turn.relevant_top)[:3]
        recall = (sum(1 for r in rel if r in working_set) / len(rel)) if rel else 1.0
        recall_vals.append(recall)

        # ---- prefetch/warm hit-rate: fraction of this pack already warm in the working-set ----
        if turn_index >= 2 and served_set:
            prefetch_vals.append(len(served_set & known_before) / len(served_set))

        turn_checks.append(
            {
                "turn": turn_index,
                "task": turn.task,
                "delta_present": delta_present,
                "served_count": len(served_order),
                "new_count": len(new_refs),
                "evicted": evicted,
                "superseded": [{"ref": r, "replacement": rep} for r, rep in superseded],
                "cursor": delta.get("cursor"),
                "resent": resent,
                "no_resend_ok": no_resend_ok,
                "evict_bad": evict_bad,
                "sup_bad": sup_bad,
                "recon_before_ok": recon_before_ok,
                "recon_after_ok": recon_after_ok,
                "eviction_ok": eviction_ok,
                "leaked": leaked,
                "no_leak_ok": no_leak_ok,
                "recall@3": round(recall, 6),
                "ok": no_resend_ok and eviction_ok and no_leak_ok,
            }
        )

    # ---- resume-rehydration: a FRESH store reproduces the exact known-set and re-sends nothing ----
    known_a = store._load_session_working_set(user_id, session_id)
    store_resume = CortexStore(db_path, vault_path)
    known_b = store_resume._load_session_working_set(user_id, session_id)
    byte_ok = json.dumps(known_a, sort_keys=True) == json.dumps(known_b, sort_keys=True)
    recon_match = set(known_a["known"]) == held_recon
    last = session.turns[-1]
    resume_pack = store_resume.assemble_context(
        user_id,
        last.task,
        token_budget=session.token_budget,
        sector=session.sector,
        intent=last.intent,
        session_id=session_id,
        record_reuse=False,
    )
    assert isinstance(resume_pack, dict)
    resume_delta = _working_memory_from_pack(resume_pack) or {}
    resume_new = set(_new_refs(resume_delta))
    resume_no_resend = bool(resume_delta) and not (resume_new & set(known_b["known"]))
    resume_ok = byte_ok and recon_match and resume_no_resend

    # ---- latency SLO sanity: turn-2 vs turn-1 (warmed working-set) ----
    t1 = latencies_ms[0] if latencies_ms else 0.0
    t2 = latencies_ms[1] if len(latencies_ms) > 1 else t1
    latency_ok = t2 <= max(t1 * SESSION_LATENCY_SLO_TOLERANCE, t1 + SESSION_LATENCY_SLO_FLOOR_MS)

    evictions = sum(len(c["evicted"]) for c in turn_checks)
    return {
        "session": session.name,
        "sector": session.sector,
        "turn_count": len(turn_checks),
        "evictions_observed": evictions,
        "turns": turn_checks,
        "resume": {
            "byte_comparable": byte_ok,
            "reconstruction_match": recon_match,
            "resume_no_resend": resume_no_resend,
            "ok": resume_ok,
        },
        "latency": {"turn1_ms": round(t1, 3), "turn2_ms": round(t2, 3), "ok": latency_ok},
        "naive_tokens": naive_tokens,
        "delta_tokens": delta_tokens,
        "recall_vals": recall_vals,
        "prefetch_vals": prefetch_vals,
    }


def _no_session_control(store: CortexStore, user_id: str) -> dict[str, Any]:
    """Negative control: the default (no session_id) path must NOT carry a working_memory delta —
    neither on the JSON warnings channel nor as a non-null SMP envelope field. This is what pins
    'byte-identical to Wave-2 when no session_id' as a checkable invariant."""
    case = SESSION_REPLAYS[0].turns[0]
    sector = SESSION_REPLAYS[0].sector
    json_pack = store.assemble_context(
        user_id, case.task, token_budget=2500, sector=sector, intent=case.intent
    )
    assert isinstance(json_pack, dict)
    smp_env = store.assemble_context(
        user_id, case.task, token_budget=2500, sector=sector, intent=case.intent, response_format="smp"
    )
    assert isinstance(smp_env, dict)
    json_clean = _working_memory_from_pack(json_pack) is None
    smp_clean = smp_env.get("working_memory") is None
    return {"json_clean": json_clean, "smp_clean": smp_clean, "ok": json_clean and smp_clean}


def _smp_channel_parity(store: CortexStore, user_id: str) -> dict[str, Any]:
    """Positive control: under a session, the delta ALSO rides the SMP envelope's `working_memory`
    field (the contract's primary channel), with the same shape and ids. On a fresh session every
    served id is `new`, so the SMP delta's new-set must equal the projected item ref-set."""
    case = SESSION_REPLAYS[0].turns[0]
    sector = SESSION_REPLAYS[0].sector
    env = store.assemble_context(
        user_id,
        case.task,
        token_budget=2500,
        sector=sector,
        intent=case.intent,
        session_id="cxr_smp_probe",
        response_format="smp",
    )
    assert isinstance(env, dict)
    wm = env.get("working_memory")
    present = isinstance(wm, dict) and {"new", "evicted", "superseded", "cursor"} <= set(wm.keys())
    item_refs = {str(it.get("ref") or "") for it in (env.get("items") or []) if str(it.get("ref") or "")}
    new_refs = set(_new_refs(wm)) if isinstance(wm, dict) else set()
    fresh_all_new = present and new_refs == item_refs and not (wm.get("evicted") or [])
    return {"present": bool(present), "fresh_all_new": bool(fresh_all_new), "ok": bool(fresh_all_new)}


def run_session_replay(
    store: CortexStore,
    db_path: Path,
    vault_path: Path | None,
    user_id: str,
    id_sector: dict[str, str],
) -> dict[str, Any]:
    """Drive every ReplaySession and roll the per-turn checks up into the build-plan #14 metrics."""
    sessions = [
        _evaluate_one_replay(store, db_path, vault_path, user_id, id_sector, session)
        for session in SESSION_REPLAYS
    ]
    control = _no_session_control(store, user_id)
    smp_channel = _smp_channel_parity(store, user_id)

    all_turns = [t for s in sessions for t in s["turns"]]
    turn_count = len(all_turns)

    def _turn_rate(key: str) -> float:
        if not all_turns:
            return 1.0
        return round(sum(1.0 for t in all_turns if t.get(key)) / len(all_turns), 6)

    naive_tokens = sum(s["naive_tokens"] for s in sessions)
    delta_tokens = sum(s["delta_tokens"] for s in sessions)
    token_savings_ratio = round(delta_tokens / naive_tokens, 6) if naive_tokens > 0 else 0.0
    recall_vals = [v for s in sessions for v in s["recall_vals"]]
    prefetch_vals = [v for s in sessions for v in s["prefetch_vals"]]
    recall_at_3 = round(sum(recall_vals) / len(recall_vals), 6) if recall_vals else 1.0
    prefetch_hit_rate = round(min(prefetch_vals), 6) if prefetch_vals else 1.0
    resume_ok = all(s["resume"]["ok"] for s in sessions)
    latency_ok = all(s["latency"]["ok"] for s in sessions)

    metrics = {
        "turn_count": turn_count,
        "session_count": len(sessions),
        "delta_no_leak": _turn_rate("no_leak_ok"),
        "delta_no_resend": _turn_rate("no_resend_ok"),
        "eviction_soundness": _turn_rate("eviction_ok"),
        "resume_rehydration_exact": 1.0 if resume_ok else 0.0,
        "no_session_clean": 1.0 if control["ok"] else 0.0,
        "smp_channel_parity": 1.0 if smp_channel["ok"] else 0.0,
        "recall@3": recall_at_3,
        "prefetch_hit_rate": prefetch_hit_rate,
        "latency_slo_ok": 1.0 if latency_ok else 0.0,
        "token_savings_ratio": token_savings_ratio,
        "evictions_observed": sum(s["evictions_observed"] for s in sessions),
    }
    failures = [t for t in all_turns if not t.get("ok")]
    return {
        "metrics": metrics,
        "control": control,
        "smp_channel": smp_channel,
        "sessions": sessions,
        "failures": failures,
    }


def check_session_replay_thresholds(session_replay: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    metrics = session_replay.get("metrics") if isinstance(session_replay.get("metrics"), dict) else {}
    if not metrics:
        return ["[session_replay] missing metrics block"]

    turn_count = int(metrics.get("turn_count") or 0)
    if turn_count < SESSION_REPLAY_THRESHOLDS["min_turn_count"]:
        failures.append(
            f"[session_replay] turn_count={turn_count} < {SESSION_REPLAY_THRESHOLDS['min_turn_count']}"
        )
    for key in (
        "delta_no_leak",
        "delta_no_resend",
        "eviction_soundness",
        "resume_rehydration_exact",
        "no_session_clean",
        "smp_channel_parity",
        "recall@3",
        "prefetch_hit_rate",
        "latency_slo_ok",
    ):
        floor = float(SESSION_REPLAY_THRESHOLDS[key])
        value = float(metrics.get(key) or 0.0)
        if value < floor:
            failures.append(f"[session_replay] {key}={value} < {floor}")
    savings = float(metrics.get("token_savings_ratio") or 1.0)
    if savings > SESSION_TOKEN_SAVINGS_CEILING:
        failures.append(f"[session_replay] token_savings_ratio={savings} > {SESSION_TOKEN_SAVINGS_CEILING}")
    return failures


def run_context_pack_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    store.update_settings(
        user_id,
        {"review_new_captures": False, "allow_pending_in_context": True, "allow_agent_reads": True},
    )
    seeded = seed_representative_memories(store, user_id)
    id_sector = {str(memory["id"]): str(memory.get("sector") or "") for memory in seeded}

    profiles = {name: _run_profile(store, user_id, id_sector, name, model) for name, model in PROFILES}

    # Back-compat: top-level `checks`/`metrics` mirror the generic (byte-identical) profile so
    # existing callers/dashboards keep working; the per-profile matrix lives under `profiles`.
    generic = profiles["generic"]
    all_failures = [f for prof in profiles.values() for f in prof["failures"]]

    # Sanity: every intent shape in CONTEXT_INTENT_WEIGHTS is exercised by at least one
    # labeled case, so the intent-derivation gate covers the whole router surface.
    exercised_intents = sorted({str(c.get("expected_intent")) for c in generic["checks"] if c.get("expected_intent")})

    # Build-plan #14: the multi-turn session-replay battery runs AFTER the profile matrix (so the
    # assembler's hot code paths are warm before the latency SLO is measured) and is entirely
    # additive — the session-less profile battery above is untouched.
    session_replay = run_session_replay(store, db_path, vault_path, user_id, id_sector)
    all_failures = all_failures + list(session_replay.get("failures") or [])

    return {
        "harness": "context_pack_eval",
        "seeded_memories": len(seeded),
        "profile_names": [name for name, _ in PROFILES],
        "metrics": generic["metrics"],
        "profiles": {name: {"metrics": prof["metrics"], "failures": prof["failures"]} for name, prof in profiles.items()},
        "exercised_intents": exercised_intents,
        "session_replay": session_replay,
        "counts": {
            "total_checks": sum(len(prof["checks"]) for prof in profiles.values()),
            "failures": len(all_failures),
        },
        "checks": generic["checks"],
        "budget_checks": generic["budget_checks"],
        "failures": all_failures,
    }


def _check_profile_metrics(profile_name: str, metrics: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in (
        "layer_population",
        "ndcg@k",
        "mrr",
        "citation_coverage",
        "no_leak",
        "budget_adherence",
        "intent_derivation",
        "budget_utilization",
        "smp_valid",
        "smp_citation_coverage",
        "smp_no_leak",
        "smp_byte_parity",
    ):
        floor = float(CONTEXT_PACK_THRESHOLDS[key])
        value = float(metrics.get(key) or 0.0)
        if value < floor:
            failures.append(f"[{profile_name}] {key}={value} < {floor}")
    regret = float(metrics.get("budget_regret") or 0.0)
    if regret > CONTEXT_PACK_REGRET_CEILING:
        failures.append(f"[{profile_name}] budget_regret={regret} > {CONTEXT_PACK_REGRET_CEILING}")
    return failures


def check_context_pack_thresholds(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}

    case_count = int(metrics.get("case_count") or 0)
    if case_count < CONTEXT_PACK_THRESHOLDS["min_case_count"]:
        failures.append(f"corpus shrank: case_count={case_count} < {CONTEXT_PACK_THRESHOLDS['min_case_count']}")

    # Build-plan #14 session-replay gate (present-and-checked whenever the harness produced it).
    session_replay = result.get("session_replay")
    if isinstance(session_replay, dict):
        failures.extend(check_session_replay_thresholds(session_replay))

    profiles = result.get("profiles") if isinstance(result.get("profiles"), dict) else {}
    if not profiles:
        # Legacy single-profile shape: check the top-level metrics directly.
        failures.extend(_check_profile_metrics("generic", metrics))
        return failures
    for name, prof in profiles.items():
        prof_metrics = prof.get("metrics") if isinstance(prof.get("metrics"), dict) else {}
        failures.extend(_check_profile_metrics(str(name), prof_metrics))
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cortex context-assembly (get_context) quality gate.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--report-only", action="store_true", help="Print metrics without failing on regressions.")
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print the complete per-case result instead of the concise default summary.",
    )
    args = parser.parse_args()

    if args.db_path:
        result = run_context_pack_eval(
            args.db_path.expanduser(),
            args.vault_path.expanduser() if args.vault_path else None,
            args.user_id,
        )
    else:
        tmp = Path(tempfile.mkdtemp(prefix="context-pack-eval-"))
        result = run_context_pack_eval(tmp / "context-pack-eval.sqlite", tmp / "Cortex.vault", args.user_id)

    failures = check_context_pack_thresholds(result)
    if args.json or failures:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        metrics = result["metrics"]
        replay_metrics = result["session_replay"]["metrics"]
        print(
            json.dumps(
                {
                    "status": "ok",
                    "checks": result["counts"]["total_checks"],
                    "profiles": result.get("profile_names", []),
                    "ndcg@k": metrics.get("ndcg@k"),
                    "mrr": metrics.get("mrr"),
                    "citation_coverage": metrics.get("citation_coverage"),
                    "no_leak": metrics.get("no_leak"),
                    "budget_adherence": metrics.get("budget_adherence"),
                    "session_turns": replay_metrics.get("turn_count"),
                    "session_delta_token_ratio": replay_metrics.get("token_savings_ratio"),
                    "note": "Use --json for per-case and session-replay diagnostics.",
                },
                indent=2,
                sort_keys=True,
            )
        )
    if failures and not args.report_only:
        print("\nCONTEXT PACK GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
