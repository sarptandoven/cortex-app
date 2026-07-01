from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType
from typing import Any

from backend.app.database import init_db
from backend.app.storage import CortexStore


READWISE_TOKEN = "readwise_scheduled_secret_123"
GMAIL_TOKEN = "gmail_scheduled_secret_123"
OUTLOOK_TOKEN = "outlook_scheduled_secret_123"
GOOGLE_DRIVE_TOKEN = "google_drive_scheduled_secret_123"
RAINDROP_TOKEN = "raindrop_scheduled_secret_123"
ZOTERO_TOKEN = "zotero_scheduled_secret_123"
LINEAR_TOKEN = "linear_scheduled_secret_123"
NOTION_TOKEN = "notion_scheduled_secret_123"

SECRET_VALUES = (READWISE_TOKEN, GMAIL_TOKEN, OUTLOOK_TOKEN, GOOGLE_DRIVE_TOKEN, RAINDROP_TOKEN, ZOTERO_TOKEN, LINEAR_TOKEN, NOTION_TOKEN)


class ScheduledTokenConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.root / "vault")
        self.user_id = "scheduled-token-user"
        self.maxDiff = None

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_readwise_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "readwise",
            self._connect_readwise_account,
            "sync_readwise_account",
            {
                "token": READWISE_TOKEN,
                "api_base_url": "https://readwise.invalid/api/v2",
                "cursor_name": "highlights",
                "max_records": 200,
            },
        )

    def test_gmail_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "gmail",
            self._connect_gmail_account,
            "sync_gmail_account",
            {
                "access_token": GMAIL_TOKEN,
                "api_base_url": "https://gmail.invalid/gmail/v1",
                "query": "label:inbox",
                "label_ids": ["INBOX"],
                "include_body": False,
                "cursor_name": "messages",
                "max_records": 200,
            },
        )

    def test_google_drive_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "google-drive",
            self._connect_google_drive_account,
            "sync_google_drive_account",
            {
                "access_token": GOOGLE_DRIVE_TOKEN,
                "api_base_url": "https://drive.invalid/drive/v3",
                "query": "name contains 'Cortex'",
                "mime_types": ["application/vnd.google-apps.document"],
                "include_content": False,
                "cursor_name": "files",
                "max_records": 200,
            },
        )

    def test_outlook_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "outlook",
            self._connect_outlook_account,
            "sync_outlook_account",
            {
                "access_token": OUTLOOK_TOKEN,
                "api_base_url": "https://graph.invalid/v1.0",
                "query": "from/emailAddress/address eq 'sarp@example.com'",
                "include_body": False,
                "cursor_name": "messages",
                "max_records": 200,
            },
        )

    def test_raindrop_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "raindrop",
            self._connect_raindrop_account,
            "sync_raindrop_account",
            {
                "token": RAINDROP_TOKEN,
                "collection_id": "42",
                "api_base_url": "https://raindrop.invalid/rest/v1",
                "include_highlights": False,
                "cursor_name": "raindrops",
                "max_records": 200,
            },
        )

    def test_zotero_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "zotero",
            self._connect_zotero_account,
            "sync_zotero_account",
            {
                "token": ZOTERO_TOKEN,
                "library_type": "group",
                "library_id": "12345",
                "api_base_url": "https://zotero.invalid/api",
                "include_attachments": True,
                "cursor_name": "items",
                "max_records": 200,
            },
        )

    def test_linear_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "linear",
            self._connect_linear_account,
            "sync_linear_account",
            {
                "token": LINEAR_TOKEN,
                "api_url": "https://linear.invalid/graphql",
                "cursor_name": "issues",
                "max_records": 200,
            },
        )

    def test_notion_dispatches_from_local_credentials(self) -> None:
        self._assert_credential_backed_dispatch(
            "notion",
            self._connect_notion_account,
            "sync_notion_account",
            {
                "token": NOTION_TOKEN,
                "api_base_url": "https://notion.invalid/v1",
                "include_content": False,
                "notion_version": "2026-03-11",
                "cursor_name": "pages",
                "max_records": 200,
            },
        )

    def _assert_credential_backed_dispatch(
        self,
        source: str,
        connect_account: Any,
        method_name: str,
        expected: dict[str, Any],
    ) -> None:
        account = self._mark_account_due(connect_account())
        credential = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])
        self.assertIsNotNone(credential)
        self.assertEqual(account["metadata"]["credential_ref"], f"source_credential:{account['id']}")
        self._assert_values_absent(account["metadata"], SECRET_VALUES)
        calls: list[dict[str, Any]] = []

        def fake_sync(store: CortexStore, user_id: str, **kwargs: Any) -> dict[str, Any]:
            calls.append({"user_id": user_id, **kwargs})
            return self._sync_result(source, kwargs)

        setattr(self.store, method_name, MethodType(fake_sync, self.store))

        ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["source_account_id"], account["id"])
        self.assertEqual(calls[0]["processing"], "async")
        for key, value in expected.items():
            self.assertEqual(calls[0].get(key), value)
        self._assert_values_absent(ran["jobs"][0]["payload"], SECRET_VALUES)
        self._assert_values_absent(ran["jobs"][0]["result"], SECRET_VALUES)

    def _connect_readwise_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertTrue(url.startswith("https://readwise.invalid/api/v2/export/"))
            self.assertEqual(headers["Authorization"], f"Token {READWISE_TOKEN}")
            return {"results": [], "nextPageCursor": None}

        result = self.store.sync_readwise_account(
            self.user_id,
            token=READWISE_TOKEN,
            api_base_url="https://readwise.invalid/api/v2",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_gmail_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(headers["Authorization"], f"Bearer {GMAIL_TOKEN}")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            self.assertTrue(url.startswith("https://gmail.invalid/gmail/v1/users/me/messages?"))
            self.assertIn("q=label%3Ainbox", url)
            self.assertIn("labelIds=INBOX", url)
            return {"messages": [], "nextPageToken": None}

        result = self.store.sync_gmail_account(
            self.user_id,
            access_token=GMAIL_TOKEN,
            query="label:inbox",
            label_ids=["INBOX"],
            include_body=False,
            api_base_url="https://gmail.invalid/gmail/v1",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_google_drive_account(self) -> dict[str, Any]:
        def fake_request_value(url: str, headers: dict[str, str]) -> dict[str, Any] | str:
            self.assertEqual(headers["Authorization"], f"Bearer {GOOGLE_DRIVE_TOKEN}")
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com"}}
            self.assertTrue(url.startswith("https://drive.invalid/drive/v3/files?"))
            self.assertIn("name+contains+%27Cortex%27", url)
            return {"files": [], "nextPageToken": None}

        result = self.store.sync_google_drive_account(
            self.user_id,
            access_token=GOOGLE_DRIVE_TOKEN,
            query="name contains 'Cortex'",
            mime_types=["application/vnd.google-apps.document"],
            include_content=False,
            api_base_url="https://drive.invalid/drive/v3",
            processing="sync",
            max_records=10,
            request_value=fake_request_value,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_outlook_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(headers["Authorization"], f"Bearer {OUTLOOK_TOKEN}")
            if url.endswith("/me?%24select=mail%2CuserPrincipalName%2CdisplayName"):
                return {"mail": "sarp@example.com"}
            self.assertTrue(url.startswith("https://graph.invalid/v1.0/me/messages?"))
            self.assertIn("from%2FemailAddress%2Faddress+eq+%27sarp%40example.com%27", url)
            return {"value": [], "@odata.nextLink": None}

        result = self.store.sync_outlook_account(
            self.user_id,
            access_token=OUTLOOK_TOKEN,
            query="from/emailAddress/address eq 'sarp@example.com'",
            include_body=False,
            api_base_url="https://graph.invalid/v1.0",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_raindrop_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertTrue(url.startswith("https://raindrop.invalid/rest/v1/raindrops/42?"))
            self.assertEqual(headers["Authorization"], f"Bearer {RAINDROP_TOKEN}")
            return {"result": True, "items": []}

        result = self.store.sync_raindrop_account(
            self.user_id,
            token=RAINDROP_TOKEN,
            collection_id="42",
            include_highlights=False,
            api_base_url="https://raindrop.invalid/rest/v1",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_zotero_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str]) -> list[dict[str, Any]]:
            self.assertTrue(url.startswith("https://zotero.invalid/api/groups/12345/items?"))
            self.assertEqual(headers["Zotero-API-Key"], ZOTERO_TOKEN)
            return []

        result = self.store.sync_zotero_account(
            self.user_id,
            token=ZOTERO_TOKEN,
            library_type="group",
            library_id="12345",
            include_attachments=True,
            api_base_url="https://zotero.invalid/api",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_linear_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> dict[str, Any]:
            self.assertEqual(url, "https://linear.invalid/graphql")
            self.assertEqual(headers["Authorization"], LINEAR_TOKEN)
            self.assertEqual(body["variables"]["first"], 10)
            return {"data": {"issues": {"nodes": [], "pageInfo": {"hasNextPage": False, "endCursor": None}}}}

        result = self.store.sync_linear_account(
            self.user_id,
            token=LINEAR_TOKEN,
            api_url="https://linear.invalid/graphql",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _connect_notion_account(self) -> dict[str, Any]:
        def fake_request_json(url: str, headers: dict[str, str], body: dict[str, Any], method: str) -> dict[str, Any]:
            self.assertEqual(url, "https://notion.invalid/v1/search")
            self.assertEqual(headers["Authorization"], f"Bearer {NOTION_TOKEN}")
            self.assertEqual(method, "POST")
            return {"results": [], "has_more": False, "next_cursor": None}

        result = self.store.sync_notion_account(
            self.user_id,
            token=NOTION_TOKEN,
            include_content=False,
            api_base_url="https://notion.invalid/v1",
            notion_version="2026-03-11",
            processing="sync",
            max_records=10,
            request_json=fake_request_json,
        )
        self.assertEqual(result["status"], "empty")
        return result["source_account"]

    def _mark_account_due(self, account: dict[str, Any]) -> dict[str, Any]:
        return self.store.upsert_source_account(
            self.user_id,
            source=account["source"],
            account_label=account["account_label"],
            account_identifier=account["account_identifier"],
            connection_type=account["connection_type"],
            status="connected",
            auth_state="healthy",
            policy=account["policy"],
            metadata={
                **account["metadata"],
                "sync_interval_seconds": 60,
                "next_sync_due_at": "2000-01-01T00:00:00Z",
            },
            account_id=account["id"],
        )

    def _sync_result(self, source: str, kwargs: dict[str, Any]) -> dict[str, Any]:
        return {
            "source_account_id": kwargs["source_account_id"],
            "source": source,
            "status": "complete",
            "processing": kwargs["processing"],
            "received": 0,
            "queued": 0,
            "saved": 0,
            "skipped": 0,
            "failed": 0,
            "archived_missing": 0,
            "capture_ids": [],
            "cursor": {"cursor_name": kwargs["cursor_name"]},
        }

    def _assert_values_absent(self, payload: Any, values: tuple[str, ...]) -> None:
        serialized = json.dumps(payload, sort_keys=True, default=str)
        for value in values:
            self.assertNotIn(value, serialized)


if __name__ == "__main__":
    unittest.main()
