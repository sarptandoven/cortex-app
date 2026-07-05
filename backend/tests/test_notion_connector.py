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
            if "/blocks/page-1/children" in url:
                return {
                    "has_more": False,
                    "next_cursor": None,
                    "results": [
                        {"id": "heading-1", "type": "heading_1", "heading_1": {"rich_text": [{"plain_text": "Memory loop"}]}},
                        {
                            "id": "toggle-1",
                            "type": "toggle",
                            "has_children": True,
                            "toggle": {"rich_text": [{"plain_text": "Nested decisions"}]},
                        },
                    ],
                }
            self.assertIn("/blocks/toggle-1/children", url)
            return {
                "has_more": False,
                "next_cursor": None,
                "results": [
                    {
                        "id": "paragraph-1",
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"plain_text": "We decided Notion sync should cite nested page content."}]},
                    },
                ],
            }

        sync = fetch_notion_records(
            token="notion-test",
            max_records=5,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 3)
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
        self.assertIn("Toggle: Nested decisions", record["content"])
        self.assertIn("  Paragraph: We decided Notion sync should cite nested page content.", record["content"])
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

    def test_fetch_notion_records_reports_error_object_responses(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str], body: dict | None, method: str):
            calls.append(url)
            return {
                "object": "error",
                "status": 429,
                "code": "rate_limited",
                "message": "Rate limited, retry later. Rejected Authorization: Bearer notion-test",
            }

        sync = fetch_notion_records(token="notion-test", include_content=False, request_json=fake_request)

        # Pagination stops on the first error object instead of looping.
        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.records, [])
        self.assertEqual(sync.records_returned, 0)
        self.assertEqual(len(sync.errors), 1)
        self.assertEqual(sync.errors[0]["category"], "rate_limited")
        self.assertEqual(sync.errors[0]["status_code"], 429)
        self.assertIn("rate_limited", sync.errors[0]["error"])
        self.assertNotIn("notion-test", sync.errors[0]["error"])


if __name__ == "__main__":
    unittest.main()
