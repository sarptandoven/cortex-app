from __future__ import annotations

import unittest

from backend.app.connectors.notion import fetch_notion_records


class NotionConnectorTests(unittest.TestCase):
    def test_fetch_notion_records_normalizes_pages_and_blocks(self) -> None:
        calls: list[tuple[str, dict[str, str], dict | None, str]] = []

        def fake_request(url: str, headers: dict[str, str], body: dict | None, method: str):
            calls.append((url, headers, body, method))
            self.assertEqual(headers["Authorization"], "Bearer notion-test")
            self.assertEqual(headers["Notion-Version"], "2026-03-11")
            if method == "POST":
                self.assertTrue(url.endswith("/search"))
                self.assertEqual(body["filter"], {"value": "page", "property": "object"})
                return {
                    "has_more": False,
                    "next_cursor": None,
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "created_time": "2026-06-29T10:00:00Z",
                            "last_edited_time": "2026-06-30T10:00:00Z",
                            "url": "https://www.notion.so/doppl/page-1",
                            "parent": {"type": "workspace", "workspace": True},
                            "properties": {
                                "Name": {"type": "title", "title": [{"plain_text": "Project Atlas Plan"}]},
                                "Status": {"type": "status", "status": {"name": "In Progress"}},
                                "Tags": {"type": "multi_select", "multi_select": [{"name": "memory"}, {"name": "backend"}]},
                            },
                        }
                    ],
                }
            self.assertEqual(method, "GET")
            self.assertIn("/blocks/page-1/children", url)
            return {
                "has_more": False,
                "next_cursor": None,
                "results": [
                    {"type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Memory loop"}]}},
                    {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "We decided Notion sync should cite page URLs."}]}},
                ],
            }

        sync = fetch_notion_records(
            token="notion-test",
            max_records=5,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.records_found, 1)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.high_water_mark, "2026-06-30T10:00:00Z")
        record = sync.records[0].to_source_account_record()
        self.assertEqual(record["external_id"], "notion:page:page-1")
        self.assertEqual(record["source_url"], "https://www.notion.so/doppl/page-1")
        self.assertIn("Source: Notion", record["content"])
        self.assertIn("Page: Project Atlas Plan", record["content"])
        self.assertIn("Properties: Status: In Progress; Tags: memory, backend", record["content"])
        self.assertIn("Heading 1: Memory loop", record["content"])
        self.assertIn("Paragraph: We decided Notion sync should cite page URLs.", record["content"])
        self.assertEqual(record["metadata"]["title"], "Project Atlas Plan")

    def test_fetch_notion_records_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_notion_records(token="")

    def test_fetch_notion_records_skips_archived_and_trash_pages(self) -> None:
        def fake_request(url: str, headers: dict[str, str], body: dict | None, method: str):
            self.assertEqual(method, "POST")
            return {
                "has_more": False,
                "results": [
                    {
                        "object": "page",
                        "id": "archived-page",
                        "archived": True,
                        "properties": {"Name": {"type": "title", "title": [{"plain_text": "Archived"}]}},
                    },
                    {
                        "object": "page",
                        "id": "trash-page",
                        "in_trash": True,
                        "properties": {"Name": {"type": "title", "title": [{"plain_text": "Trash"}]}},
                    },
                ],
            }

        sync = fetch_notion_records(token="notion-test", include_content=False, request_json=fake_request)

        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 0)
        self.assertEqual(sync.records, [])


if __name__ == "__main__":
    unittest.main()
