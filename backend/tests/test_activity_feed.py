"""Regression tests for the LIVE ACTIVITY feed (backend/app/activity.py + storage hooks).

``ActivityHub`` is a thread-safe, bounded, monotonic-sequence ring buffer the macOS app
long-polls to render a per-item "what's happening right now" ticker. It must:
  - hand out strictly increasing seq numbers and replay in order via ``since``,
  - filter by cursor and by user_id (empty user_id = process-wide broadcast to everyone),
  - stay bounded (drop the oldest past the buffer max),
  - return immediately from ``wait_since`` when matching events already exist, and block up to
    ``timeout`` otherwise.
Storage hooks publish one ``memory``/``learned`` event per NEW (non-deduplicated) memory.

Hub-unit tests build a FRESH ``ActivityHub()`` so seq numbers don't bleed across tests; only
the integration test touches the process-global ``activity_hub`` (reading ``latest_seq()``
BEFORE the action to isolate just-published events).
"""
from __future__ import annotations

import os
import threading
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

# Deterministic embeddings for the storage-integration test (a save writes vector rows).
os.environ["CORTEX_EMBEDDING_PROVIDER"] = "hash"

from backend.app.activity import ActivityHub, activity_hub
from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


class ActivityHubUnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.hub = ActivityHub()

    def test_publish_returns_increasing_seq_and_since_orders(self) -> None:
        s1 = self.hub.publish("u1", "memory", "learned", title="a")
        s2 = self.hub.publish("u1", "memory", "learned", title="b")
        s3 = self.hub.publish("u1", "import", "started", title="c")
        self.assertEqual([s1, s2, s3], [1, 2, 3])
        seqs = [e["seq"] for e in self.hub.since(0)]
        self.assertEqual(seqs, [1, 2, 3])
        # payload is preserved and ordered
        self.assertEqual([e["title"] for e in self.hub.since(0)], ["a", "b", "c"])

    def test_since_filters_by_cursor(self) -> None:
        self.hub.publish("u1", "memory", "learned")
        cursor = self.hub.latest_seq()
        self.hub.publish("u1", "memory", "learned")
        self.hub.publish("u1", "memory", "learned")
        newer = self.hub.since(cursor)
        self.assertEqual([e["seq"] for e in newer], [cursor + 1, cursor + 2])
        self.assertTrue(all(e["seq"] > cursor for e in newer))

    def test_user_filtering_and_broadcast(self) -> None:
        self.hub.publish("a", "memory", "learned", title="for-a")
        self.hub.publish("b", "memory", "learned", title="for-b")
        self.hub.publish("", "info", "server", title="broadcast")  # empty user_id -> everyone

        for_b = self.hub.since(0, user_id="b")
        titles_b = {e["title"] for e in for_b}
        self.assertIn("for-b", titles_b)
        self.assertIn("broadcast", titles_b)  # broadcast reaches user b
        self.assertNotIn("for-a", titles_b)  # a's private event does not

        for_a = self.hub.since(0, user_id="a")
        self.assertIn("broadcast", {e["title"] for e in for_a})  # broadcast reaches user a too

    def test_bounded_buffer_drops_oldest(self) -> None:
        hub = ActivityHub()  # default maxlen == 512
        total = 512 + 40
        for i in range(total):
            hub.publish("u1", "memory", "learned", title=f"m{i}")
        events = hub.since(0)
        # Buffer never exceeds its max...
        self.assertEqual(len(events), 512)
        # ...and it keeps the MOST RECENT: the oldest 40 were dropped.
        first_kept_seq = events[0]["seq"]
        self.assertEqual(first_kept_seq, total - 512 + 1)  # seq 41
        # latest_seq keeps counting monotonically past the buffer size.
        self.assertEqual(hub.latest_seq(), total)
        # The very first event is gone from the buffer.
        self.assertNotIn("m0", {e["title"] for e in events})
        self.assertIn(f"m{total - 1}", {e["title"] for e in events})

    def test_wait_since_returns_immediately_when_events_exist(self) -> None:
        self.hub.publish("u1", "memory", "learned")
        start = time.monotonic()
        got = self.hub.wait_since(0, user_id="u1", timeout=5.0)
        elapsed = time.monotonic() - start
        self.assertEqual(len(got), 1)
        self.assertLess(elapsed, 0.1)  # near-instant, did not block

    def test_wait_since_blocks_then_returns_empty_on_timeout(self) -> None:
        cursor = self.hub.latest_seq()
        start = time.monotonic()
        got = self.hub.wait_since(cursor, user_id="u1", timeout=0.2)
        elapsed = time.monotonic() - start
        self.assertEqual(got, [])
        self.assertGreaterEqual(elapsed, 0.15)  # actually blocked ~timeout

    def test_wait_since_wakes_on_late_publish(self) -> None:
        """A producer publishing after a short delay wakes the waiter promptly."""
        cursor = self.hub.latest_seq()
        timer = threading.Timer(0.05, self.hub.publish, args=("u1", "memory", "learned"))
        timer.start()
        self.addCleanup(timer.cancel)
        start = time.monotonic()
        got = self.hub.wait_since(cursor, user_id="u1", timeout=2.0)
        elapsed = time.monotonic() - start
        self.assertEqual(len(got), 1)
        self.assertLess(elapsed, 1.0)  # woke on the publish, not on the 2s timeout


class ActivityStorageIntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.user_id = "activity-user"

    def _save(self, content: str, *, source: str = "chatgpt") -> dict:
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=None,
            title=None,
            extracted=extract_context(content, source),
            cite_capture_provenance=True,
            auto_approve=True,
        )

    def test_save_capture_publishes_learned_events(self) -> None:
        """save_capture emits a memory/learned event per new memory, tagged with source+user."""
        before = activity_hub.latest_seq()  # snapshot the global cursor first
        self._save("My favorite programming language is Rust.", source="chatgpt")
        self._save("My favorite color is blue.", source="chatgpt")
        learned = [
            e
            for e in activity_hub.since(before, user_id=self.user_id)
            if e["kind"] == "memory" and e["action"] == "learned"
        ]
        self.assertGreaterEqual(len(learned), 1)
        self.assertTrue(all(e["source"] == "chatgpt" for e in learned))
        self.assertTrue(all(e["user_id"] == self.user_id for e in learned))

    def test_deduplicated_save_does_not_re_emit(self) -> None:
        """Saving identical content twice emits no second learned event for the duplicate."""
        before = activity_hub.latest_seq()
        self._save("My favorite programming language is Rust.", source="chatgpt")
        after_first = activity_hub.latest_seq()
        learned_first = [
            e
            for e in activity_hub.since(before, user_id=self.user_id)
            if e["kind"] == "memory" and e["action"] == "learned"
        ]
        self.assertEqual(len(learned_first), 1)

        # Re-save the SAME content: the dedup hit must not spam the ticker.
        self._save("My favorite programming language is Rust.", source="chatgpt")
        learned_after_dup = [
            e
            for e in activity_hub.since(after_first, user_id=self.user_id)
            if e["kind"] == "memory" and e["action"] == "learned"
        ]
        self.assertEqual(learned_after_dup, [])


if __name__ == "__main__":
    unittest.main()
