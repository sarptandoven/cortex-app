from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, now_iso


class MirrorInsightStoreTests(unittest.TestCase):
    """P0-B wiring: CortexStore.mirror_insight surfaces the deterministic, cited Mirror Moment
    from real memories (or abstains on a thin corpus). The heuristic itself is covered in
    test_mirror_insight; this locks the store integration the API endpoints call."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "mirror-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_preference(self, count: int) -> None:
        for i in range(count):
            self.store.save_capture(
                user_id=self.user_id,
                content=f"You prefer to decline meetings before 10am (instance {i}).",
                source="calendar",
                source_url=f"calendar://event/{i}",
                title=f"pref-{i}",
                extracted={
                    "_timestamp": now_iso(),
                    "summary": "prefers no early meetings",
                    "records": [
                        {"id": f"mem_pref_{i}", "kind": "preference", "layer": "preference",
                         "content": "prefers to decline meetings before 10am",
                         "confidence": "confirmed", "importance": 4, "topics": ["meetings"], "entity_ids": []}
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

    def test_abstains_on_thin_corpus(self) -> None:
        self._seed_preference(2)
        self.assertIsNone(self.store.mirror_insight(self.user_id))

    def test_surfaces_cited_repeated_preference(self) -> None:
        self._seed_preference(6)
        insight = self.store.mirror_insight(self.user_id)
        self.assertIsNotNone(insight)
        self.assertIn("meetings before 10am", insight["headline"])
        self.assertEqual(insight["evidence"]["source"], "calendar")
        self.assertEqual(insight["evidence"]["count"], 6)
        self.assertEqual(len(insight["evidence"]["memory_ids"]), 6)
        self.assertEqual(insight["layer"], "preference")

    def test_deterministic(self) -> None:
        self._seed_preference(6)
        self.assertEqual(self.store.mirror_insight(self.user_id), self.store.mirror_insight(self.user_id))

    def test_excludes_unreviewed_until_approved(self) -> None:
        """Under product DEFAULTS (review_new_captures=True, allow_pending_in_context=False), a
        repeated pattern that lives only in PENDING (unreviewed) captures must NOT drive a
        'CORTEX NOTICED' insight — Cortex should only claim things about you that you've reviewed.
        Once the captures are approved, the insight surfaces with a reviewed-only count. This locks
        the review gate in mirror._load_active_memories; the other tests here auto-approve and would
        pass with or without it."""
        # Restore defaults (setUp auto-approves; we want the review gate active).
        self.store.update_settings(
            self.user_id,
            {"review_new_captures": True, "allow_pending_in_context": False},
        )
        capture_ids: list[str] = []
        for i in range(6):
            result = self.store.save_capture(
                user_id=self.user_id,
                content=f"You prefer to decline meetings before 10am (instance {i}).",
                source="calendar",
                source_url=f"calendar://event/{i}",
                title=f"pref-{i}",
                extracted={
                    "_timestamp": now_iso(),
                    "summary": "prefers no early meetings",
                    "records": [
                        {"id": f"mem_pref_{i}", "kind": "preference", "layer": "preference",
                         "content": "prefers to decline meetings before 10am",
                         "confidence": "confirmed", "importance": 4, "topics": ["meetings"], "entity_ids": []}
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )
            capture_ids.append(result["capture_id"])

        # Unreviewed → the mirror must abstain (without the gate, 6 active memories would surface it).
        self.assertIsNone(self.store.mirror_insight(self.user_id))

        # Approve every capture → now reviewed, so the insight is allowed to surface.
        for cid in capture_ids:
            self.assertTrue(self.store.approve_capture(self.user_id, cid))

        insight = self.store.mirror_insight(self.user_id)
        self.assertIsNotNone(insight)
        self.assertIn("meetings before 10am", insight["headline"])
        self.assertEqual(insight["evidence"]["count"], 6)


if __name__ == "__main__":
    unittest.main()
