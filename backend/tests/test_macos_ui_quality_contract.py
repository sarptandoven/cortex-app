from __future__ import annotations

import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / "macos" / "Sources"
CORTEX_APP = SOURCES / "CortexApp.swift"
CORTEX_CLOUD_AUTH = SOURCES / "CortexCloudAuth.swift"
CITATION_DISPLAY = SOURCES / "CitationDisplay.swift"
MODEL_TAB = SOURCES / "ModelTab.swift"
REVIEW_TAB = SOURCES / "ReviewTab.swift"
MENU_PANEL = SOURCES / "MenuBarQuickPanel.swift"
CONNECTIONS = SOURCES / "ConnectionsPrivacySheet.swift"
EXTENSION_POPUP = ROOT / "extension" / "popup.html"
EXTENSION_OPTIONS = ROOT / "extension" / "options.html"
EXTENSION_CONTENT = ROOT / "extension" / "content.css"
SITE_STYLES = ROOT / "site" / "styles.css"


class MacOSWindowQualityContractTests(unittest.TestCase):
    def test_hosting_controller_cannot_resize_the_main_window_from_scroll_content(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")
        self.assertIn("hosting.sizingOptions = []", source)
        self.assertIn('setFrameAutosaveName("CortexMainWindowV3")', source)

    def test_primary_navigation_and_header_are_bounded_on_wide_windows(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")
        self.assertIn("Color.clear.frame(width: 72, height: 2.5)", source)
        self.assertIn(".frame(maxWidth: 720)", source)
        self.assertIn(".frame(maxWidth: 760)", source)

    def test_sheets_have_small_screen_minimums_and_desktop_ideals(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")
        self.assertIn("idealWidth: 760", source)
        self.assertIn("idealWidth: 780", source)
        self.assertIn("minHeight: 540", source)
        self.assertIn("minHeight: 560", source)
        self.assertIn(".frame(minWidth: 560, minHeight: 560)", CONNECTIONS.read_text(encoding="utf-8"))

    def test_export_picker_uses_modern_content_types(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")
        self.assertIn("panel.allowedContentTypes", source)
        self.assertNotIn("panel.allowedFileTypes", source)


class MacOSSignInQualityContractTests(unittest.TestCase):
    def test_required_sign_in_wall_teaches_sources_and_ai_tool_setup(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")

        self.assertIn("What happens after sign-in", source)
        self.assertIn("Connect a memory source: local notes, ChatGPT/Claude export", source)
        self.assertIn("Claude Desktop, ChatGPT", source)
        self.assertIn("ScrollView", source)

    def test_native_apple_button_is_full_width_and_entitlement_gated(self) -> None:
        # The Apple button renders ONLY when it can actually work: the build carries the
        # applesignin entitlement AND the hosted backend advertises apple as a configured
        # provider. (The old shape always rendered it and merely .disabled() it — a dead
        # button on ad-hoc builds, a confirmed audit defect.)
        source = CORTEX_CLOUD_AUTH.read_text(encoding="utf-8")

        self.assertIn("hasAppleSignInEntitlement", source)
        self.assertIn("com.apple.developer.applesignin", source)
        self.assertIn(".frame(maxWidth: .infinity, minHeight: 44, maxHeight: 44)", source)
        self.assertIn("canUseNativeAppleSignIn && backendOffersApple", source)
        # Provider-aware sign-in: a real labeled button per configured provider (never a
        # dead one), discovered from GET /v1/auth/providers.
        self.assertIn("loadCloudAuthProviders", source)
        self.assertIn("Sign in with GitHub", source)


class MacOSDisplayTextQualityContractTests(unittest.TestCase):
    def test_shared_text_normalizer_handles_escaped_controls_paths_and_ids(self) -> None:
        helper = CITATION_DISPLAY.read_text(encoding="utf-8")
        for symbol in ("normalizedProse", "displayProse", "suggestionSubject", "quotedFileNames"):
            with self.subTest(symbol=symbol):
                self.assertIn(symbol, helper)
        self.assertIn('.replacingOccurrences(of: "\\\\n", with: " ")', helper)
        self.assertIn("uuidPattern", helper)
        self.assertIn('return names.count == 1 ? "File:', helper)

    def test_high_traffic_surfaces_use_clean_display_prose(self) -> None:
        self.assertIn("MemoryText.displayProse(insight.headline", MODEL_TAB.read_text(encoding="utf-8"))
        self.assertIn("MemoryText.displayProse(text, maxLength: 280)", REVIEW_TAB.read_text(encoding="utf-8"))
        self.assertIn("MemoryText.displayProse(summary, maxLength: 180)", MENU_PANEL.read_text(encoding="utf-8"))
        self.assertIn("MemoryText.suggestionSubject(content)", CORTEX_APP.read_text(encoding="utf-8"))


class CrossSurfaceVisualQualityContractTests(unittest.TestCase):
    def test_extension_uses_the_archive_palette_not_the_old_purple_gradient(self) -> None:
        combined = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (EXTENSION_POPUP, EXTENSION_OPTIONS, EXTENSION_CONTENT)
        ).lower()
        self.assertIn("#8c3a2b", combined)
        self.assertIn("#f7f4ed", combined)
        self.assertNotIn("#6d5efc", combined)
        self.assertNotIn("#8b5cf6", combined)
        self.assertIn("focus-visible", combined)

    def test_website_is_mobile_keyboard_and_reduced_motion_safe(self) -> None:
        styles = SITE_STYLES.read_text(encoding="utf-8")
        self.assertIn("@media (max-width: 520px)", styles)
        self.assertIn("@media (prefers-reduced-motion: reduce)", styles)
        self.assertIn("focus-visible", styles)
        self.assertIn("--coral: #8c3a2b", styles)
        self.assertIn(".privacy-main h1", styles)


if __name__ == "__main__":
    unittest.main()
