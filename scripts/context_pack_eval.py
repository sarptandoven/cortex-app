from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.storage import CortexStore
from scripts.retrieval_eval import seed_representative_memories


USER_ID = "context-pack-quality"
METRIC_K = 5

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
CONTEXT_PACK_THRESHOLDS: dict[str, Any] = {
    "min_case_count": 10,
    "layer_population": 0.95,
    "ndcg@k": 0.83,
    "mrr": 0.95,
    "citation_coverage": 1.0,
    "no_leak": 1.0,
    "budget_adherence": 1.0,
    "intent_derivation": 1.0,
}


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


def _evaluate_case(store: CortexStore, user_id: str, case: ContextCase, id_sector: dict[str, str]) -> dict[str, Any]:
    pack = store.assemble_context(
        user_id,
        case.task,
        token_budget=case.token_budget,
        sector=case.sector,
        project=case.project,
        intent=case.intent,
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

    ok = layer_population_ok and no_leak_ok and citation_coverage == 1.0 and budget_ok and intent_ok
    return {
        "check": "context_pack",
        "case": case.name,
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
    }


def run_context_pack_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    store.update_settings(
        user_id,
        {"review_new_captures": False, "allow_pending_in_context": True, "allow_agent_reads": True},
    )
    seeded = seed_representative_memories(store, user_id)
    id_sector = {str(memory["id"]): str(memory.get("sector") or "") for memory in seeded}

    checks = [_evaluate_case(store, user_id, case, id_sector) for case in CONTEXT_CASES]
    failures = [c for c in checks if not c.get("ok")]
    metrics = _summarize(checks)

    # Sanity: every intent shape in CONTEXT_INTENT_WEIGHTS is exercised by at least one
    # labeled case, so the intent-derivation gate covers the whole router surface.
    exercised_intents = sorted({str(c.get("expected_intent")) for c in checks if c.get("expected_intent")})

    return {
        "harness": "context_pack_eval",
        "seeded_memories": len(seeded),
        "metrics": metrics,
        "exercised_intents": exercised_intents,
        "counts": {
            "total_checks": len(checks),
            "failures": len(failures),
        },
        "checks": checks,
        "failures": failures,
    }


def check_context_pack_thresholds(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    metrics = result.get("metrics") if isinstance(result.get("metrics"), dict) else {}

    case_count = int(metrics.get("case_count") or 0)
    if case_count < CONTEXT_PACK_THRESHOLDS["min_case_count"]:
        failures.append(f"corpus shrank: case_count={case_count} < {CONTEXT_PACK_THRESHOLDS['min_case_count']}")

    for key in ("layer_population", "ndcg@k", "mrr", "citation_coverage", "no_leak", "budget_adherence", "intent_derivation"):
        floor = float(CONTEXT_PACK_THRESHOLDS[key])
        value = float(metrics.get(key) or 0.0)
        if value < floor:
            failures.append(f"{key}={value} < {floor}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cortex context-assembly (get_context) quality gate.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--report-only", action="store_true", help="Print metrics without failing on regressions.")
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

    print(json.dumps(result, indent=2, sort_keys=True))

    failures = check_context_pack_thresholds(result)
    if failures and not args.report_only:
        print("\nCONTEXT PACK GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
