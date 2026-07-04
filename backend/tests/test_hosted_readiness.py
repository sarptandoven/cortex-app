from __future__ import annotations

import unittest
from dataclasses import replace
from pathlib import Path

from backend.app.config import Settings
from backend.app.hosted_readiness import hosted_readiness_contract


class HostedReadinessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.settings = Settings(
            vault_path=Path("/tmp/cortex-test-vault"),
            db_path=Path("/tmp/cortex-test-vault/index.sqlite"),
            api_key="test-api-key",
            public_base_url="http://127.0.0.1:8766",
        )

    def ready_hosted_settings(self, **overrides) -> Settings:
        values = {
            "shard_mode": "bucket",
            "require_scoped_api_tokens": True,
            "public_base_url": "https://api.cortex-hq.com",
            "sync_signing_key": "sync-signing-key",
            "hosted_database_url": "postgresql://cortex:secret@db.cortex.internal/cortex",
            "hosted_vector_backend": "pgvector",
            "worker_mode": "external",
            "observability_enabled": True,
            "embedding_provider": "openai",
            # These tests exercise the post-10k postgres tier explicitly; the DEFAULT tier is
            # sharded_sqlite (the sanctioned 10k runtime), covered by ShardedSqliteTierTests.
            "hosted_runtime_tier": "postgres",
            **overrides,
        }
        return replace(self.settings, **values)

    def ready_runtime(self) -> dict:
        return {
            "control_plane": {
                "active_api_tokens": 1,
                "active_mcp_tokens": 1,
                "active_users": 1,
                "active_ready_users": 1,
            },
            "worker_queue": {
                "status": "ok",
                "ready_user_count": 1,
                "counts": {"queued": 0, "running": 0, "succeeded": 0, "failed": 0},
                "stale_running_count": 0,
            },
            "storage": {
                "database_backend": "postgres",
                "database_live": True,
                "vector_backend": "pgvector",
                "vector_live": True,
            },
        }

    def test_local_mode_is_ready_with_local_defaults(self) -> None:
        contract = hosted_readiness_contract(self.settings)

        self.assertEqual(contract["status"], "ok")
        self.assertFalse(contract["hosted_mode"])
        self.assertEqual(contract["global_token_user_switching"], "allowed_local_compatibility")
        self.assertTrue(all(check["status"] == "ok" for check in contract["checks"]))

    def test_hosted_mode_blocks_until_production_controls_are_configured(self) -> None:
        contract = hosted_readiness_contract(replace(self.settings, shard_mode="bucket"))

        self.assertEqual(contract["status"], "blocked")
        self.assertTrue(contract["hosted_mode"])
        self.assertEqual(contract["global_token_user_switching"], "blocked")
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertIn("scoped_api_tokens_required", blocked)
        self.assertIn("public_base_url", blocked)
        self.assertIn("sync_signing_key", blocked)
        self.assertIn("embedding_provider", blocked)
        self.assertIn("runtime_hosted_storage", blocked)
        self.assertIn("background_workers", blocked)
        self.assertIn("background_worker_queue", blocked)
        self.assertIn("observability", blocked)
        self.assertIn("control_plane_scoped_tokens", blocked)
        # Under the default sharded_sqlite tier, bucket shard mode satisfies the database
        # check and sqlite-vec is the expected vector backend; live evidence is still
        # demanded by runtime_hosted_storage (asserted blocked above).
        self.assertNotIn("hosted_database", blocked)
        self.assertNotIn("hosted_vector_backend", blocked)

    def test_hosted_mode_still_blocks_without_runtime_control_plane_evidence(self) -> None:
        runtime = self.ready_runtime()
        runtime.pop("control_plane")
        contract = hosted_readiness_contract(self.ready_hosted_settings(), runtime=runtime)

        self.assertEqual(contract["status"], "blocked")
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertEqual(blocked, {"control_plane_scoped_tokens"})

    def test_hosted_mode_blocks_worker_queue_attention_state(self) -> None:
        runtime = self.ready_runtime()
        runtime["worker_queue"] = {
            **runtime["worker_queue"],
            "status": "attention",
            "counts": {"queued": 2, "running": 1, "succeeded": 4, "failed": 0},
            "oldest_queued_age_seconds": 42,
        }

        contract = hosted_readiness_contract(self.ready_hosted_settings(), runtime=runtime)

        self.assertEqual(contract["status"], "blocked")
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertEqual(blocked, {"background_worker_queue"})
        queue_check = next(check for check in contract["checks"] if check["name"] == "background_worker_queue")
        self.assertIn("status attention", queue_check["detail"])
        self.assertIn("2 queued job", queue_check["detail"])
        self.assertIn("1 running job", queue_check["detail"])

    def test_hosted_mode_blocks_partial_worker_queue_evidence(self) -> None:
        runtime = self.ready_runtime()
        runtime["worker_queue"] = {
            **runtime["worker_queue"],
            "status": "ok",
            "ready_user_count": 20,
            "total_ready_user_count": 42,
            "ready_user_limit": 20,
            "truncated": True,
            "counts": {"queued": 0, "running": 0, "succeeded": 120, "failed": 0},
            "stale_running_count": 0,
        }

        contract = hosted_readiness_contract(self.ready_hosted_settings(), runtime=runtime)

        self.assertEqual(contract["status"], "blocked")
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertEqual(blocked, {"background_worker_queue"})
        queue_check = next(check for check in contract["checks"] if check["name"] == "background_worker_queue")
        self.assertIn("20 of 42 ready user", queue_check["detail"])
        self.assertIn("complete queue-health evidence", queue_check["detail"])

    def test_hosted_mode_passes_when_10k_platform_controls_are_declared_and_proven(self) -> None:
        contract = hosted_readiness_contract(
            self.ready_hosted_settings(),
            runtime=self.ready_runtime(),
        )

        self.assertEqual(contract["status"], "ok")
        self.assertTrue(contract["hosted_mode"])
        self.assertEqual(contract["global_token_user_switching"], "blocked")
        self.assertTrue(all(check["status"] == "ok" for check in contract["checks"]))

    def test_hosted_mode_blocks_unsafe_public_base_urls(self) -> None:
        unsafe_urls = [
            "http://api.cortex-hq.com",
            "https://localhost:8766",
            "https://127.0.0.1:8766",
            "https://10.0.0.5",
            "https://api",
            "https://api.cortex.example",
            "https://api.example.com",
            "https://api.cortex-hq.com/v1",
            "https://api.cortex-hq.com?token=secret",
            "https://user:pass@api.cortex-hq.com",
            "https://[::1",
        ]

        for public_base_url in unsafe_urls:
            with self.subTest(public_base_url=public_base_url):
                contract = hosted_readiness_contract(
                    self.ready_hosted_settings(public_base_url=public_base_url),
                    runtime=self.ready_runtime(),
                )

                public_url_check = next(check for check in contract["checks"] if check["name"] == "public_base_url")
                blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
                self.assertEqual(public_url_check["status"], "blocked")
                self.assertEqual(blocked, {"public_base_url"})
                self.assertEqual(contract["status"], "blocked")

    def test_hosted_mode_requires_postgres_database_url(self) -> None:
        unsafe_database_urls = [
            "",
            "sqlite:///tmp/cortex.sqlite",
            "file:///tmp/cortex.sqlite",
            "mysql://db.cortex.internal/cortex",
            "postgresql://db.cortex.internal",
            "postgresql:///cortex",
        ]

        for hosted_database_url in unsafe_database_urls:
            with self.subTest(hosted_database_url=hosted_database_url):
                contract = hosted_readiness_contract(
                    self.ready_hosted_settings(hosted_database_url=hosted_database_url),
                    runtime=self.ready_runtime(),
                )

                database_check = next(check for check in contract["checks"] if check["name"] == "hosted_database")
                blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
                self.assertEqual(database_check["status"], "blocked")
                self.assertEqual(blocked, {"hosted_database"})
                self.assertEqual(contract["status"], "blocked")


