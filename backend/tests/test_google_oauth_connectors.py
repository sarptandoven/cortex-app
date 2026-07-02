from __future__ import annotations

import json
import os
import atexit
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
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "google-oauth-fastapi.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "google-oauth-fastapi.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from backend.app import main as main_module


atexit.register(MODULE_TMP.cleanup)

GOOGLE_CODE = "google_auth_code_secret_123"
GOOGLE_CLIENT_ID = "google_client_id_secret_123"
GOOGLE_CLIENT_SECRET = "google_client_secret_123"
GOOGLE_ACCESS_TOKEN = "google_access_token_secret_123"
GOOGLE_REFRESH_TOKEN = "google_refresh_token_secret_123"
GOOGLE_CODE_VERIFIER = "google_pkce_code_verifier_secret_1234567890"
GOOGLE_CODE_CHALLENGE = "google_pkce_code_challenge_123"
SECRET_VALUES = (
    GOOGLE_CODE,
    GOOGLE_CLIENT_ID,
    GOOGLE_CLIENT_SECRET,
    GOOGLE_ACCESS_TOKEN,
    GOOGLE_REFRESH_TOKEN,
    GOOGLE_CODE_VERIFIER,
)

class GoogleOAuthConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.db_path = self.root / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=self.root / "vault")
        self.user_id = "google-oauth-user"
        self.fastapi_tmp = tempfile.TemporaryDirectory()
        self.fastapi_root = Path(self.fastapi_tmp.name)
        main_module.settings = Settings(
            db_path=self.fastapi_root / "fastapi.sqlite",
            vault_path=self.fastapi_root / "fastapi.vault",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
        main_module.store = StoreRegistry.from_settings(main_module.settings)
        main_module._google_oauth_pending.clear()

    def tearDown(self) -> None:
        self.fastapi_tmp.cleanup()
        self.tmp.cleanup()

    def test_google_oauth_start_uses_read_only_offline_consent_url(self) -> None:
        started = self.store.start_google_oauth(
            "gmail",
            redirect_uri="http://127.0.0.1:8766/oauth/google",
            state="state-123",
            client_id=GOOGLE_CLIENT_ID,
            code_challenge=GOOGLE_CODE_CHALLENGE,
            code_challenge_method="S256",
        )

        query = parse_qs(urlsplit(started["authorization_url"]).query)

        self.assertEqual(started["source"], "gmail")
        self.assertEqual(started["provider"], "google")
        self.assertEqual(query["client_id"], [GOOGLE_CLIENT_ID])
        self.assertEqual(query["redirect_uri"], ["http://127.0.0.1:8766/oauth/google"])
        self.assertEqual(query["response_type"], ["code"])
        self.assertEqual(query["access_type"], ["offline"])
        self.assertEqual(query["prompt"], ["consent"])
        self.assertEqual(query["include_granted_scopes"], ["true"])
        self.assertEqual(query["state"], ["state-123"])
        self.assertEqual(query["code_challenge"], [GOOGLE_CODE_CHALLENGE])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        self.assertEqual(query["scope"], ["https://www.googleapis.com/auth/gmail.readonly"])
        self.assertNotIn(GOOGLE_CLIENT_SECRET, started["authorization_url"])

    def test_google_oauth_complete_stores_refreshable_gmail_credentials_locally(self) -> None:
        def fake_token_request(token_endpoint: str, form: dict[str, str]) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/token")
            self.assertEqual(form["grant_type"], "authorization_code")
            self.assertEqual(form["code"], GOOGLE_CODE)
            self.assertEqual(form["client_id"], GOOGLE_CLIENT_ID)
            self.assertEqual(form["client_secret"], GOOGLE_CLIENT_SECRET)
            self.assertEqual(form["redirect_uri"], "http://127.0.0.1:8766/oauth/google")
            return {
                "access_token": GOOGLE_ACCESS_TOKEN,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            }

        completed = self.store.complete_google_oauth(
            self.user_id,
            "gmail",
            code=GOOGLE_CODE,
            redirect_uri="http://127.0.0.1:8766/oauth/google",
            state="state-123",
            expected_state="state-123",
            client_id=GOOGLE_CLIENT_ID,
            client_secret=GOOGLE_CLIENT_SECRET,
            token_endpoint="https://oauth2.invalid/token",
            account_label="Sarp Gmail",
            account_identifier="sarp@example.com",
            query="label:inbox",
            label_ids=["INBOX"],
            request_token=fake_token_request,
        )

        account = completed["source_account"]
        self.assertEqual(account["source"], "gmail")
        self.assertEqual(account["connection_type"], "oauth-token")
        self.assertEqual(account["status"], "connected")
        self.assertEqual(account["auth_state"], "healthy")
        self.assertEqual(account["metadata"]["managed_oauth"], True)
        self.assertEqual(account["metadata"]["oauth_provider"], "google")
        self.assertEqual(account["metadata"]["credential_ref"], completed["credential_ref"])
        self.assertTrue(completed["sync_plan"]["scheduler_supported"])
        self.assertTrue(completed["sync_plan"]["due_now"])

        credential = self.store.vault.read_source_credential(
            user_id=self.user_id,
            source_account_id=account["id"],
        )
        assert credential is not None
        self.assertEqual(credential["payload"]["access_token"], GOOGLE_ACCESS_TOKEN)
        self.assertEqual(credential["payload"]["refresh_token"], GOOGLE_REFRESH_TOKEN)
        self.assertEqual(credential["payload"]["client_id"], GOOGLE_CLIENT_ID)
        self.assertEqual(credential["payload"]["client_secret"], GOOGLE_CLIENT_SECRET)
        self.assertEqual(credential["payload"]["query"], "label:inbox")
        self.assertEqual(credential["payload"]["label_ids"], ["INBOX"])

        response_payload = json.dumps(completed, sort_keys=True, default=str)
        account_metadata = json.dumps(account["metadata"], sort_keys=True, default=str)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, response_payload)
            self.assertNotIn(secret, account_metadata)

    def test_google_oauth_complete_supports_pkce_without_client_secret(self) -> None:
        def fake_token_request(token_endpoint: str, form: dict[str, str]) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/token")
            self.assertEqual(form["grant_type"], "authorization_code")
            self.assertEqual(form["code"], GOOGLE_CODE)
            self.assertEqual(form["client_id"], GOOGLE_CLIENT_ID)
            self.assertEqual(form["code_verifier"], GOOGLE_CODE_VERIFIER)
            self.assertNotIn("client_secret", form)
            return {
                "access_token": GOOGLE_ACCESS_TOKEN,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            }

        completed = self.store.complete_google_oauth(
            self.user_id,
            "gmail",
            code=GOOGLE_CODE,
            redirect_uri="http://127.0.0.1:8766/oauth/google",
            state="state-123",
            expected_state="state-123",
            client_id=GOOGLE_CLIENT_ID,
            code_verifier=GOOGLE_CODE_VERIFIER,
            token_endpoint="https://oauth2.invalid/token",
            account_label="Sarp Gmail",
            account_identifier="sarp@example.com",
            request_token=fake_token_request,
        )

        account = completed["source_account"]
        credential = self.store.vault.read_source_credential(
            user_id=self.user_id,
            source_account_id=account["id"],
        )
        assert credential is not None
        self.assertEqual(credential["payload"]["client_id"], GOOGLE_CLIENT_ID)
        self.assertNotIn("client_secret", credential["payload"])
        serialized = json.dumps(completed, sort_keys=True, default=str)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, serialized)

    def test_google_oauth_catalog_exposes_managed_gmail_and_drive_only(self) -> None:
        catalog = {item["id"]: item for item in self.store.source_connector_catalog()}

        for source in ("gmail", "google-drive"):
            setup = catalog[source]["connection_setup"]
            with self.subTest(source=source):
                self.assertTrue(setup["managed_oauth_shipped"])
                self.assertEqual(setup["oauth_provider"], "google")
                self.assertEqual(setup["oauth_start_endpoint"], "/v1/connectors/google/oauth/start")
                self.assertEqual(setup["oauth_complete_endpoint"], "/v1/connectors/google/oauth/complete")

        outlook_setup = catalog["outlook"]["connection_setup"]
        self.assertTrue(outlook_setup["managed_oauth_shipped"])
        self.assertEqual(outlook_setup["oauth_provider"], "microsoft")
        self.assertEqual(outlook_setup["oauth_start_endpoint"], "/v1/connectors/oauth/start")
        self.assertEqual(outlook_setup["oauth_complete_endpoint"], "/v1/connectors/oauth/complete")

    def test_fastapi_google_oauth_complete_redacts_connector_secrets(self) -> None:
        client = TestClient(main_module.app)
        user = "google-oauth-fastapi-user"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}

        def fake_token_request(token_endpoint: str, form: dict[str, str]) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/token")
            self.assertEqual(form["grant_type"], "authorization_code")
            self.assertEqual(form["code"], GOOGLE_CODE)
            self.assertEqual(form["client_id"], GOOGLE_CLIENT_ID)
            self.assertEqual(form["client_secret"], GOOGLE_CLIENT_SECRET)
            return {
                "access_token": GOOGLE_ACCESS_TOKEN,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/drive.readonly",
            }

        with patch("backend.app.storage._request_oauth_token", side_effect=fake_token_request):
            response = client.post(
                "/v1/connectors/google/oauth/complete",
                json={
                    "source": "google-drive",
                    "code": GOOGLE_CODE,
                    "redirect_uri": "http://127.0.0.1:8766/oauth/google",
                    "state": "drive-state",
                    "expected_state": "drive-state",
                    "client_id": GOOGLE_CLIENT_ID,
                    "client_secret": GOOGLE_CLIENT_SECRET,
                    "token_endpoint": "https://oauth2.invalid/token",
                    "account_label": "Sarp Drive",
                    "account_identifier": "sarp-drive",
                    "query": "name contains 'Cortex'",
                    "mime_types": ["application/vnd.google-apps.document"],
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "google-drive")
        self.assertEqual(payload["source_account"]["source"], "google-drive")
        self.assertEqual(payload["source_account"]["connection_type"], "oauth-token")
        self.assertTrue(payload["source_account"]["metadata"]["managed_oauth"])
        serialized = json.dumps(payload, sort_keys=True)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, serialized)

        account_id = payload["source_account"]["id"]
        credential = main_module.store.store_for_user(user).vault.read_source_credential(
            user_id=user,
            source_account_id=account_id,
        )
        assert credential is not None
        self.assertEqual(credential["payload"]["access_token"], GOOGLE_ACCESS_TOKEN)
        self.assertEqual(credential["payload"]["refresh_token"], GOOGLE_REFRESH_TOKEN)
        self.assertEqual(credential["payload"]["mime_types"], ["application/vnd.google-apps.document"])

    def test_fastapi_google_oauth_callback_finishes_pending_sign_in_and_queues_sync(self) -> None:
        client = TestClient(main_module.app)
        user = "google-oauth-callback-user"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}

        def fake_token_request(token_endpoint: str, form: dict[str, str]) -> dict[str, object]:
            self.assertEqual(token_endpoint, "https://oauth2.invalid/token")
            self.assertEqual(form["grant_type"], "authorization_code")
            self.assertEqual(form["code"], GOOGLE_CODE)
            self.assertEqual(form["client_id"], GOOGLE_CLIENT_ID)
            self.assertEqual(form["code_verifier"], GOOGLE_CODE_VERIFIER)
            self.assertNotIn("client_secret", form)
            return {
                "access_token": GOOGLE_ACCESS_TOKEN,
                "refresh_token": GOOGLE_REFRESH_TOKEN,
                "expires_in": 3600,
                "scope": "https://www.googleapis.com/auth/gmail.readonly",
            }

        started = client.post(
            "/v1/connectors/google/oauth/start",
            json={
                "source": "gmail",
                "redirect_uri": "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
                "client_id": GOOGLE_CLIENT_ID,
                "token_endpoint": "https://oauth2.invalid/token",
                "code_verifier": GOOGLE_CODE_VERIFIER,
                "code_challenge": GOOGLE_CODE_CHALLENGE,
                "code_challenge_method": "S256",
                "account_label": "Sarp Gmail",
                "account_identifier": "sarp@example.com",
                "query": "label:inbox",
                "label_ids": ["INBOX"],
            },
            headers=headers,
        )
        self.assertEqual(started.status_code, 200)
        state = started.json()["state"]
        self.assertNotIn(GOOGLE_CLIENT_SECRET, json.dumps(started.json(), sort_keys=True))

        with patch("backend.app.storage._request_oauth_token", side_effect=fake_token_request):
            callback = client.get(
                "/v1/connectors/google/oauth/callback",
                params={"code": GOOGLE_CODE, "state": state},
            )

        self.assertEqual(callback.status_code, 200)
        self.assertIn("Google is connected", callback.text)
        for secret in SECRET_VALUES:
            self.assertNotIn(secret, callback.text)

        accounts = main_module.store.store_for_user(user).list_source_accounts(user, include_disconnected=True)
        account = next(item for item in accounts if item["source"] == "gmail")
        self.assertEqual(account["account_label"], "Sarp Gmail")
        self.assertEqual(account["status"], "connected")

        credential = main_module.store.store_for_user(user).vault.read_source_credential(
            user_id=user,
            source_account_id=account["id"],
        )
        assert credential is not None
        self.assertEqual(credential["payload"]["refresh_token"], GOOGLE_REFRESH_TOKEN)

        jobs = main_module.store.store_for_user(user).list_jobs(
            user,
            status="queued",
            job_type="source_account_sync",
            limit=10,
        )
        self.assertTrue(any(job["object_id"] == account["id"] for job in jobs))


if __name__ == "__main__":
    unittest.main()
