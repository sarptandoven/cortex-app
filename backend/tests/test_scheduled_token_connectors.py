from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import MethodType
from typing import Any
from unittest.mock import patch

from backend.app.database import init_db
from backend.app.storage import CortexStore


READWISE_TOKEN = "readwise_scheduled_secret_123"
GMAIL_TOKEN = "gmail_scheduled_secret_123"
GMAIL_EXPIRED_TOKEN = "gmail_expired_secret_123"
GMAIL_FRESH_TOKEN = "gmail_fresh_secret_123"
GMAIL_REFRESH_TOKEN = "gmail_refresh_secret_123"
GMAIL_ROTATED_REFRESH_TOKEN = "gmail_rotated_refresh_secret_123"
GMAIL_CLIENT_SECRET = "gmail_client_secret_123"
OUTLOOK_TOKEN = "outlook_scheduled_secret_123"
GOOGLE_DRIVE_TOKEN = "google_drive_scheduled_secret_123"
GOOGLE_DRIVE_EXPIRED_TOKEN = "google_drive_expired_secret_123"
GOOGLE_DRIVE_FRESH_TOKEN = "google_drive_fresh_secret_123"
GOOGLE_DRIVE_REFRESH_TOKEN = "google_drive_refresh_secret_123"
GOOGLE_DRIVE_ROTATED_REFRESH_TOKEN = "google_drive_rotated_refresh_secret_123"
GOOGLE_DRIVE_CLIENT_SECRET = "google_drive_client_secret_123"
OUTLOOK_EXPIRED_TOKEN = "outlook_expired_secret_123"
OUTLOOK_FRESH_TOKEN = "outlook_fresh_secret_123"
OUTLOOK_REFRESH_TOKEN = "outlook_refresh_secret_123"
OUTLOOK_ROTATED_REFRESH_TOKEN = "outlook_rotated_refresh_secret_123"
OUTLOOK_CLIENT_SECRET = "outlook_client_secret_123"
RAINDROP_TOKEN = "raindrop_scheduled_secret_123"
ZOTERO_TOKEN = "zotero_scheduled_secret_123"
LINEAR_TOKEN = "linear_scheduled_secret_123"
NOTION_TOKEN = "notion_scheduled_secret_123"
NOTION_EXPIRED_TOKEN = "notion_expired_secret_123"
NOTION_FRESH_TOKEN = "notion_fresh_secret_123"
NOTION_REFRESH_TOKEN = "notion_refresh_secret_123"
NOTION_ROTATED_REFRESH_TOKEN = "notion_rotated_refresh_secret_123"
NOTION_CLIENT_SECRET = "notion_client_secret_123"

