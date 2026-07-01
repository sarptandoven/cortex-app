from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.storage import CortexStore


DEFAULT_USER_ID = "retrieval-scale-eval"
DEFAULT_RECORD_COUNT = 1000
DEFAULT_CHUNK_SIZE = 250
DEFAULT_LIMIT = 10
DEFAULT_MAX_RANK = 3
DEFAULT_MAX_SEARCH_MS = 750.0
DEFAULT_RUNS = 3
DEFAULT_WARMUPS = 1
MAX_RECORD_COUNT = 10_000
TARGET_ID = "scale_eval_target_cited_record"
TARGET_SOURCE_URL = "cortex-eval://retrieval-scale/target#line=42&excerpt=canonical-target-record"
QUERY = "Project Taipei retrieval scale source backed latency target record near tie corpus"
BASE_TIMESTAMP = "2026-07-01T12:00:00Z"


def _bounded_int(name: str, value: str, *, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise argparse.ArgumentTypeError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _record_count(value: str) -> int:
    return _bounded_int("record-count", value, minimum=2, maximum=MAX_RECORD_COUNT)


def _positive_int(name: str, maximum: int):
    def parse(value: str) -> int:
        return _bounded_int(name, value, minimum=1, maximum=maximum)

    return parse


def _target_record() -> dict[str, Any]:
    content = (
        "Decision: Project Taipei retrieval scale source backed latency target record near tie corpus "
        "uses the canonical cited release gate. Canonical target marker: ferry-alpha handoff."
    )
    return {
        "id": TARGET_ID,
        "kind": "decision",
        "layer": "decision",
        "content": content,
        "summary": "Canonical cited retrieval-scale target record for Project Taipei latency evaluation.",
        "confidence": "confirmed",
        "importance": 5,
        "topics": [],
        "entity_ids": [],
        "metadata": {"source_quality": "canonical", "scale_eval": True},
    }


def _distractor_record(index: int, seed: int) -> dict[str, Any]:
    bucket = (index * 37 + seed) % 997
    variant = ("amber", "blue", "green", "silver", "white", "violet", "orange")[bucket % 7]
    content = (
        "Decision: Project Taipei retrieval scale source backed latency target record near tie corpus "
        f"uses a noncanonical distractor gate for shard {index:05d}. "
        f"Noise bucket {bucket:03d} follows {variant} review routing."
    )
    return {
        "id": f"scale_eval_distractor_{index:05d}",
        "kind": "decision",
        "layer": "decision",
        "content": content,
        "summary": f"Near-tie distractor {index:05d} for retrieval-scale latency evaluation.",
        "confidence": "confirmed",
        "importance": 4 if index % 11 else 5,
        "topics": [],
        "entity_ids": [],
        "metadata": {"scale_eval": True, "noise_bucket": str(bucket)},
    }


def _save_records(
    store: CortexStore,
    user_id: str,
    *,
    records: list[dict[str, Any]],
    source_url: str,
    title: str,
    capture_id: str,
    timestamp: str = BASE_TIMESTAMP,
) -> list[dict[str, Any]]:
    content = "\n".join(record["content"] for record in records)
    saved = store.save_capture(
        user_id=user_id,
        content=content,
        source="retrieval-scale-eval",
        source_url=source_url,
        title=title,
        extracted={
            "_timestamp": timestamp,
            "summary": title,
            "records": records,
            "tasks": [],
            "entities": [],
        },
        capture_id_override=capture_id,
    )
    return list(saved["memories"])


def seed_corpus(
    store: CortexStore,
    user_id: str,
    *,
    record_count: int,
    chunk_size: int,
    seed: int,
) -> dict[str, Any]:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": False})
    seeded = 0
    chunks = 0
    chunk_size = max(1, chunk_size)
    distractor_count = record_count - 1
    started = time.perf_counter()

    for offset in range(0, distractor_count, chunk_size):
        chunk_records = [
            _distractor_record(index, seed)
            for index in range(offset, min(offset + chunk_size, distractor_count))
        ]
        if not chunk_records:
            continue
        chunks += 1
        seeded += len(
            _save_records(
                store,
                user_id,
                records=chunk_records,
                source_url=f"cortex-eval://retrieval-scale/distractors?chunk={chunks}#line=1&excerpt=near-tie-distractors",
                title=f"Retrieval scale near-tie distractors chunk {chunks}",
                capture_id=f"cap_retrieval_scale_distractors_{chunks:04d}",
            )
        )

    target_memories = _save_records(
        store,
        user_id,
        records=[_target_record()],
        source_url=TARGET_SOURCE_URL,
        title="Retrieval scale canonical cited target",
        capture_id="cap_retrieval_scale_target",
        timestamp="2026-07-01T12:00:01Z",
    )
    seeded += len(target_memories)

    return {
        "record_count": seeded,
        "distractor_count": distractor_count,
        "target_id": target_memories[0]["id"],
        "chunks": chunks + 1,
        "seed_ms": round((time.perf_counter() - started) * 1000, 3),
    }


def _rank(results: list[dict[str, Any]], memory_id: str) -> int | None:
    for index, item in enumerate(results, start=1):
        if item.get("id") == memory_id:
            return index
    return None


def _result_summary(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "rank": index,
            "id": item.get("id"),
            "source": item.get("source"),
            "source_url_present": bool(item.get("source_url")),
            "source_url": item.get("source_url") if item.get("id") == TARGET_ID else None,
            "importance": item.get("importance"),
        }
        for index, item in enumerate(results, start=1)
    ]


