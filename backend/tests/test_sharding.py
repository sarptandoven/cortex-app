from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.config import Settings
from backend.app.sharding import ShardRouter, StoreRegistry


class ShardingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def settings(self, *, mode: str = "local", shard_count: int = 4) -> Settings:
        return Settings(
            vault_path=self.root / "local.vault",
            db_path=self.root / "local.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            default_user_id="local",
            shard_mode=mode,
            shard_root=self.root / "shards",
            shard_count=shard_count,
        )

    def test_local_mode_preserves_existing_paths(self) -> None:
        router = ShardRouter.from_settings(self.settings())
        alice = router.assignment_for("alice")
        bob = router.assignment_for("bob")

        self.assertEqual(alice.db_path, self.root / "local.sqlite")
        self.assertEqual(alice.vault_path, self.root / "local.vault")
        self.assertEqual(alice, bob)

        registry = StoreRegistry.from_settings(self.settings())
        self.assertIs(registry.store_for_user("alice"), registry.store_for_user("bob"))
        self.assertEqual(registry.health_payload(mode="test", auth=True)["sharding"]["mode"], "local")

    def test_user_mode_routes_each_user_to_a_dedicated_store(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))

        alice_store = registry.store_for_user("alice@example.com")
        bob_store = registry.store_for_user("bob@example.com")

        self.assertNotEqual(alice_store.db_path, bob_store.db_path)
        self.assertIn("alice-example.com", str(alice_store.db_path))
        self.assertIn("bob-example.com", str(bob_store.db_path))
        self.assertTrue(alice_store.db_path.exists())
        self.assertTrue(bob_store.db_path.exists())

    def test_bucket_mode_is_deterministic_and_isolates_bucket_files(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="bucket", shard_count=8))
        first = registry.assignment_for("enterprise-user-1")
        second = registry.assignment_for("enterprise-user-1")
        other = registry.assignment_for("enterprise-user-2")

        self.assertEqual(first, second)
        self.assertTrue(first.shard_id.startswith("bucket-"))
        self.assertIn("/buckets/", str(first.db_path))
        self.assertLess(int(first.shard_id.removeprefix("bucket-")), 8)
        self.assertLess(int(other.shard_id.removeprefix("bucket-")), 8)

    def test_bucket_mode_blocks_whole_shard_backup_and_backup_delete(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="bucket", shard_count=1))

        with self.assertRaisesRegex(ValueError, "not tenant-safe"):
            registry.create_backup("alice")
        with self.assertRaisesRegex(ValueError, "not tenant-safe"):
            registry.delete_backups("alice")
        with self.assertRaisesRegex(ValueError, "not tenant-safe"):
            registry.restore_latest_backup("alice")
        with self.assertRaisesRegex(ValueError, "not tenant-safe"):
            registry.delete_user_data("alice", include_backups=True)

        registry.update_settings("alice", {"review_new_captures": False})
        deleted = registry.delete_user_data("alice", include_backups=False)
        self.assertFalse(deleted["include_backups"])

    def test_facade_routes_user_scoped_calls_without_changing_call_sites(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        registry.update_settings("alice", {"review_new_captures": False})
        registry.update_settings("bob", {"review_new_captures": True})

        alice = registry.save_capture(
            user_id="alice",
            content="Alice decided sharded Cortex storage should stay isolated.",
            source="unit-test",
            source_url=None,
            title="Alice shard",
            extracted={
                "_timestamp": "2026-06-29T12:00:00Z",
                "summary": "Alice shard",
                "records": [
                    {
                        "id": "alice_memory",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Alice decided sharded Cortex storage should stay isolated.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["sharding"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self.assertEqual(len(alice["memories"]), 1)
        self.assertTrue(registry.search("alice", "sharded storage isolated"))
        self.assertFalse(registry.search("bob", "sharded storage isolated"))

    def test_scoped_api_token_authenticates_from_user_shard(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        token = "cxa_alice_shard_token_123456789"
        registry.ensure_api_token("alice", token, label="Alice shard token", scopes=["read"])

        scoped = registry.authenticate_api_token(token, user_id="alice")
        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["user_id"], "alice")
        self.assertIsNone(registry.authenticate_api_token(token, user_id="bob"))
        self.assertEqual(registry.authenticate_api_token(token)["user_id"], "alice")

    def test_scoped_api_token_authenticates_from_control_index_without_opening_user_shard(self) -> None:
        settings = self.settings(mode="user")
        issuer = StoreRegistry.from_settings(settings)
        token = "cxa_alice_control_index_token_123456789"
        issuer.ensure_api_token("alice", token, label="Alice API", scopes=["read", "write"])

        registry = StoreRegistry.from_settings(settings)
        self.assertEqual(registry._stores, {})

        scoped = registry.authenticate_api_token(token)

        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["user_id"], "alice")
        self.assertEqual(scoped["audience"], "api")
        self.assertEqual(scoped["scopes"], ["read", "write"])
        self.assertTrue(scoped["control_index"])
        self.assertEqual(registry._stores, {})

    def test_scoped_mcp_token_uses_control_index_in_bucket_mode(self) -> None:
        settings = self.settings(mode="bucket", shard_count=8)
        issuer = StoreRegistry.from_settings(settings)
        token = "cxm_alice_control_index_token_123456789"
        issuer.ensure_mcp_token("alice", token, label="Alice MCP", scopes=["read", "export"])

        registry = StoreRegistry.from_settings(settings)
        scoped = registry.authenticate_mcp_token(token)

        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["user_id"], "alice")
        self.assertEqual(scoped["audience"], "mcp")
        self.assertEqual(set(scoped["scopes"]), {"read", "export"})
        self.assertEqual(registry._stores, {})

    def test_generated_tokens_are_added_to_control_index(self) -> None:
        settings = self.settings(mode="bucket", shard_count=8)
        issuer = StoreRegistry.from_settings(settings)
        self.assertEqual(issuer.control_plane_status()["status"], "blocked")
        api_token = issuer.create_api_token("alice", label="Generated API", scopes=["read"])
        mcp_token = issuer.create_mcp_token("alice", label="Generated MCP", scopes=["read"])
        control = issuer.control_plane_status()
        self.assertEqual(control["status"], "ok")
        self.assertEqual(control["active_api_tokens"], 1)
        self.assertEqual(control["active_mcp_tokens"], 1)
        self.assertEqual(control["active_users"], 1)
        self.assertEqual(control["active_ready_users"], 1)

        registry = StoreRegistry.from_settings(settings)
        scoped_api = registry.authenticate_api_token(api_token["token"])
        scoped_mcp = registry.authenticate_mcp_token(mcp_token["token"])

        self.assertIsNotNone(scoped_api)
        self.assertIsNotNone(scoped_mcp)
        self.assertEqual(scoped_api["user_id"], "alice")
        self.assertEqual(scoped_mcp["user_id"], "alice")
        self.assertTrue(scoped_api["control_index"])
        self.assertTrue(scoped_mcp["control_index"])
        self.assertEqual(registry._stores, {})

    def test_control_plane_requires_one_user_with_api_and_mcp_tokens(self) -> None:
        settings = self.settings(mode="bucket", shard_count=8)
        registry = StoreRegistry.from_settings(settings)
        registry.ensure_api_token("alice", "cxa_alice_control_ready_token_123456789", label="Hosted API", scopes=["read"])
        registry.ensure_mcp_token("bob", "cxm_bob_control_ready_token_123456789", label="Hosted MCP", scopes=["read"])

        split = registry.control_plane_status()
        self.assertEqual(split["active_api_tokens"], 1)
        self.assertEqual(split["active_mcp_tokens"], 1)
        self.assertEqual(split["active_users"], 2)
        self.assertEqual(split["active_ready_users"], 0)
        self.assertEqual(split["status"], "blocked")

        registry.ensure_mcp_token("alice", "cxm_alice_control_ready_token_123456789", label="Hosted MCP", scopes=["read"])

        ready = registry.control_plane_status()
        self.assertEqual(ready["active_ready_users"], 1)
        self.assertEqual(ready["status"], "ok")

    def test_control_index_respects_user_hint_revoke_and_user_deletion(self) -> None:
        settings = self.settings(mode="user")
        registry = StoreRegistry.from_settings(settings)
        token = "cxa_alice_revoked_control_index_token_123456789"
        metadata = registry.ensure_api_token("alice", token, label="Alice API", scopes=["read"])

        self.assertIsNone(registry.authenticate_api_token(token, user_id="bob"))
        self.assertIsNotNone(StoreRegistry.from_settings(settings).authenticate_api_token(token, user_id="alice"))

        revoked = registry.revoke_token("alice", metadata["token_id"])
        self.assertIsNotNone(revoked)
        self.assertIsNone(StoreRegistry.from_settings(settings).authenticate_api_token(token))

        replacement = "cxa_alice_deleted_control_index_token_123456789"
        registry.ensure_api_token("alice", replacement, label="Alice API replacement", scopes=["read"])
        self.assertIsNotNone(StoreRegistry.from_settings(settings).authenticate_api_token(replacement))

        deleted = registry.delete_user_data("alice", include_backups=False)
        self.assertFalse(deleted["include_backups"])
        self.assertEqual(deleted["sqlite"]["api_tokens"], 2)
        self.assertIsNone(StoreRegistry.from_settings(settings).authenticate_api_token(replacement))

    def test_source_accounts_and_sync_cursors_are_user_sharded(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))

        alice_account = registry.upsert_source_account(
            "alice",
            source="gmail",
            account_label="Alice Gmail",
            account_identifier="alice@example.com",
            connection_type="oauth",
            status="connected",
            auth_state="healthy",
        )
        bob_account = registry.upsert_source_account(
            "bob",
            source="gmail",
            account_label="Bob Gmail",
            account_identifier="bob@example.com",
            connection_type="oauth",
            status="connected",
            auth_state="healthy",
        )
        registry.upsert_sync_cursor(
            "alice",
            source="gmail",
            source_account_id=alice_account["id"],
            cursor_name="messages",
            cursor_value="alice-cursor",
        )

        self.assertNotEqual(alice_account["id"], bob_account["id"])
        self.assertEqual([item["account_label"] for item in registry.list_source_accounts("alice")], ["Alice Gmail"])
        self.assertEqual([item["account_label"] for item in registry.list_source_accounts("bob")], ["Bob Gmail"])
        self.assertEqual(registry.list_sync_cursors("alice")[0]["cursor_value"], "alice-cursor")
        self.assertEqual(registry.list_sync_cursors("bob"), [])


if __name__ == "__main__":
    unittest.main()
