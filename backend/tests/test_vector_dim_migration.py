from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app import embeddings as E
from backend.app.database import connect, init_db, sqlite_vec_status
from backend.app.storage import CortexStore, now_iso


def _sqlite_vec_available(db_path: Path) -> bool:
    try:
        with connect(db_path) as conn:
            return bool(sqlite_vec_status(conn)["available"])
    except Exception:
        return False


class VectorDimMigrationTests(unittest.TestCase):
    """P0-A3: the vector index dimension follows the ACTIVE embedding model (256 for
    potion-base-8M vs the 384 hash index). Constructing a store reconciles the (rebuildable) vec
    table to the model's dimension and re-embeds in the background — never silently disabling
    vectors or inserting a mismatched-dimension vector. The migration runs once at store
    construction (a model change is a restart-level event), so tests build the store under the
    provider they exercise."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        self.vault_path = root / "vault"
        init_db(self.db_path)
        if not _sqlite_vec_available(self.db_path):
            self.skipTest("sqlite-vec not loadable in this environment")
        self.user_id = "vec-user"
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None
        # Create the user's settings once (persist in the DB; later stores reread them).
        CortexStore(self.db_path, self.vault_path).update_settings(
            self.user_id, {"review_new_captures": False, "allow_pending_in_context": True}
        )

    def tearDown(self) -> None:
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None
        self._tmp.cleanup()

    def _store(self) -> CortexStore:
        return CortexStore(self.db_path, self.vault_path)

    def _seed(self, store: CortexStore, memory_id: str, content: str, topics: list[str]) -> None:
        store.save_capture(
            user_id=self.user_id,
            content=content,
            source="obsidian",
            source_url=f"local-file://{memory_id}.md",
            title=memory_id,
            extracted={
                "_timestamp": now_iso(),
                "summary": content,
                "records": [
                    {"id": memory_id, "kind": "claim", "layer": "semantic", "content": content,
                     "confidence": "confirmed", "importance": 3, "topics": topics, "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _active_dims(self) -> int | None:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT dimensions FROM vec_index_meta WHERE id = 1").fetchone()
        return int(row[0]) if row and row[0] else None

    def _model2vec_available(self) -> bool:
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            try:
                return E._load_model2vec_model() is not None
            except Exception:
                return False

    def test_hash_provider_uses_fts_only(self) -> None:
        # The hash embedding is keyword plumbing, not semantics, so the vector path stays OFF for
        # the no-model default (FTS ranking, which the retrieval eval is tuned against). Memories
        # still store + retrieve by keyword; they just aren't vector-embedded.
        store = self._store()
        self._seed(store, "mem_hash", "We chose sharded SQLite over Postgres for the launch.", ["database"])
        store.run_due_jobs(self.user_id, limit=50)
        with connect(self.db_path) as conn:
            self.assertFalse(store._vector_ready(conn))
            self.assertEqual(conn.execute("SELECT count(*) FROM memory_vec").fetchone()[0], 0)
        self.assertTrue(any(h.get("id") == "mem_hash" for h in store.search(self.user_id, "SQLite", limit=5)))

    def test_model2vec_index_is_256_and_semantically_searchable(self) -> None:
        if not self._model2vec_available():
            self.skipTest("model2vec model unavailable")
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            store = self._store()  # constructed under model2vec -> vec table reconciled to 256
            self.assertEqual(self._active_dims(), E.MODEL2VEC_DIMENSIONS)
            self._seed(store, "mem_db", "We chose sharded SQLite over Postgres for the launch.", ["database"])
            self._seed(store, "mem_lang", "My favourite programming language is Rust.", ["language"])
            store.run_due_jobs(self.user_id, limit=50)
            with connect(self.db_path) as conn:
                self.assertGreater(conn.execute("SELECT count(*) FROM memory_vec").fetchone()[0], 0)
            # A paraphrase with no keyword overlap ("datastore" isn't in the memory) should still
            # surface the right memory via the semantic vector path — the whole point of P0.
            hits = store.search(self.user_id, "which datastore did we pick", limit=5)
            self.assertTrue(any(h.get("id") == "mem_db" for h in hits), [h.get("id") for h in hits])

    def test_switching_model_rebuilds_index_and_reembeds(self) -> None:
        if not self._model2vec_available():
            self.skipTest("model2vec model unavailable")
        # Embed a memory on the hash index (384).
        hash_store = self._store()
        self._seed(hash_store, "mem_db", "We chose sharded SQLite over Postgres for the launch.", ["database"])
        hash_store.run_due_jobs(self.user_id, limit=50)
        self.assertEqual(self._active_dims(), E.VECTOR_DIMENSIONS)

        # Switch the model (restart-level): a new store reconciles the index to 256 and re-embeds.
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            store = self._store()  # __init__ rebuilds memory_vec at 256 + enqueues re-embed
            self.assertEqual(self._active_dims(), E.MODEL2VEC_DIMENSIONS)
            store.run_due_jobs(self.user_id, limit=50)  # process the re-embed jobs
            with connect(self.db_path) as conn:
                self.assertGreater(conn.execute("SELECT count(*) FROM memory_vec").fetchone()[0], 0)
            hits = store.search(self.user_id, "which datastore did we pick", limit=5)
            self.assertTrue(any(h.get("id") == "mem_db" for h in hits), [h.get("id") for h in hits])


if __name__ == "__main__":
    unittest.main()
