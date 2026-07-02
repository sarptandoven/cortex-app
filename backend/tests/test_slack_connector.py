from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.slack import discover_slack_channels, fetch_slack_records


class SlackConnectorTests(unittest.TestCase):
    def test_discover_slack_channels_returns_sync_values_and_identity(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb-discover")
            parsed = urlparse(url)
            if parsed.path.endswith("/auth.test"):
                return {"ok": True, "user_id": "U123", "team_id": "T123", "team": "Doppl"}
            self.assertTrue(parsed.path.endswith("/conversations.list"))
            query = parse_qs(parsed.query)
            self.assertEqual(query["exclude_archived"], ["true"])
            self.assertEqual(query["types"], ["public_channel,private_channel"])
            self.assertEqual(query["limit"], ["2"])
            return {
                "ok": True,
                "channels": [
                    {
                        "id": "C123ABC",
                        "name": "general",
                        "is_private": False,
                        "is_member": True,
                        "num_members": 42,
                        "purpose": {"value": "Company-wide updates"},
                        "topic": {"value": "Launch work"},
                    },
                    {
                        "id": "G456DEF",
                        "name": "founders",
                        "is_private": True,
                        "is_member": True,
                        "num_members": 3,
                    },
                ],
                "response_metadata": {"next_cursor": "next-page"},
            }

        discovered = discover_slack_channels(
            token="xoxb-discover",
            limit=2,
            request_json=fake_request,
            api_base_url="https://slack.test/api",
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(discovered.auth_identity["team"], "Doppl")
        self.assertEqual(discovered.channels_found, 2)
        self.assertEqual(discovered.channels_returned, 2)
        self.assertEqual(discovered.next_cursor, "next-page")
        self.assertEqual(discovered.channels[0]["label"], "#general")
        self.assertEqual(discovered.channels[0]["sync_value"], "C123ABC|general")
        self.assertEqual(discovered.channels[0]["purpose"], "Company-wide updates")
        self.assertTrue(discovered.channels[1]["is_private"])

    def test_fetch_slack_records_normalizes_messages_with_cursors(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            if parsed.path.endswith("/auth.test"):
                self.assertEqual(headers["Authorization"], "Bearer xoxb-test")
                return {"ok": True, "user_id": "U123", "user": "sarpt", "team_id": "T123", "team": "Doppl"}
            self.assertTrue(parsed.path.endswith("/conversations.history"))
            query = parse_qs(parsed.query)
            self.assertEqual(query["channel"], ["C123ABC"])
            self.assertEqual(query["limit"], ["2"])
            self.assertEqual(headers["Authorization"], "Bearer xoxb-test")
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "We decided Slack sync should cite <https://example.com|source links>.",
                        "ts": "1782739200.000100",
                    },
                    {
                        "type": "message",
                        "user": "U456",
                        "text": "I prefer compact review queues for Slack imports.",
                        "ts": "1782739201.000200",
                        "thread_ts": "1782739200.000100",
                    },
                ],
                "response_metadata": {"next_cursor": "cursor-next"},
            }

        sync = fetch_slack_records(
            token="xoxb-test",
            channels=["C123ABC|general"],
            max_records=2,
            workspace_url="https://doppl.slack.com",
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.next_cursors, {"C123ABC": "cursor-next"})
        self.assertEqual(sync.auth_identity["user_id"], "U123")
        self.assertEqual(sync.auth_identity["team"], "Doppl")
        first = sync.records[0].to_source_account_record()
        self.assertEqual(first["external_id"], "slack:C123ABC:1782739200.000100")
        self.assertEqual(first["source_url"], "https://doppl.slack.com/archives/C123ABC/p1782739200000100")
        self.assertIn("Source: Slack", first["content"])
        self.assertIn("Channel: #general", first["content"])
        self.assertIn("We decided Slack sync should cite source links (https://example.com).", first["content"])
        self.assertEqual(first["metadata"]["channel"], "general")
        self.assertEqual(first["metadata"]["message"], "1782739200.000100")
        second = sync.records[1].to_source_account_record()
        self.assertEqual(second["metadata"]["thread_ts"], "1782739200.000100")

    def test_fetch_slack_records_adds_parent_context_to_thread_replies(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb-thread")
            parsed = urlparse(url)
            if parsed.path.endswith("/auth.test"):
                return {"ok": True, "user_id": "U123", "user": "sarpt"}
            if parsed.path.endswith("/conversations.history"):
                return {
                    "ok": True,
                    "messages": [
                        {
                            "type": "message",
                            "user": "U123",
                            "text": "Project Atlas release thread for backend launch.",
                            "ts": "1782739200.000100",
                            "thread_ts": "1782739200.000100",
                            "reply_count": 1,
                            "latest_reply": "1782739210.000200",
                        }
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            self.assertTrue(parsed.path.endswith("/conversations.replies"))
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "Project Atlas release thread for backend launch.",
                        "ts": "1782739200.000100",
                        "thread_ts": "1782739200.000100",
                    },
                    {
                        "type": "message",
                        "user": "U456",
                        "text": "We decided Slack replies need parent context.",
                        "ts": "1782739210.000200",
                        "thread_ts": "1782739200.000100",
                    },
                ],
                "response_metadata": {"next_cursor": ""},
            }

        sync = fetch_slack_records(
            token="xoxb-thread",
            channels=["C123ABC|general"],
            max_records=3,
            workspace_url="https://doppl.slack.com",
            request_json=fake_request,
        )

        self.assertTrue(any("conversations.replies" in url for url in calls))
        self.assertEqual(sync.records_returned, 2)
        reply = sync.records[1].to_source_account_record()
        self.assertIn("Thread parent: U123: Project Atlas release thread for backend launch.", reply["content"])
        self.assertIn("Thread context: Project Atlas release thread for backend launch.", reply["content"])
        self.assertEqual(reply["metadata"]["author"], "U456")
        self.assertEqual(reply["metadata"]["thread_parent_author"], "U123")
        self.assertEqual(reply["metadata"]["thread_parent_text_excerpt"], "Project Atlas release thread for backend launch.")

    def test_fetch_slack_records_requires_token_and_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_slack_records(token="", channels=["C123ABC"])
        with self.assertRaisesRegex(ValueError, "channel"):
            fetch_slack_records(token="xoxb-test", channels=[])

    def test_fetch_slack_records_partial_channel_failure_holds_shared_watermark(self) -> None:
        # The high_water_mark is shared across independent channels but the
        # scheduler feeds it back as the `oldest` bound for every channel. If one
        # channel fails while another advances the watermark, the failed channel's
        # older messages would be permanently skipped next sync. On any partial
        # failure the watermark must stay pinned to the input `since`.
        since = "2026-06-01T00:00:00Z"

        def fake_request(url: str, headers: dict[str, str]):
            parsed = urlparse(url)
            if parsed.path.endswith("/auth.test"):
                return {"ok": True, "user_id": "U1", "team": "Doppl"}
            self.assertTrue(parsed.path.endswith("/conversations.history"))
            channel = parse_qs(parsed.query)["channel"][0]
            if channel == "CFAIL":
                raise TimeoutError("slack history fetch failed")
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U9",
                        "text": "Newer message in the healthy channel.",
                        "ts": "1893456000.000100",
                    }
                ],
            }

        sync = fetch_slack_records(
            token="xoxb-test",
            channels=["CFAIL|failing", "COK|healthy"],
            since=since,
            max_records=10,
            request_json=fake_request,
        )

        # The healthy channel still produced its record (partial data is kept)...
        self.assertEqual(sync.records_returned, 1)
        self.assertTrue(sync.errors)
        self.assertEqual(sync.errors[0]["channel"], "CFAIL")
        healthy_captured_at = sync.records[0].captured_at
        self.assertIsNotNone(healthy_captured_at)
        # ...but the shared watermark must NOT jump to the healthy channel's newer
        # timestamp; it stays at the input `since` so the failed channel's tail is
        # re-fetched (deduplicated downstream) instead of skipped.
        self.assertNotEqual(sync.high_water_mark, healthy_captured_at)
        self.assertEqual(sync.high_water_mark, since)
        self.assertEqual(sync.cursor_value, since)


if __name__ == "__main__":
    unittest.main()
