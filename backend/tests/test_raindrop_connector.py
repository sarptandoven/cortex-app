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


if __name__ == "__main__":
    unittest.main()
