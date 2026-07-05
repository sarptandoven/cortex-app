from __future__ import annotations

import unittest

from backend.app.storage import BASELINE_10K_CONNECTOR_IDS
from scripts import check_connector_baseline


class ConnectorBaselineCheckTests(unittest.TestCase):
    def test_connector_module_name_normalizes_ids(self) -> None:
        self.assertEqual(check_connector_baseline.connector_module_name("google-drive"), "google_drive")
        self.assertEqual(check_connector_baseline.connector_module_name("gmail"), "gmail")

    def test_expected_fetch_symbol_matches_obsidian_and_api_connectors(self) -> None:
        self.assertEqual(check_connector_baseline.expected_fetch_symbol("obsidian"), "scan_obsidian_vault")
        self.assertEqual(check_connector_baseline.expected_fetch_symbol("google-drive"), "fetch_google_drive_records")

    def test_scheduler_probes_cover_local_and_credential_sync_paths(self) -> None:
        self.assertEqual(
            [probe["label"] for probe in check_connector_baseline.scheduler_probe_accounts("obsidian")],
            ["local_vault_path"],
        )
        self.assertEqual(
            [probe["label"] for probe in check_connector_baseline.scheduler_probe_accounts("github")],
            ["local_credential_ref"],
        )
        self.assertEqual(
            [probe["label"] for probe in check_connector_baseline.scheduler_probe_accounts("zotero")],
            ["local_credential_ref", "local_zotero_api"],
        )

    def test_baseline_checker_passes_current_connector_contract(self) -> None:
        payload = check_connector_baseline.check_baseline()

        self.assertEqual(payload["status"], "ok", payload["errors"])
        self.assertEqual(payload["baseline_count"], len(BASELINE_10K_CONNECTOR_IDS))
        self.assertGreaterEqual(payload["baseline_count"], 10)
        self.assertEqual({item["id"] for item in payload["connectors"]}, set(BASELINE_10K_CONNECTOR_IDS))
        self.assertTrue(all(item["scheduler_supported"] for item in payload["connectors"]))
        self.assertTrue(all(item["scheduler_probes"] for item in payload["connectors"]))


if __name__ == "__main__":
    unittest.main()