def evaluate_search(
    store: CortexStore,
    user_id: str,
    *,
    limit: int,
    runs: int,
    warmups: int,
    max_rank: int,
    max_search_ms: float,
) -> dict[str, Any]:
    for _ in range(warmups):
        store.search(user_id, QUERY, limit=limit)

    latencies: list[float] = []
    ranks: list[int | None] = []
    diagnostics: dict[str, Any] = {}
    final_results: list[dict[str, Any]] = []

    for _ in range(runs):
        run_diagnostics: dict[str, Any] = {}
        started = time.perf_counter()
        results = store.search(user_id, QUERY, limit=limit, _diagnostics=run_diagnostics)
        elapsed_ms = (time.perf_counter() - started) * 1000
        latencies.append(round(elapsed_ms, 3))
        ranks.append(_rank(results, TARGET_ID))
        diagnostics = run_diagnostics
        final_results = results

    target = next((item for item in final_results if item.get("id") == TARGET_ID), None)
    target_rank = _rank(final_results, TARGET_ID)
    target_source_url = str((target or {}).get("source_url") or "")
    failures: list[str] = []
    if target_rank is None:
        failures.append(f"target {TARGET_ID} was not returned in the top {limit} results")
    elif target_rank > max_rank:
        failures.append(f"target rank {target_rank} exceeded max rank {max_rank}")
    if not target_source_url:
        failures.append("target result did not include a source_url citation")
    elif target_source_url != TARGET_SOURCE_URL:
        failures.append(f"target source_url mismatch: {target_source_url!r}")
    worst_ms = max(latencies) if latencies else 0.0
    if worst_ms > max_search_ms:
        failures.append(f"worst search latency {worst_ms:.3f}ms exceeded {max_search_ms:.3f}ms")
    if any(rank is None or rank > max_rank for rank in ranks):
        failures.append(f"one or more measured runs missed the target rank threshold: {ranks}")

    return {
        "status": "fail" if failures else "ok",
        "query": QUERY,
        "limit": limit,
        "thresholds": {
            "max_rank": max_rank,
            "max_search_ms": max_search_ms,
        },
        "latency": {
            "warmups": warmups,
            "runs": runs,
            "search_ms": latencies,
            "median_ms": round(statistics.median(latencies), 3) if latencies else 0.0,
            "worst_ms": round(worst_ms, 3),
        },
        "target": {
            "id": TARGET_ID,
            "rank": target_rank,
            "source_url": target_source_url or None,
            "cited": bool(target_source_url),
        },
        "run_ranks": ranks,
        "diagnostics": diagnostics,
        "results": _result_summary(final_results),
        "failures": failures,
    }


