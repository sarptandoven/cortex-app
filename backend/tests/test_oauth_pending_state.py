from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.config import Settings
from backend.app.database import connect, init_db
from backend.app.sharding import StoreRegistry
from backend.app.storage import CortexStore


GOOGLE_PAYLOAD = {
    "source": "gmail",
    "redirect_uri": "http://127.0.0.1:8766/v1/connectors/google/oauth/callback",
    "client_id": "client-123",
    "client_secret": "secret-abc",
    "token_endpoint": "https://oauth2.googleapis.com/token",
    "code_verifier": "pkce-verifier-xyz",
    "source_account_id": None,
    "account_label": "Personal Gmail",
    "account_identifier": "me@example.com",
    "query": "label:important",
    "label_ids": ["INBOX"],
    "mime_types": [],
    "include_body": True,
    "include_content": True,
}


class OAuthPendingStateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex.sqlite"
        init_db(self.db_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_pending_state_survives_backend_restart(self) -> None:
        writer = CortexStore(self.db_path)
        writer.remember_oauth_pending(
            state="state-google-1",
            user_id="local",
            flow="google",
            payload=GOOGLE_PAYLOAD,
        )

        # Simulate a backend restart mid-auth: a brand-new store instance on the
        # same DB. The old process-local dict would have been lost here, failing
        # the callback; the persisted row must survive.
        reader = CortexStore(self.db_path)
        pending = reader.pop_oauth_pending("state-google-1", flow="google")

        self.assertIsNotNone(pending)
        self.assertEqual(pending["user_id"], "local")
        self.assertEqual(pending["source"], "gmail")
        # Sensitive setup fields (secret + PKCE verifier) must round-trip so the
        # token exchange can still complete after the restart.
        self.assertEqual(pending["client_secret"], "secret-abc")
        self.assertEqual(pending["code_verifier"], "pkce-verifier-xyz")
        self.assertEqual(pending["label_ids"], ["INBOX"])
        self.assertTrue(pending["include_body"])

        # Single-use: a second pop finds nothing.
        self.assertIsNone(reader.pop_oauth_pending("state-google-1", flow="google"))

    def test_pending_state_is_flow_scoped(self) -> None:
        store = CortexStore(self.db_path)
        store.remember_oauth_pending(
            state="shared-state", user_id="local", flow="google", payload={"source": "gmail"}
        )

        # Popping with the wrong flow neither returns nor consumes the entry.
        self.assertIsNone(store.pop_oauth_pending("shared-state", flow="managed"))
        pending = store.pop_oauth_pending("shared-state", flow="google")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["source"], "gmail")

    def test_expired_pending_state_is_not_returned(self) -> None:
        store = CortexStore(self.db_path)
        store.remember_oauth_pending(
            state="expired-state",
            user_id="local",
            flow="managed",
            payload={"source": "notion"},
            ttl_seconds=600,
        )

        # Force the row to look expired without sleeping.
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE oauth_pending SET expires_at = ? WHERE state = ?",
                ("2000-01-01T00:00:00Z", "expired-state"),
            )

        self.assertIsNone(store.pop_oauth_pending("expired-state", flow="managed"))

    def test_registry_pops_pending_by_state_without_user_id(self) -> None:
        settings = Settings(
            vault_path=Path(self.tmp.name) / "local.vault",
            db_path=Path(self.tmp.name) / "local.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            default_user_id="local",
            shard_mode="user",
            shard_root=Path(self.tmp.name) / "shards",
            shard_count=4,
        )
        registry = StoreRegistry.from_settings(settings)
        registry.remember_oauth_pending(
            state="reg-state", user_id="alice", flow="managed", payload={"source": "notion"}
        )

        # The unauthenticated OAuth callback resolves the pending entry from
        # `state` alone. The registry must not misroute `state` as a user id;
        # the resolved user id is carried inside the payload instead.
        pending = registry.pop_oauth_pending("reg-state", flow="managed")
        self.assertIsNotNone(pending)
        self.assertEqual(pending["user_id"], "alice")
        self.assertEqual(pending["source"], "notion")


if __name__ == "__main__":
    unittest.main()
