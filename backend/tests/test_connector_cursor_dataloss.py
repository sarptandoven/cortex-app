from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app.database import init_db
from backend.app.storage import MAX_CONSECUTIVE_FAILING_SYNC_PAGES, CortexStore


class ConnectorCursorDataLossTests(unittest.TestCase):
    """A connector sync page must not advance its cursor past records that failed to save —
    otherwise those records are lost forever. It should hold and re-fetch (idempotent retry),
    then advance once they succeed; and it must not stall forever on a truly unsaveable record."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.user_id = "conn-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        self.account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Test",
            account_identifier="test-acct",
            connection_type="oauth_token",
            status="connected",
            auth_state="healthy",
            policy={"review_required": False, "allow_ai_context": True},
        )
        self.account_id = self.account["id"]
        self.records = [
            {"content": "Good record about project alpha.", "title": "Alpha", "external_id": "ext-alpha"},
            {"content": "FAILME bad record about project beta.", "title": "Beta", "external_id": "ext-beta"},
        ]

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _sync(self, fail: bool):
        original_save = self.store.save_capture

        def flaky_save(*args, **kwargs):
            if fail and "FAILME" in str(kwargs.get("content") or ""):
                raise ValueError("content is too large")
            return original_save(*args, **kwargs)

        with mock.patch.object(self.store, "save_capture", side_effect=flaky_save):
            return self.store.sync_source_account_records(
                self.user_id,
                self.account_id,
                records=self.records,
                cursor_name="pages",
                cursor_value="page2",
                high_water_mark="2026-07-02T02:00:00Z",
                state={"next_page_token": "P3"},
                processing="sync",
            )

    def test_cursor_holds_on_failure_then_advances_on_retry(self) -> None:
        # Sync 1: the beta record fails to save.
        result = self._sync(fail=True)
        self.assertEqual(result["failed"], 1)

        cursor = self.store._latest_sync_cursor(self.user_id, self.account_id, "pages")
        # Cursor did NOT advance to page2 (held to the previous position — None on first sync).
        self.assertNotEqual(cursor.get("cursor_value"), "page2")
        state = cursor.get("state") or {}
        self.assertEqual(state.get("consecutive_failing_pages"), 1)
        # The forward pagination token was stripped so the same window is re-fetched.
        self.assertNotIn("next_page_token", state)

        # Sync 2: the same window is retried, this time the save succeeds.
        result2 = self._sync(fail=False)
        self.assertEqual(result2["failed"], 0)
        cursor2 = self.store._latest_sync_cursor(self.user_id, self.account_id, "pages")
        self.assertEqual(cursor2.get("cursor_value"), "page2")  # now safe to advance
        self.assertNotIn("consecutive_failing_pages", cursor2.get("state") or {})
        # Both records are now saved (no data loss).
        self.assertTrue(self.store.search(self.user_id, "project beta", limit=5))

    def test_poison_record_eventually_advances_to_avoid_permanent_stall(self) -> None:
        # A permanently-unsaveable record must not stall the account forever.
        for _ in range(MAX_CONSECUTIVE_FAILING_SYNC_PAGES):
            self._sync(fail=True)
        cursor = self.store._latest_sync_cursor(self.user_id, self.account_id, "pages")
        # After the retry budget, the cursor advances past the poison window.
        self.assertEqual(cursor.get("cursor_value"), "page2")
        state = cursor.get("state") or {}
        self.assertEqual(state.get("consecutive_failing_pages"), 0)
        self.assertEqual(state.get("skipped_failing_page_after_retries"), MAX_CONSECUTIVE_FAILING_SYNC_PAGES)

    def test_forward_pagination_token_restored_from_prior_cursor_on_failure(self) -> None:
        # A newest-first connector persists a forward-pagination token pointing at the NEXT page.
        # On a later failing page, the hold must re-fetch the SAME page — so it must restore the
        # token that fetched it (from the previously persisted cursor), not the connector's next
        # token, and not drop it (which would fall back to high_water_mark and skip the backlog).
        self.store.sync_source_account_records(
            self.user_id,
            self.account_id,
            records=[{"content": "page one, saved fine.", "title": "P1", "external_id": "ext-p1"}],
            cursor_name="pages",
            cursor_value="page1",
            high_water_mark="2026-07-01T00:00:00Z",
            state={"next_page_token": "TOKEN_FOR_PAGE2"},
            processing="sync",
        )
        prior = self.store._latest_sync_cursor(self.user_id, self.account_id, "pages")
        self.assertEqual((prior.get("state") or {}).get("next_page_token"), "TOKEN_FOR_PAGE2")

        original_save = self.store.save_capture

        def flaky_save(*args, **kwargs):
            if "FAILME" in str(kwargs.get("content") or ""):
                raise ValueError("content is too large")
            return original_save(*args, **kwargs)

        with mock.patch.object(self.store, "save_capture", side_effect=flaky_save):
            self.store.sync_source_account_records(
                self.user_id,
                self.account_id,
                records=[{"content": "FAILME page two beta.", "title": "P2", "external_id": "ext-p2"}],
                cursor_name="pages",
                cursor_value="page2",
                high_water_mark="2026-07-02T00:00:00Z",
                state={"next_page_token": "TOKEN_FOR_PAGE3"},
                processing="sync",
            )
        held = self.store._latest_sync_cursor(self.user_id, self.account_id, "pages")
        held_state = held.get("state") or {}
        # The token that fetched the failing page is restored (not the next one, not dropped),
        # and the offset is held at the prior position so the exact same window is retried.
        self.assertEqual(held_state.get("next_page_token"), "TOKEN_FOR_PAGE2")
        self.assertEqual(held.get("cursor_value"), "page1")
        self.assertEqual(held_state.get("consecutive_failing_pages"), 1)

    def test_persist_cursor_false_defers_the_cursor_write(self) -> None:
        # The primitive the batched-sync fix relies on: a batched caller can suppress the per-batch
        # cursor write so a later clean batch cannot advance past an earlier failed batch. With
        # persist_cursor=False, no cursor is persisted at all.
        result = self.store.sync_source_account_records(
            self.user_id,
            self.account_id,
            records=[{"content": "batch record", "title": "B", "external_id": "ext-b"}],
            cursor_name="batched",
            cursor_value="advanced",
            state={"next_page_token": "NP"},
            processing="sync",
            persist_cursor=False,
        )
        self.assertIsNone(result.get("cursor"))
        self.assertIsNone(self.store._latest_sync_cursor(self.user_id, self.account_id, "batched"))


if __name__ == "__main__":
    unittest.main()
