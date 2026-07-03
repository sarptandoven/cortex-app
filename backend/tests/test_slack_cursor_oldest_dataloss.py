from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.slack import fetch_slack_records


class SlackCursorOldestDataLossTests(unittest.TestCase):
    """Regression: resuming a channel from a saved page cursor must NOT apply the
    `oldest` (previous-run HWM) bound to that cursored request.

    Slack pagination walks backward from newest, so a saved `next_cursor` points
    at an OLDER page. If the incremental sync applies `oldest = HWM1` to the
    cursored request, Slack filters out every page-2 message older than HWM1 and
    those messages are never captured on any run -> silent high data-loss.
    """

    def test_resumed_cursor_request_omits_oldest_bound(self) -> None:
        # Run 1 saved this cursor for channel A after hitting the record cap on
        # the newest page and returned high_water_mark = HWM1. Run 2 now passes
        # BOTH since=HWM1 and page_cursors={A: C1}.
        saved_cursor = "cursor-page-2"
        hwm1 = "2026-06-15T00:00:00Z"

        history_queries: list[dict[str, list[str]]] = []

        # Older page-2 messages: their timestamps are OLDER than HWM1. If `oldest`
        # were applied to the cursored request, Slack would drop all of these.
        page_2_messages = [
            {
                "type": "message",
                "user": "U100",
                "text": "Older page-2 message that must not be dropped.",
                "ts": "1749513600.000100",  # 2025-06-10, older than HWM1
            },
            {
                "type": "message",
                "user": "U101",
                "text": "Second older page-2 message, also older than the HWM.",
                "ts": "1749513500.000200",
            },
        ]

        def fake_request(url: str, headers: dict[str, str]):
            parsed = urlparse(url)
            if parsed.path.endswith("/auth.test"):
                return {"ok": True, "user_id": "U1", "team": "Doppl"}
            self.assertTrue(parsed.path.endswith("/conversations.history"))
            query = parse_qs(parsed.query)
            history_queries.append(query)
            # This is the resumed request: it carries the saved cursor.
            self.assertEqual(query["cursor"], [saved_cursor])
            # Simulate Slack's server-side filtering: any message older than the
            # `oldest` bound (if present) is filtered out of the response. This is
            # what silently drops the page-2 tail when the bug is present.
            oldest = query.get("oldest", [None])[0]
            if oldest is not None:
                oldest_ts = float(oldest)
                returned = [m for m in page_2_messages if float(m["ts"]) >= oldest_ts]
            else:
                returned = list(page_2_messages)
            return {
                "ok": True,
                "messages": returned,
                "response_metadata": {"next_cursor": ""},
            }

        sync = fetch_slack_records(
            token="xoxb-resume",
            channels=["C123ABC|general"],
            since=hwm1,
            page_cursors={"C123ABC": saved_cursor},
            max_records=10,
            include_threads=False,
            request_json=fake_request,
        )

        # Exactly one history request was made (the resumed page).
        self.assertEqual(len(history_queries), 1)
        # The core assertion: the resumed cursored request must NOT carry the
        # `oldest` bound (this would have failed before the fix).
        self.assertNotIn("oldest", history_queries[0])
        self.assertEqual(history_queries[0]["cursor"], [saved_cursor])

        # Because `oldest` was not applied, both older page-2 messages survived.
        self.assertEqual(sync.records_returned, 2)
        captured = {record.metadata["message_ts"] for record in sync.records}
        self.assertEqual(captured, {"1749513600.000100", "1749513500.000200"})
        self.assertFalse(sync.errors)

    def test_non_resumed_channel_still_applies_oldest_bound(self) -> None:
        # A channel that starts a run WITHOUT a saved cursor must still get the
        # `oldest` bound so incremental sync stays efficient.
        hwm1 = "2026-06-15T00:00:00Z"
        history_queries: list[dict[str, list[str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            parsed = urlparse(url)
            if parsed.path.endswith("/auth.test"):
                return {"ok": True, "user_id": "U1"}
            self.assertTrue(parsed.path.endswith("/conversations.history"))
            query = parse_qs(parsed.query)
            history_queries.append(query)
            self.assertNotIn("cursor", query)
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U9",
                        "text": "Newest message on the first page.",
                        "ts": "1893456000.000100",
                    }
                ],
                "response_metadata": {"next_cursor": ""},
            }

        fetch_slack_records(
            token="xoxb-fresh",
            channels=["C123ABC|general"],
            since=hwm1,
            max_records=10,
            include_threads=False,
            request_json=fake_request,
        )

        self.assertEqual(len(history_queries), 1)
        # Non-resumed first page DOES carry the `oldest` bound.
        self.assertIn("oldest", history_queries[0])


if __name__ == "__main__":
    unittest.main()
