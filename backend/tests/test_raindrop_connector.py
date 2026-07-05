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

    def test_capped_scan_resumes_via_page_continuation_without_losing_tail(self) -> None:
        # Items arrive sorted by -lastUpdate (newest first). A record cap must not
        # advance the watermark past unconsumed older items (they would be skipped
        # forever by the next sync's `captured_at <= since` filter), and it must not
        # pin the cursor without a continuation (the same head would be refetched
        # forever with no forward progress). Instead a capped scan finishes its page,
        # emits a next_page continuation, keeps the watermark at `since`, and carries
        # the true newest timestamp in pending_high_water_mark until the scan
        # completes.
        all_items = [
            {"_id": 1, "title": "Item 12", "lastUpdate": "2026-06-30T12:00:00Z"},
            {"_id": 2, "title": "Item 11", "lastUpdate": "2026-06-30T11:00:00Z"},
            {"_id": 3, "title": "Item 10", "lastUpdate": "2026-06-30T10:00:00Z"},
            {"_id": 4, "title": "Item 0930", "lastUpdate": "2026-06-30T09:30:00Z"},
            {"_id": 5, "title": "Item 0915", "lastUpdate": "2026-06-30T09:15:00Z"},
        ]

        def paginating_request(url: str, headers: dict[str, str]):
            query = parse_qs(urlparse(url).query)
            page = int(query["page"][0])
            per_page = int(query["perpage"][0])
            start = page * per_page
            return {"result": True, "items": all_items[start : start + per_page]}

        since = "2026-06-30T09:00:00Z"
        first = fetch_raindrop_records(
            token="raindrop_test",
            since=since,
            max_records=2,
            request_json=paginating_request,
        )
        self.assertEqual([r.external_id for r in first.records], ["raindrop:item:1", "raindrop:item:2"])
        self.assertEqual(first.next_page, "1")
        self.assertEqual(first.high_water_mark, since)
        self.assertEqual(first.pending_high_water_mark, "2026-06-30T12:00:00Z")
        self.assertEqual(first.cursor_value, "1")

        # Scheduled replay contract: continuation runs with since=None and the
        # stored page + pending high-water mark.
        second = fetch_raindrop_records(
            token="raindrop_test",
            since=None,
            page=first.next_page,
            pending_high_water_mark=first.pending_high_water_mark,
            max_records=2,
            request_json=paginating_request,
        )
        self.assertEqual([r.external_id for r in second.records], ["raindrop:item:3", "raindrop:item:4"])
        self.assertEqual(second.next_page, "2")
        self.assertEqual(second.pending_high_water_mark, "2026-06-30T12:00:00Z")

        third = fetch_raindrop_records(
            token="raindrop_test",
            since=None,
            page=second.next_page,
            pending_high_water_mark=second.pending_high_water_mark,
            max_records=2,
            request_json=paginating_request,
        )
        self.assertEqual([r.external_id for r in third.records], ["raindrop:item:5"])
        # Scan complete: the carried pending mark becomes the watermark, so the
        # next incremental sync starts from the true newest item.
        self.assertIsNone(third.next_page)
        self.assertIsNone(third.pending_high_water_mark)
        self.assertEqual(third.high_water_mark, "2026-06-30T12:00:00Z")
        self.assertEqual(third.cursor_value, "2026-06-30T12:00:00Z")

        # Stability: a follow-up incremental sync consumes nothing and keeps the
        # watermark, proving the cycle terminates.
        fourth = fetch_raindrop_records(
            token="raindrop_test",
            since=third.cursor_value,
            max_records=2,
            request_json=paginating_request,
        )
        self.assertEqual(fourth.records_returned, 0)
        self.assertIsNone(fourth.next_page)
        self.assertEqual(fourth.high_water_mark, third.cursor_value)
        self.assertEqual(fourth.cursor_value, third.cursor_value)

    def test_over_returning_page_is_fully_consumed_so_tail_is_not_lost(self) -> None:
        # A server that ignores perpage and over-returns must not lose the tail:
        # the page is consumed past the record cap rather than truncated mid-page.
        items = [
            {"_id": 1, "title": "Newest", "lastUpdate": "2026-06-30T12:00:00Z"},
            {"_id": 2, "title": "Middle", "lastUpdate": "2026-06-30T11:00:00Z"},
            {"_id": 3, "title": "Oldest", "lastUpdate": "2026-06-30T10:00:00Z"},
        ]

        def fake_request(url: str, headers: dict[str, str]):
            return {"result": True, "items": items}

        since = "2026-06-30T09:00:00Z"
        sync = fetch_raindrop_records(
            token="raindrop_test",
            since=since,
            max_records=2,
            request_json=fake_request,
        )

        self.assertEqual(
            [record.external_id for record in sync.records],
            ["raindrop:item:1", "raindrop:item:2", "raindrop:item:3"],
        )
        # The watermark only advances once the continuation completes.
        self.assertEqual(sync.high_water_mark, since)
        self.assertEqual(sync.pending_high_water_mark, "2026-06-30T12:00:00Z")


if __name__ == "__main__":
    unittest.main()
