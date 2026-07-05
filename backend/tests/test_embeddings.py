from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.app.embeddings import VECTOR_MODEL, embed_text_result, embedding_status, hash_embed_text


class FakeEmbeddingResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


class EmbeddingProviderTests(unittest.TestCase):
    def test_hash_embedding_is_deterministic_and_normalized(self) -> None:
        first = hash_embed_text("Cortex remembers concise engineering preferences.")
        second = hash_embed_text("Cortex remembers concise engineering preferences.")

        self.assertEqual(first, second)
        self.assertEqual(len(first), 384)
        self.assertAlmostEqual(sum(value * value for value in first), 1.0, places=4)

    def test_openai_provider_uses_embeddings_endpoint_contract(self) -> None:
        calls = []

        def fake_urlopen(request, timeout):
            calls.append((request, timeout))
            return FakeEmbeddingResponse(
                {
                    "model": "text-embedding-3-small",
                    "data": [{"embedding": [0.1, 0.2, 0.3]}],
                }
            )

        with patch.dict(
            os.environ,
            {
                "CORTEX_EMBEDDING_PROVIDER": "openai",
                "CORTEX_EMBEDDING_MODEL": "text-embedding-3-small",
                "CORTEX_EMBEDDING_DIMENSIONS": "3",
                "CORTEX_EMBEDDING_STRICT": "0",
                "CORTEX_OPENAI_EMBEDDINGS_URL": "https://example.test/v1/embeddings",
                "OPENAI_API_KEY": "sk-test",
            },
        ):
            with patch("backend.app.embeddings.urllib.request.urlopen", side_effect=fake_urlopen):
                result = embed_text_result("Remember the search backend decision.")

        self.assertEqual(result.provider, "openai")
        self.assertEqual(result.model, "text-embedding-3-small")
        self.assertEqual(result.dimensions, 3)
        self.assertEqual(result.vector, [0.1, 0.2, 0.3])
        self.assertEqual(len(calls), 1)
        request, timeout = calls[0]
        self.assertEqual(timeout, 10.0)
        self.assertEqual(request.full_url, "https://example.test/v1/embeddings")
        self.assertEqual(request.headers["Authorization"], "Bearer sk-test")
        payload = json.loads(request.data.decode("utf-8"))
        self.assertEqual(payload["input"], "Remember the search backend decision.")
        self.assertEqual(payload["model"], "text-embedding-3-small")
        self.assertEqual(payload["dimensions"], 3)
        self.assertEqual(payload["encoding_format"], "float")

    def test_openai_provider_falls_back_to_hash_when_not_strict(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CORTEX_EMBEDDING_PROVIDER": "openai",
                "CORTEX_EMBEDDING_DIMENSIONS": "3",
                "CORTEX_EMBEDDING_STRICT": "0",
                "OPENAI_API_KEY": "sk-test",
            },
        ):
            with patch("backend.app.embeddings.urllib.request.urlopen", side_effect=TimeoutError("slow")):
                result = embed_text_result("Fallback should keep local search usable.")

        self.assertEqual(result.provider, "hash")
        self.assertEqual(result.model, VECTOR_MODEL)
        self.assertEqual(result.dimensions, 3)
        self.assertEqual(len(result.vector), 3)

    def test_openai_provider_raises_when_strict(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CORTEX_EMBEDDING_PROVIDER": "openai",
                "CORTEX_EMBEDDING_DIMENSIONS": "3",
                "CORTEX_EMBEDDING_STRICT": "1",
                "OPENAI_API_KEY": "sk-test",
            },
        ):
            with patch("backend.app.embeddings.urllib.request.urlopen", side_effect=TimeoutError("slow")):
                with self.assertRaises(TimeoutError):
                    embed_text_result("Strict mode should expose provider failures.")

    def test_embedding_status_reports_configured_provider(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CORTEX_EMBEDDING_PROVIDER": "openai",
                "CORTEX_EMBEDDING_MODEL": "text-embedding-3-large",
                "CORTEX_EMBEDDING_DIMENSIONS": "384",
                "CORTEX_EMBEDDING_STRICT": "yes",
            },
        ):
            status = embedding_status()

        self.assertEqual(status["provider"], "openai")
        self.assertEqual(status["model"], "text-embedding-3-large")
        self.assertEqual(status["dimensions"], 384)
        self.assertEqual(status["schema_dimensions"], 384)
        self.assertTrue(status["index_compatible"])
        self.assertTrue(status["network_required"])
        self.assertTrue(status["strict"])

    def test_embedding_status_uses_real_schema_dimensions_when_given(self) -> None:
        """embedding_status(schema_dimensions=...) reports the ACTUAL index dimension so
        index_compatible is truthful. Regression: model2vec (256) against a 256 index is
        compatible; the no-arg call still falls back to the 384 build constant."""
        with patch.dict(os.environ, {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            os.environ.pop("CORTEX_EMBEDDING_DIMENSIONS", None)
            # Model emits 256; a reconciled 256-dim index is compatible.
            compatible = embedding_status(schema_dimensions=256)
            self.assertEqual(compatible["dimensions"], 256)
            self.assertEqual(compatible["schema_dimensions"], 256)
            self.assertTrue(compatible["index_compatible"])
            # A stale 384-dim index is (truthfully) reported incompatible.
            stale = embedding_status(schema_dimensions=384)
            self.assertEqual(stale["schema_dimensions"], 384)
            self.assertFalse(stale["index_compatible"])
            # No argument: unchanged legacy behaviour (falls back to the constant, 384).
            self.assertEqual(embedding_status()["schema_dimensions"], 384)


if __name__ == "__main__":
    unittest.main()
