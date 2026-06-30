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
        self.assertIn("hosted_vector_backend", blocked)
        self.assertIn("background_workers", blocked)
        self.assertIn("observability", blocked)
        self.assertIn("control_plane_scoped_tokens", blocked)

    def test_hosted_mode_still_blocks_without_runtime_control_plane_evidence(self) -> None:
        contract = hosted_readiness_contract(
            replace(
                self.settings,
                shard_mode="bucket",
                require_scoped_api_tokens=True,
                public_base_url="https://api.cortex.example",
                sync_signing_key="sync-signing-key",
                hosted_vector_backend="pgvector",
                worker_mode="external",
                observability_enabled=True,
                embedding_provider="openai",
            )
        )

        self.assertEqual(contract["status"], "blocked")
        blocked = {check["name"] for check in contract["checks"] if check["status"] == "blocked"}
        self.assertEqual(blocked, {"control_plane_scoped_tokens"})

    def test_hosted_mode_passes_when_10k_platform_controls_are_declared_and_proven(self) -> None:
        contract = hosted_readiness_contract(
            replace(
                self.settings,
                shard_mode="bucket",
                require_scoped_api_tokens=True,
                public_base_url="https://api.cortex.example",
                sync_signing_key="sync-signing-key",
                hosted_vector_backend="pgvector",
                worker_mode="external",
                observability_enabled=True,
                embedding_provider="openai",
            ),
            runtime={
                "control_plane": {
                    "active_api_tokens": 1,
                    "active_mcp_tokens": 1,
                    "active_users": 1,
                    "active_ready_users": 1,
                }
            },
        )

        self.assertEqual(contract["status"], "ok")
        self.assertTrue(contract["hosted_mode"])
        self.assertEqual(contract["global_token_user_switching"], "blocked")
        self.assertTrue(all(check["status"] == "ok" for check in contract["checks"]))


if __name__ == "__main__":
    unittest.main()
