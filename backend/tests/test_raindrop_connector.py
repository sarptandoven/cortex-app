from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.raindrop import fetch_raindrop_records


class RaindropConnectorTests(unittest.TestCase):
    def test_fetch_raindrop_records_normalizes_bookmarks_and_highlights(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            self.assertEqual(parsed.path, "/rest/v1/raindrops/0")
            query = parse_qs(parsed.query)
            self.assertEqual(query["page"], ["0"])
            self.assertEqual(query["perpage"], ["10"])
            self.assertEqual(query["sort"], ["-lastUpdate"])
            self.assertEqual(headers["Authorization"], "Bearer raindrop_test")
            return {
                "result": True,
                "count": 1,
                "items": [
                    {
                        "_id": 123,
                        "title": "Local-first memory systems",
                        "link": "https://example.com/local-first-memory",
                        "domain": "example.com",
                        "excerpt": "Cortex should preserve bookmark citations.",
                        "note": "Useful for research recall.",
                        "tags": ["memory", "research"],
                        "important": True,
                        "collection": {"$id": 0},
                        "created": "2026-06-29T10:00:00Z",
                        "lastUpdate": "2026-06-30T10:30:00Z",
                        "highlights": [
                            {
                                "text": "Raindrop highlights should sync as cited memory.",
                                "note": "Important for recall.",
                            }
                        ],
                    }
                ],
            }

        sync = fetch_raindrop_records(token="raindrop_test", max_records=10, request_json=fake_request)

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records_found, 1)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.high_water_mark, "2026-06-30T10:30:00Z")
        self.assertNotIn("raindrop_test", str(sync.to_summary()))
        record = sync.records[0].to_source_account_record()
        self.assertEqual(record["external_id"], "raindrop:item:123")
        self.assertEqual(record["source_url"], "https://example.com/local-first-memory")
        self.assertIn("Source: Raindrop", record["content"])
        self.assertIn("Highlight 1:", record["content"])
        self.assertIn("Raindrop highlights should sync as cited memory.", record["content"])
        self.assertEqual(record["metadata"]["tags"], ["memory", "research"])
        self.assertEqual(record["metadata"]["highlight_count"], 1)

    def test_fetch_raindrop_records_requires_token_and_supports_page_cursor(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_raindrop_records(token="")

        def fake_request(url: str, headers: dict[str, str]):
            parsed = urlparse(url)
            query = parse_qs(parsed.query)
            self.assertEqual(parsed.path, "/rest/v1/raindrops/99")
            self.assertEqual(query["page"], ["2"])
            self.assertEqual(headers["Authorization"], "Bearer raindrop_test")
            return {
                "result": True,
                "items": [
                    {
                        "_id": 456,
                        "title": "Private bookmark",
                        "lastUpdate": "2026-06-30T11:00:00Z",
                    }
                ],
            }

        sync = fetch_raindrop_records(
            token="raindrop_test",
            collection_id="99",
            page="2",
            max_records=1,
            request_json=fake_request,
        )

        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.records[0].source_url, "raindrop://raindrop/456")

    def test_truncated_scan_keeps_cursor_so_tail_items_are_not_lost(self) -> None:
        # Items arrive sorted by -lastUpdate (newest first), so the high-water mark is
        # taken from the FIRST consumed record. When max_records truncates the scan
        # mid-page, the unconsumed tail items are OLDER than that mark; advancing
        # cursor_value to it would make the next sync's `captured_at <= since` filter
        # skip them permanently. The fix keeps cursor_value at the input `since` for a
        # truncated scan so the next sync re-covers the unconsumed range (re-fetched
        # duplicates are deduplicated downstream via stable external ids).
        items = [
            {"_id": 1, "title": "Newest", "lastUpdate": "2026-06-30T12:00:00Z"},
            {"_id": 2, "title": "Middle", "lastUpdate": "2026-06-30T11:00:00Z"},
            {"_id": 3, "title": "Oldest", "lastUpdate": "2026-06-30T10:00:00Z"},
        ]

        def fake_request(url: str, headers: dict[str, str]):
            return {"result": True, "items": items}

        since = "2026-06-30T09:00:00Z"
        first = fetch_raindrop_records(
            token="raindrop_test",
            since=since,
            max_records=2,
            request_json=fake_request,
        )

        self.assertEqual(first.records_returned, 2)
        self.assertEqual(
            [record.external_id for record in first.records],
            ["raindrop:item:1", "raindrop:item:2"],
        )
        # Regression: the truncated scan must not advance the cursor past `since`
        # (previously cursor_value was the 12:00 high-water mark, losing item 3),
        # and must not emit a page continuation that skips the rest of this page.
        self.assertIsNone(first.next_page)
        self.assertEqual(first.cursor_value, since)

        second = fetch_raindrop_records(
            token="raindrop_test",
            since=first.cursor_value,
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(
            [record.external_id for record in second.records],
            ["raindrop:item:1", "raindrop:item:2", "raindrop:item:3"],
        )
        # An untruncated scan reports the true high-water mark again.
        self.assertEqual(second.high_water_mark, "2026-06-30T12:00:00Z")
        self.assertEqual(second.cursor_value, "2026-06-30T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
