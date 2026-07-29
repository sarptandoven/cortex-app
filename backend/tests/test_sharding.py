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

    def test_provision_user_creates_shard_tokens_and_registry_entry(self) -> None:
        settings = self.settings(mode="user")
        issuer = StoreRegistry.from_settings(settings)
        result = issuer.provision_user("alice", display_name="Alice Example", plan="pro")

        self.assertEqual(result["user"]["user_id"], "alice")
        self.assertEqual(result["user"]["display_name"], "Alice Example")
        self.assertEqual(result["user"]["plan"], "pro")
        self.assertEqual(result["user"]["status"], "active")
        self.assertEqual(result["shard"]["mode"], "user")
        api_token = result["api_token"]["token"]
        mcp_token = result["mcp_token"]["token"]
        self.assertTrue(api_token.startswith("cxa_"))
        self.assertTrue(mcp_token.startswith("cxm_"))

        # A cold registry (nothing cached) authenticates the minted tokens to the
        # right user purely from the control index, without opening the shard.
        registry = StoreRegistry.from_settings(settings)
        self.assertEqual(registry._stores, {})
        self.assertEqual(registry.authenticate_api_token(api_token)["user_id"], "alice")
        self.assertEqual(registry.authenticate_mcp_token(mcp_token)["user_id"], "alice")
        self.assertEqual([user["user_id"] for user in registry.list_users()], ["alice"])
        self.assertEqual(registry.control_plane_status()["active_ready_users"], 1)

    def test_provision_user_rejects_duplicate_without_allow_existing(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        registry.provision_user("alice")
        with self.assertRaisesRegex(ValueError, "already provisioned"):
            registry.provision_user("alice")
        again = registry.provision_user("alice", allow_existing=True)
        self.assertTrue(again["api_token"]["token"].startswith("cxa_"))

    def test_provisioned_users_are_isolated(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        alice = registry.provision_user("alice")
        registry.provision_user("bob")

        alice_token = alice["api_token"]["token"]
        self.assertEqual(registry.authenticate_api_token(alice_token)["user_id"], "alice")
        self.assertIsNone(registry.authenticate_api_token(alice_token, user_id="bob"))
        self.assertEqual({user["user_id"] for user in registry.list_users()}, {"alice", "bob"})

    def test_suspended_user_tokens_are_rejected_until_reactivated(self) -> None:
        settings = self.settings(mode="user")
        issuer = StoreRegistry.from_settings(settings)
        api_token = issuer.provision_user("alice")["api_token"]["token"]

        registry = StoreRegistry.from_settings(settings)
        self.assertEqual(registry.authenticate_api_token(api_token)["user_id"], "alice")
        self.assertEqual(registry.control_plane_status()["active_ready_users"], 1)

        self.assertEqual(registry.suspend_user("alice")["status"], "suspended")
        self.assertIsNone(registry.authenticate_api_token(api_token))
        # A suspended user drops out of the "ready" enumeration so workers pause.
        self.assertEqual(registry.control_plane_status()["active_ready_users"], 0)
        self.assertEqual(registry.token_index.ready_user_ids(), [])

        self.assertEqual(registry.reactivate_user("alice")["status"], "active")
        self.assertEqual(registry.authenticate_api_token(api_token)["user_id"], "alice")
        self.assertEqual(registry.control_plane_status()["active_ready_users"], 1)

    def test_deprovision_user_removes_tokens_and_registry_entry(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        api_token = registry.provision_user("alice")["api_token"]["token"]
        self.assertEqual(registry.authenticate_api_token(api_token)["user_id"], "alice")

        registry.deprovision_user("alice")
        self.assertIsNone(registry.authenticate_api_token(api_token))
        self.assertIsNone(registry.get_user("alice"))
        self.assertEqual(registry.list_users(), [])

    def test_store_cache_evicts_least_recently_used_shards(self) -> None:
        registry = StoreRegistry(
            ShardRouter.from_settings(self.settings(mode="user")),
            default_user_id="local",
            store_cache_size=2,
        )
        alice = registry.store_for_user("alice")
        registry.store_for_user("bob")
        self.assertEqual(len(registry._stores), 2)

        # Touch alice so bob becomes least-recently-used, then open carol.
        registry.store_for_user("alice")
        registry.store_for_user("carol")

        cached = set(registry._stores.keys())
        self.assertEqual(len(cached), 2)
        self.assertIn(str(registry.assignment_for("alice").db_path), cached)
        self.assertIn(str(registry.assignment_for("carol").db_path), cached)
        self.assertNotIn(str(registry.assignment_for("bob").db_path), cached)
        # Alice was retained as the same cached instance (not rebuilt).
        self.assertIs(registry.store_for_user("alice"), alice)

    def test_evicted_shard_rematerializes_with_persisted_data(self) -> None:
        registry = StoreRegistry(
            ShardRouter.from_settings(self.settings(mode="user")),
            default_user_id="local",
            store_cache_size=1,
        )
        registry.update_settings("alice", {"allow_pending_in_context": True})

        # Opening other users' shards evicts alice (cache holds only one shard).
        for other in ("bob", "carol", "dave"):
            registry.update_settings(other, {"allow_pending_in_context": False})
        self.assertEqual(len(registry._stores), 1)
        self.assertNotIn(str(registry.assignment_for("alice").db_path), set(registry._stores.keys()))

        # Re-accessing alice re-opens her shard from disk with data intact.
        self.assertTrue(registry.settings("alice")["allow_pending_in_context"])

    def _control_lookup_hashes(self, index) -> list:
        import sqlite3

        conn = sqlite3.connect(index.path)
        try:
            return [row[0] for row in conn.execute("SELECT lookup_hash FROM scoped_token_index").fetchall()]
        finally:
            conn.close()

    def test_token_index_writes_lookup_hash_for_new_tokens(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        registry.ensure_api_token("alice", "cxa_lookup_hash_new_token_123456789", label="Lookup", scopes=["read"])
        hashes = self._control_lookup_hashes(registry.token_index)
        self.assertEqual(len(hashes), 1)
        self.assertTrue(hashes[0])

    def test_token_index_lookup_hash_backfills_legacy_tokens(self) -> None:
        import sqlite3

        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        token = "cxa_legacy_lookup_token_123456789"
        registry.ensure_api_token("alice", token, label="Legacy", scopes=["read"])
        index = registry.token_index

        # Simulate a pre-migration row that predates the lookup_hash column.
        conn = sqlite3.connect(index.path)
        try:
            conn.execute("UPDATE scoped_token_index SET lookup_hash = NULL")
            conn.commit()
        finally:
            conn.close()
        self.assertEqual(self._control_lookup_hashes(index), [None])

        # It still authenticates via the legacy fallback scan...
        scoped = index.authenticate(token, audience="api")
        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["user_id"], "alice")

        # ...and its lookup_hash is backfilled so it is O(1) next time.
        self.assertTrue(self._control_lookup_hashes(index)[0])
        self.assertEqual(index.authenticate(token, audience="api")["user_id"], "alice")

    def test_token_index_rejects_unknown_and_wrong_audience_tokens(self) -> None:
        registry = StoreRegistry.from_settings(self.settings(mode="user"))
        registry.ensure_api_token("alice", "cxa_known_lookup_token_123456789", label="Known", scopes=["read"])
        index = registry.token_index
        self.assertIsNone(index.authenticate("cxa_unknown_lookup_token_987654321", audience="api"))
        # A real api token is not valid for the mcp audience.
        self.assertIsNone(index.authenticate("cxa_known_lookup_token_123456789", audience="mcp"))

    def test_control_index_migrates_pre_lookup_hash_schema(self) -> None:
        # Regression: an existing control index created before the lookup_hash
        # column must upgrade cleanly. Fresh-DB tests miss this because the fresh
        # CREATE TABLE already has the column; a real upgrade does not.
        import hashlib
        import sqlite3

        from backend.app.sharding import TokenControlIndex

        control_path = self.root / "shards" / "control" / "token_index.sqlite"
        control_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(control_path)
        conn.executescript(
            """
            CREATE TABLE scoped_token_index (
              token_id TEXT PRIMARY KEY,
              user_id TEXT NOT NULL,
              audience TEXT NOT NULL,
              label TEXT NOT NULL DEFAULT '',
              token_salt TEXT NOT NULL,
              token_hash TEXT NOT NULL,
              scopes_json TEXT NOT NULL DEFAULT '[]',
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              last_used_at TEXT,
              revoked_at TEXT
            );
            """
        )
        salt = "legacysalt123456"
        token = "cxa_pre_migration_legacy_token_123456789"
        token_hash = hashlib.sha256(f"{salt}:{token}".encode("utf-8")).hexdigest()
        conn.execute(
            "INSERT INTO scoped_token_index (token_id, user_id, audience, label, token_salt, token_hash, scopes_json, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("tok_legacy", "alice", "api", "Legacy", salt, token_hash, '["read"]', "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
        )
        conn.commit()
        conn.close()

        index = TokenControlIndex(control_path)
        # authenticate() -> _connect() must migrate the old schema without raising
        # "no such column: lookup_hash", and the legacy token must still resolve.
        scoped = index.authenticate(token, audience="api")
        self.assertIsNotNone(scoped)
        self.assertEqual(scoped["user_id"], "alice")

        conn = sqlite3.connect(control_path)
        try:
            columns = {row[1] for row in conn.execute("PRAGMA table_info(scoped_token_index)").fetchall()}
            indexes = {row[1] for row in conn.execute("PRAGMA index_list(scoped_token_index)").fetchall()}
        finally:
            conn.close()
        self.assertIn("lookup_hash", columns)
        self.assertIn("idx_scoped_token_index_lookup", indexes)

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

    def test_hosted_job_health_only_marks_truncated_when_ready_users_exceed_limit(self) -> None:
        settings = self.settings(mode="bucket", shard_count=8)
        registry = StoreRegistry.from_settings(settings)
        for user_id in ("alice", "bob"):
            registry.ensure_api_token(user_id, f"cxa_{user_id}_hosted_queue_token_123456789", label=f"{user_id} API", scopes=["read"])
            registry.ensure_mcp_token(user_id, f"cxm_{user_id}_hosted_queue_token_123456789", label=f"{user_id} MCP", scopes=["read"])

        exact = registry.hosted_job_health(ready_user_limit=2)
        partial = registry.hosted_job_health(ready_user_limit=1)

        self.assertEqual(exact["ready_user_count"], 2)
        self.assertEqual(exact["total_ready_user_count"], 2)
        self.assertFalse(exact["truncated"])
        self.assertEqual(partial["ready_user_count"], 1)
        self.assertEqual(partial["total_ready_user_count"], 2)
        self.assertTrue(partial["truncated"])

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

    def _shared_bucket_registry(self):
        """Bucket mode with a single shard routes EVERY user into the same sqlite file — the
        hosted deployment shape in which a cross-tenant id collision is actually reachable."""
        registry = StoreRegistry.from_settings(self.settings(mode="bucket", shard_count=1))
        self.assertEqual(
            registry.assignment_for("tenant-a").db_path,
            registry.assignment_for("tenant-b").db_path,
        )
        return registry

    @staticmethod
    def _colliding_extraction(content: str) -> dict:
        """Two tenants submitting the SAME explicit record/task id — the collision under test."""
        return {
            "_timestamp": "2026-06-29T12:00:00Z",
            "summary": content,
            "records": [
                {
                    "id": "collide_mem",
                    "kind": "observation",
                    "layer": "semantic",
                    "content": content,
                    "confidence": "confirmed",
                    "importance": 3,
                    "topics": [],
                    "entity_ids": [],
                }
            ],
            "tasks": [
                {"id": "collide_task", "kind": "action", "content": f"{content} task", "topics": [], "entity_ids": []}
            ],
            "entities": [],
        }

    def _save_colliding(self, registry, user_id: str, content: str) -> None:
        registry.save_capture(
            user_id=user_id,
            content=f"{user_id} capture",
            source="unit-test",
            source_url=None,
            title=user_id,
            extracted=self._colliding_extraction(content),
            auto_approve=True,
        )

    def _rows(self, registry, table: str) -> list[tuple]:
        import sqlite3

        conn = sqlite3.connect(registry.assignment_for("tenant-a").db_path)
        try:
            return conn.execute(f"SELECT id, user_id, content FROM {table} ORDER BY user_id").fetchall()
        finally:
            conn.close()

    def test_colliding_memory_and_task_ids_do_not_clobber_another_tenant(self) -> None:
        """Regression: memories.id / tasks.id are the sole PRIMARY KEY, and on the ordinary
        local/CLI path an id is content-derived or caller-supplied with no user salt. INSERT OR
        REPLACE resolves conflicts purely on that key, so an unguarded write silently DELETES the
        first tenant's row. Both tenants must survive, each still owning its own content."""
        registry = self._shared_bucket_registry()
        self._save_colliding(registry, "tenant-a", "Tenant A private content")
        self._save_colliding(registry, "tenant-b", "Tenant B private content")

        memories = self._rows(registry, "memories")
        self.assertEqual(len(memories), 2, f"both tenants' memories must survive, got {memories}")
        self.assertEqual([row[1] for row in memories], ["tenant-a", "tenant-b"])
        self.assertEqual(memories[0][2], "Tenant A private content")
        self.assertEqual(memories[1][2], "Tenant B private content")
        # The colliding id was re-salted for exactly one tenant, so the rows are distinct.
        self.assertNotEqual(memories[0][0], memories[1][0])

        tasks = self._rows(registry, "tasks")
        self.assertEqual(len(tasks), 2, f"both tenants' tasks must survive, got {tasks}")
        self.assertEqual([row[1] for row in tasks], ["tenant-a", "tenant-b"])
        self.assertNotEqual(tasks[0][0], tasks[1][0])

    def test_colliding_ids_keep_each_tenants_retrieval_isolated(self) -> None:
        """The clobber's real damage is retrieval: after a collision each tenant must still find
        its own memory and must never surface the other tenant's."""
        registry = self._shared_bucket_registry()
        self._save_colliding(registry, "tenant-a", "The aardvark decision is confidential")
        self._save_colliding(registry, "tenant-b", "The bobcat decision is confidential")

        a_hits = " ".join(str(hit.get("content", "")) for hit in registry.search("tenant-a", "confidential decision"))
        b_hits = " ".join(str(hit.get("content", "")) for hit in registry.search("tenant-b", "confidential decision"))

        self.assertIn("aardvark", a_hits, "tenant-a must still retrieve its own memory")
        self.assertNotIn("bobcat", a_hits, "tenant-a must never retrieve tenant-b's memory")
        self.assertIn("bobcat", b_hits, "tenant-b must still retrieve its own memory")
        self.assertNotIn("aardvark", b_hits, "tenant-b must never retrieve tenant-a's memory")

    def test_cross_tenant_salt_does_not_resurrect_a_forgotten_memory(self) -> None:
        """The collision salt must not defeat anti-resurrection. A memory is tombstoned under its
        PLAIN derived id; if another tenant later takes that id, the re-derivation salts to a
        different string, so checking only the post-salt id would miss the tombstone and silently
        restore content the user explicitly forgot."""
        registry = self._shared_bucket_registry()

        self._save_colliding(registry, "tenant-a", "Tenant A forgotten content")
        a_before = self._rows(registry, "memories")
        self.assertEqual(len(a_before), 1)
        self.assertEqual(a_before[0][0], "collide_mem")

        # Tenant A explicitly forgets it (tombstone is recorded under the plain id).
        self.assertTrue(registry.delete_memory("tenant-a", "collide_mem"))
        self.assertEqual(self._rows(registry, "memories"), [])

        # Tenant B now takes that literal id for its own unrelated memory.
        self._save_colliding(registry, "tenant-b", "Tenant B unrelated content")

        # Tenant A resyncs the same source: re-derives "collide_mem", which now collides with B's
        # row and salts away from it. The forget must still be honored.
        self._save_colliding(registry, "tenant-a", "Tenant A forgotten content")

        rows = self._rows(registry, "memories")
        owners = [row[1] for row in rows]
        self.assertNotIn("tenant-a", owners, f"tenant-a's forgotten memory must not be resurrected, got {rows}")
        self.assertEqual(owners, ["tenant-b"], "tenant-b's memory must be untouched")


if __name__ == "__main__":
    unittest.main()
