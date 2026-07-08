"""Phase-2 local->hosted push-sync: the local capture change-feed (capture_change_page) and the
idempotency the hosted /v1/sync/ingest relies on (save_capture with a client-supplied capture id)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore

USER = "sync-push-user"


class SyncPushTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "sync.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, content: str, cid: str | None = None, ts: str | None = None) -> dict:
        extracted = extract_context(content, "macos")
        if ts:
            extracted["_timestamp"] = ts
        return self.store.save_capture(
            user_id=USER, content=content, source="macos", source_url=None, title=None,
            extracted=extracted, capture_id_override=cid, cite_capture_provenance=True, auto_approve=True,
        )

    def test_feed_carries_content_oldest_first_with_monotonic_cursor(self) -> None:
        self._save("Alpha note about databases")
        self._save("Beta note about deploys")
        page = self.store.capture_change_page(USER, 0, 100)
        self.assertEqual(len(page["items"]), 2)
        self.assertEqual(page["items"][0]["content"], "Alpha note about databases")  # content present, oldest first
        self.assertLess(page["items"][0]["seq"], page["items"][1]["seq"])            # monotonic rowid cursor
        self.assertFalse(page["has_more"])
        self.assertEqual(page["next_seq"], page["items"][1]["seq"])

    def test_cursor_resume_returns_only_new_rows(self) -> None:
        self._save("one")
        self._save("two")
        page = self.store.capture_change_page(USER, 0, 100)
        cursor = page["next_seq"]
        self.assertEqual(self.store.capture_change_page(USER, cursor, 100)["items"], [])  # caught up
        self._save("three")
        resumed = self.store.capture_change_page(USER, cursor, 100)
        self.assertEqual(len(resumed["items"]), 1)
        self.assertEqual(resumed["items"][0]["content"], "three")

    def test_pagination_has_more(self) -> None:
        for i in range(5):
            self._save(f"note {i}")
        page = self.store.capture_change_page(USER, 0, 2)
        self.assertEqual(len(page["items"]), 2)
        self.assertTrue(page["has_more"])

    def test_ingest_idempotent_by_client_capture_id(self) -> None:
        # Re-pushing the same batch (same client capture id + pinned timestamp) must UPSERT, not
        # duplicate — this is the property /v1/sync/ingest relies on for safe retries.
        first = self._save("Decision: use sqlite-vec", cid="cap_fixed_1", ts="2026-07-08T00:00:00+00:00")
        again = self._save("Decision: use sqlite-vec", cid="cap_fixed_1", ts="2026-07-08T00:00:00+00:00")
        self.assertEqual(first["capture_id"], again["capture_id"])
        self.assertEqual(len(self.store.capture_change_page(USER, 0, 100)["items"]), 1)

    def test_isolation_between_users(self) -> None:
        self._save("mine")
        self.assertEqual(self.store.capture_change_page("other-user", 0, 100)["items"], [])


if __name__ == "__main__":
    unittest.main()
