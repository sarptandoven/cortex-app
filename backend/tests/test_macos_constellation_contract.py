"""Contract tests for the Constellation (memory map) redesign.

The user-facing complaint these pin: the constellation was an unreadable ring of dots —
no way to zoom, no way to see what connects to what, labels colliding, and a legend that
didn't match node colors. These tests keep the readability mechanics from regressing:

- force-directed layout (proximity = relatedness), deterministic (no randomness),
- zoom + pan camera with on-canvas controls,
- selection focus mode that highlights the selected node's edges and names them,
- a zoom-scaled label budget with collision-checked labels,
- an honest, clickable legend driven by the backend's community labels,
- the old duplicate ring-layout GraphCanvas stays deleted.

Source-level checks (like the other macOS UI contracts) because SwiftUI has no
headless test harness in this repo; the strings asserted here are structural, not copy.
"""

from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MEMORY_MAP = ROOT / "macos" / "Sources" / "MemoryMapView.swift"
CORTEX_APP = ROOT / "macos" / "Sources" / "CortexApp.swift"


class ConstellationLayoutContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MEMORY_MAP.read_text(encoding="utf-8")

    def test_layout_is_force_directed_not_radial_ring(self) -> None:
        # Springs along edges + grid repulsion are what make proximity mean relatedness.
        self.assertIn("struct Spring", self.source)
        self.assertIn("repulsion", self.source.lower())
        self.assertIn("springs.append", self.source)
        # Community members gravitate to their centroid so clusters read as clusters.
        self.assertIn("centroids", self.source)

    def test_layout_is_deterministic(self) -> None:
        # Seeded from the stable FNV-1a id hash; fixed iteration count; no randomness.
        self.assertIn("stableHash", self.source)
        self.assertIn("let iterations", self.source)
        self.assertNotIn("random(", self.source)
        self.assertNotIn(".random", self.source)

    def test_layout_is_memoized_for_render_performance(self) -> None:
        # Hover/zoom re-render every frame; the simulation must not re-run each time.
        self.assertIn("cachedLayout", self.source)
        self.assertIn("static func layout(", self.source)
        self.assertIn("MemoryMapLayout.layout(", self.source)

    def test_overlap_relaxation_separates_touching_nodes(self) -> None:
        self.assertIn("Overlap relaxation", self.source)


class ConstellationCameraContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MEMORY_MAP.read_text(encoding="utf-8")

    def test_canvas_supports_zoom_and_pan(self) -> None:
        self.assertIn("MagnificationGesture", self.source)
        self.assertIn("DragGesture", self.source)
        self.assertIn("zoomScale", self.source)
        self.assertIn("panOffset", self.source)

    def test_zoom_controls_are_discoverable_buttons(self) -> None:
        # Pinch alone is not discoverable on a Mac.
        self.assertIn("plus.magnifyingglass", self.source)
        self.assertIn("minus.magnifyingglass", self.source)
        self.assertIn("Reset view", self.source)

    def test_hit_testing_accounts_for_camera(self) -> None:
        # Taps and hovers must convert screen->layout space or selection breaks when zoomed.
        self.assertIn("func layoutPoint(", self.source)
        self.assertIn("tolerance: 12 / effectiveZoom", self.source)

    def test_label_budget_grows_with_zoom(self) -> None:
        self.assertIn("topLabelNodeIDs(budget:", self.source)
        self.assertIn("effectiveZoom", self.source)


class ConstellationConnectionsContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MEMORY_MAP.read_text(encoding="utf-8")

    def test_selection_enters_focus_mode(self) -> None:
        # Selected node's edges bright, everything else receded — the "see connections" fix.
        # A node's direct neighbors drive the focus lens; the same computation now also powers
        # the Obsidian-style hover-highlight (hover a node -> its neighborhood lights up).
        self.assertIn("directNeighbors", self.source)
        self.assertIn("nodeEmphasis", self.source)
        self.assertIn("edgeIsHighlighted", self.source)
        # Selection reads full-strength accent edges (a hover-only focus is a touch softer).
        self.assertIn("CortexDesign.accent.opacity(strength)", self.source)
        self.assertIn("selectedNodeID != nil ? 0.85", self.source)

    def test_detail_panel_names_every_map_connection(self) -> None:
        # Non-entity nodes must not leave highlighted edges unexplained.
        self.assertIn("NodeEdgeSummary", self.source)
        self.assertIn("selectedEdgeSummaries", self.source)
        self.assertIn("CONNECTED ON THE MAP", self.source)

    def test_detail_panel_names_the_cluster(self) -> None:
        self.assertIn("communityName", self.source)
        self.assertIn("cluster", self.source)


class ConstellationLegendContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = MEMORY_MAP.read_text(encoding="utf-8")

    def test_legend_uses_backend_community_labels_when_present(self) -> None:
        # Node color is community-driven when analysis exists, so the legend must be too.
        self.assertIn("communityLegendEntries", self.source)
        self.assertIn("community_labels", self.source)

    def test_legend_chips_spotlight_on_click(self) -> None:
        self.assertIn("MapSpotlight", self.source)
        self.assertIn("legendChip", self.source)

    def test_composition_line_reports_real_counts(self) -> None:
        self.assertIn("compositionLine", self.source)
        self.assertIn("connection", self.source)


class DuplicateGraphViewRemovalTests(unittest.TestCase):
    def test_old_ring_layout_graphcanvas_is_gone(self) -> None:
        # There must be exactly one constellation implementation.
        app_source = CORTEX_APP.read_text(encoding="utf-8")
        self.assertNotIn("struct GraphCanvas", app_source)
        self.assertIn("MemoryMapView(state: state, canvasHeight: 260)", app_source)


if __name__ == "__main__":
    unittest.main()
