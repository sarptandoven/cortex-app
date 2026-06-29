from __future__ import annotations

import argparse
import json
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore, MEMORY_LAYERS


USER_ID = "retrieval-quality"
SEED_TIMESTAMP = "2026-01-01T00:00:00Z"
METRIC_K_VALUES = (1, 3)


@dataclass(frozen=True)
class SeedMemory:
    id: str
    kind: str
    layer: str
    content: str
    summary: str
    topics: tuple[str, ...]


@dataclass(frozen=True)
class RetrievalCase:
    name: str
    query: str
    expected_id: str
    expected_layer: str
    expected_phrase: str
    category: str = "focused"


SEED_MEMORIES: tuple[SeedMemory, ...] = (
    SeedMemory(
        id="rq_semantic_project_helio",
        kind="claim",
        layer="semantic",
        content="Project Helio uses a local SQLite vault as the canonical storage contract and source of truth for Cortex.",
        summary="Helio storage contract uses the durable local SQLite database vault.",
        topics=("helio", "storage", "sqlite", "database"),
    ),
    SeedMemory(
        id="rq_episodic_taipei_importer",
        kind="event",
        layer="episodic",
        content="On 2026-04-18, Vamika met Riley to debug the Taipei importer retry path.",
        summary="Taipei importer retry debug session with Vamika and Riley.",
        topics=("taipei", "importer", "meeting"),
    ),
    SeedMemory(
        id="rq_style_short_notes",
        kind="style",
        layer="style",
        content="Writing style: use crisp implementation notes with concise short paragraphs, a spare tone, and no hype.",
        summary="Use concise short implementation notes with a spare tone.",
        topics=("writing", "style"),
    ),
    SeedMemory(
        id="rq_decision_stdlib_eval",
        kind="decision",
        layer="decision",
        content="Decision: keep retrieval evaluation in Python standard library unittest so CI stays dependency-light and lightweight.",
        summary="Retrieval evaluation should stay lightweight in stdlib unittest.",
        topics=("retrieval", "testing", "ci"),
    ),
    SeedMemory(
        id="rq_preference_tradeoffs",
        kind="preference",
        layer="preference",
        content="Preference: when presenting tradeoffs, lead with the recommended option first and then list risks.",
        summary="Put the recommendation first before caveats and risks.",
        topics=("preference", "tradeoffs"),
    ),
    SeedMemory(
        id="rq_negative_fluffy_copy",
        kind="negative",
        layer="negative",
        content="Do not use broad metaphorical intros, marketing prose, or fluffy launch copy in engineering summaries.",
        summary="Avoid fluffy launch copy and marketing prose in engineering summaries.",
        topics=("negative", "writing", "avoid"),
    ),
)


RETRIEVAL_CASES: tuple[RetrievalCase, ...] = (
    RetrievalCase(
        name="semantic_project_fact",
        query="canonical storage contract Helio",
        expected_id="rq_semantic_project_helio",
        expected_layer="semantic",
        expected_phrase="local SQLite vault",
    ),
    RetrievalCase(
        name="episodic_meeting",
        query="Taipei importer retry meeting",
        expected_id="rq_episodic_taipei_importer",
        expected_layer="episodic",
        expected_phrase="met Riley",
    ),
    RetrievalCase(
        name="style_guidance",
        query="writing style short paragraphs hype",
        expected_id="rq_style_short_notes",
        expected_layer="style",
        expected_phrase="crisp implementation notes",
        category="style_recall",
    ),
    RetrievalCase(
        name="decision_record",
        query="retrieval evaluation stdlib unittest decision",
        expected_id="rq_decision_stdlib_eval",
        expected_layer="decision",
        expected_phrase="dependency-light",
    ),
    RetrievalCase(
        name="preference_tradeoffs",
        query="presenting tradeoffs recommended option risks",
        expected_id="rq_preference_tradeoffs",
        expected_layer="preference",
        expected_phrase="lead with the recommended option",
    ),
    RetrievalCase(
        name="negative_constraint",
        query="fluffy launch copy engineering summaries",
        expected_id="rq_negative_fluffy_copy",
        expected_layer="negative",
        expected_phrase="Do not use",
        category="negative_recall",
    ),
    RetrievalCase(
        name="semantic_paraphrase_vault_source",
        query="Helio source truth database vault",
        expected_id="rq_semantic_project_helio",
        expected_layer="semantic",
        expected_phrase="source of truth",
        category="paraphrase",
    ),
    RetrievalCase(
        name="episodic_paraphrase_debug_session",
        query="Taipei importer retry debug session Riley",
        expected_id="rq_episodic_taipei_importer",
        expected_layer="episodic",
        expected_phrase="met Riley",
        category="paraphrase",
    ),
    RetrievalCase(
        name="decision_paraphrase_lightweight_eval",
        query="lightweight evaluation Python standard library CI",
        expected_id="rq_decision_stdlib_eval",
        expected_layer="decision",
        expected_phrase="dependency-light",
        category="paraphrase",
    ),
    RetrievalCase(
        name="preference_paraphrase_caveats",
        query="recommendation first caveats tradeoffs risks",
        expected_id="rq_preference_tradeoffs",
        expected_layer="preference",
        expected_phrase="recommended option first",
        category="paraphrase",
    ),
    RetrievalCase(
        name="style_recall_concise_spare_tone",
        query="concise implementation notes spare tone",
        expected_id="rq_style_short_notes",
        expected_layer="style",
        expected_phrase="spare tone",
        category="style_recall",
    ),
    RetrievalCase(
        name="negative_recall_marketing_prose",
        query="avoid marketing prose engineering summaries",
        expected_id="rq_negative_fluffy_copy",
        expected_layer="negative",
        expected_phrase="marketing prose",
        category="negative_recall",
    ),
    RetrievalCase(
        name="negative_recall_metaphorical_intros",
        query="metaphorical intros launch copy",
        expected_id="rq_negative_fluffy_copy",
        expected_layer="negative",
        expected_phrase="metaphorical intros",
        category="negative_recall",
    ),
)

