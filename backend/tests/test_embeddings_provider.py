from __future__ import annotations

import unittest
from unittest import mock

from backend.app import embeddings as E


class EmbeddingProviderTests(unittest.TestCase):
    """P0: real on-device embeddings with a graceful hash fallback. The shipping backend must
    never hard-fail if the local model/deps are absent, and embedding_status() must always work
    (a regression here previously broke ~270 tests)."""

    def setUp(self) -> None:
        # Drop any cached model AND the degrade latch between tests so provider switches and the
        # actual-embed-outcome reporting take effect deterministically.
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None
        E._MODEL2VEC_LOAD_FAILED = False
        E._MODEL2VEC_FAILURE_LOGGED = False

    def tearDown(self) -> None:
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None
        E._MODEL2VEC_LOAD_FAILED = False
        E._MODEL2VEC_FAILURE_LOGGED = False

    def test_embedding_status_is_callable(self) -> None:
        # Guards the _strict_embeddings regression: embedding_status() must not raise.
        with mock.patch.dict("os.environ", {}, clear=False):
            status = E.embedding_status()
        self.assertIn("provider", status)
        self.assertIn("strict", status)
        self.assertIsInstance(status["strict"], bool)

    def test_hash_provider_is_the_default(self) -> None:
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": ""}, clear=False):
            result = E.embed_text_result("I decline meetings before 10am")
        self.assertEqual(result.provider, "hash")
        self.assertEqual(result.dimensions, E.VECTOR_DIMENSIONS)
        self.assertEqual(len(result.vector), E.VECTOR_DIMENSIONS)

    def test_model2vec_falls_back_to_hash_when_model_absent(self) -> None:
        # A machine without the bundled model must still work — degrade to hash, never raise.
        with mock.patch.dict(
            "os.environ",
            {"CORTEX_EMBEDDING_PROVIDER": "model2vec", "CORTEX_MODEL2VEC_PATH": "/nonexistent/model-dir"},
            clear=False,
        ):
            result = E.embed_text_result("hello world")
        self.assertEqual(result.provider, "hash")
        self.assertTrue(result.vector)

    def test_strict_mode_raises_instead_of_falling_back(self) -> None:
        with mock.patch.dict(
            "os.environ",
            {
                "CORTEX_EMBEDDING_PROVIDER": "model2vec",
                "CORTEX_MODEL2VEC_PATH": "/nonexistent/model-dir",
                "CORTEX_EMBEDDING_STRICT": "1",
            },
            clear=False,
        ):
            with self.assertRaises(Exception):
                E.embed_text_result("hello world")

    def test_degraded_model2vec_reports_hash_not_model2vec(self) -> None:
        # HONESTY: a model2vec-configured store whose live model can't load must NOT keep reporting
        # provider=model2vec (a lying "semantic search works" state). The actual embed outcome must
        # drive the reported provider — after the fallback fires, status is truthfully degraded.
        with mock.patch.dict(
            "os.environ",
            {"CORTEX_EMBEDDING_PROVIDER": "model2vec", "CORTEX_MODEL2VEC_PATH": "/nonexistent/model-dir"},
            clear=False,
        ):
            # Before any embed the config is model2vec; a warmup/probe trips the latch on failure.
            E.warmup_embedding_provider()
            self.assertTrue(E._MODEL2VEC_LOAD_FAILED)
            self.assertEqual(E.effective_embedding_provider(), "hash")
            self.assertTrue(E.embedding_provider_degraded())

            status = E.embedding_status()
            self.assertEqual(status["provider"], "hash", "must not lie that model2vec is live")
            self.assertEqual(status["configured_provider"], "model2vec")
            self.assertTrue(status["degraded"])
            self.assertEqual(status["degraded_reason"], "embedding_model_load_failed")

    def test_latch_self_heals_when_model_loads_again(self) -> None:
        # A transient failure must not permanently pin the store to degraded once the model works.
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            E._note_model2vec_failure(RuntimeError("boom"))
            self.assertEqual(E.effective_embedding_provider(), "hash")
            with mock.patch.object(
                E,
                "_model2vec_embedding",
                return_value=E.EmbeddingResult(vector=[0.1] * E.MODEL2VEC_DIMENSIONS, model="m", provider="model2vec", dimensions=E.MODEL2VEC_DIMENSIONS),
            ):
                result = E.embed_text_result("hello")
            self.assertEqual(result.provider, "model2vec")
            self.assertFalse(E._MODEL2VEC_LOAD_FAILED)
            self.assertEqual(E.effective_embedding_provider(), "model2vec")

    def test_configured_hash_is_not_reported_degraded(self) -> None:
        # An intentionally hash-configured (no-model) store is a legitimate provider, not degraded —
        # only a model2vec request that fell back is degraded.
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "hash"}, clear=False):
            status = E.embedding_status()
        self.assertEqual(status["provider"], "hash")
        self.assertEqual(status["configured_provider"], "hash")
        self.assertFalse(status["degraded"])
        self.assertIsNone(status["degraded_reason"])

    def test_model2vec_real_inference_when_available(self) -> None:
        # If the local model2vec model is present (bundled/cached), it produces a real,
        # deterministic, native-dimension embedding whose meaning beats the keyword hash.
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            try:
                self.assertIsNotNone(E._load_model2vec_model())
            except Exception as exc:  # noqa: BLE001
                self.skipTest(f"model2vec model unavailable in this environment: {exc}")
            r1 = E.embed_text_result("We chose sharded SQLite over Postgres for the launch")
            r2 = E.embed_text_result("We chose sharded SQLite over Postgres for the launch")
            query = E.embed_text("what database did we pick")
            distractor = E.embed_text("my favorite language is Rust")

        self.assertEqual(r1.provider, "model2vec")
        self.assertGreater(r1.dimensions, 0)
        self.assertEqual(r1.dimensions, len(r1.vector))
        self.assertEqual(r1.vector, r2.vector, "embeddings must be deterministic")

        def cos(a, b):
            import math
            dot = sum(x * y for x, y in zip(a, b))
            na = math.sqrt(sum(x * x for x in a)) or 1.0
            nb = math.sqrt(sum(y * y for y in b)) or 1.0
            return dot / (na * nb)

        # The paraphrased query is closer to the on-topic memory than to the distractor.
        self.assertGreater(cos(query, r1.vector), cos(query, distractor))


if __name__ == "__main__":
    unittest.main()
