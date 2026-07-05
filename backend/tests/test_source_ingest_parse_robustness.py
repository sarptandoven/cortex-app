"""Regression tests for parse robustness in source_ingest.

Each test feeds malformed user-export input to a specific parse path that
previously raised an uncaught exception (aborting the entire import page) and
asserts the parser now degrades gracefully -- returning records / [] or
skipping the bad item -- instead of raising.
"""

from __future__ import annotations

import csv
import json
import unittest

from backend.app import source_ingest
from backend.app.source_ingest import SourceAsset


def _asset(name: str, text: str) -> SourceAsset:
    return SourceAsset(name=name, display_path=name, data=text.encode("utf-8"))


class ChatGPTCreateTimeTest(unittest.TestCase):
    """Bug 1: non-numeric create_time must not raise ValueError."""

    def test_string_create_time_does_not_raise(self) -> None:
        conversation = {
            "title": "Trip planning",
            "mapping": {
                "n1": {
                    "message": {
                        "author": {"role": "user"},
                        # A string date instead of a unix float.
                        "create_time": "2023-01-01",
                        "content": {"parts": ["Hello there"]},
                    }
                }
            },
        }
        messages = source_ingest._chatgpt_messages(conversation)
        self.assertTrue(any("Hello there" in line for line in messages))

    def test_full_parse_skips_bad_time(self) -> None:
        payload = [
            {
                "title": "Trip planning",
                "mapping": {
                    "n1": {
                        "message": {
                            "author": {"role": "user"},
                            "create_time": "not-a-number",
                            "content": {"parts": ["Where should I go?"]},
                        }
                    }
                },
            }
        ]
        asset = _asset("conversations.json", json.dumps(payload))
        records = source_ingest._parse_chatgpt([asset], "chatgpt")
        self.assertTrue(records)
        self.assertIn("Where should I go?", records[0].content)


class ChatGPTAuthorTest(unittest.TestCase):
    """Bug 2: a non-dict 'author' must not raise AttributeError."""

    def test_string_author_does_not_raise(self) -> None:
        conversation = {
            "title": "Support chat",
            "mapping": {
                "n1": {
                    "message": {
                        # author is a bare string, not a dict.
                        "author": "user",
                        "create_time": 1_700_000_000,
                        "content": {"parts": ["Need help"]},
                    }
                }
            },
        }
        messages = source_ingest._chatgpt_messages(conversation)
        self.assertTrue(any("Need help" in line for line in messages))
        # Falls back to the 'unknown' role rather than crashing.
        self.assertTrue(any("unknown" in line for line in messages))


class DeeplyNestedStructuredJsonTest(unittest.TestCase):
    """Bug 3: deeply-nested structured JSON exports must not RecursionError."""

    def _deep_payload(self) -> dict:
        # Nest well past MAX_PARSE_NESTING_DEPTH so an unguarded recursion would
        # overflow the stack.
        depth = source_ingest.MAX_PARSE_NESTING_DEPTH + 500
        node: dict = {"leaf": "bottom"}
        for _ in range(depth):
            node = {"wrap": node}
        return node

    def test_rows_from_payload_degrades(self) -> None:
        rows = source_ingest._json_rows_from_payload(self._deep_payload(), "github")
        self.assertEqual(rows, [])

    def test_json_export_rows_degrades(self) -> None:
        asset = _asset("issues.json", json.dumps(self._deep_payload()))
        rows = source_ingest._json_export_rows(asset, asset.read_text(), "github")
        self.assertEqual(rows, [])


class DiscordOversizedCsvFieldTest(unittest.TestCase):
    """Bug 4: an oversized CSV field (or NUL) must not raise csv.Error."""

    def test_oversized_field_does_not_raise(self) -> None:
        big = "x" * (csv.field_size_limit() + 1000)
        text = 'Timestamp,Author,Contents\n2023-01-01,alice,"' + big + '"\n'
        asset = SourceAsset(
            name="messages/general/messages.csv",
            display_path="messages/general/messages.csv",
            data=text.encode("utf-8"),
        )
        # Sanity: this input really does trip the raw reader.
        with self.assertRaises(csv.Error):
            list(csv.DictReader(text.splitlines()))
        # The parser must degrade to no records rather than propagating.
        records = source_ingest._parse_discord([asset], "discord")
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
