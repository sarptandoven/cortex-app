from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.connectors.outlook import fetch_outlook_records
from backend.app.database import init_db
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore, connect


def _recipient(address: str, name: str = "") -> dict:
    return {"emailAddress": {"address": address, "name": name or address}}


def _outlook_message(
    message_id: str,
    *,
    sender: str,
    subject: str,
    body: str,
    received_at: str = "2026-06-29T17:00:00Z",
) -> dict:
    return {
        "id": message_id,
        "conversationId": f"conversation-{message_id}",
        "internetMessageId": f"<{message_id}@example.com>",
        "subject": subject,
        "from": _recipient(sender, "Sender"),
        "toRecipients": [_recipient("sarp@example.com", "Sarp Doven")],
        "ccRecipients": [],
        "receivedDateTime": received_at,
        "sentDateTime": received_at,
        "bodyPreview": body[:80],
        "body": {"contentType": "text", "content": body},
        "webLink": f"https://outlook.office.com/mail/id/{message_id}",
    }


class OutlookConnectorTests(unittest.TestCase):
    def test_fetch_outlook_records_normalizes_messages_and_body(self) -> None:
        calls: list[str] = []

        def fake_request(url: str, headers: dict[str, str]):
            calls.append(url)
            self.assertEqual(headers["Authorization"], "Bearer outlook-test")
            if url.endswith("/me?%24select=mail%2CuserPrincipalName%2CdisplayName"):
                return {"mail": "sarp@example.com", "displayName": "Sarp Doven"}
            if "/me/messages?" in url:
                self.assertIn("%24filter=%28from%2FemailAddress%2Faddress+eq+%27sarp%40example.com%27%29+and+%28receivedDateTime+gt+2026-06-28T10%3A00%3A00Z%29", url)
                return {
                    "value": [
                        _outlook_message(
                            "msg-1",
                            sender="sarp@example.com",
                            subject="Outlook memory loop",
                            body="We decided Outlook sync should preserve cited message bodies.",
                        )
                    ],
                    "@odata.nextLink": "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=page-2",
                }
            raise AssertionError(f"Unexpected URL {url}")

        sync = fetch_outlook_records(
            access_token="outlook-test",
            query="from/emailAddress/address eq 'sarp@example.com'",
            since="2026-06-28T10:00:00Z",
            max_records=1,
            request_json=fake_request,
        )

        self.assertEqual(len(calls), 2)
        self.assertEqual(sync.user_email, "sarp@example.com")
        self.assertEqual(sync.records_found, 1)
        self.assertEqual(sync.records_returned, 1)
        self.assertEqual(sync.next_page_token, "https://graph.microsoft.com/v1.0/me/messages?$skiptoken=page-2")
        record = sync.records[0].to_source_account_record()
        self.assertEqual(record["external_id"], "outlook:message:msg-1")
        self.assertEqual(record["source_url"], "https://outlook.office.com/mail/id/msg-1")
        self.assertIn("Source: Email", record["content"])
        self.assertIn("Subject: Outlook memory loop", record["content"])
        self.assertIn("Body:", record["content"])
        self.assertIn("preserve cited message bodies", record["content"])
        self.assertEqual(record["metadata"]["author_role"], "user")
        self.assertEqual(record["metadata"]["user_email"], "sarp@example.com")

    def test_fetch_outlook_records_requires_access_token(self) -> None:
        with self.assertRaisesRegex(ValueError, "access token"):
            fetch_outlook_records(access_token="")

    def test_fetch_outlook_records_redacts_access_token_from_errors(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            if url.endswith("/me?%24select=mail%2CuserPrincipalName%2CdisplayName"):
                return {"mail": "sarp@example.com"}
            raise RuntimeError("Authorization Bearer outlook-secret-token failed")

        sync = fetch_outlook_records(access_token="outlook-secret-token", request_json=fake_request)

        self.assertEqual(sync.records_returned, 0)
        self.assertEqual(len(sync.errors), 1)
        self.assertNotIn("outlook-secret-token", json.dumps(sync.errors))
        self.assertIn("[REDACTED_CONNECTOR_SECRET]", sync.errors[0]["error"])


class OutlookStorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.root / "vault")
        self.user_id = "outlook-storage-user"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_outlook_account_sync_persists_citations_and_filters_external_preferences(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer outlook-sync-token")
            if url.endswith("/me?%24select=mail%2CuserPrincipalName%2CdisplayName"):
                return {"mail": "sarp@example.com", "displayName": "Sarp Doven"}
            if "/me/messages?" in url:
                return {
                    "value": [
                        _outlook_message(
                            "external-pref",
                            sender="teammate@example.com",
                            subject="External preference",
                            body="I prefer external-only Outlook preferences to be ignored by Cortex.",
                            received_at="2026-06-29T17:00:01Z",
                        ),
                        _outlook_message(
                            "user-pref",
                            sender="sarp@example.com",
                            subject="User preference",
                            body="I prefer Outlook retrieval to keep concise source-backed memory.",
                            received_at="2026-06-29T17:00:02Z",
                        ),
                    ]
                }
            raise AssertionError(f"Unexpected URL {url}")

        result = self.store.sync_outlook_account(
            self.user_id,
            access_token="outlook-sync-token",
            processing="sync",
            max_records=10,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "outlook")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 2)
        self.assertEqual(result["source_account"]["source"], "outlook")
        self.assertEqual(result["source_account"]["connection_type"], "oauth-token")
        self.assertEqual(result["source_account"]["account_identifier"], "sarp@example.com")
        self.assertEqual(result["source_account"]["metadata"]["user_email"], "sarp@example.com")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertNotIn("outlook-sync-token", json.dumps(result))
        self.assertTrue(result["records"][0]["source_url"].startswith("https://outlook.office.com/mail/id/"))

        for capture_id in result["capture_ids"]:
            self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "concise source-backed memory", limit=5)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "outlook")
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
                  AND content LIKE '%external-only Outlook preferences%'
                """,
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(external_preference_count, 0)

    def test_mcp_outlook_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer outlook-mcp-token")
            if url.endswith("/me?%24select=mail%2CuserPrincipalName%2CdisplayName"):
                return {"mail": "sarp@example.com"}
            if "/me/messages?" in url:
                return {
                    "value": [
                        _outlook_message(
                            "mcp-msg",
                            sender="sarp@example.com",
                            subject="MCP Outlook sync",
                            body="We decided MCP Outlook sync should write source-account records.",
                        )
                    ]
                }
            raise AssertionError(f"Unexpected URL {url}")

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_outlook",
                {"access_token": "outlook-mcp-token"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.outlook._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_outlook",
                {"access_token": "outlook-mcp-token", "processing": "sync", "max_records": 10},
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "outlook")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("outlook-mcp-token", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "https://outlook.office.com/mail/id/mcp-msg")


if __name__ == "__main__":
    unittest.main()
