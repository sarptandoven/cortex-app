from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CORTEX_APP = ROOT / "macos" / "Sources" / "CortexApp.swift"
CONNECTIONS_SHEET = ROOT / "macos" / "Sources" / "ConnectionsPrivacySheet.swift"


class MacOSConnectorUIContractTests(unittest.TestCase):
    def test_direct_connector_ui_only_allows_backend_wired_sources(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")
        match = re.search(
            r"private static let directConnectorSyncIDs:\s*Set<String>\s*=\s*\[(?P<body>.*?)\]",
            source,
            re.S,
        )
        self.assertIsNotNone(match)
        connector_ids = set(re.findall(r'"([^"]+)"', match.group("body")))

        self.assertEqual(
            connector_ids,
            {
                "calendar",
                "github",
                "jira",
                "linear",
                "notion",
                "raindrop",
                "readwise",
                "slack",
                "zotero",
            },
        )
        self.assertTrue({"gmail", "google-drive", "google-docs", "outlook", "microsoft-365"}.isdisjoint(connector_ids))

        self.assertIn("service_baseline", source)
        self.assertIn("hasNativeDirectSync", source)
        self.assertIn("isDirectConnectorSyncWired", source)
        self.assertIn('sync_plan?.mode == "planned_account_sync"', source)
        self.assertIn('primary_beta_path == "account-sign-in-planned"', source)

    def test_connections_sheet_does_not_promote_manual_or_fake_source_sync(self) -> None:
        source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

        self.assertIn("Other source connections", source)
        self.assertIn("Planned sign-in services stay hidden until they are real.", source)
        self.assertIn("state.isDirectConnectorSyncWired", source)
        self.assertNotIn("Advanced source sync", source)

        display_text = source.lower()
        for forbidden in (
            "manual import",
            "file upload",
            "choose file",
            "choose folder",
            "copy context",
            "copy-context",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, display_text)


if __name__ == "__main__":
    unittest.main()
