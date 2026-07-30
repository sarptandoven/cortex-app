from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from fastapi import HTTPException

from backend.app import main as main_module


class HostedTrustBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_settings = main_module.settings
        main_module.settings = replace(
            self.original_settings,
            shard_mode="user",
            require_scoped_api_tokens=True,
        )

    def tearDown(self) -> None:
        main_module.settings = self.original_settings

    def test_hosted_mode_rejects_server_filesystem_operations(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            main_module._require_local_filesystem_access("Path-based import")

        self.assertEqual(caught.exception.status_code, 403)
        self.assertIn("local mode", str(caught.exception.detail))

    def test_hosted_mode_rejects_custom_credential_bearing_origin(self) -> None:
        with self.assertRaises(HTTPException) as caught:
            main_module._require_hosted_connector_origin("slack", "http://127.0.0.1:8080")

        self.assertEqual(caught.exception.status_code, 422)
        self.assertIn("Custom slack API origins", str(caught.exception.detail))

    def test_hosted_mode_accepts_the_official_connector_origin(self) -> None:
        main_module._require_hosted_connector_origin("slack", "https://slack.com/api/")
        main_module._require_hosted_connector_origin("github", None)

    def test_hosted_mode_accepts_only_official_oauth_token_endpoints(self) -> None:
        main_module._require_hosted_oauth_token_endpoint(
            "gmail",
            "https://oauth2.googleapis.com/token",
        )
        main_module._require_hosted_oauth_token_endpoint("notion", None)

        with self.assertRaises(HTTPException) as caught:
            main_module._require_hosted_oauth_token_endpoint(
                "outlook",
                "http://169.254.169.254/latest/meta-data",
            )
        self.assertEqual(caught.exception.status_code, 422)

    def test_hosted_jira_accepts_atlassian_cloud_only(self) -> None:
        main_module._require_hosted_jira_cloud_origin("https://acme.atlassian.net")

        for unsafe in (
            "http://acme.atlassian.net",
            "https://127.0.0.1",
            "https://acme.example.com",
            "https://user:pass@acme.atlassian.net",
            "https://acme.atlassian.net:8443",
        ):
            with self.subTest(unsafe=unsafe), self.assertRaises(HTTPException):
                main_module._require_hosted_jira_cloud_origin(unsafe)

    def test_local_mode_preserves_desktop_and_test_overrides(self) -> None:
        main_module.settings = replace(self.original_settings, shard_mode="local")

        main_module._require_local_filesystem_access("Path-based import")
        main_module._require_hosted_connector_origin("slack", "http://127.0.0.1:8080")
        main_module._require_hosted_jira_cloud_origin("http://localhost:8080")

    def test_sharded_store_enforces_policy_below_http_routes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            hosted_settings = replace(
                self.original_settings,
                db_path=root / "control.sqlite",
                vault_path=root / "control.vault",
                shard_root=root / "shards",
                shard_mode="user",
                require_scoped_api_tokens=True,
            )
            registry = main_module.StoreRegistry.from_settings(hosted_settings)
            hosted_store = registry.store_for_user("alice")

            self.assertTrue(hosted_store.hosted_mode)
            with self.assertRaisesRegex(ValueError, "Custom slack API origins"):
                hosted_store.sync_slack_account(
                    "alice",
                    token="xoxb-test",
                    channels=["C123"],
                    api_base_url="http://169.254.169.254/latest/meta-data",
                )
            with self.assertRaisesRegex(ValueError, "local mode"):
                hosted_store.sync_obsidian_vault("alice", vault_path="/etc")
            with self.assertRaisesRegex(ValueError, "local mode"):
                hosted_store.sync_calendar_account("alice", feed_url="http://127.0.0.1/private.ics")
            with self.assertRaisesRegex(ValueError, "OAuth token endpoints"):
                hosted_store._source_credential_payload_with_fresh_oauth_token(
                    "alice",
                    {"id": "account-1", "source": "gmail"},
                    {
                        "access_token": "expired",
                        "access_token_expires_at": "2000-01-01T00:00:00Z",
                        "refresh_token": "refresh-secret",
                        "client_id": "client-id",
                        "token_endpoint": "http://169.254.169.254/latest/meta-data",
                    },
                )


if __name__ == "__main__":
    unittest.main()
