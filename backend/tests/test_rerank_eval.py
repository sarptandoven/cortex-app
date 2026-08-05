from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path


class RerankEvalGateTests(unittest.TestCase):
    def test_no_regression_under_model2vec(self):
        prev = os.environ.get("CORTEX_EMBEDDING_PROVIDER")
        os.environ["CORTEX_EMBEDDING_PROVIDER"] = "model2vec"
        try:
            from backend.app.embeddings import embedding_status

            if embedding_status().get("provider") != "model2vec":
                self.skipTest("model2vec embedder unavailable in this environment")
            from scripts.rerank_eval import run_rerank_eval

            with tempfile.TemporaryDirectory() as tmp:
                summary = run_rerank_eval(Path(tmp) / "cortex.db", Path(tmp) / "vault")
            self.assertTrue(summary["no_regression"], summary)
            self.assertTrue(summary["clears_floor"], summary)
        finally:
            if prev is None:
                os.environ.pop("CORTEX_EMBEDDING_PROVIDER", None)
            else:
                os.environ["CORTEX_EMBEDDING_PROVIDER"] = prev


if __name__ == "__main__":
    unittest.main()
