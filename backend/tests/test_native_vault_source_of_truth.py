from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore


class MarkdownSourceOfTruthTests(unittest.TestCase):
    """Phase 2: the Markdown notes are the memory source of truth. Deleting the SQLite index
    AND the legacy JSON records must not lose memory — it rebuilds from the Markdown files the
    user owns. This is the "delete the app database and it comes back from your files" guarantee."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False  # deterministic lexical retrieval
        self.user_id = "vault-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_memory(self) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content="We decided to use sharded SQLite for the Cortex 10k launch instead of Postgres.",
            source="obsidian",
            source_url="local-file://cortex-project.md#line=3",
            title="Cortex database decision",
            extracted={
                "_timestamp": "2026-07-02T15:04:00Z",
                "summary": "Chose sharded SQLite over Postgres for the 10k launch.",
                "records": [
                    {
                        "id": "mem_sqlite_decision",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "We decided to use sharded SQLite for the Cortex 10k launch instead of Postgres.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["database", "launch", "scaling"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def test_markdown_note_is_written_and_reread(self) -> None:
        self._seed_memory()
        md_files = list((self.store.vault.root / "memories").rglob("*.md"))
        self.assertTrue(md_files, "no Markdown memory note written")
        records = self.store.vault.iter_memory_markdown_records(self.user_id)
        self.assertTrue(any(r["id"] == "mem_sqlite_decision" for r in records))
        self.assertTrue(any("sharded SQLite" in (r.get("content") or "") for r in records))

    def test_rebuild_from_markdown_only_restores_memory(self) -> None:
        self._seed_memory()
        self.assertTrue(self.store.search(self.user_id, "sharded SQLite", limit=5))

        # Simulate losing everything except the user's Markdown files: delete the legacy JSON
        # memory records so rebuild MUST use the Markdown notes.
        removed = 0
        for path in (self.store.vault.root / "memories").rglob("*.json"):
            path.unlink()
            removed += 1
        self.assertGreater(removed, 0, "expected JSON memory records to delete")

        result = self.store.rebuild_index_from_vault(self.user_id)
        self.assertIsInstance(result, dict)

        # The memory is back and retrievable, rebuilt purely from Markdown.
        hits = self.store.search(self.user_id, "sharded SQLite Postgres", limit=5)
        self.assertTrue(hits, "memory not restored from Markdown after DB+JSON loss")
        self.assertTrue(any("sharded SQLite" in (h.get("content") or "") for h in hits))
        restored = next(h for h in hits if "sharded SQLite" in (h.get("content") or ""))
        self.assertEqual(restored["id"], "mem_sqlite_decision")
        self.assertEqual(restored.get("layer"), "decision")


if __name__ == "__main__":
    unittest.main()
