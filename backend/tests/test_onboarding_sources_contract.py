"""Contract for the onboarding "connect an app" source grid (macos/Sources/OnboardingView.swift).

Beat 3 of onboarding surfaces a curated set of one-click import sources so a new user whose data
lives in Notion/Google/GitHub can connect right there instead of digging through Connections. The
grid is data-driven off the SAME connector catalog the Connections library uses, and each tile
dispatches on the SAME connect path (device flow / managed OAuth). These tests pin, at the source
level, that the catalog keeps exposing those connectors with a real connect path — and that the
Swift grid list and the backend catalog can't silently drift apart.
"""
from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore

# The curated onboarding priority set (must match OnboardingAddMemoryStep.onboardingSourceIDs).
ONBOARDING_SOURCE_IDS = ["notion", "gmail", "google-drive", "github"]

_ONBOARDING_SWIFT = (
    Path(__file__).resolve().parents[2] / "macos" / "Sources" / "OnboardingView.swift"
)


class OnboardingCatalogContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "m.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")
        self.catalog = {c["id"]: c for c in self.store.source_connector_catalog()}

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_priority_connectors_are_in_the_catalog(self) -> None:
        for cid in ONBOARDING_SOURCE_IDS:
            self.assertIn(cid, self.catalog, f"onboarding source {cid!r} missing from the catalog")

    def test_every_onboarding_source_has_a_real_connect_path(self) -> None:
        # The Swift grid dispatches: device flow (github) OR managed OAuth (notion/google). Every
        # tile must advertise ONE of those, or it would render a dead button.
        for cid in ONBOARDING_SOURCE_IDS:
            setup = self.catalog[cid].get("connection_setup") or {}
            has_device_flow = bool(setup.get("device_flow_provider"))
            has_managed_oauth = bool(setup.get("managed_oauth_shipped")) and bool(setup.get("oauth_provider"))
            self.assertTrue(
                has_device_flow or has_managed_oauth,
                f"{cid} has neither device flow nor managed OAuth — the onboarding tile would be dead",
            )

    def test_github_is_the_secretless_device_flow_tile(self) -> None:
        setup = self.catalog["github"].get("connection_setup") or {}
        self.assertEqual(setup.get("device_flow_provider"), "github")
        self.assertTrue(setup.get("device_flow_start_endpoint"))
        self.assertTrue(setup.get("device_flow_poll_endpoint"))

    def test_google_connectors_route_to_the_google_provider(self) -> None:
        # gmail + google-drive are the Google import sources; both go through the google OAuth
        # provider (which the hosted broker now centralizes — see oauth_broker.py "google" spec).
        for cid in ("gmail", "google-drive"):
            setup = self.catalog[cid].get("connection_setup") or {}
            self.assertTrue(setup.get("managed_oauth_shipped"), f"{cid} managed OAuth not shipped")
            self.assertEqual(setup.get("oauth_provider"), "google", cid)

    def test_notion_is_a_managed_oauth_tile(self) -> None:
        setup = self.catalog["notion"].get("connection_setup") or {}
        self.assertTrue(setup.get("managed_oauth_shipped"))
        self.assertEqual(setup.get("oauth_provider"), "notion")


class OnboardingSwiftGridContractTests(unittest.TestCase):
    """Pin the Swift grid at the source level so the app's list and the backend catalog stay aligned."""

    def setUp(self) -> None:
        self.assertTrue(_ONBOARDING_SWIFT.exists(), f"missing {_ONBOARDING_SWIFT}")
        self.swift = _ONBOARDING_SWIFT.read_text(encoding="utf-8")

    def test_grid_declares_exactly_the_priority_source_ids(self) -> None:
        m = re.search(r"onboardingSourceIDs\s*=\s*\[([^\]]*)\]", self.swift)
        self.assertIsNotNone(m, "onboardingSourceIDs list not found in OnboardingView.swift")
        ids = re.findall(r'"([^"]+)"', m.group(1))
        self.assertEqual(ids, ONBOARDING_SOURCE_IDS, "Swift grid IDs drifted from the contract")

    def test_grid_reuses_the_shared_connect_dispatch(self) -> None:
        # The tile must call the SAME AppState methods the Connections library uses — one connect
        # path, no divergence.
        self.assertIn("state.startGitHubDeviceFlow(connector)", self.swift)
        self.assertIn("state.startManagedOAuthConnector(connector)", self.swift)
        # And gate managed-OAuth tiles on real configuration (no dead buttons).
        self.assertIn("state.managedOAuthIsConfigured(connector)", self.swift)


if __name__ == "__main__":
    unittest.main()