NOISY_IMPORT_TEXT = """Source: ChatGPT
Conversation: Project Atlas memory UI
Created: 2026-06-29T12:00:00+00:00

--- Messages ---
assistant: I prefer splashy launch pages with lots of marketing copy.
user: We decided Project Atlas should keep the memory UI to five tabs: Model, Sources, Review, Ask, Trust.
assistant: I will remember that.
user: On June 29, 2026, Project Atlas passed the noisy import retrieval eval.
"""


def seed_representative_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    timestamp = SEED_TIMESTAMP
    content = "\n".join(memory.content for memory in SEED_MEMORIES)
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    result = store.save_capture(
        user_id=user_id,
        content=content,
        source="retrieval-eval",
        source_url=None,
        title="Retrieval quality seed",
        extracted={
            "_timestamp": timestamp,
            "summary": "Representative memory-layer retrieval quality seed.",
            "records": [
                {
                    "id": memory.id,
                    "kind": memory.kind,
                    "layer": memory.layer,
                    "content": memory.content,
                    "summary": memory.summary,
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": list(memory.topics),
                    "entity_ids": [],
                }
                for memory in SEED_MEMORIES
            ],
            "tasks": [],
            "entities": [],
        },
    )
    return result["memories"]


def seed_noisy_import_memories(store: CortexStore, user_id: str = USER_ID) -> list[dict[str, Any]]:
    extracted = extract_context(NOISY_IMPORT_TEXT, "chatgpt")
    result = store.save_capture(
        user_id=user_id,
        content=NOISY_IMPORT_TEXT,
        source="chatgpt",
        source_url="/tmp/project-atlas-chatgpt-export.json",
        title="Project Atlas memory UI",
        extracted=extracted,
    )
    memories = result["memories"]
    joined = "\n".join(memory["content"] for memory in memories)
    for boilerplate in ("Source:", "Conversation:", "Created:", "--- Messages ---"):
        if boilerplate in joined:
            raise AssertionError(f"Noisy import leaked boilerplate into memory content: {boilerplate}")
    if "splashy launch pages" in joined:
        raise AssertionError("Noisy import treated assistant preference as user memory")
    if not all(memory.get("source_url") for memory in memories):
        raise AssertionError("Noisy import memories did not preserve source_url citations")
    return memories


def _metrics_for_results(expected_id: str, result_ids: list[str], k_values: tuple[int, ...]) -> dict[str, float]:
    metrics: dict[str, float] = {}
    for k in k_values:
        top_k = result_ids[:k]
        hits = 1 if expected_id in top_k else 0
        metrics[f"recall@{k}"] = float(hits)
        metrics[f"precision@{k}"] = hits / len(top_k) if top_k else 0.0
    return metrics


