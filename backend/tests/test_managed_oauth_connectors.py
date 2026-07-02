from __future__ import annotations

import atexit
import json
import os
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.database import init_db
from backend.app.sharding import StoreRegistry
from backend.app.storage import CortexStore


MODULE_TMP = tempfile.TemporaryDirectory()
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "managed-oauth-fastapi.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "managed-oauth-fastapi.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from backend.app import main as main_module


atexit.register(MODULE_TMP.cleanup)

NOTION_CODE = "notion_auth_code_secret_123"
NOTION_CLIENT_ID = "notion_client_id_secret_123"
NOTION_CLIENT_SECRET = "notion_client_secret_123"
NOTION_ACCESS_TOKEN = "notion_access_token_secret_123"
NOTION_REFRESH_TOKEN = "notion_refresh_token_secret_123"
MICROSOFT_CODE = "microsoft_auth_code_secret_123"
MICROSOFT_CLIENT_ID = "microsoft_client_id_secret_123"
MICROSOFT_CLIENT_SECRET = "microsoft_client_secret_123"
MICROSOFT_ACCESS_TOKEN = "microsoft_access_token_secret_123"
MICROSOFT_REFRESH_TOKEN = "microsoft_refresh_token_secret_123"
SECRET_VALUES = (
    NOTION_CODE,
    NOTION_CLIENT_ID,
    NOTION_CLIENT_SECRET,
    NOTION_ACCESS_TOKEN,
    NOTION_REFRESH_TOKEN,
    MICROSOFT_CODE,
    MICROSOFT_CLIENT_ID,
    MICROSOFT_CLIENT_SECRET,
    MICROSOFT_ACCESS_TOKEN,
    MICROSOFT_REFRESH_TOKEN,
)


def fake_notion_token_response() -> dict[str, object]:
    return {
        "access_token": NOTION_ACCESS_TOKEN,
        "token_type": "bearer",
        "refresh_token": NOTION_REFRESH_TOKEN,
        "bot_id": "bot-notion-123",
        "workspace_name": "Doppl Workspace",
        "workspace_id": "workspace-notion-123",
        "workspace_icon": None,
        "owner": {"type": "workspace", "workspace": True},
        "duplicated_template_id": None,
        "request_id": "request-notion-123",
    }


def fake_microsoft_token_response() -> dict[str, object]:
    return {
        "access_token": MICROSOFT_ACCESS_TOKEN,
        "refresh_token": MICROSOFT_REFRESH_TOKEN,
        "expires_in": 3600,
        "scope": "offline_access User.Read Mail.Read",
        "token_type": "Bearer",
        "claims": {
            "preferred_username": "sarp@example.com",
            "name": "Sarp Doven",
            "oid": "microsoft-user-123",
        },
    }


class ManagedOAuthConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-managed-oauth.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.root / "vault")
        self.user_id = "managed-oauth-user"
        self.fastapi_tmp = tempfile.TemporaryDirectory()
        self.fastapi_root = Path(self.fastapi_tmp.name)
        main_module.settings = Settings(
            db_path=self.fastapi_root / "fastapi.sqlite",
            vault_path=self.fastapi_root / "fastapi.vault",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
        main_module.store = StoreRegistry.from_settings(main_module.settings)
        main_module._managed_oauth_pending.clear()

    def tearDown(self) -> None:
        self.fastapi_tmp.cleanup()
        self.tmp.cleanup()

    def test_notion_oauth_start_uses_public_connection_authorization_url(self) -> None:
        started = self.store.start_managed_oauth(
            "notion",
            redirect_uri="http://127.0.0.1:8766/v1/connectors/oauth/callback",
            state="state-123",
            client_id=NOTION_CLIENT_ID,
        )

        query = parse_qs(urlsplit(started["authorization_url"]).query)

        self.assertEqual(started["source"], "notion")
        self.assertEqual(started["provider"], "notion")
        self.assertEqual(started["authorization_endpoint"], "https://api.notion.com/v1/oauth/authorize")
        self.assertEqual(query["client_id"], [NOTION_CLIENT_ID])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8766/v1/connectors/oauth/callback"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["owner"], ["user"])
        self.assertEqual(query["state"], ["state-123"])
        self.assertNotIn(NOTION_CLIENT_SECRET, started["authorization_url"])

    def test_notion_oauth_complete_stores_schedulable_token_without_leaking_secrets(self) -> None:
        def fake_token_request(
            token_endpoint: str,
            payload: dict[str, object],
            *,
            client_id: str,
            client_secret: str,
            headers: dict[str, str],
        ) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/notion-token")
            self.assertEqual(payload["grant_type"], "authorization_code")
            self.assertEqual(payload["code"], NOTION_CODE)
            self.assertEqual(payload["redirect_uri"], "http://127.0.0.1:8766/v1/connectors/oauth/callback")
            self.assertEqual(client_id, NOTION_CLIENT_ID)
            self.assertEqual(client_secret, NOTION_CLIENT_SECRET)
            self.assertEqual(headers["Notion-Version"], "2026-03-11")
            return fake_notion_token_response()

        completed = self.store.complete_managed_oauth(
            self.user_id,
            "notion",
            code=NOTION_CODE,
            redirect_uri="http://127.0.0.1:8766/v1/connectors/oauth/callback",
            state="state-123",
            expected_state="state-123",
            client_id=NOTION_CLIENT_ID,
            client_secret=NOTION_CLIENT_SECRET,
            token_endpoint="https://oauth2.invalid/notion-token",
            include_content=True,
            request_token=fake_token_request,
        )

        account = completed["source_account"]
        self.assertEqual(account["source"], "notion")
        self.assertEqual(account["account_label"], "Doppl Workspace")
        self.assertEqual(account["account_identifier"], "workspace-notion-123")
        self.assertEqual(account["connection_type"], "oauth-token")
        self.assertEqual(account["metadata"]["managed_oauth"], True)
        self.assertEqual(account["metadata"]["oauth_provider"], "notion")
        self.assertTrue(completed["sync_plan"]["scheduler_supported"])
        self.assertTrue(completed["sync_plan"]["due_now"])

        credential = self.store.vault.read_source_credential(
            user_id=self.user_id,
            source_account_id=account["id"],
        )
        assert credential is not None
        self.assertEqual(credential["payload"]["token"], NOTION_ACCESS_TOKEN)
        self.assertEqual(credential["payload"]["refresh_token"], NOTION_REFRESH_TOKEN)
        self.assertEqual(credential["payload"]["notion_version"], "2026-03-11")
        self.assertTrue(credential["payload"]["include_content"])

        serialized = json.dumps(completed, sort_keys=True, default=str)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, serialized)

    def test_fastapi_managed_oauth_callback_finishes_notion_sign_in_and_queues_sync(self) -> None:
        client = TestClient(main_module.app)
        user = "managed-oauth-fastapi-user"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}

        started = client.post(
            "/v1/connectors/oauth/start",
            json={
                "source": "notion",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
                "client_id": NOTION_CLIENT_ID,
                "client_secret": NOTION_CLIENT_SECRET,
                "token_endpoint": "https://oauth2.invalid/notion-token",
            },
            headers=headers,
        )
        self.assertEqual(started.status_code, 200)
        state = started.json()["state"]

        def fake_token_request(
            token_endpoint: str,
            payload: dict[str, object],
            *,
            client_id: str,
            client_secret: str,
            headers: dict[str, str],
        ) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/notion-token")
            self.assertEqual(payload["code"], NOTION_CODE)
            self.assertEqual(client_id, NOTION_CLIENT_ID)
            self.assertEqual(client_secret, NOTION_CLIENT_SECRET)
            self.assertEqual(headers["Notion-Version"], "2026-03-11")
            return fake_notion_token_response()

        with patch("backend.app.storage._request_basic_json_oauth_token", side_effect=fake_token_request):
            callback = client.get(
                "/v1/connectors/oauth/callback",
                params={"code": NOTION_CODE, "state": state},
            )

        self.assertEqual(callback.status_code, 200)
        self.assertIn("Source is connected", callback.text)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, callback.text)

        user_store = main_module.store.store_for_user(user)
        accounts = user_store.list_source_accounts(user, include_disconnected=True)
        account = next(item for item in accounts if item["source"] == "notion")
        credential = user_store.vault.read_source_credential(user_id=user, source_account_id=account["id"])
        assert credential is not None
        self.assertEqual(credential["payload"]["token"], NOTION_ACCESS_TOKEN)
        jobs = user_store.list_jobs(user, status="queued", job_type="source_account_sync", limit=10)
        self.assertTrue(any(job["object_id"] == account["id"] for job in jobs))

    def test_outlook_oauth_start_uses_microsoft_authorization_url_and_least_privilege_scopes(self) -> None:
        started = self.store.start_managed_oauth(
            "outlook",
            redirect_uri="http://127.0.0.1:8766/v1/connectors/oauth/callback",
            state="state-ms-123",
            client_id=MICROSOFT_CLIENT_ID,
        )

        parsed = urlsplit(started["authorization_url"])
        query = parse_qs(parsed.query)

        self.assertEqual(started["source"], "outlook")
        self.assertEqual(started["provider"], "microsoft")
        self.assertEqual(started["authorization_endpoint"], "https://login.microsoftonline.com/common/oauth2/v2.0/authorize")
        self.assertEqual(started["token_endpoint"], "https://login.microsoftonline.com/common/oauth2/v2.0/token")
        self.assertEqual(query["client_id"], [MICROSOFT_CLIENT_ID])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8766/v1/connectors/oauth/callback"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["response_mode"], ["query"])
        self.assertEqual(query["state"], ["state-ms-123"])
        self.assertEqual(set(query["scope"][0].split()), {"offline_access", "User.Read", "Mail.Read"})
        self.assertNotIn(MICROSOFT_CLIENT_SECRET, started["authorization_url"])

    def test_outlook_oauth_start_accepts_provider_named_environment_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "CORTEX_OUTLOOK_OAUTH_CLIENT_ID": "",
                "CORTEX_MICROSOFT_OAUTH_CLIENT_ID": MICROSOFT_CLIENT_ID,
                "CORTEX_MANAGED_OAUTH_CLIENT_ID": "",
            },
        ):
            started = self.store.start_managed_oauth(
                "outlook",
                redirect_uri="http://127.0.0.1:8766/v1/connectors/oauth/callback",
                state="state-ms-env",
            )

        query = parse_qs(urlsplit(started["authorization_url"]).query)
        self.assertEqual(started["provider"], "microsoft")
        self.assertEqual(query["client_id"], [MICROSOFT_CLIENT_ID])

    def test_outlook_oauth_complete_stores_refreshable_schedulable_graph_account(self) -> None:
        def fake_token_request(token_endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/microsoft-token")
            self.assertEqual(payload["grant_type"], "authorization_code")
            self.assertEqual(payload["code"], MICROSOFT_CODE)
            self.assertEqual(payload["redirect_uri"], "http://127.0.0.1:8766/v1/connectors/oauth/callback")
            self.assertEqual(payload["client_id"], MICROSOFT_CLIENT_ID)
            self.assertEqual(payload["client_secret"], MICROSOFT_CLIENT_SECRET)
            self.assertEqual(set(str(payload["scope"]).split()), {"offline_access", "User.Read", "Mail.Read"})
            return fake_microsoft_token_response()

        completed = self.store.complete_managed_oauth(
            self.user_id,
            "outlook",
            code=MICROSOFT_CODE,
            redirect_uri="http://127.0.0.1:8766/v1/connectors/oauth/callback",
            state="state-ms-123",
            expected_state="state-ms-123",
            client_id=MICROSOFT_CLIENT_ID,
            client_secret=MICROSOFT_CLIENT_SECRET,
            token_endpoint="https://oauth2.invalid/microsoft-token",
            include_content=True,
            request_token=fake_token_request,
        )

        account = completed["source_account"]
        self.assertEqual(account["source"], "outlook")
        self.assertEqual(account["account_label"], "Outlook: Sarp Doven")
        self.assertEqual(account["account_identifier"], "sarp@example.com")
        self.assertEqual(account["connection_type"], "oauth-token")
        self.assertEqual(account["metadata"]["managed_oauth"], True)
        self.assertEqual(account["metadata"]["oauth_provider"], "microsoft")
        self.assertEqual(account["metadata"]["api_base_url"], "https://graph.microsoft.com/v1.0")
        self.assertEqual(account["metadata"]["email"], "sarp@example.com")
        self.assertEqual(set(account["metadata"]["scopes"]), {"offline_access", "User.Read", "Mail.Read"})
        self.assertTrue(completed["sync_plan"]["scheduler_supported"])
        self.assertTrue(completed["sync_plan"]["due_now"])

        credential = self.store.vault.read_source_credential(
            user_id=self.user_id,
            source_account_id=account["id"],
        )
        assert credential is not None
        self.assertEqual(credential["payload"]["access_token"], MICROSOFT_ACCESS_TOKEN)
        self.assertEqual(credential["payload"]["refresh_token"], MICROSOFT_REFRESH_TOKEN)
        self.assertEqual(credential["payload"]["token_endpoint"], "https://oauth2.invalid/microsoft-token")
        self.assertEqual(credential["payload"]["client_id"], MICROSOFT_CLIENT_ID)
        self.assertEqual(credential["payload"]["client_secret"], MICROSOFT_CLIENT_SECRET)
        self.assertEqual(credential["payload"]["api_base_url"], "https://graph.microsoft.com/v1.0")
        self.assertTrue(credential["payload"]["include_body"])
        self.assertIn("access_token_expires_at", credential["payload"])

        serialized = json.dumps(completed, sort_keys=True, default=str)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, serialized)

    def test_outlook_oauth_complete_requires_refresh_token_for_scheduled_sync(self) -> None:
        def fake_token_request(token_endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            response = fake_microsoft_token_response()
            response.pop("refresh_token")
            return response

        with self.assertRaisesRegex(ValueError, "refresh token"):
            self.store.complete_managed_oauth(
                self.user_id,
                "outlook",
                code=MICROSOFT_CODE,
                redirect_uri="http://127.0.0.1:8766/v1/connectors/oauth/callback",
                state="state-ms-123",
                expected_state="state-ms-123",
                client_id=MICROSOFT_CLIENT_ID,
                client_secret=MICROSOFT_CLIENT_SECRET,
                token_endpoint="https://oauth2.invalid/microsoft-token",
                request_token=fake_token_request,
            )

    def test_fastapi_managed_oauth_callback_finishes_outlook_sign_in_and_queues_sync(self) -> None:
        client = TestClient(main_module.app)
        user = "managed-oauth-fastapi-outlook-user"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}

        started = client.post(
            "/v1/connectors/oauth/start",
            json={
                "source": "outlook",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
                "client_id": MICROSOFT_CLIENT_ID,
                "client_secret": MICROSOFT_CLIENT_SECRET,
                "token_endpoint": "https://oauth2.invalid/microsoft-token",
            },
            headers=headers,
        )
        self.assertEqual(started.status_code, 200)
        state = started.json()["state"]

        def fake_token_request(token_endpoint: str, payload: dict[str, object]) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/microsoft-token")
            self.assertEqual(payload["code"], MICROSOFT_CODE)
            self.assertEqual(payload["client_id"], MICROSOFT_CLIENT_ID)
            self.assertEqual(payload["client_secret"], MICROSOFT_CLIENT_SECRET)
            return fake_microsoft_token_response()

        with patch("backend.app.storage._request_oauth_token", side_effect=fake_token_request):
            callback = client.get(
                "/v1/connectors/oauth/callback",
                params={"code": MICROSOFT_CODE, "state": state},
            )

        self.assertEqual(callback.status_code, 200)
        self.assertIn("Source is connected", callback.text)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, callback.text)

        user_store = main_module.store.store_for_user(user)
        accounts = user_store.list_source_accounts(user, include_disconnected=True)
        account = next(item for item in accounts if item["source"] == "outlook")
        self.assertEqual(account["account_identifier"], "sarp@example.com")
        credential = user_store.vault.read_source_credential(user_id=user, source_account_id=account["id"])
        assert credential is not None
        self.assertEqual(credential["payload"]["access_token"], MICROSOFT_ACCESS_TOKEN)
        jobs = user_store.list_jobs(user, status="queued", job_type="source_account_sync", limit=10)
        self.assertTrue(any(job["object_id"] == account["id"] for job in jobs))


if __name__ == "__main__":
    unittest.main()
