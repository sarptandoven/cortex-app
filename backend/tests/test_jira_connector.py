from __future__ import annotations

import base64
import unittest

from backend.app.connectors.jira import fetch_jira_records


class JiraConnectorTests(unittest.TestCase):
    def test_fetch_jira_records_normalizes_issues_with_pagination(self) -> None:
        calls: list[tuple[str, dict[str, str], dict]] = []
        expected_auth = base64.b64encode(b"sarp@example.com:jira_api_test").decode("ascii")

        def fake_request(url: str, headers: dict[str, str], body: dict):
            calls.append((url, headers, body))
            self.assertEqual(url, "https://doppl.atlassian.net/rest/api/3/search/jql")
            self.assertEqual(headers["Authorization"], f"Basic {expected_auth}")
            self.assertEqual(headers["Content-Type"], "application/json")
            self.assertEqual(body["jql"], "project = COR ORDER BY updated DESC")
            self.assertIn("summary", body["fields"])
            if len(calls) == 1:
                self.assertEqual(body["maxResults"], 2)
                self.assertNotIn("nextPageToken", body)
                return {
                    "isLast": False,
                    "nextPageToken": "token-next",
                    "issues": [
                        {
                            "id": "10001",
                            "key": "COR-42",
                            "fields": {
                                "summary": "Ship Jira memory sync",
                                "description": {
                                    "type": "doc",
                                    "version": 1,
                                    "content": [
                                        {
                                            "type": "paragraph",
                                            "content": [
                                                {
                                                    "type": "text",
                                                    "text": "We decided Jira issues should cite source links.",
                                                }
                                            ],
                                        }
                                    ],
                                },
                                "project": {"key": "COR", "name": "Cortex"},
                                "issuetype": {"name": "Task"},
                                "status": {"name": "In Progress"},
                                "assignee": {"displayName": "Sarp"},
                                "reporter": {"displayName": "Codex"},
                                "priority": {"name": "High"},
                                "labels": ["backend", "first-100"],
                                "components": [{"name": "Connectors"}],
                                "created": "2026-06-29T10:00:00.000+0000",
                                "updated": "2026-06-30T10:00:00.000+0000",
                            },
                        }
                    ],
                }
            self.assertEqual(body["maxResults"], 1)
            self.assertEqual(body["nextPageToken"], "token-next")
            return {
                "isLast": True,
                "issues": [
                    {
                        "id": "10002",
                        "key": "COR-43",
                        "fields": {
                            "summary": "Keep Jira citations stable",
                            "description": "Preserve Jira browse URLs.",
                            "created": "2026-06-29T11:00:00.000+0000",
                            "updated": "2026-06-30T11:00:00.000+0000",
                        },
                    }
                ],
            }

        sync = fetch_jira_records(
            email="sarp@example.com",
            api_token="jira_api_test",
            site_url="https://doppl.atlassian.net",
            jql="project = COR ORDER BY updated DESC",
            max_records=2,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.high_water_mark, "2026-06-30T11:00:00Z")
        self.assertNotIn("jira_api_test", str(sync.to_summary()))
        first = sync.records[0].to_source_account_record()
        self.assertEqual(first["external_id"], "jira:issue:10001")
        self.assertEqual(first["source_url"], "https://doppl.atlassian.net/browse/COR-42")
        self.assertIn("Source: Jira", first["content"])
        self.assertIn("Issue: COR-42", first["content"])
        self.assertIn("Description:", first["content"])
        self.assertIn("Jira issues should cite source links", first["content"])
        self.assertEqual(first["metadata"]["labels"], ["backend", "first-100"])
        self.assertEqual(first["metadata"]["components"], ["Connectors"])
        second = sync.records[1].to_source_account_record()
        self.assertEqual(second["source_url"], "https://doppl.atlassian.net/browse/COR-43")

    def test_fetch_jira_records_requires_credentials(self) -> None:
        with self.assertRaisesRegex(ValueError, "email"):
            fetch_jira_records(email="", api_token="jira_api_test", site_url="https://doppl.atlassian.net")
        with self.assertRaisesRegex(ValueError, "API token"):
            fetch_jira_records(email="sarp@example.com", api_token="", site_url="https://doppl.atlassian.net")
        with self.assertRaisesRegex(ValueError, "site_url"):
            fetch_jira_records(email="sarp@example.com", api_token="jira_api_test", site_url="")


if __name__ == "__main__":
    unittest.main()
