from __future__ import annotations

import unittest

from backend.app.connectors.linear import fetch_linear_records


class LinearConnectorTests(unittest.TestCase):
    def test_fetch_linear_records_normalizes_issues_with_pagination(self) -> None:
        calls = []

        def fake_request(url: str, headers: dict[str, str], body: dict):
            calls.append((url, headers, body))
            self.assertEqual(url, "https://api.linear.test/graphql")
            self.assertEqual(headers["Authorization"], "lin_api_test")
            self.assertIn("issues", body["query"])
            if len(calls) == 1:
                self.assertEqual(body["variables"]["first"], 2)
                self.assertIsNone(body["variables"]["after"])
                return {
                    "data": {
                        "issues": {
                            "pageInfo": {"hasNextPage": True, "endCursor": "cursor-next"},
                            "nodes": [
                                {
                                    "id": "lin-1",
                                    "identifier": "COR-42",
                                    "title": "Ship Linear memory sync",
                                    "description": "We decided Linear issues should cite source links.",
                                    "url": "https://linear.app/doppl/issue/COR-42/ship-linear-memory-sync",
                                    "createdAt": "2026-06-29T10:00:00Z",
                                    "updatedAt": "2026-06-30T10:00:00Z",
                                    "state": {"name": "In Progress", "type": "started"},
                                    "team": {"key": "COR", "name": "Cortex"},
                                    "project": {"name": "Connectors", "url": "https://linear.app/doppl/project/connectors"},
                                    "assignee": {"name": "Sarp", "email": "sdoven@example.com"},
                                    "creator": {"name": "Codex", "email": "codex@example.com"},
                                    "labels": {"nodes": [{"name": "backend"}, {"name": "first-100"}]},
                                    "priorityLabel": "High",
                                }
                            ],
                        }
                    }
                }
            self.assertEqual(body["variables"]["after"], "cursor-next")
            return {
                "data": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "lin-2",
                                "identifier": "COR-43",
                                "title": "Keep citations stable",
                                "url": "",
                                "createdAt": "2026-06-29T11:00:00Z",
                                "updatedAt": "2026-06-30T11:00:00Z",
                                "labels": {"nodes": []},
                            }
                        ],
                    }
                }
            }

        sync = fetch_linear_records(
            token="lin_api_test",
            max_records=2,
            api_url="https://api.linear.test/graphql",
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.high_water_mark, "2026-06-30T11:00:00Z")
        first = sync.records[0].to_source_account_record()
        self.assertEqual(first["external_id"], "linear:issue:lin-1")
        self.assertEqual(first["source_url"], "https://linear.app/doppl/issue/COR-42/ship-linear-memory-sync")
        self.assertIn("Source: Linear", first["content"])
        self.assertIn("Issue: COR-42", first["content"])
        self.assertIn("Description:", first["content"])
        self.assertEqual(first["metadata"]["labels"], ["backend", "first-100"])
        second = sync.records[1].to_source_account_record()
        self.assertEqual(second["source_url"], "linear://issue/COR-43")

    def test_fetch_linear_records_requires_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_linear_records(token="")


if __name__ == "__main__":
    unittest.main()
