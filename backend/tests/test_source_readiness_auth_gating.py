from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore


class SourceReadinessAuthGatingTests(unittest.TestCase):
    """Regression for the false-"connected" bug: a source account that was created but never
    signed into (status "available" / auth_state "not_configured") must NOT report as connected.
    The user hit this — the app said "connected" for a source they had not authenticated."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "readiness-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _github_row(self) -> dict:
        report = self.store.source_readiness_report(self.user_id)
        row = next((r for r in report["sources"] if r["source"] == "github"), None)
        self.assertIsNotNone(row, "github row must be present in the readiness report")
        return report, row

    def test_placeholder_account_is_not_connected(self) -> None:
        # A placeholder account: created, but no credential stored and never signed into.
        self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="octocat",
            status="available",
            auth_state="not_configured",
        )
        report, row = self._github_row()
        self.assertNotEqual(row["status"], "connected", row)
        self.assertEqual(row["connected_accounts"], 0, row)
        self.assertEqual(report["summary"]["connected"], 0, report["summary"])

    def test_authenticated_account_is_connected(self) -> None:
        self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="octocat",
            status="connected",
            auth_state="healthy",
        )
        report, row = self._github_row()
        self.assertEqual(row["status"], "connected", row)
        self.assertEqual(row["connected_accounts"], 1, row)
        self.assertEqual(report["summary"]["connected"], 1, report["summary"])

    def test_mixed_accounts_count_only_authenticated(self) -> None:
        # One real account + one placeholder on the same source: the source is connected, but the
        # connected count must be 1 (the authenticated one), never 2.
        self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="real",
            account_identifier="real@example.com",
            status="connected",
            auth_state="healthy",
        )
        self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="placeholder",
            account_identifier="placeholder@example.com",
            status="available",
            auth_state="not_configured",
        )
        report, row = self._github_row()
        self.assertEqual(row["status"], "connected", row)
        self.assertEqual(row["accounts"], 2, row)  # both are active (not disconnected)
        self.assertEqual(row["connected_accounts"], 1, row)  # but only one is authenticated
        self.assertEqual(report["summary"]["connected"], 1, report["summary"])


if __name__ == "__main__":
    unittest.main()
