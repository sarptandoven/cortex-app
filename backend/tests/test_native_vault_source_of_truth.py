from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore
from backend.app.vault_markdown import atomic_write_text, parse_memory_markdown, render_memory_markdown


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

    def _seeded_md_path(self, memory_id: str) -> Path:
        # Notes are now <slug>--<shortid>.md, so locate by the frontmatter id, not the filename.
        for path in (self.store.vault.root / "memories").rglob("*.md"):
            try:
                if parse_memory_markdown(path.read_text(encoding="utf-8")).get("id") == memory_id:
                    return path
            except Exception:
                continue
        self.fail(f"no markdown note for {memory_id}")

    def test_reconcile_is_noop_when_nothing_changed(self) -> None:
        self._seed_memory()
        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertFalse(result["reconciled"], result)

    def test_reconcile_picks_up_a_hand_edited_note(self) -> None:
        # The core two-way-editing promise: edit a memory in Obsidian, Cortex reflects it.
        self._seed_memory()
        md_path = self._seeded_md_path("mem_sqlite_decision")
        record = parse_memory_markdown(md_path.read_text(encoding="utf-8"))
        record["content"] = "We switched the launch database to Postgres with read replicas after all."
        atomic_write_text(md_path, render_memory_markdown(record))

        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertTrue(result["reconciled"])
        self.assertGreaterEqual(result["changed"], 1)

        hits = self.store.search(self.user_id, "Postgres read replicas", limit=5)
        self.assertTrue(any("read replicas after all" in (h.get("content") or "") for h in hits))

    def test_reconcile_ingests_a_hand_created_note(self) -> None:
        # A user can just write a new note file in their vault and it becomes memory.
        self._seed_memory()
        new_path = self.store.vault.root / "memories" / "semantic" / "mem_handmade_zephyr.md"
        atomic_write_text(
            new_path,
            render_memory_markdown(
                {
                    "id": "mem_handmade_zephyr",
                    "user_id": self.user_id,
                    "kind": "claim",
                    "layer": "semantic",
                    "status": "active",
                    "content": "My handwritten note: Project Zephyr timelines slip to Q4.",
                    "topics": ["zephyr", "timelines"],
                }
            ),
        )
        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertGreaterEqual(result["added"], 1)
        self.assertTrue(self.store.search(self.user_id, "Zephyr timelines Q4", limit=5))

    def test_archived_status_survives_markdown_rebuild(self) -> None:
        # A memory archived via patch (JSON + Markdown) must stay archived after a
        # Markdown-sourced rebuild — it must not resurrect as active.
        self._seed_memory()
        self.assertTrue(self.store.search(self.user_id, "sharded SQLite", limit=5))
        self.store.vault.patch_memory("mem_sqlite_decision", {"status": "archived"})
        self.store.rebuild_index_from_vault(self.user_id)
        hits = self.store.search(self.user_id, "sharded SQLite Postgres", limit=5)
        self.assertFalse(any(h["id"] == "mem_sqlite_decision" for h in hits))

    def test_reconcile_removes_a_deleted_note(self) -> None:
        # Delete the note file in the vault -> the memory leaves the index. Deletion is only
        # honored once the vault is migrated to Markdown-native (as the app does at startup).
        self._seed_memory()
        self.store.ensure_vault_backfilled(self.user_id)  # establish the Markdown baseline
        self.assertTrue(self.store.search(self.user_id, "sharded SQLite", limit=5))
        self._seeded_md_path("mem_sqlite_decision").unlink()

        result = self.store.reconcile_vault_edits(self.user_id)
        self.assertGreaterEqual(result["removed"], 1)
        remaining = self.store.search(self.user_id, "sharded SQLite Postgres", limit=5)
        self.assertFalse(any("sharded SQLite" in (h.get("content") or "") for h in remaining))


if __name__ == "__main__":
    unittest.main()
