from __future__ import annotations

import unittest
from urllib.parse import parse_qs, urlparse

from backend.app.connectors.github import discover_github_repositories, fetch_github_records


class GitHubConnectorTests(unittest.TestCase):
    def test_discover_github_repositories_returns_sync_values(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer ghp_discover")
            parsed = urlparse(url)
            self.assertTrue(parsed.path.endswith("/user/repos"))
            query = parse_qs(parsed.query)
            self.assertEqual(query["affiliation"], ["owner,collaborator,organization_member"])
            self.assertEqual(query["visibility"], ["all"])
            self.assertEqual(query["per_page"], ["2"])
            return [
                {
                    "full_name": "doppl-tech/cortex-app",
                    "name": "cortex-app",
                    "owner": {"login": "doppl-tech"},
                    "html_url": "https://github.com/doppl-tech/cortex-app",
                    "private": True,
                    "archived": False,
                    "fork": False,
                    "updated_at": "2026-07-01T00:00:00Z",
                    "permissions": {"pull": True, "push": False},
                },
                {
                    "full_name": "doppl-tech/cortex-site",
                    "name": "cortex-site",
                    "owner": {"login": "doppl-tech"},
                    "html_url": "https://github.com/doppl-tech/cortex-site",
                    "private": False,
                    "archived": False,
                    "fork": False,
                    "permissions": {"pull": True},
                },
            ]

        discovered = discover_github_repositories(
            token="ghp_discover",
            limit=2,
            api_base_url="https://api.github.test",
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(discovered.repositories_found, 2)
        self.assertEqual(discovered.repositories_returned, 2)
        self.assertEqual(discovered.next_page, 2)
        self.assertEqual(discovered.repositories[0]["label"], "doppl-tech/cortex-app")
        self.assertEqual(discovered.repositories[0]["sync_value"], "doppl-tech/cortex-app")
        self.assertTrue(discovered.repositories[0]["private"])
        self.assertEqual(discovered.repositories[0]["permissions"], {"pull": True, "push": False})

    def test_fetch_github_records_normalizes_issues_and_pull_requests(self) -> None:
        calls: list[tuple[str, dict[str, str]]] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append((url, headers))
            parsed = urlparse(url)
            self.assertEqual(headers["Authorization"], "Bearer ghp_test")
            if parsed.path == "/repos/doppl-tech/cortex-app/pulls/43/reviews":
                query = parse_qs(parsed.query)
                self.assertEqual(query["per_page"], ["10"])
                return [
                    {
                        "id": 9001,
                        "state": "CHANGES_REQUESTED",
                        "user": {"login": "reviewer"},
                        "submitted_at": "2026-06-30T12:45:00Z",
                        "html_url": "https://github.com/doppl-tech/cortex-app/pull/43#pullrequestreview-9001",
                        "body": "Please tighten the source-account retrieval tests.",
                    }
                ]
            if parsed.path == "/repos/doppl-tech/cortex-app/pulls/43/comments":
                query = parse_qs(parsed.query)
                self.assertEqual(query["per_page"], ["10"])
                return [
                    {
                        "id": 9101,
                        "user": {"login": "reviewer"},
                        "path": "backend/app/storage.py",
                        "line": 42,
                        "created_at": "2026-06-30T12:46:00Z",
                        "updated_at": "2026-06-30T12:47:00Z",
                        "html_url": "https://github.com/doppl-tech/cortex-app/pull/43#discussion_r9101",
                        "body": "This PR review comment should be part of Cortex memory.",
                    }
                ]
            self.assertEqual(parsed.path, "/repos/doppl-tech/cortex-app/issues")
            query = parse_qs(parsed.query)
            self.assertEqual(query["state"], ["all"])
            self.assertEqual(query["sort"], ["updated"])
            self.assertEqual(query["direction"], ["desc"])
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

        self.assertEqual(len(calls), 3)
        self.assertEqual(sync.repositories, ["doppl-tech/cortex-app"])
        self.assertEqual(sync.records_found, 2)
        self.assertEqual(sync.records_returned, 2)
        self.assertEqual(sync.reviews_found, 1)
        self.assertEqual(sync.reviews_returned, 1)
        self.assertEqual(sync.review_comments_found, 1)
        self.assertEqual(sync.review_comments_returned, 1)
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
        self.assertEqual(pull["metadata"]["reviews_returned"], 1)
        self.assertEqual(pull["metadata"]["review_comments_returned"], 1)
        self.assertIn("Pull request reviews:", pull["content"])
        self.assertIn("Please tighten the source-account retrieval tests.", pull["content"])
        self.assertIn("Review comment 1 on backend/app/storage.py:42", pull["content"])
        self.assertIn("This PR review comment should be part of Cortex memory.", pull["content"])

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

    def test_fetch_github_records_can_opt_out_of_issue_comments(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            parsed = urlparse(url)
            self.assertEqual(parsed.path, "/repos/doppl-tech/cortex-app/issues")
            return [
                {
                    "number": 42,
                    "title": "Issue comments disabled",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/42",
                    "comments_url": "https://api.github.test/repos/doppl-tech/cortex-app/issues/42/comments",
                    "comments": 2,
                    "created_at": "2026-06-30T10:00:00Z",
                    "updated_at": "2026-06-30T11:00:00Z",
                    "user": {"login": "sarp"},
                    "body": "Issue body only.",
                }
            ]

        sync = fetch_github_records(
            token="ghp_test",
            repositories=["doppl-tech/cortex-app"],
            include_comments=False,
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.comments_found, 0)
        self.assertEqual(sync.comments_returned, 0)
        self.assertNotIn("Comments:", sync.records[0].content)

    def test_fetch_github_records_comment_opt_out_skips_pull_request_reviews(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            parsed = urlparse(url)
            self.assertEqual(parsed.path, "/repos/doppl-tech/cortex-app/issues")
            return [
                {
                    "number": 43,
                    "title": "PR enrichment disabled",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/pull/43",
                    "created_at": "2026-06-30T10:00:00Z",
                    "updated_at": "2026-06-30T11:00:00Z",
                    "user": {"login": "sarp"},
                    "pull_request": {"url": "https://api.github.com/repos/doppl-tech/cortex-app/pulls/43"},
                    "body": "Pull request body only.",
                }
            ]

        sync = fetch_github_records(
            token="ghp_test",
            repositories=["doppl-tech/cortex-app"],
            include_comments=False,
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 1)
        self.assertEqual(sync.reviews_found, 0)
        self.assertEqual(sync.review_comments_found, 0)
        self.assertNotIn("Pull request reviews:", sync.records[0].content)

    def test_fetch_github_records_bounds_issue_comments(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            parsed = urlparse(url)
            if parsed.path == "/repos/doppl-tech/cortex-app/issues":
                return [
                    {
                        "number": 42,
                        "title": "Issue comments bounded",
                        "state": "open",
                        "html_url": "https://github.com/doppl-tech/cortex-app/issues/42",
                        "comments_url": "https://api.github.test/repos/doppl-tech/cortex-app/issues/42/comments?direction=asc",
                        "comments": 3,
                        "created_at": "2026-06-30T10:00:00Z",
                        "updated_at": "2026-06-30T11:00:00Z",
                        "user": {"login": "sarp"},
                        "body": "Issue body.",
                    }
                ]
            self.assertEqual(parsed.path, "/repos/doppl-tech/cortex-app/issues/42/comments")
            query = parse_qs(parsed.query)
            self.assertEqual(query["direction"], ["asc"])
            self.assertEqual(query["per_page"], ["2"])
            return [
                {"id": 1, "user": {"login": "a"}, "created_at": "2026-06-30T11:01:00Z", "body": "First bounded comment."},
                {"id": 2, "user": {"login": "b"}, "created_at": "2026-06-30T11:02:00Z", "body": "Second bounded comment."},
                {"id": 3, "user": {"login": "c"}, "created_at": "2026-06-30T11:03:00Z", "body": "Third comment should be ignored."},
            ]

        sync = fetch_github_records(
            token="ghp_test",
            repositories=["doppl-tech/cortex-app"],
            max_comments_per_item=2,
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.comments_found, 3)
        self.assertEqual(sync.comments_returned, 2)
        self.assertIn("First bounded comment.", sync.records[0].content)
        self.assertIn("Second bounded comment.", sync.records[0].content)
        self.assertNotIn("Third comment should be ignored.", sync.records[0].content)
        self.assertEqual(sync.records[0].metadata["comments_returned"], 2)

    def test_fetch_github_records_requires_token_and_repository(self) -> None:
        with self.assertRaisesRegex(ValueError, "token"):
            fetch_github_records(token="", repositories=["doppl-tech/cortex-app"])
        with self.assertRaisesRegex(ValueError, "repository"):
            fetch_github_records(token="ghp_test", repositories=[])


if __name__ == "__main__":
    unittest.main()
