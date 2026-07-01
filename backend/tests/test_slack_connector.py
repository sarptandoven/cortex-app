from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.slack import fetch_slack_records


class SlackConnectorTests(unittest.TestCase):
    def test_fetch_slack_records_normalizes_messages_with_cursors(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
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

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.next_cursors, {"C123ABC": "cursor-next"})
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

    def test_fetch_slack_records_requires_token_and_channel(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_slack_records(token="", channels=["C123ABC"])
        with self.assertRaisesRegex(ValueError, "channel"):
            fetch_slack_records(token="xoxb-test", channels=[])


if __name__ == "__main__":
    unittest.main()
