from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.connectors.gmail import fetch_gmail_records
from backend.app.database import init_db
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore, connect


def _gmail_data(value: str) -> str:
    return base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii").rstrip("=")


def _gmail_message(
    message_id: str,
    *,
    sender: str,
    subject: str,
    body: str,
    internal_date: str = "1782739200000",
) -> dict:
    return {
        "id": message_id,
        "threadId": f"thread-{message_id}",
        "labelIds": ["INBOX"],
        "internalDate": internal_date,
        "snippet": body[:80],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "Subject", "value": subject},
                {"name": "From", "value": sender},
                {"name": "To", "value": "Sarp Doven <sarp@example.com>"},
                {"name": "Date", "value": "Mon, 29 Jun 2026 10:00:00 -0700"},
            ],
            "parts": [
                {
                    "mimeType": "text/plain",
                    "body": {"data": _gmail_data(body)},
                }
            ],
        },
    }


class GmailConnectorTests(unittest.TestCase):
    def test_fetch_gmail_records_normalizes_messages_and_body(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer gmail-test")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            if "/users/me/messages?" in url:
                self.assertIn("q=from%3Ame+after%3A2026%2F06%2F28", url)
                self.assertIn("labelIds=INBOX", url)
                return {"messages": [{"id": "msg-1"}], "nextPageToken": "page-2", "resultSizeEstimate": 2}
            self.assertTrue(url.endswith("/users/me/messages/msg-1?format=full"))
            return _gmail_message(
                "msg-1",
                sender="Sarp Doven <sarp@example.com>",
                subject="Gmail memory loop",
                body="We decided Gmail sync should preserve cited message bodies.",
            )

        sync = fetch_gmail_records(
            access_token="gmail-test",
            query="from:me",
            label_ids=["INBOX"],
            since="2026-06-28T10:00:00Z",
            max_records=1,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 3)
        self.assertEqual(sync.user_email, "sarp@example.com")
        self.assertEqual(sync.records_found, 1)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.next_page_token, "page-2")
        record = sync.records[0].to_source_account_record()
        self.assertEqual(record["external_id"], "gmail:message:msg-1")
        self.assertEqual(record["source_url"], "https://mail.google.com/mail/u/0/#all/msg-1")
        self.assertIn("Source: Email", record["content"])
        self.assertIn("Subject: Gmail memory loop", record["content"])
        self.assertIn("Body:", record["content"])
        self.assertIn("preserve cited message bodies", record["content"])
        self.assertEqual(record["metadata"]["author_role"], "user")
        self.assertEqual(record["metadata"]["user_email"], "sarp@example.com")

    def test_fetch_gmail_records_requires_access_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "access token"):
            fetch_gmail_records(access_token="")

    def test_fetch_gmail_records_redacts_access_token_from_errors(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            raise RuntimeError("Authorization Bearer gmail-secret-token failed")

        sync = fetch_gmail_records(access_token="gmail-secret-token", request_json=fake_request)

        self.assertEqual(sync.records_returned, 0)
        self.assertEqual(len(sync.errors), 1)
        self.assertNotIn("gmail-secret-token", json.dumps(sync.errors))
        self.assertIn("[REDACTED_CONNECTOR_SECRET]", sync.errors[0]["error"])


class GmailStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.root / "vault")
        self.user_id = "gmail-storage-user"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_gmail_account_sync_persists_citations_and_filters_external_preferences(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer gmail-sync-token")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            if "/users/me/messages?" in url:
                return {"messages": [{"id": "external-pref"}, {"id": "user-pref"}]}
            if url.endswith("/users/me/messages/external-pref?format=full"):
                return _gmail_message(
                    "external-pref",
                    sender="Teammate <teammate@example.com>",
                    subject="External preference",
                    body="I prefer external-only Gmail preferences to be ignored by Cortex.",
                    internal_date="1782739201000",
                )
            if url.endswith("/users/me/messages/user-pref?format=full"):
                return _gmail_message(
                    "user-pref",
                    sender="Sarp Doven <sarp@example.com>",
                    subject="User preference",
                    body="I prefer Gmail retrieval to keep concise source-backed memory.",
                    internal_date="1782739202000",
                )
            raise AssertionError(f"Unexpected URL {url}")

        result = self.store.sync_gmail_account(
            self.user_id,
            access_token="gmail-sync-token",
            processing="sync",
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "gmail")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 2)
        self.assertEqual(result["source_account"]["source"], "gmail")
        self.assertEqual(result["source_account"]["connection_type"], "oauth-token")
        self.assertEqual(result["source_account"]["account_identifier"], "sarp@example.com")
        self.assertEqual(result["source_account"]["metadata"]["user_email"], "sarp@example.com")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertNotIn("gmail-sync-token", json.dumps(result))
        self.assertTrue(result["records"][0]["source_url"].startswith("https://mail.google.com/mail/u/0/#all/"))

        for capture_id in result["capture_ids"]:
            self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "concise source-backed memory", limit=5)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "gmail")
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["message_id"], "user-pref")

        with connect(self.db_path) as conn:
            external_preference_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM memories
                WHERE user_id = ?
                  AND kind = 'preference'
                  AND status = 'active'
                  AND content LIKE '%external-only Gmail preferences%'
                """,
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(external_preference_count, 0)

    def test_mcp_gmail_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer gmail-mcp-token")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            if "/users/me/messages?" in url:
                return {"messages": [{"id": "mcp-msg"}]}
            return _gmail_message(
                "mcp-msg",
                sender="Sarp Doven <sarp@example.com>",
                subject="MCP Gmail sync",
                body="We decided MCP Gmail sync should write source-account records.",
            )

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_gmail",
                {"access_token": "gmail-mcp-token"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.gmail._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_gmail",
                {"access_token": "gmail-mcp-token", "processing": "sync", "max_records": 10},
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "gmail")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("gmail-mcp-token", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "https://mail.google.com/mail/u/0/#all/mcp-msg")


if __name__ == "__main__":
    unittest.main()
