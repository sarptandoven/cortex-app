"""Startup self-heal: the readiness gate must never wedge the app in "starting" on orphan cruft.

Root cause found while testing the live app: /ready computes `issue_count` from fts/relation
orphans and returns `needs_maintenance` (503) when any exist. Normal deletes leave dangling
memory_entities / memory_topics / memory_relations rows, so a real vault (9,257 orphans on the
founder's machine) sat in "Cortex is starting" forever with no self-heal. `prune_orphans` (run on
every startup via ensure_vault_backfilled) deletes that cruft so readiness clears automatically,
without the heavy backup+reindex that `repair_storage` does.
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import CortexStore, now_iso


class StartupSelfHealTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "heal-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_memory_with_links(self, mem_id: str) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=f"Alice shipped {mem_id} with Bob on Project Zephyr.",
            source="obsidian",
            source_url=f"local-file://{mem_id}",
            title=mem_id,
            extracted={
                "_timestamp": now_iso(),
                "summary": f"note {mem_id}",
                "records": [{
                    "id": mem_id, "kind": "claim", "layer": "semantic",
                    "content": f"Alice shipped {mem_id} with Bob.", "confidence": "confirmed",
                    "importance": 3, "topics": ["shipping"],
                    "entity_ids": ["ent_alice", "ent_bob"],
                }],
                "tasks": [],
                "entities": [
                    {"id": "ent_alice", "kind": "person", "name": "Alice", "aliases": [], "context": ""},
                    {"id": "ent_bob", "kind": "person", "name": "Bob", "aliases": [], "context": ""},
                ],
            },
        )

    def _orphan_the_memory(self, mem_id: str) -> None:
        # Delete the parent memory row directly, leaving its entity/topic links dangling — exactly
        # what accumulates from normal memory deletes over time.
        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM memories WHERE user_id = ? AND id = ?", (self.user_id, mem_id))

    def _insert_ghost_relation_orphan(self) -> None:
        # Directly seed a memory_entities row pointing at a memory that never existed — a raw insert
        # with FK enforcement off, exactly the legacy path that left 9,257 relation orphans on the
        # founder's real vault (FK cascade would otherwise prevent it).
        import sqlite3
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("PRAGMA foreign_keys=OFF")
            con.execute(
                "INSERT INTO memory_entities (memory_id, entity_id, user_id, created_at) VALUES (?,?,?,?)",
                ("mem_ghost", "ent_ghost", self.user_id, now_iso()),
            )
            con.commit()
        finally:
            con.close()

    def test_orphans_flip_ready_to_needs_maintenance_then_prune_heals_it(self) -> None:
        self._seed_memory_with_links("mem_a")
        self._orphan_the_memory("mem_a")

        before = self.store.diagnostics(self.user_id)
        self.assertEqual(before["status"], "needs_maintenance")  # this is what stalls /ready

        result = self.store.prune_orphans(self.user_id)
        self.assertGreater(result["removed"], 0)

        after = self.store.diagnostics(self.user_id)
        self.assertEqual(after["status"], "ok")  # app can now leave "starting"

    def test_relation_orphans_specifically_are_pruned(self) -> None:
        # The exact founder-machine failure: dangling memory_entities rows -> relation_orphans -> stall.
        self._insert_ghost_relation_orphan()
        before = self.store.diagnostics(self.user_id)
        self.assertGreater(before["relation_orphans"], 0)
        self.assertEqual(before["status"], "needs_maintenance")

        self.store.prune_orphans(self.user_id)

        after = self.store.diagnostics(self.user_id)
        self.assertEqual(after["relation_orphans"], 0)
        self.assertEqual(after["status"], "ok")

    def test_prune_is_a_noop_on_a_clean_vault(self) -> None:
        self._seed_memory_with_links("mem_clean")
        self.assertEqual(self.store.diagnostics(self.user_id)["status"], "ok")
        result = self.store.prune_orphans(self.user_id)
        self.assertEqual(result["removed"], 0)  # nothing to clean; safe to run every boot
        self.assertEqual(self.store.diagnostics(self.user_id)["status"], "ok")

    def test_prune_does_not_touch_live_rows(self) -> None:
        # A live memory's entity links must survive the prune.
        self._seed_memory_with_links("mem_live")
        with connect(self.db_path) as conn:
            before = conn.execute("SELECT COUNT(*) FROM memory_entities WHERE user_id = ?", (self.user_id,)).fetchone()[0]
        self.assertGreater(before, 0)
        self.store.prune_orphans(self.user_id)
        with connect(self.db_path) as conn:
            after = conn.execute("SELECT COUNT(*) FROM memory_entities WHERE user_id = ?", (self.user_id,)).fetchone()[0]
        self.assertEqual(after, before, "prune deleted links for a memory that still exists")

    def test_ensure_vault_backfilled_self_heals_on_startup(self) -> None:
        # The real fix: startup housekeeping prunes orphans, so the app never boots into a stalled
        # needs_maintenance state.
        self._seed_memory_with_links("mem_b")
        self._orphan_the_memory("mem_b")
        self.assertEqual(self.store.diagnostics(self.user_id)["status"], "needs_maintenance")
        self.store.ensure_vault_backfilled(self.user_id)  # what standalone_server calls at startup
        self.assertEqual(self.store.diagnostics(self.user_id)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
