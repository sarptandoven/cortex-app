#!/usr/bin/env python3
"""Model2vec-seeded retrieval-quality gate (the "does the lift help?" track).

The main retrieval_eval runs under the hash embedder and so can't judge the semantic features
(reranker, query planning, temporal decay) — which no-op under hash. This gate forces the real
model2vec embedder and measures retrieval with the flags OFF vs ON, asserting ON never regresses
OFF and clears a floor, plus scenario checks (semantic paraphrase promotion, compound-query
decomposition). If model2vec is unavailable (bare CI without the model), it SKIPS cleanly (exit 0)
rather than falling back to hash and misjudging.

Deterministic (model2vec static embeddings are fixed). `python3 scripts/rerank_eval.py`.
"""
from __future__ import annotations

import os

os.environ.setdefault("CORTEX_EMBEDDING_PROVIDER", "model2vec")

import json  # noqa: E402
import sys  # noqa: E402
import tempfile  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.database import init_db  # noqa: E402
from backend.app.embeddings import embedding_status  # noqa: E402
from backend.app.extractor import extract_context  # noqa: E402
from backend.app.storage import CortexStore  # noqa: E402

USER = "rerank-eval-user"

# Clean, distinct-topic corpus — each memory carries a unique keyword we match on, so recall
# reflects the ranking, not content-phrasing artifacts. A compound-fact pair exercises query
# decomposition; recall@k=5 gives the ranker room so we measure the flags, not corpus crowding.
SEED = [
    ("m_datastore", "The engineering crew settled on sqlite-vec as the vector store for lookups.", "cortex-source://notes#d1"),
    ("m_billing_db", "We chose Postgres as the database for the billing service.", "cortex-source://notes#d3"),
    ("m_billing_when", "The billing data migration is scheduled for the third quarter.", "cortex-source://notes#d4"),
    ("m_auth", "Authentication uses short-lived tokens rotated every hour.", "cortex-source://notes#d5"),
    ("m_deploy", "Deploys go out on Tuesdays after the reliability report is green.", "cortex-source://notes#d6"),
    ("m_vendor", "The team evaluated Pinecone and Weaviate before choosing a local option.", "cortex-source://notes#d8"),
]
# Distinctive token per memory for robust matching.
_MATCH_TOKEN = {
    "m_datastore": "sqlite-vec",
    "m_billing_db": "postgres",
    "m_billing_when": "third quarter",
    "m_auth": "rotated every hour",
    "m_deploy": "tuesdays",
    "m_vendor": "pinecone",
}

CASES = [
    {"query": "which store did the team pick for vector lookups", "expected": {"m_datastore"}, "k": 5},
    {"query": "what database did we choose for billing and when is the migration", "expected": {"m_billing_db", "m_billing_when"}, "k": 5},
    {"query": "how do we handle authentication", "expected": {"m_auth"}, "k": 5},
    {"query": "when do deploys happen", "expected": {"m_deploy"}, "k": 5},
    {"query": "which vendors were evaluated before choosing", "expected": {"m_vendor"}, "k": 5},
]

# The flags we intend to ENABLE for users: query planning (additive decomposition + intent) and
# per-layer temporal decay (capped, tie-breaking). The reranker (CORTEX_RERANK) reorders more
# aggressively and stays OFF until a larger graded corpus proves it — this gate reports it as a
# diagnostic but only enforces no-regression for the enabled set.
FLAGS_ON = {"CORTEX_QUERY_PLAN": "1", "CORTEX_TEMPORAL_DECAY": "1"}
FLAGS_OFF = {"CORTEX_RERANK": "off", "CORTEX_QUERY_PLAN": "", "CORTEX_TEMPORAL_DECAY": ""}


def _seed(store: CortexStore) -> None:
    for mem_id, content, url in SEED:
        store.save_capture(
            user_id=USER,
            content=content,
            source="notes",
            source_url=url,
            title=None,
            extracted=extract_context(content, "notes"),
            cite_capture_provenance=True,
            capture_id_override=mem_id,
            auto_approve=True,
        )
    # Embeddings are enqueued as jobs; drain them so the vector index is populated before we
    # measure semantic retrieval (no background worker runs in this harness).
    for _ in range(20):
        result = store.run_due_jobs(USER, limit=100, schedule_source_syncs=False)
        if not result.get("pending"):
            break


def _apply(flags: dict[str, str]) -> None:
    for key, value in flags.items():
        if value:
            os.environ[key] = value
        else:
            os.environ.pop(key, None)


def _recall(store: CortexStore, flags: dict[str, str]) -> tuple[float, list[dict]]:
    _apply(flags)
    per_case: list[dict] = []
    hits = 0
    for case in CASES:
        results = store.search(USER, case["query"], limit=case["k"], include_related=True)
        # Search returns derived-memory ids (not the seed capture ids), so match by whether the
        # expected seed memory's distinctive content shows up in the returned items.
        matched = sum(1 for exp in case["expected"] if _content_hit(results, exp))
        recall = matched / len(case["expected"])
        hits += recall
        per_case.append({"query": case["query"], "recall": round(recall, 3), "matched": matched})
    return hits / len(CASES), per_case


def _content_hit(results: list[dict], expected_id: str) -> bool:
    # Robust match: the expected memory's unique keyword appears in a returned item.
    token = _MATCH_TOKEN.get(expected_id, "")
    if not token:
        return False
    return any(token in str(r.get("content") or "").lower() for r in results)


def run_rerank_eval(db_path: Path, vault_path: Path | None = None) -> dict:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    _seed(store)
    off_recall, off_cases = _recall(store, FLAGS_OFF)
    on_recall, on_cases = _recall(store, FLAGS_OFF | FLAGS_ON)
    # Diagnostic only (not enforced): the full reranker on top of the enabled flags.
    rerank_recall, _ = _recall(store, FLAGS_OFF | FLAGS_ON | {"CORTEX_RERANK": "linear+mmr"})
    _apply(FLAGS_OFF)  # leave env clean
    floor = 0.8
    return {
        "provider": embedding_status().get("provider"),
        "off_recall@k": round(off_recall, 4),
        "on_recall@k": round(on_recall, 4),
        "rerank_recall@k_diagnostic": round(rerank_recall, 4),
        "no_regression": on_recall >= off_recall - 1e-9,
        "clears_floor": on_recall >= floor,
        "floor": floor,
        "enabled_flags": sorted(FLAGS_ON.keys()),
        "off_cases": off_cases,
        "on_cases": on_cases,
    }


def main() -> int:
    provider = embedding_status().get("provider")
    if provider != "model2vec":
        print(json.dumps({"status": "skipped", "reason": f"model2vec unavailable (provider={provider})"}, indent=2))
        return 0
    with tempfile.TemporaryDirectory() as tmp:
        summary = run_rerank_eval(Path(tmp) / "cortex.db", Path(tmp) / "vault")
    print(json.dumps(summary, indent=2))
    ok = summary["no_regression"] and summary["clears_floor"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
