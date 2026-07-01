from __future__ import annotations

import base64
import unittest

from backend.app.connectors._redaction import REDACTED_CONNECTOR_SECRET, redact_error_message
from backend.app.connectors.calendar import fetch_calendar_records
from backend.app.connectors.github import fetch_github_records
from backend.app.connectors.jira import fetch_jira_records
from backend.app.connectors.linear import fetch_linear_records
from backend.app.connectors.notion import fetch_notion_records
from backend.app.connectors.raindrop import fetch_raindrop_records
from backend.app.connectors.readwise import fetch_readwise_records
from backend.app.connectors.slack import fetch_slack_records
from backend.app.connectors.zotero import fetch_zotero_records


class ConnectorRedactionTests(unittest.TestCase):
    def test_redact_error_message_removes_known_secrets_and_auth_values(self) -> None:
        message = redact_error_message(
            "failed Authorization: Bearer ghp_secret_123 api_token=rw_secret_456 raw ghp_secret_123",
            ["ghp_secret_123"],
        )

        self.assertNotIn("ghp_secret_123", message)
        self.assertNotIn("rw_secret_456", message)
        self.assertIn(REDACTED_CONNECTOR_SECRET, message)

    def test_request_exceptions_redact_tokens_across_token_connectors(self) -> None:
        cases = [
            (
                "github",
                "ghp_super_secret_123",
                lambda token, request: fetch_github_records(
                    token=token,
                    repositories=["doppl-tech/cortex"],
                    max_records=1,
                    request_json=request,
                ),
            ),
            (
                "slack",
                "xoxb-super-secret-456",
                lambda token, request: fetch_slack_records(
                    token=token,
                    channels=["C123ABC"],
                    max_records=1,
                    request_json=request,
                ),
            ),
            (
                "readwise",
                "readwise-secret-789",
                lambda token, request: fetch_readwise_records(
                    token=token,
                    max_records=1,
                    request_json=request,
                ),
            ),
            (
                "raindrop",
                "raindrop-secret-abc",
                lambda token, request: fetch_raindrop_records(
                    token=token,
                    max_records=1,
                    request_json=request,
                ),
            ),
            (
                "linear",
                "linear-secret-def",
                lambda token, request: fetch_linear_records(
                    token=token,
                    max_records=1,
                    request_json=request,
                ),
            ),
            (
                "notion",
                "notion-secret-ghi",
                lambda token, request: fetch_notion_records(
                    token=token,
                    max_records=1,
                    include_content=False,
                    request_json=request,
                ),
            ),
            (
                "zotero",
                "zotero-secret-jkl",
                lambda token, request: fetch_zotero_records(
                    token=token,
                    max_records=1,
                    request_json=request,
                ),
            ),
            (
                "jira",
                "jira-secret-mno",
                lambda token, request: fetch_jira_records(
                    email="operator@example.com",
                    api_token=token,
                    site_url="https://doppl.atlassian.net",
                    max_records=1,
                    request_json=request,
                ),
            ),
        ]

        for name, token, call_fetch in cases:
            with self.subTest(connector=name):
                def failing_request(*args):
                    headers = args[1] if len(args) > 1 and isinstance(args[1], dict) else {}
                    raise RuntimeError(f"request failed token={token} headers={headers}")

                sync = call_fetch(token, failing_request)

                self.assertEqual(sync.records_returned, 0)
                self.assertTrue(sync.errors)
                error = sync.errors[0]["error"]
                self.assertNotIn(token, error)
                self.assertIn(REDACTED_CONNECTOR_SECRET, error)
                if name == "jira":
                    basic_secret = base64.b64encode(f"operator@example.com:{token}".encode("utf-8")).decode("ascii")
                    self.assertNotIn(basic_secret, error)

    def test_linear_graphql_error_messages_are_redacted(self) -> None:
        token = "linear-payload-secret"

        def fake_request(*_args):
            return {"errors": [{"message": f"Rejected Authorization: {token}"}]}

        sync = fetch_linear_records(token=token, max_records=1, request_json=fake_request)

        self.assertTrue(sync.errors)
        self.assertNotIn(token, sync.errors[0]["error"])
        self.assertIn(REDACTED_CONNECTOR_SECRET, sync.errors[0]["error"])

    def test_calendar_feed_errors_redact_original_and_normalized_feed_tokens(self) -> None:
        token = "calendar-feed-secret"

        def fake_request(url: str) -> str:
            raise RuntimeError(f"failed reading {url} token={token}")

        sync = fetch_calendar_records(
            feed_url=f"webcal://calendar.example.com/private.ics?token={token}",
            request_text=fake_request,
        )

        self.assertTrue(sync.errors)
        error = sync.errors[0]["error"]
        self.assertNotIn(token, error)
        self.assertNotIn("calendar.example.com/private.ics", error)
        self.assertIn(REDACTED_CONNECTOR_SECRET, error)


if __name__ == "__main__":
    unittest.main()
