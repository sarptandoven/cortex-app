from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore, now_iso


class ConflictDetectionTests(unittest.TestCase):
    """Deterministic contradiction/supersession pass: the memory shouldn't hand an agent a fact
    the user already changed. Detect same-subject contradictory memories, pick current vs stale,
    and let a one-tap resolve supersede the stale one so retrieval excludes it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "conflict-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, content: str, occurred_at: str) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="obsidian",
            source_url=f"local-file://{memory_id}",
            title=memory_id,
            extracted={
                "_timestamp": occurred_at,
                "summary": content,
                "records": [
                    {"id": memory_id, "kind": "decision", "layer": "decision", "content": content,
                     "confidence": "confirmed", "importance": 4, "occurred_at": occurred_at,
                     "topics": ["database"], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _seed_conflict(self) -> None:
        self._seed("mem_sqlite", "The database is sqlite for the launch.", "2026-01-01T00:00:00Z")
        self._seed("mem_postgres", "The database is postgres for the launch.", "2026-06-01T00:00:00Z")

    def test_detects_same_subject_contradiction_with_current_pick(self) -> None:
        self._seed_conflict()
        conflicts = self.store.detect_conflicts(self.user_id)
        self.assertEqual(len(conflicts), 1, conflicts)
        conflict = conflicts[0]
        self.assertEqual(conflict["field"], "database")
        # Newer timestamp wins -> postgres is current, sqlite is stale.
        self.assertEqual(conflict["current"]["memory_id"], "mem_postgres")
        self.assertEqual(conflict["stale"]["memory_id"], "mem_sqlite")
        self.assertEqual(self.store.detect_conflicts(self.user_id), conflicts)  # deterministic

    def test_resolve_removes_stale_from_retrieval_and_from_conflicts(self) -> None:
        self._seed_conflict()
        # Before resolution both are retrievable.
        ids = {h["id"] for h in self.store.search(self.user_id, "database launch", limit=10)}
        self.assertIn("mem_sqlite", ids)
        self.assertIn("mem_postgres", ids)

        self.assertTrue(self.store.resolve_conflict(self.user_id, stale_id="mem_sqlite", current_id="mem_postgres"))

        # The stale fact is gone from retrieval; the current one remains.
        ids_after = {h["id"] for h in self.store.search(self.user_id, "database launch", limit=10)}
        self.assertNotIn("mem_sqlite", ids_after)
        self.assertIn("mem_postgres", ids_after)
        # And the conflict is resolved.
        self.assertEqual(self.store.detect_conflicts(self.user_id), [])
        # superseded_by persisted.
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT superseded_by FROM memories WHERE id = ?", ("mem_sqlite",)).fetchone()
        self.assertEqual(row["superseded_by"], "mem_postgres")

    def test_resolve_rejects_unknown_or_self(self) -> None:
        self._seed_conflict()
        self.assertFalse(self.store.resolve_conflict(self.user_id, stale_id="mem_sqlite", current_id="mem_sqlite"))
        self.assertFalse(self.store.resolve_conflict(self.user_id, stale_id="nope", current_id="mem_postgres"))

    def test_no_conflict_when_subjects_differ(self) -> None:
        self._seed("mem_db", "The database is sqlite.", "2026-01-01T00:00:00Z")
        self._seed("mem_owner", "The launch owner is Alice.", "2026-02-01T00:00:00Z")
        self.assertEqual(self.store.detect_conflicts(self.user_id), [])

    def test_quality_report_surfaces_conflicts(self) -> None:
        self._seed_conflict()
        report = self.store.memory_quality_report(self.user_id)
        self.assertIn("conflicts", report)
        self.assertTrue(report["conflicts"])


if __name__ == "__main__":
    unittest.main()
