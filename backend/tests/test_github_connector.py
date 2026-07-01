from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.github import fetch_github_records


class GitHubConnectorTests(unittest.TestCase):
    def test_fetch_github_records_normalizes_issues_and_pull_requests(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            self.assertEqual(parsed.path, "/repos/doppl-tech/cortex-app/issues")
            query = parse_qs(parsed.query)
            self.assertEqual(query["state"], ["all"])
            self.assertEqual(query["sort"], ["updated"])
            self.assertEqual(query["direction"], ["desc"])
            self.assertEqual(headers["Authorization"], "Bearer ghp_test")
            return [
                {
                    "number": 42,
                    "title": "Ship reliable GitHub memory sync",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/42",
                    "created_at": "2026-06-30T10:00:00Z",
                    "updated_at": "2026-06-30T11:00:00Z",
                    "user": {"login": "sarp"},
                    "labels": [{"name": "first-100"}, {"name": "backend"}],
                    "assignees": [{"login": "codex"}],
                    "body": "We decided Cortex should cite GitHub issue links in Ask.",
                },
                {
                    "number": 43,
                    "title": "Add pull request ingestion",
                    "state": "closed",
                    "html_url": "https://github.com/doppl-tech/cortex-app/pull/43",
                    "created_at": "2026-06-30T12:00:00Z",
                    "updated_at": "2026-06-30T12:30:00Z",
                    "closed_at": "2026-06-30T13:00:00Z",
                    "user": {"login": "teammate"},
                    "labels": [],
                    "pull_request": {"url": "https://api.github.com/repos/doppl-tech/cortex-app/pulls/43"},
                    "body": "This PR keeps connector records stable by external ID.",
                },
            ]

        sync = fetch_github_records(
            token="ghp_test",
            repositories=["https://github.com/doppl-tech/cortex-app"],
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.repositories, ["doppl-tech/cortex-app"])
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.high_water_mark, "2026-06-30T12:30:00Z")
        issue = sync.records[0].to_source_account_record()
        self.assertEqual(issue["external_id"], "github:doppl-tech/cortex-app:issue:42")
        self.assertEqual(issue["source_url"], "https://github.com/doppl-tech/cortex-app/issues/42")
        self.assertIn("Repository: doppl-tech/cortex-app", issue["content"])
        self.assertIn("Type: Issue", issue["content"])
        self.assertIn("We decided Cortex should cite GitHub issue links in Ask.", issue["content"])
        self.assertEqual(issue["metadata"]["record_scope"], "issue")
        self.assertEqual(issue["metadata"]["labels"], ["first-100", "backend"])
        pull = sync.records[1].to_source_account_record()
        self.assertEqual(pull["external_id"], "github:doppl-tech/cortex-app:pull_request:43")
        self.assertEqual(pull["metadata"]["record_scope"], "pull_request")

    def test_fetch_github_records_does_not_advance_cursor_on_partial_error(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            parsed = urlparse(url)
            if parsed.path == "/repos/doppl-tech/cortex-app/issues":
                return [
                    {
                        "number": 42,
                        "title": "Returned before another repository failed",
                        "state": "open",
                        "created_at": "2026-06-30T10:00:00Z",
                        "updated_at": "2026-06-30T11:00:00Z",
                        "user": {"login": "sarp"},
                    }
                ]
            self.assertEqual(parsed.path, "/repos/doppl-tech/cortex-ios/issues")
            raise RuntimeError("GitHub API unavailable")

        sync = fetch_github_records(
            token="ghp_test",
            repositories=["doppl-tech/cortex-app", "doppl-tech/cortex-ios"],
            since="2026-06-01T00:00:00Z",
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.high_water_mark, "2026-06-30T11:00:00Z")
        self.assertEqual(sync.cursor_value, "2026-06-01T00:00:00Z")
        self.assertEqual(len(sync.errors), 1)
        self.assertEqual(sync.errors[0]["repository"], "doppl-tech/cortex-ios")

    def test_fetch_github_records_requires_token_and_repository(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_github_records(token="", repositories=["doppl-tech/cortex-app"])
        with self.assertRaisesRegex(ValueError, "repository"):
            fetch_github_records(token="ghp_test", repositories=[])


if __name__ == "__main__":
    unittest.main()