class ShardedSqliteTierTests(unittest.TestCase):
    """The roadmap-D runtime decision: sharded SQLite on one box is the sanctioned 10k tier,
    so /ready must be achievable WITHOUT Postgres/pgvector — while still demanding live
    runtime evidence (shards live, sqlite-vec live, workers, observability, control plane)."""

    def setUp(self) -> None:
        self.base = Settings(
            vault_path=Path("/tmp/cortex-test-vault"),
            db_path=Path("/tmp/cortex-test-vault/index.sqlite"),
            api_key="test-api-key",
            public_base_url="https://api.cortex-hq.com",
            shard_mode="bucket",
            require_scoped_api_tokens=True,
            sync_signing_key="sync-signing-key",
            worker_mode="external",
            observability_enabled=True,
            embedding_provider="model2vec",
            hosted_runtime_tier="sharded_sqlite",
        )

    def sqlite_runtime(self) -> dict:
        return {
            "control_plane": {
                "active_api_tokens": 1,
                "active_mcp_tokens": 1,
                "active_users": 1,
                "active_ready_users": 1,
            },
            "worker_queue": {
                "status": "ok",
                "ready_user_count": 1,
                "counts": {"queued": 0, "running": 0, "succeeded": 3, "failed": 0},
                "stale_running_count": 0,
            },
            "storage": {
                "database_backend": "sqlite",
                "database_live": True,
                "vector_backend": "sqlite-vec",
                "vector_live": True,
            },
        }

    def test_sharded_sqlite_tier_is_ready_without_postgres(self) -> None:
        contract = hosted_readiness_contract(self.base, runtime=self.sqlite_runtime())
        blocked = {check["name"]: check["detail"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertEqual(contract["status"], "ok", blocked)
        self.assertEqual(contract["runtime_tier"], "sharded_sqlite")

    def test_sqlite_tier_still_demands_live_vector_evidence(self) -> None:
        runtime = self.sqlite_runtime()
        runtime["storage"]["vector_backend"] = "none"
        runtime["storage"]["vector_live"] = False
        contract = hosted_readiness_contract(self.base, runtime=runtime)
        self.assertEqual(contract["status"], "blocked")
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertEqual(blocked, {"runtime_hosted_storage"})

    def test_sqlite_tier_requires_tenant_shard_mode(self) -> None:
        contract = hosted_readiness_contract(
            replace(self.base, shard_mode="weird"), runtime=self.sqlite_runtime()
        )
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertIn("hosted_database", blocked)

    def test_sqlite_tier_rejects_conflicting_pgvector_config(self) -> None:
        contract = hosted_readiness_contract(
            replace(self.base, hosted_vector_backend="pgvector"), runtime=self.sqlite_runtime()
        )
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertIn("hosted_vector_backend", blocked)

    def test_hash_embeddings_still_block_the_sqlite_tier(self) -> None:
        # The tier decision never weakens the honesty gate: keyword-hash "semantics" is not
        # a production embedding provider.
        contract = hosted_readiness_contract(
            replace(self.base, embedding_provider="hash"), runtime=self.sqlite_runtime()
        )
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertIn("embedding_provider", blocked)

    def test_unknown_tier_falls_back_to_sharded_sqlite(self) -> None:
        contract = hosted_readiness_contract(
            replace(self.base, hosted_runtime_tier="mystery"), runtime=self.sqlite_runtime()
        )
        self.assertEqual(contract["runtime_tier"], "sharded_sqlite")
        self.assertEqual(contract["status"], "ok")


if __name__ == "__main__":
    unittest.main()