SECRET_VALUES = (
    READWISE_TOKEN,
    GMAIL_TOKEN,
    GMAIL_EXPIRED_TOKEN,
    GMAIL_FRESH_TOKEN,
    GMAIL_REFRESH_TOKEN,
    GMAIL_ROTATED_REFRESH_TOKEN,
    GMAIL_CLIENT_SECRET,
    OUTLOOK_TOKEN,
    GOOGLE_DRIVE_TOKEN,
    GOOGLE_DRIVE_EXPIRED_TOKEN,
    GOOGLE_DRIVE_FRESH_TOKEN,
    GOOGLE_DRIVE_REFRESH_TOKEN,
    GOOGLE_DRIVE_ROTATED_REFRESH_TOKEN,
    GOOGLE_DRIVE_CLIENT_SECRET,
    OUTLOOK_EXPIRED_TOKEN,
    OUTLOOK_FRESH_TOKEN,
    OUTLOOK_REFRESH_TOKEN,
    OUTLOOK_ROTATED_REFRESH_TOKEN,
    OUTLOOK_CLIENT_SECRET,
    RAINDROP_TOKEN,
    ZOTERO_TOKEN,
    LINEAR_TOKEN,
    NOTION_TOKEN,
    NOTION_EXPIRED_TOKEN,
    NOTION_FRESH_TOKEN,
    NOTION_REFRESH_TOKEN,
    NOTION_ROTATED_REFRESH_TOKEN,
    NOTION_CLIENT_SECRET,
)


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

    def test_successful_scheduled_token_sync_clears_due_state_and_reports_healthy(self) -> None:
        account = self._mark_account_due(self._connect_gmail_account())
        before = next(item for item in self.store.source_readiness_report(self.user_id)["sources"] if item["source"] == "gmail")
        self.assertTrue(before["sync_plan"]["due_now"])
        self.assertEqual(before["sync_plan"]["managed_sync_status"], "due")

        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(headers["Authorization"], f"Bearer {GMAIL_TOKEN}")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            self.assertTrue(url.startswith("https://gmail.invalid/gmail/v1/users/me/messages?"))
            return {"messages": [], "nextPageToken": None}

        with patch("backend.app.connectors.gmail._request_json", side_effect=fake_request_json):
            ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        refreshed_account = next(item for item in self.store.list_source_accounts(self.user_id) if item["id"] == account["id"])
        self.assertNotIn("next_sync_due_at", refreshed_account["metadata"])
        self.assertEqual(refreshed_account["metadata"]["last_scheduler_status"], "empty")
        after = next(item for item in self.store.source_readiness_report(self.user_id)["sources"] if item["source"] == "gmail")
        self.assertFalse(after["sync_plan"]["due_now"])
        self.assertEqual(after["sync_plan"]["managed_sync_status"], "healthy")
        self.assertIsNotNone(after["sync_plan"]["last_completed_at"])
        self.assertIsNotNone(after["sync_plan"]["next_sync_due_at"])
        self._assert_values_absent(ran, SECRET_VALUES)

    def test_expired_gmail_access_token_refreshes_before_scheduled_dispatch(self) -> None:
        account = self._mark_account_due(self._connect_gmail_account())
        self.store.store_source_account_credential(
            self.user_id,
            account["id"],
            source="gmail",
            payload={
                "access_token": GMAIL_EXPIRED_TOKEN,
                "refresh_token": GMAIL_REFRESH_TOKEN,
                "token_endpoint": "https://oauth2.invalid/token",
                "client_id": "gmail-client-id",
                "client_secret": GMAIL_CLIENT_SECRET,
                "access_token_expires_at": "2000-01-01T00:00:00Z",
                "query": "label:inbox",
                "label_ids": ["INBOX"],
                "include_body": False,
                "api_base_url": "https://gmail.invalid/gmail/v1",
            },
        )

        def fake_refresh(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/token")
            self.assertEqual(form["grant_type"], "refresh_token")
            self.assertEqual(form["refresh_token"], GMAIL_REFRESH_TOKEN)
            self.assertEqual(form["client_id"], "gmail-client-id")
            self.assertEqual(form["client_secret"], GMAIL_CLIENT_SECRET)
            return {
                "access_token": GMAIL_FRESH_TOKEN,
                "refresh_token": GMAIL_ROTATED_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "gmail.readonly",
            }

        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(headers["Authorization"], f"Bearer {GMAIL_FRESH_TOKEN}")
            if url.endswith("/users/me/profile"):
                return {"emailAddress": "sarp@example.com"}
            self.assertTrue(url.startswith("https://gmail.invalid/gmail/v1/users/me/messages?"))
            return {"messages": [], "nextPageToken": None}

        with patch("backend.app.storage._request_oauth_token_refresh", side_effect=fake_refresh), patch(
            "backend.app.connectors.gmail._request_json",
            side_effect=fake_request_json,
        ):
            ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(ran["jobs"][0]["result"]["source"], "gmail")
        refreshed = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])["payload"]
        self.assertEqual(refreshed["access_token"], GMAIL_FRESH_TOKEN)
        self.assertEqual(refreshed["refresh_token"], GMAIL_ROTATED_REFRESH_TOKEN)
        self.assertEqual(refreshed["scope"], "gmail.readonly")
        self.assertIn("oauth_refreshed_at", refreshed)
        self.assertNotEqual(refreshed["access_token_expires_at"], "2000-01-01T00:00:00Z")
        self._assert_values_absent(ran["jobs"][0]["payload"], SECRET_VALUES)
        self._assert_values_absent(ran["jobs"][0]["result"], SECRET_VALUES)

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

    def test_expired_google_drive_access_token_refreshes_before_scheduled_dispatch(self) -> None:
        account = self._mark_account_due(self._connect_google_drive_account())
        self.store.store_source_account_credential(
            self.user_id,
            account["id"],
            source="google-drive",
            payload={
                "access_token": GOOGLE_DRIVE_EXPIRED_TOKEN,
                "refresh_token": GOOGLE_DRIVE_REFRESH_TOKEN,
                "token_endpoint": "https://oauth2.invalid/token",
                "client_id": "google-drive-client-id",
                "client_secret": GOOGLE_DRIVE_CLIENT_SECRET,
                "access_token_expires_at": "2000-01-01T00:00:00Z",
                "query": "name contains 'Cortex'",
                "mime_types": ["application/vnd.google-apps.document"],
                "include_content": False,
                "api_base_url": "https://drive.invalid/drive/v3",
            },
        )

        def fake_refresh(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/token")
            self.assertEqual(form["refresh_token"], GOOGLE_DRIVE_REFRESH_TOKEN)
            self.assertEqual(form["client_id"], "google-drive-client-id")
            self.assertEqual(form["client_secret"], GOOGLE_DRIVE_CLIENT_SECRET)
            return {
                "access_token": GOOGLE_DRIVE_FRESH_TOKEN,
                "refresh_token": GOOGLE_DRIVE_ROTATED_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "drive.readonly",
            }

        def fake_request_value(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(headers["Authorization"], f"Bearer {GOOGLE_DRIVE_FRESH_TOKEN}")
            if url.endswith("/about?fields=user(emailAddress,displayName)"):
                return {"user": {"emailAddress": "sarp@example.com"}}
            self.assertTrue(url.startswith("https://drive.invalid/drive/v3/files?"))
            return {"files": [], "nextPageToken": None}

        with patch("backend.app.storage._request_oauth_token_refresh", side_effect=fake_refresh), patch(
            "backend.app.connectors.google_drive._request_value",
            side_effect=fake_request_value,
        ):
            ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(ran["jobs"][0]["result"]["source"], "google-drive")
        refreshed = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])["payload"]
        self.assertEqual(refreshed["access_token"], GOOGLE_DRIVE_FRESH_TOKEN)
        self.assertEqual(refreshed["refresh_token"], GOOGLE_DRIVE_ROTATED_REFRESH_TOKEN)
        self.assertEqual(refreshed["scope"], "drive.readonly")
        self.assertIn("oauth_refreshed_at", refreshed)
        self.assertNotEqual(refreshed["access_token_expires_at"], "2000-01-01T00:00:00Z")
        self._assert_values_absent(ran["jobs"][0]["payload"], SECRET_VALUES)
        self._assert_values_absent(ran["jobs"][0]["result"], SECRET_VALUES)

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

    def test_expired_outlook_access_token_refreshes_before_scheduled_dispatch(self) -> None:
        account = self._mark_account_due(self._connect_outlook_account())
        self.store.store_source_account_credential(
            self.user_id,
            account["id"],
            source="outlook",
            payload={
                "access_token": OUTLOOK_EXPIRED_TOKEN,
                "refresh_token": OUTLOOK_REFRESH_TOKEN,
                "token_endpoint": "https://login.invalid/oauth2/v2.0/token",
                "client_id": "outlook-client-id",
                "client_secret": OUTLOOK_CLIENT_SECRET,
                "access_token_expires_at": "2000-01-01T00:00:00Z",
                "query": "from/emailAddress/address eq 'sarp@example.com'",
                "include_body": False,
                "api_base_url": "https://graph.invalid/v1.0",
            },
        )

        def fake_refresh(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(token_endpoint, "https://login.invalid/oauth2/v2.0/token")
            self.assertEqual(form["refresh_token"], OUTLOOK_REFRESH_TOKEN)
            self.assertEqual(form["client_id"], "outlook-client-id")
            self.assertEqual(form["client_secret"], OUTLOOK_CLIENT_SECRET)
            return {
                "access_token": OUTLOOK_FRESH_TOKEN,
                "refresh_token": OUTLOOK_ROTATED_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "Mail.Read User.Read",
            }

        def fake_request_json(url: str, headers: dict[str, str]) -> dict[str, Any]:
            self.assertEqual(headers["Authorization"], f"Bearer {OUTLOOK_FRESH_TOKEN}")
            if url.endswith("/me?%24select=mail%2CuserPrincipalName%2CdisplayName"):
                return {"mail": "sarp@example.com"}
            self.assertTrue(url.startswith("https://graph.invalid/v1.0/me/messages?"))
            return {"value": [], "@odata.nextLink": None}

        with patch("backend.app.storage._request_oauth_token_refresh", side_effect=fake_refresh), patch(
            "backend.app.connectors.outlook._request_json",
            side_effect=fake_request_json,
        ):
            ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(ran["jobs"][0]["result"]["source"], "outlook")
        refreshed = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])["payload"]
        self.assertEqual(refreshed["access_token"], OUTLOOK_FRESH_TOKEN)
        self.assertEqual(refreshed["refresh_token"], OUTLOOK_ROTATED_REFRESH_TOKEN)
        self.assertEqual(refreshed["scope"], "Mail.Read User.Read")
        self.assertIn("oauth_refreshed_at", refreshed)
        self.assertNotEqual(refreshed["access_token_expires_at"], "2000-01-01T00:00:00Z")
        self._assert_values_absent(ran["jobs"][0]["payload"], SECRET_VALUES)
        self._assert_values_absent(ran["jobs"][0]["result"], SECRET_VALUES)

    def test_expired_notion_access_token_refreshes_before_scheduled_dispatch(self) -> None:
        account = self._mark_account_due(self._connect_notion_account())
        self.store.store_source_account_credential(
            self.user_id,
            account["id"],
            source="notion",
            payload={
                # Notion stores its access token under "token"; the OAuth-refresh path keys off
                # access_token / access_token_expires_at, so both are present after complete.
                "token": NOTION_EXPIRED_TOKEN,
                "access_token": NOTION_EXPIRED_TOKEN,
                "refresh_token": NOTION_REFRESH_TOKEN,
                "token_endpoint": "https://notion.invalid/v1/oauth/token",
                "client_id": "notion-client-id",
                "client_secret": NOTION_CLIENT_SECRET,
                "access_token_expires_at": "2000-01-01T00:00:00Z",
                "api_base_url": "https://notion.invalid/v1",
                "notion_version": "2026-03-11",
                "include_content": False,
            },
        )

        def fake_refresh(token_endpoint: str, *, refresh_token: str, client_id: str, client_secret: str, notion_version: str = "") -> dict[str, Any]:
            # Notion refreshes with Basic auth (client creds in the Authorization header), not a
            # form body, so the refresh helper is Notion-specific.
            self.assertEqual(token_endpoint, "https://notion.invalid/v1/oauth/token")
            self.assertEqual(refresh_token, NOTION_REFRESH_TOKEN)
            self.assertEqual(client_id, "notion-client-id")
            self.assertEqual(client_secret, NOTION_CLIENT_SECRET)
            self.assertEqual(notion_version, "2026-03-11")
            return {
                "access_token": NOTION_FRESH_TOKEN,
                "refresh_token": NOTION_ROTATED_REFRESH_TOKEN,
                "expires_in": 3600,
            }

        def fake_request_json(url: str, headers: dict[str, str], body: dict[str, Any] | None, method: str) -> dict[str, Any]:
            # The dispatched sync must use the freshly refreshed token, not the expired one.
            self.assertEqual(headers["Authorization"], f"Bearer {NOTION_FRESH_TOKEN}")
            return {"results": [], "has_more": False, "next_cursor": None}

        with patch("backend.app.storage._request_notion_oauth_token_refresh", side_effect=fake_refresh), patch(
            "backend.app.connectors.notion._request_json",
            side_effect=fake_request_json,
        ):
            ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(ran["jobs"][0]["result"]["source"], "notion")
        refreshed = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])["payload"]
        # The Notion sync dispatch reads "token"; it must now hold the freshly refreshed value.
        self.assertEqual(refreshed["token"], NOTION_FRESH_TOKEN)
        self.assertEqual(refreshed["refresh_token"], NOTION_ROTATED_REFRESH_TOKEN)
        # Refresh configuration is retained so the NEXT expiry can refresh again.
        self.assertEqual(refreshed["token_endpoint"], "https://notion.invalid/v1/oauth/token")
        self.assertEqual(refreshed["client_id"], "notion-client-id")
        self.assertIn("oauth_refreshed_at", refreshed)
        self.assertNotEqual(refreshed["access_token_expires_at"], "2000-01-01T00:00:00Z")
        self._assert_values_absent(ran["jobs"][0]["payload"], SECRET_VALUES)
        self._assert_values_absent(ran["jobs"][0]["result"], SECRET_VALUES)

    def test_oauth_refresh_failure_marks_account_needs_attention_without_secret_leak(self) -> None:
        account = self._mark_account_due(self._connect_gmail_account())
        self.store.store_source_account_credential(
            self.user_id,
            account["id"],
            source="gmail",
            payload={
                "access_token": GMAIL_EXPIRED_TOKEN,
                "refresh_token": GMAIL_REFRESH_TOKEN,
                "token_endpoint": "https://oauth2.invalid/token",
                "client_id": "gmail-client-id",
                "client_secret": GMAIL_CLIENT_SECRET,
                "access_token_expires_at": "2000-01-01T00:00:00Z",
                "query": "label:inbox",
                "label_ids": ["INBOX"],
                "include_body": False,
                "api_base_url": "https://gmail.invalid/gmail/v1",
            },
        )

        def fake_refresh(token_endpoint: str, form: dict[str, str]) -> dict[str, Any]:
            raise RuntimeError(
                f"invalid refresh_token={GMAIL_REFRESH_TOKEN} client_secret={GMAIL_CLIENT_SECRET}"
            )

        with patch("backend.app.storage._request_oauth_token_refresh", side_effect=fake_refresh):
            ran = self.store.run_due_jobs(self.user_id, limit=1)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "queued")
        self.assertIn("[REDACTED_CONNECTOR_SECRET]", ran["jobs"][0]["last_error"])
        refreshed_account = self.store.list_source_accounts(self.user_id)[0]
        self.assertEqual(refreshed_account["status"], "needs_attention")
        self.assertEqual(refreshed_account["auth_state"], "error")
        self.assertIn("[REDACTED_CONNECTOR_SECRET]", refreshed_account["last_error"])
        self._assert_values_absent(ran["jobs"][0], SECRET_VALUES)
        self._assert_values_absent(refreshed_account, SECRET_VALUES)

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

    def test_scheduled_incremental_sync_never_requests_archive_missing(self) -> None:
        # A scheduled (background) sync uses an incremental cursor, so it must
        # never request archive_missing/complete_snapshot: a record merely absent
        # from a later incremental batch must not be retired. Upstream-delete
        # reconciliation only happens on a proven-complete scan (e.g. Obsidian's
        # full vault rescan gated on `not truncated and not errors`), never on the
        # incremental scheduled path. This guards against a future change that
        # would silently archive valid user memory.
        connectors = [
            ("readwise", self._connect_readwise_account, "sync_readwise_account"),
            ("gmail", self._connect_gmail_account, "sync_gmail_account"),
            ("google-drive", self._connect_google_drive_account, "sync_google_drive_account"),
            ("outlook", self._connect_outlook_account, "sync_outlook_account"),
            ("raindrop", self._connect_raindrop_account, "sync_raindrop_account"),
            ("zotero", self._connect_zotero_account, "sync_zotero_account"),
            ("linear", self._connect_linear_account, "sync_linear_account"),
            ("notion", self._connect_notion_account, "sync_notion_account"),
        ]
        for source, connect_account, method_name in connectors:
            with self.subTest(source=source):
                account = self._mark_account_due(connect_account())
                calls: list[dict[str, Any]] = []

                def fake_sync(store: CortexStore, user_id: str, _source: str = source, **kwargs: Any) -> dict[str, Any]:
                    calls.append(kwargs)
                    return self._sync_result(_source, kwargs)

                setattr(self.store, method_name, MethodType(fake_sync, self.store))
                ran = self.store.run_due_jobs(self.user_id, limit=1)

                self.assertEqual(ran["processed"], 1)
                self.assertEqual(ran["jobs"][0]["status"], "succeeded")
                self.assertEqual(len(calls), 1)
                self.assertEqual(calls[0]["source_account_id"], account["id"])
                self.assertFalse(
                    calls[0].get("archive_missing", False),
                    f"{source} scheduled sync must not request archive_missing",
                )
                self.assertFalse(
                    calls[0].get("complete_snapshot", False),
                    f"{source} scheduled sync must not request complete_snapshot",
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
