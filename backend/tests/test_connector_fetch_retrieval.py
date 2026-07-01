from __future__ import annotations

import base64
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore


class ConnectorFetchRetrievalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-connectors.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "connector-retrieval-user"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_baseline_connector_fetch_paths_reach_search_and_ask_citations(self) -> None:
        cases = [
            {
                "source": "github",
                "marker": "ghcitetest",
                "query": "ghcitetest retrieval GitHub citations",
                "search_url_prefix": "https://github.com/doppl-tech/cortex-app/issues/42",
                "ask_url_prefix": "https://github.com/doppl-tech/cortex-app/issues/42",
                "sync": self._sync_github,
            },
            {
                "source": "slack",
                "marker": "slackcitetest",
                "query": "slackcitetest retrieval Slack citations",
                "search_url_prefix": "https://doppl.slack.com/archives/C123ABC/p1782739200000100",
                "ask_url_prefix": "https://doppl.slack.com/archives/C123ABC/p1782739200000100",
                "sync": self._sync_slack,
            },
            {
                "source": "readwise",
                "marker": "readwisecitetest",
                "query": "readwisecitetest retrieval Readwise citations",
                "search_url_prefix": "https://readwise.io/bookreview/111",
                "ask_url_prefix": "https://readwise.io/bookreview/111",
                "sync": self._sync_readwise,
            },
            {
                "source": "raindrop",
                "marker": "raindropcitetest",
                "query": "raindropcitetest retrieval Raindrop citations",
                "search_url_prefix": "https://example.com/raindrop-citation",
                "ask_url_prefix": "https://example.com/raindrop-citation",
                "sync": self._sync_raindrop,
            },
            {
                "source": "calendar",
                "marker": "calendarcitetest",
                "query": "calendarcitetest retrieval Calendar citations",
                "search_url_prefix": "source-account://calendar/",
                "ask_url_prefix": "source-account://calendar/",
                "sync": self._sync_calendar,
            },
            {
                "source": "gmail",
                "marker": "gmailcitetest",
                "query": "gmailcitetest retrieval Gmail citations",
                "search_url_prefix": "https://mail.google.com/mail/u/0/#all/gmail-citation-msg",
                "ask_url_prefix": "https://mail.google.com/mail/u/0/#all/gmail-citation-msg",
                "sync": self._sync_gmail,
            },
            {
                "source": "linear",
                "marker": "linearcitetest",
                "query": "linearcitetest retrieval Linear citations",
                "search_url_prefix": "https://linear.app/doppl/issue/COR-42/ship-linear-memory-sync",
                "ask_url_prefix": "https://linear.app/doppl/issue/COR-42/ship-linear-memory-sync",
                "sync": self._sync_linear,
            },
            {
                "source": "jira",
                "marker": "jiracitetest",
                "query": "jiracitetest retrieval Jira citations",
                "search_url_prefix": "https://doppl.atlassian.net/browse/COR-42",
                "ask_url_prefix": "https://doppl.atlassian.net/browse/COR-42",
                "sync": self._sync_jira,
            },
            {
                "source": "notion",
                "marker": "notioncitetest",
                "query": "notioncitetest retrieval Notion citations",
                "search_url_prefix": "https://www.notion.so/doppl/page-1",
                "ask_url_prefix": "https://www.notion.so/doppl/page-1",
                "sync": self._sync_notion,
            },
            {
                "source": "zotero",
                "marker": "zoterocitetest",
                "query": "zoterocitetest retrieval Zotero citations",
                "search_url_prefix": "zotero://select/library/items/ITEM1234",
                "ask_url_prefix": "zotero://select/library/items/ITEM1234",
                "sync": self._sync_zotero,
            },
            {
                "source": "obsidian",
                "marker": "obsidiancitetest",
                "query": "obsidiancitetest retrieval Obsidian citations",
                "search_url_prefix": "file://",
                "ask_url_prefix": "local-file://Connector%20Retrieval.md",
                "sync": self._sync_obsidian,
            },
        ]

        for case in cases:
            with self.subTest(source=case["source"]):
                result = case["sync"]()

                self.assertEqual(result["source"], case["source"])
                self.assertEqual(result["status"], "complete")
                self.assertEqual(result["failed"], 0)
                self.assertEqual(result["saved"], 1)
                self.assertEqual(result["received"], 1)
                self.assertEqual(len(result["capture_ids"]), 1)
                self.assertTrue(result["records"][0]["source_url"].startswith(case["search_url_prefix"]))
                self.assertEqual(self.store.search(self.user_id, case["query"], limit=5), [])

                capture_id = result["capture_ids"][0]
                self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

                hits = self.store.search(self.user_id, case["query"], limit=5)
                hit = self._first_result_with_marker(hits, case["source"], case["marker"])
                self.assertIsNotNone(hit)
                self.assertTrue(hit["source_url"].startswith(case["search_url_prefix"]))
                self.assertIn("line=", hit["source_url"])
                self.assertIn("excerpt=", hit["source_url"])
                provenance = hit["provenance"]
                self.assertEqual(provenance["source"], case["source"])
                self.assertEqual(provenance["source_account_id"], result["source_account_id"])
                self.assertTrue(provenance["external_id"])
                self.assertEqual(provenance["record_metadata"]["connector"], case["source"])

                answer = self.store.answer_query(self.user_id, case["query"], limit=3)
                citation = self._first_citation_with_marker(answer["citations"], case["source"], case["marker"])
                self.assertIsNotNone(citation)
                self.assertTrue(citation["source_url"].startswith(case["ask_url_prefix"]))
                self.assertIn("line=", citation["source_url"])
                self.assertIn("excerpt=", citation["source_url"])
                self.assertEqual(citation["source_account_id"], result["source_account_id"])
                self.assertEqual(citation["external_id"], provenance["external_id"])
                self.assertEqual(citation["source_record_id"], provenance["external_id"])

    def test_slack_thread_replies_reach_search_and_ask_citations(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb-thread-test")
            if "conversations.history" in url:
                return {
                    "ok": True,
                    "messages": [
                        {
                            "type": "message",
                            "user": "U123",
                            "text": "Thread parent for Cortex Slack retrieval.",
                            "ts": "1782739200.000100",
                            "thread_ts": "1782739200.000100",
                            "reply_count": 1,
                            "latest_reply": "1782739210.000200",
                        }
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            if "conversations.replies" in url:
                return {
                    "ok": True,
                    "messages": [
                        {
                            "type": "message",
                            "user": "U123",
                            "text": "Thread parent for Cortex Slack retrieval.",
                            "ts": "1782739200.000100",
                            "thread_ts": "1782739200.000100",
                        },
                        {
                            "type": "message",
                            "user": "U456",
                            "text": "We decided slackthreadtest retrieval should preserve threaded Slack decisions.",
                            "ts": "1782739210.000200",
                            "thread_ts": "1782739200.000100",
                        },
                    ],
                    "response_metadata": {"next_cursor": ""},
                }
            self.fail(f"unexpected Slack URL {url}")

        result = self.store.sync_slack_account(
            self.user_id,
            token="xoxb-thread-test",
            channels=["C123ABC|general"],
            max_records=3,
            workspace_url="https://doppl.slack.com",
            processing="sync",
            request_json=fake_request,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["received"], 2)
        self.assertEqual(result["saved"], 2)
        self.assertTrue(any("conversations.history" in url for url in calls))
        self.assertTrue(any("conversations.replies" in url for url in calls))
        self.assertEqual(self.store.search(self.user_id, "slackthreadtest threaded Slack decisions", limit=5), [])
        for capture_id in result["capture_ids"]:
            self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        hits = self.store.search(self.user_id, "slackthreadtest threaded Slack decisions", limit=5)
        hit = self._first_result_with_marker(hits, "slack", "slackthreadtest")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["source_url"].startswith("https://doppl.slack.com/archives/C123ABC/p1782739210000200"))
        self.assertEqual(hit["provenance"]["record_metadata"]["thread_ts"], "1782739200.000100")
        self.assertEqual(hit["provenance"]["record_metadata"]["thread_parent_ts"], "1782739200.000100")

        answer = self.store.answer_query(self.user_id, "slackthreadtest threaded Slack decisions", limit=5)
        citation = self._first_citation_with_marker(answer["citations"], "slack", "slackthreadtest")
        self.assertIsNotNone(citation)
        self.assertTrue(citation["source_url"].startswith("https://doppl.slack.com/archives/C123ABC/p1782739210000200"))
        self.assertEqual(citation["source_record_id"], "slack:C123ABC:1782739210.000200")

        context_hits = self.store.search(self.user_id, "Cortex Slack retrieval slackthreadtest", limit=5)
        context_hit = self._first_result_with_marker(context_hits, "slack", "slackthreadtest")
        self.assertIsNotNone(context_hit)
        context_answer = self.store.answer_query(self.user_id, "Cortex Slack retrieval slackthreadtest", limit=5)
        context_citation = self._first_citation_with_marker(context_answer["citations"], "slack", "slackthreadtest")
        self.assertIsNotNone(context_citation)
        self.assertEqual(context_citation["source_record_id"], "slack:C123ABC:1782739210.000200")

    def test_github_issue_comments_reach_search_and_ask_citations(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer ghp_comment_test")
            if "/issues?" in url:
                return [
                    {
                        "number": 42,
                        "title": "Ship GitHub comment memory",
                        "state": "open",
                        "html_url": "https://github.com/doppl-tech/cortex-app/issues/42",
                        "comments_url": "https://api.github.test/repos/doppl-tech/cortex-app/issues/42/comments",
                        "comments": 1,
                        "created_at": "2026-06-30T10:00:00Z",
                        "updated_at": "2026-06-30T11:00:00Z",
                        "user": {"login": "sarp"},
                        "labels": [{"name": "backend"}],
                        "body": "Issue body without the unique comment marker.",
                    }
                ]
            if "/comments" in url:
                self.assertIn("per_page=10", url)
                return [
                    {
                        "id": 1001,
                        "user": {"login": "teammate"},
                        "created_at": "2026-06-30T11:10:00Z",
                        "updated_at": "2026-06-30T11:12:00Z",
                        "html_url": "https://github.com/doppl-tech/cortex-app/issues/42#issuecomment-1001",
                        "body": "We decided githubcommenttest retrieval should preserve GitHub discussion comments.",
                    }
                ]
            self.fail(f"unexpected GitHub URL {url}")

        result = self.store.sync_github_account(
            self.user_id,
            token="ghp_comment_test",
            repositories=["doppl-tech/cortex-app"],
            max_records=1,
            processing="sync",
            api_base_url="https://api.github.test",
            request_json=fake_request,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["received"], 1)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["sync"]["comments_found"], 1)
        self.assertEqual(result["sync"]["comments_returned"], 1)
        self.assertTrue(any("/issues?" in url for url in calls))
        self.assertTrue(any("/comments" in url for url in calls))
        self.assertEqual(self.store.search(self.user_id, "githubcommenttest discussion comments", limit=5), [])

        self.assertTrue(self.store.approve_capture(self.user_id, result["capture_ids"][0]))

        hits = self.store.search(self.user_id, "githubcommenttest discussion comments", limit=5)
        hit = self._first_result_with_marker(hits, "github", "githubcommenttest")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["source_url"].startswith("https://github.com/doppl-tech/cortex-app/issues/42"))
        self.assertEqual(hit["provenance"]["external_id"], "github:doppl-tech/cortex-app:issue:42")
        self.assertEqual(hit["provenance"]["record_metadata"]["comments_returned"], 1)

        answer = self.store.answer_query(self.user_id, "githubcommenttest discussion comments", limit=5)
        citation = self._first_citation_with_marker(answer["citations"], "github", "githubcommenttest")
        self.assertIsNotNone(citation)
        self.assertTrue(citation["source_url"].startswith("https://github.com/doppl-tech/cortex-app/issues/42"))
        self.assertEqual(citation["source_record_id"], "github:doppl-tech/cortex-app:issue:42")

    def test_github_pr_reviews_reach_search_and_ask_citations(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer ghp_review_test")
            if "/issues?" in url:
                return [
                    {
                        "number": 88,
                        "title": "Ship PR review memory",
                        "state": "open",
                        "html_url": "https://github.com/doppl-tech/cortex-app/pull/88",
                        "created_at": "2026-06-30T10:00:00Z",
                        "updated_at": "2026-06-30T11:00:00Z",
                        "user": {"login": "sarp"},
                        "labels": [{"name": "backend"}],
                        "pull_request": {"url": "https://api.github.test/repos/doppl-tech/cortex-app/pulls/88"},
                        "body": "Pull request body without the unique review marker.",
                    }
                ]
            if "/pulls/88/reviews" in url:
                self.assertIn("per_page=10", url)
                return [
                    {
                        "id": 9001,
                        "state": "CHANGES_REQUESTED",
                        "user": {"login": "reviewer"},
                        "submitted_at": "2026-06-30T11:15:00Z",
                        "html_url": "https://github.com/doppl-tech/cortex-app/pull/88#pullrequestreview-9001",
                        "body": "We decided githubreviewtest retrieval should preserve requested changes from PR reviews.",
                    }
                ]
            if "/pulls/88/comments" in url:
                self.assertIn("per_page=10", url)
                return [
                    {
                        "id": 9101,
                        "user": {"login": "reviewer"},
                        "path": "backend/app/storage.py",
                        "line": 42,
                        "created_at": "2026-06-30T11:16:00Z",
                        "updated_at": "2026-06-30T11:17:00Z",
                        "html_url": "https://github.com/doppl-tech/cortex-app/pull/88#discussion_r9101",
                        "body": "The githubreviewtest line comment should also be searchable with citations.",
                    }
                ]
            self.fail(f"unexpected GitHub URL {url}")

        result = self.store.sync_github_account(
            self.user_id,
            token="ghp_review_test",
            repositories=["doppl-tech/cortex-app"],
            max_records=1,
            processing="sync",
            api_base_url="https://api.github.test",
            request_json=fake_request,
        )

        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["received"], 1)
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["sync"]["reviews_found"], 1)
        self.assertEqual(result["sync"]["reviews_returned"], 1)
        self.assertEqual(result["sync"]["review_comments_found"], 1)
        self.assertEqual(result["sync"]["review_comments_returned"], 1)
        self.assertTrue(any("/pulls/88/reviews" in url for url in calls))
        self.assertTrue(any("/pulls/88/comments" in url for url in calls))
        self.assertEqual(self.store.search(self.user_id, "githubreviewtest requested changes", limit=5), [])

        self.assertTrue(self.store.approve_capture(self.user_id, result["capture_ids"][0]))

        hits = self.store.search(self.user_id, "githubreviewtest requested changes", limit=5)
        hit = self._first_result_with_marker(hits, "github", "githubreviewtest")
        self.assertIsNotNone(hit)
        self.assertTrue(hit["source_url"].startswith("https://github.com/doppl-tech/cortex-app/pull/88"))
        self.assertEqual(hit["provenance"]["external_id"], "github:doppl-tech/cortex-app:pull_request:88")
        self.assertEqual(hit["provenance"]["record_metadata"]["reviews_returned"], 1)
        self.assertEqual(hit["provenance"]["record_metadata"]["review_comments_returned"], 1)

        answer = self.store.answer_query(self.user_id, "githubreviewtest requested changes", limit=5)
        citation = self._first_citation_with_marker(answer["citations"], "github", "githubreviewtest")
        self.assertIsNotNone(citation)
        self.assertTrue(citation["source_url"].startswith("https://github.com/doppl-tech/cortex-app/pull/88"))
        self.assertEqual(citation["source_record_id"], "github:doppl-tech/cortex-app:pull_request:88")

    def test_search_and_ask_can_scope_to_source_account_and_connector_facets(self) -> None:
        def fake_github_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer ghp_scope_test")
            self.assertIn("/repos/doppl-tech/cortex-app/issues", url)
            return [
                {
                    "number": 77,
                    "title": "Scoped GitHub retrieval",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/77",
                    "created_at": "2026-06-30T10:00:00Z",
                    "updated_at": "2026-06-30T11:00:00Z",
                    "user": {"login": "sarp"},
                    "labels": [{"name": "backend"}],
                    "body": "We decided scopefiltertest retrieval should isolate GitHub repository context.",
                }
            ]

        def fake_slack_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer xoxb-scope-test")
            self.assertIn("conversations.history", url)
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "We decided scopefiltertest retrieval should isolate Slack channel context.",
                        "ts": "1782739300.000100",
                    }
                ],
                "response_metadata": {"next_cursor": ""},
            }

        github = self.store.sync_github_account(
            self.user_id,
            token="ghp_scope_test",
            repositories=["doppl-tech/cortex-app"],
            max_records=1,
            processing="sync",
            request_json=fake_github_request,
        )
        slack = self.store.sync_slack_account(
            self.user_id,
            token="xoxb-scope-test",
            channels=["C123ABC|general"],
            max_records=1,
            workspace_url="https://doppl.slack.com",
            processing="sync",
            request_json=fake_slack_request,
        )
        self.assertTrue(self.store.approve_capture(self.user_id, github["capture_ids"][0]))
        self.assertTrue(self.store.approve_capture(self.user_id, slack["capture_ids"][0]))

        all_hits = self.store.search(self.user_id, "scopefiltertest retrieval isolate", limit=10)
        self.assertEqual({hit["source"] for hit in all_hits}, {"github", "slack"})

        github_hits = self.store.search(
            self.user_id,
            "scopefiltertest retrieval isolate",
            limit=10,
            source="github",
            metadata_filters={"repository": "doppl-tech/cortex-app", "state": "open"},
        )
        self.assertEqual([hit["source"] for hit in github_hits], ["github"])
        self.assertEqual(github_hits[0]["provenance"]["record_metadata"]["repository"], "doppl-tech/cortex-app")

        slack_hits = self.store.search(
            self.user_id,
            "scopefiltertest retrieval isolate",
            limit=10,
            source_account_id=slack["source_account_id"],
            metadata_filters={"channel": "general"},
        )
        self.assertEqual([hit["source"] for hit in slack_hits], ["slack"])
        self.assertEqual(slack_hits[0]["provenance"]["record_metadata"]["channel"], "general")

        self.assertEqual(
            self.store.search(
                self.user_id,
                "scopefiltertest retrieval isolate",
                limit=10,
                source="slack",
                metadata_filters={"channel": "random"},
            ),
            [],
        )

        answer = self.store.answer_query(
            self.user_id,
            "scopefiltertest retrieval isolate",
            limit=5,
            source="github",
            metadata_filters={"repository": "doppl-tech/cortex-app"},
        )
        self.assertEqual(answer["filters"]["source"], "github")
        self.assertEqual(answer["filters"]["metadata"]["repository"], "doppl-tech/cortex-app")
        self.assertEqual([citation["source"] for citation in answer["citations"]], ["github"])

    def test_slack_sync_uses_channel_page_cursor(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb-cursor-test")
            if "auth.test" in url:
                return {"ok": True, "user_id": "U123", "user": "sarpt"}
            self.assertIn("conversations.history", url)
            return {"ok": True, "messages": [], "response_metadata": {"next_cursor": ""}}

        result = self.store.sync_slack_account(
            self.user_id,
            token="xoxb-cursor-test",
            channels=["C123ABC|general"],
            page_cursors={"C123ABC": "cursor-one"},
            max_records=3,
            processing="sync",
            request_json=fake_request,
        )

        self.assertEqual(result["status"], "empty")
        self.assertTrue(calls)
        history_call = next(url for url in calls if "conversations.history" in url)
        self.assertIn("cursor=cursor-one", history_call)

    def _first_result_with_marker(self, hits: list[dict], source: str, marker: str) -> dict | None:
        for hit in hits:
            if hit["source"] == source and marker in hit["content"].lower():
                return hit
        return None

    def _first_citation_with_marker(self, citations: list[dict], source: str, marker: str) -> dict | None:
        for citation in citations:
            if citation["source"] == source and marker in citation["excerpt"].lower():
                return citation
        return None

    def _sync_github(self) -> dict:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer ghp_test")
            self.assertIn("/repos/doppl-tech/cortex-app/issues", url)
            return [
                {
                    "number": 42,
                    "title": "Ship reliable GitHub memory sync",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/42",
                    "created_at": "2026-06-30T10:00:00Z",
                    "updated_at": "2026-06-30T11:00:00Z",
                    "user": {"login": "sarp"},
                    "labels": [{"name": "backend"}],
                    "body": "We decided ghcitetest retrieval should preserve GitHub issue citations.",
                }
            ]

        return self.store.sync_github_account(
            self.user_id,
            token="ghp_test",
            repositories=["doppl-tech/cortex-app"],
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_slack(self) -> dict:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer xoxb-test")
            self.assertIn("conversations.history", url)
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "We decided slackcitetest retrieval should preserve Slack message citations.",
                        "ts": "1782739200.000100",
                    }
                ],
                "response_metadata": {"next_cursor": ""},
            }

        return self.store.sync_slack_account(
            self.user_id,
            token="xoxb-test",
            channels=["C123ABC|general"],
            max_records=1,
            workspace_url="https://doppl.slack.com",
            processing="sync",
            request_json=fake_request,
        )

    def _sync_readwise(self) -> dict:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Token readwise-test")
            self.assertIn("/export/", url)
            return {
                "nextPageCursor": None,
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
                                "text": "We decided readwisecitetest retrieval should preserve Readwise highlight citations.",
                                "highlighted_at": "2026-06-29T10:00:00Z",
                                "updated": "2026-06-30T10:30:00Z",
                                "tags": [{"name": "retrieval"}],
                            }
                        ],
                    }
                ],
            }

        return self.store.sync_readwise_account(
            self.user_id,
            token="readwise-test",
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_raindrop(self) -> dict:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer raindrop_test")
            self.assertIn("/raindrops/0", url)
            return {
                "result": True,
                "count": 1,
                "items": [
                    {
                        "_id": 123,
                        "title": "Local-first memory systems",
                        "link": "https://example.com/raindrop-citation",
                        "domain": "example.com",
                        "excerpt": "We decided raindropcitetest retrieval should preserve Raindrop bookmark citations.",
                        "tags": ["retrieval"],
                        "created": "2026-06-29T10:00:00Z",
                        "lastUpdate": "2026-06-30T10:30:00Z",
                    }
                ],
            }

        return self.store.sync_raindrop_account(
            self.user_id,
            token="raindrop_test",
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_calendar(self) -> dict:
        ics_text = """BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:event-1@example.com
DTSTAMP:20260630T100000Z
DTSTART:20260701T160000Z
DTEND:20260701T170000Z
SUMMARY:Cortex retrieval review
DESCRIPTION:We decided calendarcitetest retrieval should preserve Calendar event citations.
LOCATION:Doppl HQ
END:VEVENT
END:VCALENDAR
"""

        def fake_request(url: str) -> str:
            self.assertEqual(url, "https://calendar.example.com/private.ics")
            return ics_text

        return self.store.sync_calendar_account(
            self.user_id,
            feed_url="https://calendar.example.com/private.ics",
            max_records=1,
            processing="sync",
            request_text=fake_request,
        )

    def _sync_gmail(self) -> dict:
        def gmail_data(value: str) -> str:
            return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")

        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer gmail-test")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            if "/users/me/messages?" in url:
                return {"messages": [{"id": "gmail-citation-msg"}]}
            self.assertTrue(url.endswith("/users/me/messages/gmail-citation-msg?format=full"))
            return {
                "id": "gmail-citation-msg",
                "threadId": "thread-gmail-citation-msg",
                "labelIds": ["INBOX"],
                "internalDate": "1782739200000",
                "payload": {
                    "mimeType": "text/plain",
                    "headers": [
                        {"name": "Subject", "value": "Gmail retrieval citation"},
                        {"name": "From", "value": "Sarp Doven <sarp@example.com>"},
                        {"name": "To", "value": "sarp@example.com"},
                        {"name": "Date", "value": "Mon, 29 Jun 2026 10:00:00 -0700"},
                    ],
                    "body": {
                        "data": gmail_data("We decided gmailcitetest retrieval should preserve Gmail message citations.")
                    },
                },
            }

        return self.store.sync_gmail_account(
            self.user_id,
            access_token="gmail-test",
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_linear(self) -> dict:
        def fake_request(url: str, headers: dict[str, str], body: dict):
            self.assertEqual(headers["Authorization"], "lin_api_test")
            return {
                "data": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "lin-1",
                                "identifier": "COR-42",
                                "title": "Ship Linear memory sync",
                                "description": "We decided linearcitetest retrieval should preserve Linear issue citations.",
                                "url": "https://linear.app/doppl/issue/COR-42/ship-linear-memory-sync",
                                "createdAt": "2026-06-29T10:00:00Z",
                                "updatedAt": "2026-06-30T10:00:00Z",
                                "state": {"name": "In Progress", "type": "started"},
                                "team": {"key": "COR", "name": "Cortex"},
                                "labels": {"nodes": [{"name": "retrieval"}]},
                            }
                        ],
                    }
                }
            }

        return self.store.sync_linear_account(
            self.user_id,
            token="lin_api_test",
            max_records=1,
            api_url="https://api.linear.test/graphql",
            processing="sync",
            request_json=fake_request,
        )

    def _sync_jira(self) -> dict:
        def fake_request(url: str, headers: dict[str, str], body: dict):
            self.assertIn("/rest/api/3/search/jql", url)
            self.assertTrue(headers["Authorization"].startswith("Basic "))
            return {
                "isLast": True,
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
                                                "text": "We decided jiracitetest retrieval should preserve Jira issue citations.",
                                            }
                                        ],
                                    }
                                ],
                            },
                            "project": {"key": "COR", "name": "Cortex"},
                            "issuetype": {"name": "Task"},
                            "status": {"name": "In Progress"},
                            "labels": ["retrieval"],
                            "created": "2026-06-29T10:00:00.000+0000",
                            "updated": "2026-06-30T10:00:00.000+0000",
                        },
                    }
                ],
            }

        return self.store.sync_jira_account(
            self.user_id,
            email="sarp@example.com",
            api_token="jira_api_test",
            site_url="https://doppl.atlassian.net",
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_notion(self) -> dict:
        def fake_request(url: str, headers: dict[str, str], body: dict | None, method: str):
            self.assertEqual(headers["Authorization"], "Bearer notion-test")
            if method == "POST":
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
                            },
                        }
                    ],
                }
            if "/blocks/page-1/children" in url:
                return {
                    "has_more": False,
                    "next_cursor": None,
                    "results": [
                        {
                            "id": "toggle-1",
                            "type": "toggle",
                            "has_children": True,
                            "toggle": {"rich_text": [{"plain_text": "Nested Cortex memory"}]},
                        }
                    ],
                }
            return {
                "has_more": False,
                "next_cursor": None,
                "results": [
                    {
                        "id": "paragraph-1",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "plain_text": "We decided notioncitetest retrieval should preserve nested Notion page citations.",
                                }
                            ]
                        },
                    }
                ],
            }

        return self.store.sync_notion_account(
            self.user_id,
            token="notion-test",
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_zotero(self) -> dict:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Zotero-API-Version"], "3")
            return [
                {
                    "key": "ITEM1234",
                    "version": 87,
                    "data": {
                        "key": "ITEM1234",
                        "itemType": "journalArticle",
                        "title": "Local-first memory systems",
                        "creators": [{"creatorType": "author", "firstName": "Ada", "lastName": "Lovelace"}],
                        "abstractNote": "We decided zoterocitetest retrieval should preserve Zotero item citations.",
                        "dateAdded": "2026-06-29T10:00:00Z",
                        "dateModified": "2026-06-30T10:30:00Z",
                        "tags": [{"tag": "retrieval"}],
                    },
                }
            ]

        return self.store.sync_zotero_account(
            self.user_id,
            max_records=1,
            processing="sync",
            request_json=fake_request,
        )

    def _sync_obsidian(self) -> dict:
        vault = Path(self.tmp.name) / "vault"
        note = vault / "Connector Retrieval.md"
        note.parent.mkdir(parents=True, exist_ok=True)
        note.write_text(
            "# Connector Retrieval\n\n"
            "Decision: We decided obsidiancitetest retrieval should preserve Obsidian file citations.\n",
            encoding="utf-8",
        )

        return self.store.sync_obsidian_vault(
            self.user_id,
            vault_path=str(vault),
            max_records=10,
            processing="sync",
        )


if __name__ == "__main__":
    unittest.main()
