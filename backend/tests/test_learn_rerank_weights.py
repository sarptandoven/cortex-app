from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from scripts.learn_rerank_weights import (
    BASELINE_WEIGHTS,
    learn,
    load_pairs_from_fixture,
    pairwise_accuracy,
    train,
)


def _synthetic_pairs():
    # Relevance correlates strongly with `sem`: the used (pos) item always has higher sem than the
    # skipped (neg) item; rank/ent are non-discriminative. A good learner upweights sem.
    pairs = []
    for i in range(20):
        hi = 0.6 + (i % 5) * 0.05
        lo = 0.1 + (i % 5) * 0.03
        pairs.append((
            {"sem": hi, "rank": 0.5, "ent": 0.2},
            {"sem": lo, "rank": 0.5, "ent": 0.2},
        ))
    return pairs


class LearnerTests(unittest.TestCase):
    def test_train_upweights_discriminative_feature(self):
        weights = train(_synthetic_pairs())
        self.assertAlmostEqual(sum(weights.values()), 1.0, places=3)
        self.assertGreater(weights["sem"], weights["rank"])
        self.assertGreater(weights["sem"], weights["ent"])

    def test_learn_emits_when_beating_baseline(self):
        result = learn(_synthetic_pairs())
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["emit"])
        self.assertGreaterEqual(result["learned_val_accuracy"], result["baseline_val_accuracy"])
        self.assertAlmostEqual(sum(result["weights"].values()), 1.0, places=3)

    def test_insufficient_data_falls_back_to_baseline(self):
        result = learn([({"sem": 0.9, "rank": 0.1, "ent": 0.0}, {"sem": 0.1, "rank": 0.1, "ent": 0.0})])
        self.assertEqual(result["status"], "insufficient_data")
        self.assertFalse(result["emit"])
        self.assertEqual(result["weights"], {k: round(v, 6) for k, v in _norm(BASELINE_WEIGHTS).items()})

    def test_pairwise_accuracy(self):
        pairs = _synthetic_pairs()
        # A sem-only weighting perfectly separates these pairs.
        self.assertEqual(pairwise_accuracy({"sem": 1.0, "rank": 0.0, "ent": 0.0}, pairs), 1.0)

    def test_fixture_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = Path(tmp) / "pairs.json"
            fixture.write_text(json.dumps([{"pos": p, "neg": n} for p, n in _synthetic_pairs()]))
            pairs = load_pairs_from_fixture(fixture)
            self.assertEqual(len(pairs), 20)


class WeightsLoadingTests(unittest.TestCase):
    def test_reranker_reads_configured_weights(self):
        import os

        from backend.app import storage

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "w.json"
            path.write_text(json.dumps({"sem": 0.8, "rank": 0.1, "ent": 0.1}))
            prev_env = os.environ.get("CORTEX_RERANK_WEIGHTS_PATH")
            prev_cache = storage._RERANK_WEIGHTS_CACHE
            try:
                os.environ["CORTEX_RERANK_WEIGHTS_PATH"] = str(path)
                storage._RERANK_WEIGHTS_CACHE = None
                weights = storage._configured_rerank_weights()
                self.assertEqual(weights["sem"], 0.8)
            finally:
                storage._RERANK_WEIGHTS_CACHE = prev_cache
                if prev_env is None:
                    os.environ.pop("CORTEX_RERANK_WEIGHTS_PATH", None)
                else:
                    os.environ["CORTEX_RERANK_WEIGHTS_PATH"] = prev_env

    def test_default_weights_when_unset(self):
        from backend.app import storage

        prev_cache = storage._RERANK_WEIGHTS_CACHE
        try:
            storage._RERANK_WEIGHTS_CACHE = None
            weights = storage._configured_rerank_weights()
            self.assertEqual(weights, storage._DEFAULT_RERANK_WEIGHTS)
        finally:
            storage._RERANK_WEIGHTS_CACHE = prev_cache


def _norm(weights):
    from scripts.learn_rerank_weights import _normalize

    return _normalize(weights)


if __name__ == "__main__":
    unittest.main()
