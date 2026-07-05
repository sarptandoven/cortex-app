from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module


class HostedDeletionIsolationTests(unittest.TestCase):
    """A hosted tenant deleting their own data must (a) be gated by the destructive Trust
    control, (b) have their scoped token revoked once deleted, and (c) never touch another
    tenant's data or token. This is the GDPR/CCPA delete-and-stay-gone guarantee at the HTTP
    layer across the real sharded multi-tenant stack."""

    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def _provision(self, admin: dict, user_id: str, scopes: list[str]) -> str:
        response = self.client.post(
            "/v1/admin/users",
            json={"user_id": user_id, "api_scopes": scopes, "mcp_scopes": ["read"]},
            headers=admin,
        )
        self.assertEqual(response.status_code, 201, response.text)
        token = response.json()["api_token"]["token"]
        self.assertTrue(token.startswith("cxa_"))
        return token

    def _capture(self, token: str, content: str) -> None:
        headers = {"Authorization": f"Bearer {token}"}
        created = self.client.post("/v1/captures", json={"content": content, "source": "isolation-test"}, headers=headers)
        self.assertEqual(created.status_code, 200, created.text)

    def test_hosted_deletion_isolates_users_and_revokes_token(self) -> None:
        original_settings = main_module.settings
        original_store = main_module.store
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            hosted_settings = replace(
                original_settings,
                db_path=root / "hosted.sqlite",
                vault_path=root / "hosted.vault",
                shard_root=root / "shards",
                default_user_id="hosted-default",
                shard_mode="user",
                require_scoped_api_tokens=True,
            )
            main_module.settings = hosted_settings
            main_module.store = main_module.StoreRegistry.from_settings(hosted_settings)
            admin = {"Authorization": "Bearer test-token"}

            alice_token = self._provision(admin, "alice", ["read", "write", "destructive"])
            bob_token = self._provision(admin, "bob", ["read", "write"])
            alice = {"Authorization": f"Bearer {alice_token}"}
            bob = {"Authorization": f"Bearer {bob_token}"}

            # Skip the review queue so the captured memories are immediately searchable.
            for uid in ("alice", "bob"):
                main_module.store.store_for_user(uid).update_settings(
                    uid, {"review_new_captures": False, "allow_pending_in_context": True}
                )

            self._capture(alice_token, "Alice private launch memory about project Halcyon.")
            self._capture(bob_token, "Bob private roadmap memory about project Zephyr.")
            # Materialize memories so search is deterministic.
            main_module.store.store_for_user("alice").run_due_jobs("alice", limit=20)
            main_module.store.store_for_user("bob").run_due_jobs("bob", limit=20)

            self.assertTrue(self.client.get("/v1/search", params={"query": "Halcyon"}, headers=alice).json()["results"])
            self.assertTrue(self.client.get("/v1/search", params={"query": "Zephyr"}, headers=bob).json()["results"])

            # Destructive delete is gated by the Trust control, which defaults OFF: rejected.
            blocked = self.client.delete("/v1/user-data", params={"include_backups": True}, headers=alice)
            self.assertEqual(blocked.status_code, 403, blocked.text)
            # Nothing was deleted by the rejected call.
            self.assertTrue(self.client.get("/v1/search", params={"query": "Halcyon"}, headers=alice).json()["results"])

            # Operator enables destructive Trust on Alice's shard, then Alice self-deletes.
            main_module.store.store_for_user("alice").update_settings("alice", {"allow_agent_destructive_actions": True})
            deleted = self.client.delete("/v1/user-data", params={"include_backups": True}, headers=alice)
            self.assertEqual(deleted.status_code, 200, deleted.text)
            report = deleted.json()
            # Per-user deletion counts are reported.
            self.assertIn("sqlite", report)

            # Alice's scoped token is fully revoked (not merely "valid token, empty data").
            self.assertEqual(self.client.get("/v1/search", params={"query": "Halcyon"}, headers=alice).status_code, 401)
            self.assertEqual(self.client.get("/v1/ask", params={"query": "Halcyon", "limit": 5}, headers=alice).status_code, 401)

            # Bob is completely untouched: token valid, data intact, still writable.
            bob_search = self.client.get("/v1/search", params={"query": "Zephyr"}, headers=bob)
            self.assertEqual(bob_search.status_code, 200)
            self.assertTrue(bob_search.json()["results"])
            self._capture(bob_token, "Bob adds another Zephyr note after Alice's deletion.")

            # Alice is gone from the control plane; Bob remains.
            listed = self.client.get("/v1/admin/users", headers=admin).json()
            user_ids = {item["user_id"] for item in listed.get("results", [])}
            self.assertIn("bob", user_ids)
            self.assertNotIn("alice", user_ids)
        finally:
            main_module.settings = original_settings
            main_module.store = original_store
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
