from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.readwise import fetch_readwise_records


class ReadwiseConnectorTests(unittest.TestCase):
    def test_fetch_readwise_records_normalizes_highlights_with_pagination(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            self.assertTrue(parsed.path.endswith("/export/"))
            query = parse_qs(parsed.query)
            self.assertEqual(headers["Authorization"], "Token readwise-test")
            if len(calls) == 1:
                self.assertEqual(query["updatedAfter"], ["2026-06-01T00:00:00Z"])
                return {
                    "nextPageCursor": "cursor-next",
                    "results": [
                        {
                            "user_book_id": 111,
                            "title": "Designing Data-Intensive Applications",
                            "author": "Martin Kleppmann",
                            "category": "books",
                            "source": "kindle",
                            "source_url": "https://readwise.io/bookreview/111",
                            "updated": "2026-06-30T11:00:00Z",
                            "highlights": [
                                {
                                    "id": 222,
                                    "text": "Indexes are derived structures that speed up reads.",
                                    "note": "Useful for Cortex retrieval planning.",
                                    "highlighted_at": "2026-06-29T10:00:00Z",
                                    "updated": "2026-06-30T10:30:00Z",
                                    "location": 42,
                                    "location_type": "page",
                                    "tags": [{"name": "retrieval"}, {"name": "backend"}],
                                }
                            ],
                        }
                    ],
                }
            self.assertEqual(query["pageCursor"], ["cursor-next"])
            return {
                "nextPageCursor": None,
                "results": [
                    {
                        "user_book_id": 333,
                        "title": "The Mom Test",
                        "author": "Rob Fitzpatrick",
                        "category": "books",
                        "highlights": [
                            {
                                "id": 444,
                                "text": "Talk about their life instead of your idea.",
                                "highlighted_at": "2026-06-28T10:00:00Z",
                                "updated": "2026-06-30T12:00:00Z",
                            }
                        ],
                    }
                ],
            }

        sync = fetch_readwise_records(
            token="readwise-test",
            since="2026-06-01T00:00:00Z",
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.high_water_mark, "2026-06-30T12:00:00Z")
        first = sync.records[0].to_source_account_record()
        self.assertEqual(first["external_id"], "readwise:highlight:222")
        self.assertEqual(first["source_url"], "https://readwise.io/bookreview/111")
        self.assertIn("Source: Readwise", first["content"])
        self.assertIn("Book: Designing Data-Intensive Applications", first["content"])
        self.assertIn("Highlight:", first["content"])
        self.assertIn("Indexes are derived structures that speed up reads.", first["content"])
        self.assertIn("Note:", first["content"])
        self.assertEqual(first["metadata"]["tags"], ["retrieval", "backend"])
        second = sync.records[1].to_source_account_record()
        self.assertEqual(second["source_url"], "readwise://book/333/highlight/444")

    def test_fetch_readwise_records_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_readwise_records(token="")


if __name__ == "__main__":
    unittest.main()
