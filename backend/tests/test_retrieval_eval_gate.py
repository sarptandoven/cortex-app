from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from scripts.retrieval_eval import (
    RETRIEVAL_METRIC_THRESHOLDS,
    check_retrieval_metric_thresholds,
    run_retrieval_eval,
)


def _healthy_categories() -> dict[str, dict[str, float]]:
    return {
        category: {"top1_accuracy": 1.0, "recall@3": 1.0}
        for category in RETRIEVAL_METRIC_THRESHOLDS["required_categories"]
    }


def _result(overall: dict, by_category: dict) -> dict:
    return {"metrics": {"overall": overall, "by_category": by_category}}


class RetrievalEvalGateTests(unittest.TestCase):
    def test_current_eval_passes_the_gate(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = run_retrieval_eval(root / "eval.sqlite", root / "vault")
        self.assertEqual(result["status"], "ok")
        self.assertEqual(check_retrieval_metric_thresholds(result), [])
        self.assertGreaterEqual(
            result["metrics"]["overall"]["case_count"], RETRIEVAL_METRIC_THRESHOLDS["min_case_count"]
        )
        for category in RETRIEVAL_METRIC_THRESHOLDS["required_categories"]:
            self.assertIn(category, result["metrics"]["by_category"])

    def test_gate_flags_low_overall_metrics(self) -> None:
        result = _result(
            {"top1_accuracy": 0.5, "recall@1": 0.5, "recall@3": 0.5, "case_count": 48},
            _healthy_categories(),
        )
        failures = check_retrieval_metric_thresholds(result)
        self.assertTrue(any("overall top1_accuracy" in failure for failure in failures))
        self.assertTrue(any("recall@3" in failure for failure in failures))

    def test_gate_flags_shrunk_corpus(self) -> None:
        result = _result(
            {"top1_accuracy": 1.0, "recall@1": 1.0, "recall@3": 1.0, "case_count": 10},
            _healthy_categories(),
        )
        self.assertTrue(any("corpus shrank" in failure for failure in check_retrieval_metric_thresholds(result)))

    def test_gate_flags_missing_required_layer(self) -> None:
        categories = _healthy_categories()
        del categories["negative_recall"]
        result = _result(
            {"top1_accuracy": 1.0, "recall@1": 1.0, "recall@3": 1.0, "case_count": 48},
            categories,
        )
        self.assertIn("missing required category 'negative_recall'", check_retrieval_metric_thresholds(result))

    def test_gate_flags_per_category_regression(self) -> None:
        categories = _healthy_categories()
        categories["style_recall"] = {"top1_accuracy": 0.0, "recall@3": 0.0}
        result = _result(
            {"top1_accuracy": 1.0, "recall@1": 1.0, "recall@3": 1.0, "case_count": 48},
            categories,
        )
        failures = check_retrieval_metric_thresholds(result)
        self.assertTrue(any("category 'style_recall'" in failure for failure in failures))


if __name__ == "__main__":
    unittest.main()