def _summarize_metrics(checks: list[dict[str, Any]], k_values: tuple[int, ...]) -> dict[str, Any]:
    def summarize(items: list[dict[str, Any]]) -> dict[str, Any]:
        total = len(items)
        if total == 0:
            return {"case_count": 0, "top1_accuracy": 0.0}

        summary: dict[str, Any] = {
            "case_count": total,
            "top1_accuracy": round(sum(1 for item in items if item["expected_rank"] == 1) / total, 3),
        }
        for k in k_values:
            summary[f"recall@{k}"] = round(sum(item["metrics"][f"recall@{k}"] for item in items) / total, 3)
            summary[f"precision@{k}"] = round(sum(item["metrics"][f"precision@{k}"] for item in items) / total, 3)
        return summary

    categories = sorted({item["category"] for item in checks})
    return {
        "overall": summarize(checks),
        "by_category": {
            category: summarize([item for item in checks if item["category"] == category])
            for category in categories
        },
    }


def _evaluate_case(store: CortexStore, user_id: str, case: RetrievalCase, limit: int) -> dict[str, Any]:
    results = store.search(user_id, case.query, limit=limit)
    if not results:
        raise AssertionError(f"{case.name}: query returned no results: {case.query!r}")

    top = results[0]
    if top["id"] != case.expected_id:
        raise AssertionError(
            f"{case.name}: expected top result {case.expected_id}, got {top['id']} from {[item['id'] for item in results]}"
        )
    if top["layer"] != case.expected_layer:
        raise AssertionError(f"{case.name}: expected layer {case.expected_layer}, got {top['layer']}")
    if case.expected_phrase not in top["content"]:
        raise AssertionError(f"{case.name}: expected phrase {case.expected_phrase!r} in {top['content']!r}")

    layer_results = store.search(user_id, case.query, limit=limit, layer=case.expected_layer)
    layer_ids = [item["id"] for item in layer_results]
    if case.expected_id not in layer_ids:
        raise AssertionError(f"{case.name}: layer-filtered search missed {case.expected_id}: {layer_ids}")

    result_ids = [item["id"] for item in results]
    expected_rank = result_ids.index(case.expected_id) + 1 if case.expected_id in result_ids else None
    return {
        "name": case.name,
        "category": case.category,
        "query": case.query,
        "expected_layer": case.expected_layer,
        "expected_id": case.expected_id,
        "expected_rank": expected_rank,
        "top_result": top["id"],
        "top_layer": top["layer"],
        "result_ids": result_ids,
        "layer_filtered_results": layer_ids,
        "metrics": _metrics_for_results(case.expected_id, result_ids, METRIC_K_VALUES),
    }


def evaluate_retrieval(store: CortexStore, user_id: str = USER_ID, limit: int = 3) -> dict[str, Any]:
    seeded = seed_representative_memories(store, user_id)
    seeded_layers = {memory["layer"] for memory in seeded}
    if seeded_layers != MEMORY_LAYERS:
        missing = sorted(MEMORY_LAYERS - seeded_layers)
        extra = sorted(seeded_layers - MEMORY_LAYERS)
        raise AssertionError(f"Seeded layer mismatch: missing={missing}, extra={extra}")

    checks: list[dict[str, Any]] = []
    for case in RETRIEVAL_CASES:
        checks.append(_evaluate_case(store, user_id, case, limit))

    noisy_memories = seed_noisy_import_memories(store, user_id)
    noisy_cases = (
        RetrievalCase(
            name="noisy_import_decision_tabs",
            query="Project Atlas five tabs Review Trust",
            expected_id=next(memory["id"] for memory in noisy_memories if "five tabs" in memory["content"]),
            expected_layer="decision",
            expected_phrase="five tabs",
            category="noisy_import",
        ),
        RetrievalCase(
            name="noisy_import_event_eval",
            query="Project Atlas noisy import retrieval eval June 29",
            expected_id=next(memory["id"] for memory in noisy_memories if "noisy import retrieval eval" in memory["content"]),
            expected_layer="episodic",
            expected_phrase="noisy import retrieval eval",
            category="noisy_import",
        ),
    )
    for case in noisy_cases:
        checks.append(_evaluate_case(store, user_id, case, limit))

    return {
        "status": "ok",
        "seeded_memories": len(seeded),
        "noisy_import_memories": len(noisy_memories),
        "seeded_layers": sorted(seeded_layers),
        "metrics": _summarize_metrics(checks, METRIC_K_VALUES),
        "checks": checks,
    }


def run_retrieval_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    return evaluate_retrieval(store, user_id)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a lightweight Cortex retrieval quality harness.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    args = parser.parse_args()

    if args.db_path:
        result = run_retrieval_eval(args.db_path.expanduser(), args.vault_path.expanduser() if args.vault_path else None, args.user_id)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_retrieval_eval(root / "retrieval-eval.sqlite", root / "Cortex.vault", args.user_id)

    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
