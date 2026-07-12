from __future__ import annotations

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
CORTEX_APP = ROOT / "macos" / "Sources" / "CortexApp.swift"
CONNECTIONS_SHEET = ROOT / "macos" / "Sources" / "ConnectionsPrivacySheet.swift"
INFO_PLIST = ROOT / "macos" / "Info.plist"
PRIMARY_UI_SURFACES = (
    ROOT / "macos" / "Sources" / "AskTab.swift",
    ROOT / "macos" / "Sources" / "ModelTab.swift",
    ROOT / "macos" / "Sources" / "OnboardingView.swift",
    ROOT / "macos" / "Sources" / "ReviewTab.swift",
    ROOT / "macos" / "Sources" / "SourceConnectionComponents.swift",
)


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
                "gmail",
                "github",
                "google-drive",
                "jira",
                "linear",
                "notion",
                "outlook",
                "raindrop",
                "readwise",
                "slack",
                "zotero",
            },
        )
        self.assertTrue({"google-docs", "microsoft-365"}.isdisjoint(connector_ids))

        self.assertIn("service_baseline", source)
        self.assertIn("hasNativeDirectSync", source)
        self.assertIn("isDirectConnectorSyncWired", source)
        self.assertIn('sync_plan?.mode == "planned_account_sync"', source)
        self.assertIn('primary_beta_path == "account-sign-in-planned"', source)

    def test_connections_sheet_does_not_promote_manual_or_fake_source_sync(self) -> None:
        source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

        self.assertIn("Connections library", source)
        # Planned/fake sign-in services must still not be promoted as connectable: the library is
        # built from wiredConnectors and planned ones are summarized separately, never listed.
        self.assertIn("planned sign-in", source)
        self.assertIn("state.isDirectConnectorSyncWired", source)
        self.assertIn("state.pauseDirectConnectorSync", source)
        self.assertIn("state.resumeDirectConnectorSync", source)
        self.assertIn("state.startManagedOAuthConnector(connector)", source)
        self.assertIn("managedOAuthProviderName", source)
        self.assertIn("Sign in with \\(managedOAuthProviderName)", source)
        self.assertIn("Already synced local memory stays available", source)
        self.assertIn("state.discoverDirectConnectorOptions", source)
        self.assertIn("Find \\(field.displayLabel)", source)
        self.assertIn("Select the \\(field.displayLabel.lowercased()) Cortex should keep synced.", source)
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

    def test_direct_connector_sync_status_explains_snapshot_archive_safety(self) -> None:
        source = CORTEX_APP.read_text(encoding="utf-8")

        self.assertIn("SourceArchiveMissingDecision", source)
        self.assertIn("archive_missing_decision", source)
        self.assertIn("archive_missing_suppressed", source)
        self.assertIn("archived_missing", source)
        self.assertIn("directConnectorSyncMessage", source)
        self.assertIn("archiveSuppressionReason", source)
        self.assertIn("Archived \\(archived) stale item", source)
        self.assertIn("Kept older memory because", source)
        self.assertIn("sync was incomplete", source)
        self.assertIn("more source pages remain", source)
        self.assertIn("the sync reached \\(maxRecords) items", source)

    def test_managed_oauth_is_provider_gated_and_not_token_setup_first(self) -> None:
        app_source = CORTEX_APP.read_text(encoding="utf-8")
        sheet_source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

        self.assertIn("code_verifier", app_source)
        self.assertIn("code_challenge", app_source)
        self.assertIn('body["code_challenge_method"] = "S256"', app_source)
        self.assertIn("CortexGoogleOAuthClientID", app_source)
        self.assertIn("CortexMicrosoftOAuthClientID", app_source)
        self.assertIn("CortexMicrosoftOAuthClientSecret", app_source)
        self.assertIn("CORTEX_MICROSOFT_OAUTH_CLIENT_ID", app_source)
        self.assertIn("CORTEX_OUTLOOK_OAUTH_CLIENT_ID", app_source)
        self.assertIn("CortexNotionOAuthClientID", app_source)
        self.assertIn("CortexNotionOAuthClientSecret", app_source)
        plist_source = INFO_PLIST.read_text(encoding="utf-8")
        self.assertIn("CortexMicrosoftOAuthClientID", plist_source)
        self.assertIn("CortexMicrosoftOAuthClientSecret", plist_source)
        self.assertIn("supportsManagedOAuth", app_source)
        self.assertIn('["google", "microsoft", "notion"].contains(provider)', app_source)
        self.assertIn('["notion", "microsoft"].contains(provider)', app_source)
        self.assertIn("hasManagedOAuth", sheet_source)
        self.assertIn("managedOAuthProviderName", sheet_source)
        self.assertIn('case "microsoft": return "Microsoft"', app_source)
        self.assertIn('case "microsoft": return "Microsoft"', sheet_source)
        self.assertIn('case "notion": return "Notion"', sheet_source)

        managed_oauth_branch = re.search(
            r"default:\s*\n\s*if hasManagedOAuth \{(?P<body>.*?)\} else if hasStoredConfig",
            sheet_source,
            re.S,
        )
        self.assertIsNotNone(managed_oauth_branch)
        assert managed_oauth_branch is not None
        branch_body = managed_oauth_branch.group("body")
        self.assertIn("startManagedOAuthConnector", branch_body)
        self.assertIn("syncConnectedSourceConnector", branch_body)
        self.assertNotIn("openTokenSetup", branch_body)
        self.assertNotIn("saveDirectConnectorPayload", branch_body)

    def test_primary_ui_surfaces_keep_first_source_notes_first(self) -> None:
        display_text = "\n".join(path.read_text(encoding="utf-8").lower() for path in PRIMARY_UI_SURFACES)

        self.assertIn("connect notes", display_text)
        # Notes-first onboarding copy (Obsidian is no longer named in the UI).
        self.assertIn("choose a local notes folder", display_text)
        for forbidden in (
            "direct service connections live",
            "manual import",
            "file upload",
            "copy memory brief",
            "copy context pack",
            "advanced source sync",
            "planned sign-in services",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, display_text)

    def test_connections_surface_keeps_ai_tool_setup_discoverable(self) -> None:
        source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

        self.assertIn("@State private var advancedExpanded = true", source)
        self.assertIn("AI tools & permissions", source)
        # The AI-apps group is now the hero of the sheet (top + open), titled for the use-your-memory
        # promise and naming the concrete tools so "connect to ChatGPT/Claude" is unmissable.
        self.assertIn("Use your memory in AI apps", source)
        self.assertIn("Connect Claude Desktop, ChatGPT, Cursor, and other AI apps", source)
        self.assertIn("Copy tool config", source)
        # Web chats (ChatGPT/Claude web) can't run a local server, so they reach memory through a
        # LIVE remote connector — never a copied-out blob. The sheet says so plainly.
        self.assertIn("ChatGPT and Claude web reach it through a live connector", source)
        self.assertNotIn("if detectedAIToolCount > 0 || state.connectedAIIntegrationCount > 0", source)

    def test_ask_surface_shows_memory_freshness_and_citation_confidence(self) -> None:
        source = (ROOT / "macos" / "Sources" / "AskTab.swift").read_text(encoding="utf-8")

        self.assertIn("AskMemoryContextStrip", source)
        self.assertIn("sourceReadinessReport", source)
        self.assertIn("latestSync", source)
        self.assertIn("citationConfidenceLabel", source)
        self.assertIn("Source health", source)
        self.assertIn("AskSourceConfidenceChip", source)
        self.assertIn("citation_coverage", source)
        self.assertIn("last_completed_at", source)

    def test_review_surface_shows_source_health_without_extra_primary_actions(self) -> None:
        source = (ROOT / "macos" / "Sources" / "ReviewTab.swift").read_text(encoding="utf-8")

        self.assertIn("ReviewSourceHealthStrip", source)
        self.assertIn("sourceReadinessReport", source)
        # (The UI-overhaul redesign folded the per-source label/latest-sync into
        # ReviewSourceHealthChip, which reads sourceReadinessReport directly; the old
        # sourceHealthLabel/latestSync computed props were contract-only and are gone.)
        self.assertIn("ReviewSourceHealthChip", source)
        self.assertIn("Approve useful items, archive noise", source)
        self.assertIn('Label("Approve", systemImage: "checkmark.seal")', source)
        self.assertIn('Label("Archive", systemImage: "archivebox")', source)
        # A bulk "Approve all N" clears the 99+ backlog the 10-at-a-time batch can't reach, but it
        # stays SECONDARY — "Approve N shown" keeps the single wax-red primary on the surface.
        self.assertIn('title: "Approve all \\(totalPendingCount)", systemImage: "checkmark.seal.fill", role: .secondary', source)
        self.assertIn('title: "Approve \\(approveBatch.count) shown", systemImage: "checkmark.seal", role: .primary', source)
        self.assertNotIn('title: "Approve all \\(totalPendingCount)", systemImage: "checkmark.seal.fill", role: .primary', source)
        self.assertNotIn("Archive all", source)

    def test_mas_integration_state_honours_copied_config_signal(self) -> None:
        """Regression: on Mac App Store builds the sandbox can't read another app's config file to
        verify a paste, so both the cached refresh AND the direct accessor must report a tool as
        configured when the user copied its setup config. A prior version hardcoded configured:false
        in integrationState(for:) (while refreshIntegrationStates set it from the copied signal),
        which left copied MAS tools "not connected" forever and made the Connect-an-app wizard's
        test/Finish gate unreachable. Pin that both MAS branches read the copied signal."""
        source = CORTEX_APP.read_text(encoding="utf-8")

        # Both the refresh path and the direct accessor derive `configured` from the copied signal.
        self.assertEqual(source.count("configured: copied"), 2)
        # The direct accessor must consult the copied-config set inside its App Store branch, not
        # return a hardcoded false.
        accessor = re.search(
            r"func integrationState\(for integration: AIIntegration\).*?\n    \}",
            source,
            re.S,
        )
        self.assertIsNotNone(accessor)
        accessor_body = accessor.group(0)
        self.assertIn("appStoreCopiedIntegrationIDs()", accessor_body)
        self.assertNotIn("configured: false", accessor_body)

    def test_home_source_status_reflects_attention_and_due_sync(self) -> None:
        source = (ROOT / "macos" / "Sources" / "ModelTab.swift").read_text(encoding="utf-8")

        self.assertIn("needsAttentionSources", source)
        self.assertIn("dueSyncSources", source)
        self.assertIn("Source needs attention", source)
        self.assertIn("Sync due", source)
        self.assertIn("Check source connection", source)
        self.assertIn("Sync connected sources", source)
        self.assertIn("sourceReadinessReport?.summary.needs_attention", source)
        self.assertIn("sync_plan?.due_now == true", source)

    def test_onboarding_source_gate_uses_source_health_not_connection_only(self) -> None:
        app_source = CORTEX_APP.read_text(encoding="utf-8")
        onboarding_source = (ROOT / "macos" / "Sources" / "OnboardingView.swift").read_text(encoding="utf-8")

        self.assertIn("onboardingHealthyMemorySources", app_source)
        self.assertIn("onboardingSourceHealthMessage", app_source)
        self.assertIn("onboardingSourceIsHealthyAndUsable", app_source)
        self.assertIn("sourceReadinessReport != nil", app_source)
        self.assertIn('source.status == "needs_attention" || source.status == "empty"', app_source)
        # Error states still block, but a due or still-running sync must NOT hold onboarding
        # hostage once usable data exists (a large first sync can run for minutes while earlier
        # batches are already citable).
        self.assertIn('"needs_attention", "backing_off"', app_source)
        self.assertIn('"needs_review", "synced", "imported", "connected", "syncing"', app_source)
        self.assertIn("return !onboardingHealthyMemorySources.isEmpty", app_source)

        # The guided onboarding gates on the HEALTH-derived AppState signals (onboardingHasSource
        # is computed from source health above, and notesNeedContent flags an empty/needs-content
        # source) rather than mere connection — so the walkthrough never advances on a source that
        # is connected-but-empty. (The old view-local sourceCardDetail/onboardingSourceHealthMessage
        # helpers were removed in the animated-onboarding overhaul; the gate now lives in AppState.)
        self.assertIn("onboardingHasSource", onboarding_source)
        self.assertIn("notesNeedContent", onboarding_source)

    def test_onboarding_walkthrough_can_actually_complete_the_setup_loop(self) -> None:
        """Every OnboardingStep gate must be satisfiable from inside the walkthrough. The last
        gate (`.trustBackup`) needs an explicit first-backup decision, so the closing beat must
        offer both createBackup and skipFirstBackup — otherwise finishOnboarding() always falls
        back to session-dismiss and onboarding completion never persists."""
        app_source = CORTEX_APP.read_text(encoding="utf-8")
        onboarding_source = (ROOT / "macos" / "Sources" / "OnboardingView.swift").read_text(encoding="utf-8")

        # The gate itself stays intact in AppState.
        self.assertIn("case .trustBackup:", app_source)
        self.assertIn("return onboardingHasBackupDecision", app_source)
        self.assertIn("func skipFirstBackup()", app_source)

        # The walkthrough renders a real decision: back up now, or explicitly skip.
        self.assertIn("state.createBackup()", onboarding_source)
        self.assertIn("state.skipFirstBackup()", onboarding_source)
        self.assertIn("onboardingHasBackupDecision", onboarding_source)

    def test_onboarding_add_memory_honors_the_export_drag_promise(self) -> None:
        """The add-memory beat promises "drag in a ChatGPT / Claude export" — so the step must
        actually accept a file drop and offer the export file picker, routed through the same
        AppState import paths Connections uses."""
        onboarding_source = (ROOT / "macos" / "Sources" / "OnboardingView.swift").read_text(encoding="utf-8")

        self.assertIn("ChatGPT / Claude export", onboarding_source)
        self.assertIn(".onDrop(of: [.fileURL]", onboarding_source)
        self.assertIn("state.importFromPath(path)", onboarding_source)
        self.assertIn("state.importAIChatExport()", onboarding_source)
        self.assertIn("state.importInFlight", onboarding_source)

    def test_obsidian_plugin_is_installed_and_configured_by_mac_app(self) -> None:
        app_source = CORTEX_APP.read_text(encoding="utf-8")

        self.assertIn("installObsidianPluginIfPossible", app_source)
        self.assertIn("installBundledObsidianPlugin", app_source)
        self.assertIn("registerObsidianPluginToken", app_source)
        self.assertIn('"obsidian-cortex-plugin"', app_source)
        self.assertIn('["manifest.json", "main.js", "versions.json"]', app_source)
        self.assertIn('"apiToken": apiToken', app_source)
        self.assertIn('"autoSyncOnStartup": true', app_source)
        self.assertIn('"maxRecords": 5000', app_source)
        self.assertIn("community-plugins.json", app_source)
        self.assertIn("enabledPlugins.append(Self.obsidianPluginID)", app_source)
        # The plugin-bridge status is user-facing, so it no longer names Obsidian.
        self.assertIn("Cortex bridge installed.", app_source)

    def test_connections_advanced_shows_mcp_permissions_and_audit_summary(self) -> None:
        source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

        self.assertIn("ConnectionsMCPAccessSection", source)
        self.assertIn("Tool permissions", source)
        self.assertIn("Connected AI tools can only use the permissions below", source)
        self.assertIn("allow_agent_reads", source)
        self.assertIn("allow_agent_writes", source)
        self.assertIn("allow_agent_exports", source)
        self.assertIn("allow_agent_maintenance", source)
        self.assertIn("activeMCPTokens", source)
        self.assertIn("recentToolEvents", source)
        self.assertIn("Recent tool activity", source)
        self.assertIn("resetMCPIntegrationToken", source)
        self.assertIn("No AI tool activity recorded yet.", source)
        self.assertIn("Recent activity is shown without raw memory content.", source)


if __name__ == "__main__":
    unittest.main()
