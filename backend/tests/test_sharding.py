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


if __name__ == "__main__":
    unittest.main()
