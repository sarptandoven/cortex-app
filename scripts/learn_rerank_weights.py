#!/usr/bin/env python3
"""Offline reranker-weight learner (Phase 8 feedback loop).

Learns the {sem, rank, ent} blend the reranker uses from usage feedback — WITHOUT touching the
request path. Pure-Python pairwise logistic ranking (no numpy). Reads training pairs from a Cortex
DB's memory_events (served candidates + which citation the agent actually used) or from a JSON
fixture. A guardrail refuses to emit weights unless they beat the tuned baseline on a held-out
split, so a bad/sparse signal can never degrade ranking.

Usage:
  python3 scripts/learn_rerank_weights.py --db <cortex.db> --out weights.json
  python3 scripts/learn_rerank_weights.py --fixture pairs.json --out weights.json
The reranker loads the emitted file via CORTEX_RERANK_WEIGHTS_PATH.
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database_maintenance import shared_database_access  # noqa: E402

FEATURES = ("sem", "rank", "ent")
BASELINE_WEIGHTS = {"sem": 0.6, "rank": 0.25, "ent": 0.15}


def _sigmoid(x: float) -> float:
    if x < -60:
        return 0.0
    if x > 60:
        return 1.0
    return 1.0 / (1.0 + math.exp(-x))


def _score(weights: dict[str, float], features: dict[str, float]) -> float:
    return sum(weights.get(k, 0.0) * float(features.get(k, 0.0)) for k in FEATURES)


def _normalize(weights: dict[str, float]) -> dict[str, float]:
    clamped = {k: max(0.0, weights.get(k, 0.0)) for k in FEATURES}
    total = sum(clamped.values())
    if total <= 0:
        return dict(BASELINE_WEIGHTS)
    return {k: round(v / total, 6) for k, v in clamped.items()}


def pairwise_accuracy(weights: dict[str, float], pairs: list[tuple[dict, dict]]) -> float:
    if not pairs:
        return 0.0
    correct = sum(1 for pos, neg in pairs if _score(weights, pos) > _score(weights, neg))
    return correct / len(pairs)


def train(pairs: list[tuple[dict, dict]], *, epochs: int = 300, lr: float = 0.5) -> dict[str, float]:
    """Fit weights by pairwise logistic ranking: maximize P(score(pos) > score(neg))."""
    weights = {k: 1.0 for k in FEATURES}
    for _ in range(epochs):
        grad = {k: 0.0 for k in FEATURES}
        for pos, neg in pairs:
            diff = _score(weights, pos) - _score(weights, neg)
            coeff = 1.0 - _sigmoid(diff)  # d/dw of -log sigmoid(diff)
            for k in FEATURES:
                grad[k] += coeff * (float(pos.get(k, 0.0)) - float(neg.get(k, 0.0)))
        for k in FEATURES:
            weights[k] += lr * grad[k] / max(1, len(pairs))
    return _normalize(weights)


def load_pairs_from_fixture(path: Path) -> list[tuple[dict, dict]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    pairs: list[tuple[dict, dict]] = []
    for row in data:
        if isinstance(row, dict) and isinstance(row.get("pos"), dict) and isinstance(row.get("neg"), dict):
            pairs.append((row["pos"], row["neg"]))
    return pairs


def load_pairs_from_events(db_path: Path) -> list[tuple[dict, dict]]:
    """Best-effort: read retrieval feedback from memory_events. Returns [] if the feedback events
    have not been logged yet (live feature logging is a documented follow-up), so the script simply
    reports 'no data' rather than failing."""
    pairs: list[tuple[dict, dict]] = []
    with shared_database_access(db_path):
        try:
            conn = sqlite3.connect(str(db_path))
        except sqlite3.Error:
            return []
        try:
            cursor = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE event_type = 'retrieval_feedback' ORDER BY created_at DESC LIMIT 5000"
            )
            for (metadata_json,) in cursor.fetchall():
                try:
                    meta = json.loads(metadata_json or "{}")
                except (TypeError, json.JSONDecodeError):
                    continue
                used = meta.get("used_features")
                for skipped in meta.get("skipped_features") or []:
                    if isinstance(used, dict) and isinstance(skipped, dict):
                        pairs.append((used, skipped))
        except sqlite3.Error:
            return []
        finally:
            conn.close()
    return pairs


def learn(pairs: list[tuple[dict, dict]]) -> dict:
    if len(pairs) < 8:
        return {"status": "insufficient_data", "pairs": len(pairs), "weights": _normalize(BASELINE_WEIGHTS), "emit": False}
    split = int(len(pairs) * 0.8)
    train_pairs, val_pairs = pairs[:split], pairs[split:] or pairs[:1]
    learned = train(train_pairs)
    baseline_acc = pairwise_accuracy(BASELINE_WEIGHTS, val_pairs)
    learned_acc = pairwise_accuracy(learned, val_pairs)
    emit = learned_acc >= baseline_acc  # guardrail: never ship a regression
    return {
        "status": "ok",
        "pairs": len(pairs),
        "baseline_val_accuracy": round(baseline_acc, 4),
        "learned_val_accuracy": round(learned_acc, 4),
        "weights": learned if emit else _normalize(BASELINE_WEIGHTS),
        "emit": bool(emit),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=str, default="")
    parser.add_argument("--fixture", type=str, default="")
    parser.add_argument("--out", type=str, default="")
    args = parser.parse_args()

    if args.fixture:
        pairs = load_pairs_from_fixture(Path(args.fixture))
    elif args.db:
        pairs = load_pairs_from_events(Path(args.db))
    else:
        pairs = []

    result = learn(pairs)
    if args.out and result.get("emit"):
        Path(args.out).write_text(json.dumps(result["weights"], indent=2), encoding="utf-8")
        result["written_to"] = args.out
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
