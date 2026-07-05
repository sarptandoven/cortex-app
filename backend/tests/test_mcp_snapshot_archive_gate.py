from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore


class SnapshotArchiveGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=Path(self.tmp.name) / "vault")
        self.user_id = "snapshot-archive-user"
        # Enable the Trust-controls layer for agent write/maintenance so the
        # remaining gate under test is purely the token-scope check, matching
        # how sync_source_records is guarded.
        self.store.update_settings(
            self.user_id,
            {"allow_agent_writes": True, "allow_agent_maintenance": True},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_direct_connector_complete_snapshot_requires_maintenance_scope(self) -> None:
        # A write-only token must not be able to archive prior memories via
        # complete_snapshot=true on a direct connector sync tool. If this gate
        # is missing, call_tool would reach sync_github_account and attempt a
        # real sync instead of raising.
        def _fail_if_called(*args: object, **kwargs: object) -> dict[str, object]:
            raise AssertionError("sync_github_account must not run for a write-only snapshot")

        self.store.sync_github_account = _fail_if_called  # type: ignore[assignment]

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_github",
                {
                    "token": "gh-token",
                    "repositories": ["octocat/hello"],
                    "complete_snapshot": True,
                },
                token_scopes=["write"],
            )

    def test_direct_connector_complete_snapshot_allowed_with_maintenance_scope(self) -> None:
        # With maintenance scope the archival snapshot is permitted; stub the
        # underlying store method so no real sync happens.
        calls: list[dict[str, object]] = []

        def _record(user_id: str, **kwargs: object) -> dict[str, object]:
            calls.append(dict(kwargs))
            return {"synced": True}

        self.store.sync_github_account = _record  # type: ignore[assignment]

        result = call_tool(
            self.store,
            self.user_id,
            "sync_github",
            {
                "token": "gh-token",
                "repositories": ["octocat/hello"],
                "complete_snapshot": True,
            },
            token_scopes=["write", "maintenance"],
        )

        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0]["complete_snapshot"])
        self.assertEqual(result, {"synced": True})


if __name__ == "__main__":
    unittest.main()
