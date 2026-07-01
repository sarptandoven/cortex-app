from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore


class ConnectorCatalogAcceptanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "catalog-acceptance-user"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_first_100_catalog_only_promotes_real_primary_connectors(self) -> None:
        catalog = {item["id"]: item for item in self.store.source_connector_catalog()}
        readiness = self.store.source_readiness_report(self.user_id)
        readiness_by_source = {item["source"]: item for item in readiness["sources"]}

        primary_catalog_ids = {source_id for source_id, item in catalog.items() if item["show_in_primary_ui"]}
        primary_readiness_ids = {
            source_id for source_id, item in readiness_by_source.items() if item["show_in_primary_ui"]
        }

        self.assertEqual(primary_catalog_ids, {"obsidian"})
        self.assertEqual(primary_readiness_ids, {"obsidian"})
        self.assertEqual(catalog["obsidian"]["beta_status"], "ready")
        self.assertEqual(catalog["obsidian"]["primary_beta_path"], "native-local-connector")
        self.assertEqual(catalog["obsidian"]["live_status"], "import_ready")
        self.assertTrue(catalog["obsidian"]["supports_import"])

        for source_id in ("gmail", "outlook", "notion", "slack", "github", "google-drive"):
            with self.subTest(source_id=source_id):
                catalog_entry = catalog[source_id]
                readiness_entry = readiness_by_source[source_id]

                self.assertFalse(catalog_entry["show_in_primary_ui"])
                self.assertFalse(catalog_entry["primary_beta"])
                self.assertFalse(readiness_entry["show_in_primary_ui"])
                self.assertFalse(readiness_entry["primary_beta"])

                self.assertIn(catalog_entry["live_status"], {"api_token", "local_api", "local_only"})
                self.assertEqual(catalog_entry["beta_status"], "ready")
                self.assertTrue(catalog_entry["scopes"] or catalog_entry["formats"])
                self.assertNotEqual(readiness_entry["sync_plan"]["mode"], "planned_account_sync")

        for source_id, item in catalog.items():
            with self.subTest(primary_capability_source_id=source_id):
                if not item["show_in_primary_ui"]:
                    continue
                self.assertEqual(item["beta_status"], "ready")
                self.assertIn(item["primary_beta_path"], {"native-local-connector", "connected-source-account"})
                self.assertIn(item["live_status"], {"import_ready", "local_api", "local_only"})
                self.assertTrue(item["supports_import"])

    def test_source_readiness_action_text_does_not_advertise_manual_intake(self) -> None:
        readiness = self.store.source_readiness_report(self.user_id)
        display_text = "\n".join(
            str(value)
            for item in readiness["sources"]
            for value in (
                item.get("next_action"),
                item.get("first_100_note"),
                *(item.get("permissions_required") or []),
            )
            if value
        ).lower()

        for forbidden in (
            "manual upload",
            "manual import",
            "copy-context",
            "copy context",
            "copy/paste context",
            "file upload",
            "choose file",
            "choose folder",
            "selected export",
            "selected files",
            "user-selected",
            "takeout",
            "upload",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, display_text)


if __name__ == "__main__":
    unittest.main()
