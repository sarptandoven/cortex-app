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


class AskSemanticCitationTests(unittest.TestCase):
    """P1 (#43): with a real embedding model, Ask must CITE a memory that semantic search found
    even when the query is a paraphrase with no keyword overlap — instead of always abstaining
    because the citation-relevance check was keyword-only. But it must still abstain honestly when
    nothing is genuinely related (the cite-or-abstain safety must not regress)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        self.vault_path = root / "vault"
        init_db(self.db_path)
        if not _sqlite_vec_available(self.db_path):
            self.skipTest("sqlite-vec not loadable in this environment")
        self.user_id = "ask-user"
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None
        CortexStore(self.db_path, self.vault_path).update_settings(
            self.user_id, {"review_new_captures": False, "allow_pending_in_context": True}
        )
        if not self._model2vec_available():
            self.skipTest("model2vec model unavailable")

    def tearDown(self) -> None:
        E._MODEL2VEC_MODEL = None
        E._MODEL2VEC_MODEL_KEY = None
        self._tmp.cleanup()

    def _model2vec_available(self) -> bool:
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            try:
                return E._load_model2vec_model() is not None
            except Exception:
                return False

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

    def test_ask_cites_paraphrase_match_and_abstains_on_nonsense(self) -> None:
        with mock.patch.dict("os.environ", {"CORTEX_EMBEDDING_PROVIDER": "model2vec"}, clear=False):
            store = self._store()
            self._seed(store, "mem_db", "We chose sharded SQLite over Postgres for the launch.", ["database"])
            self._seed(store, "mem_lang", "My favourite programming language is Rust.", ["language"])
            store.run_due_jobs(self.user_id, limit=50)

            # Paraphrase: no keyword overlap with "sharded SQLite" — must now be CITED via semantics.
            answer = store.answer_query(self.user_id, "which datastore did we pick for the launch", limit=5)
            cited_ids = [c.get("id") for c in (answer.get("citations") or [])]
            self.assertIn("mem_db", cited_ids, answer)

            # Unrelated nonsense: nothing is genuinely close -> honest abstention (no citations).
            nonsense = store.answer_query(self.user_id, "what is the boiling point of helium on Mars", limit=5)
            self.assertEqual(nonsense.get("citations") or [], [], nonsense)

    def _store(self) -> CortexStore:
        return CortexStore(self.db_path, self.vault_path)


if __name__ == "__main__":
    unittest.main()