def run_retrieval_scale_eval(
    db_path: Path,
    vault_path: Path | None,
    *,
    user_id: str,
    record_count: int,
    chunk_size: int,
    limit: int,
    max_rank: int,
    max_search_ms: float,
    runs: int,
    warmups: int,
    seed: int,
) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    seed_report = seed_corpus(store, user_id, record_count=record_count, chunk_size=chunk_size, seed=seed)
    search_report = evaluate_search(
        store,
        user_id,
        limit=limit,
        runs=runs,
        warmups=warmups,
        max_rank=max_rank,
        max_search_ms=max_search_ms,
    )
    return {
        "status": search_report["status"],
        "user_id": user_id,
        "seed": seed,
        "database": str(db_path),
        "vault": str(vault_path) if vault_path else None,
        "seed_report": seed_report,
        "search_report": search_report,
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Seed a bounded noisy retrieval corpus and verify cited target search latency."
    )
    parser.add_argument("--record-count", type=_record_count, default=DEFAULT_RECORD_COUNT)
    parser.add_argument("--json-output", type=Path, help="Optional path to write the JSON result.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=DEFAULT_USER_ID)
    parser.add_argument("--chunk-size", type=_positive_int("chunk-size", 1000), default=DEFAULT_CHUNK_SIZE)
    parser.add_argument("--limit", type=_positive_int("limit", 100), default=DEFAULT_LIMIT)
    parser.add_argument("--max-rank", type=_positive_int("max-rank", 100), default=DEFAULT_MAX_RANK)
    parser.add_argument("--max-search-ms", type=float, default=DEFAULT_MAX_SEARCH_MS)
    parser.add_argument("--runs", type=_positive_int("runs", 50), default=DEFAULT_RUNS)
    parser.add_argument("--warmups", type=_bounded_int_zero_to_50, default=DEFAULT_WARMUPS)
    parser.add_argument("--seed", type=int, default=1337)
    return parser


def _bounded_int_zero_to_50(value: str) -> int:
    return _bounded_int("warmups", value, minimum=0, maximum=50)


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    if args.max_rank > args.limit:
        parser.error("--max-rank must be less than or equal to --limit")
    if args.max_search_ms <= 0:
        parser.error("--max-search-ms must be greater than zero")

    json_output = args.json_output.expanduser() if args.json_output else None

    if args.db_path:
        db_path = args.db_path.expanduser()
        vault_path = args.vault_path.expanduser() if args.vault_path else db_path.parent / "Cortex.vault"
        result = run_retrieval_scale_eval(
            db_path,
            vault_path,
            user_id=args.user_id,
            record_count=args.record_count,
            chunk_size=args.chunk_size,
            limit=args.limit,
            max_rank=args.max_rank,
            max_search_ms=args.max_search_ms,
            runs=args.runs,
            warmups=args.warmups,
            seed=args.seed,
        )
    else:
        with tempfile.TemporaryDirectory(prefix="cortex-retrieval-scale-") as tmp:
            root = Path(tmp)
            result = run_retrieval_scale_eval(
                root / "retrieval-scale.sqlite",
                root / "Cortex.vault",
                user_id=args.user_id,
                record_count=args.record_count,
                chunk_size=args.chunk_size,
                limit=args.limit,
                max_rank=args.max_rank,
                max_search_ms=args.max_search_ms,
                runs=args.runs,
                warmups=args.warmups,
                seed=args.seed,
            )

    if json_output:
        _write_json(json_output, result)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
