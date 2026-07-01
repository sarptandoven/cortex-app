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
        self.assertIn("hosted_database", blocked)
        self.assertIn("embedding_provider", blocked)
        self.assertIn("hosted_vector_backend", blocked)
        self.assertIn("background_workers", blocked)
        self.assertIn("background_worker_queue", blocked)
        self.assertIn("observability", blocked)
        self.assertIn("control_plane_scoped_tokens", blocked)

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


if __name__ == "__main__":
    unittest.main()
