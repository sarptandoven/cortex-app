"""Contract tests for external AI connections: every tool gets a REAL, LIVE action.

The moat these pin: Cortex holds the user's memory and serves it LIVE, on demand; it is
never copied out. So the connection paths are all live links, and the anti-moat "copy a
memory pack blob into the chat" path is gone:

- every integration declares a connection kind (mcpConfig / mcpDeeplink / cliCommand /
  remoteMCP / httpAPI) and the card renders a matching LIVE action, never a data-blob copy,
- Cursor and VS Code connect via native one-click MCP install DEEPLINKS,
- Claude Desktop et al. connect via config-write + auto-relaunch (mcpConfig),
- ChatGPT and Claude web (which can't run a local server) connect via a LIVE remote
  connector (hosted /mcp URL + scoped token), never a copied-out memory blob,
- CLI tools (Claude Code) get the exact terminal command with a scoped token,
- self-hosted stacks get base URL + scoped token + schema endpoints.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORTEX_APP = ROOT / "macos" / "Sources" / "CortexApp.swift"
CONNECTIONS_SHEET = ROOT / "macos" / "Sources" / "ConnectionsPrivacySheet.swift"


class ConnectionKindContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CORTEX_APP.read_text(encoding="utf-8")

    def test_connection_kind_enum_covers_all_paths(self) -> None:
        self.assertIn("enum IntegrationConnectionKind", self.source)
        for kind in ("mcpConfig", "mcpDeeplink", "cliCommand", "remoteMCP", "httpAPI"):
            with self.subTest(kind=kind):
                self.assertIn(f"case {kind}", self.source)

    def test_every_integration_action_dispatches_on_kind(self) -> None:
        self.assertIn("func connectIntegration(_ integration: AIIntegration)", self.source)
        self.assertIn("switch integration.connectionKind", self.source)

    def test_no_reference_only_dead_ends(self) -> None:
        self.assertNotIn("Reference only. Direct connections will appear here", self.source)
        # The badge no longer paints purple "Reference" chips.
        self.assertNotIn('"Reference"', self.source)

    def test_browser_assistants_are_live_remote_connectors(self) -> None:
        # ChatGPT and Claude web can't run a local MCP server, so they get a LIVE remote
        # connector (hosted /mcp URL + scoped token) — never a copied-out memory blob.
        chatgpt = re.search(r'id: "chatgpt".*?\),\n', self.source, re.S)
        self.assertIsNotNone(chatgpt)
        self.assertIn("connectionKind: .remoteMCP", chatgpt.group(0))
        claude_web = re.search(r'id: "claude-web".*?\),\n', self.source, re.S)
        self.assertIsNotNone(claude_web)
        self.assertIn("connectionKind: .remoteMCP", claude_web.group(0))

    def test_cli_tools_carry_command_templates(self) -> None:
        claude_code = re.search(r'id: "claude-code".*?\),\n', self.source, re.S)
        self.assertIsNotNone(claude_code)
        self.assertIn("connectionKind: .cliCommand", claude_code.group(0))
        self.assertIn("claude mcp add-json cortex", claude_code.group(0))

    def test_deeplink_tools_open_native_installer(self) -> None:
        # Cursor and VS Code support native one-click MCP install DEEPLINKS: Cortex opens the
        # URL and the app prompts to install. No file write, no paste, no manual restart.
        for tool_id in ("cursor", "vscode-copilot"):
            with self.subTest(tool=tool_id):
                entry = re.search(rf'id: "{tool_id}".*?\),\n', self.source, re.S)
                self.assertIsNotNone(entry)
                self.assertIn("connectionKind: .mcpDeeplink", entry.group(0))
        self.assertIn("func mcpInstallDeeplink(", self.source)
        self.assertIn("cursor://anysphere.cursor-deeplink/mcp/install", self.source)
        self.assertIn("vscode:mcp/install", self.source)

    def test_local_stacks_use_http_api(self) -> None:
        for tool_id in ("lm-studio", "open-webui", "librechat", "anythingllm"):
            with self.subTest(tool=tool_id):
                entry = re.search(rf'id: "{tool_id}".*?\),\n', self.source, re.S)
                self.assertIsNotNone(entry)
                self.assertIn("connectionKind: .httpAPI", entry.group(0))


class MemoryPackContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CORTEX_APP.read_text(encoding="utf-8")

    def test_memory_pack_uses_context_engine(self) -> None:
        # The pack must come from the same cited context pack MCP clients get, not an
        # ad-hoc export.
        self.assertIn("func copyMemoryPack(", self.source)
        self.assertIn("/v1/context-pack", self.source)

    def test_memory_pack_includes_assistant_instructions(self) -> None:
        self.assertIn("high-priority context", self.source)
        self.assertIn("Cite it when you rely on it", self.source)

    def test_memory_pack_handles_empty_memory_honestly(self) -> None:
        self.assertIn("No approved memory to pack yet", self.source)

    def test_cli_command_embeds_scoped_token(self) -> None:
        self.assertIn("func copyCLICommand(", self.source)
        self.assertIn("registerMCPTokenInBackground(for: integration)", self.source)
        self.assertIn("{CONFIG}", self.source)

    def test_http_api_details_include_schema_endpoints(self) -> None:
        self.assertIn("func copyHTTPAPIDetails(", self.source)
        self.assertIn("/v1/tools/schema", self.source)
        self.assertIn("/v1/tools/call", self.source)


class IntegrationCardContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CORTEX_APP.read_text(encoding="utf-8")

    def test_cards_render_kind_specific_actions(self) -> None:
        self.assertIn("private var actionTitle: String", self.source)
        self.assertIn("Copy connect command", self.source)
        self.assertIn("Copy API details", self.source)
        # Live one-click actions replace the old data-blob "Copy memory pack".
        self.assertIn("Install in ", self.source)
        self.assertIn("Add \\(DistributionMode.appDisplayName) to ", self.source)

    def test_full_catalog_is_visible_not_only_installable(self) -> None:
        # The category grid must not filter to supportsInstall-only tools.
        match = re.search(
            r"private func integrations\(in category: IntegrationCategory\).*?\n    \}",
            self.source,
            re.S,
        )
        self.assertIsNotNone(match)
        filter_lines = [
            line for line in match.group(0).splitlines()
            if "filter" in line
        ]
        self.assertTrue(filter_lines)
        for line in filter_lines:
            self.assertNotIn("supportsInstall", line)

    def test_badge_names_the_connection_path(self) -> None:
        self.assertIn('"Terminal"', self.source)
        self.assertIn('"Local API"', self.source)
        # Live-connection badges: one-click deeplink + remote connector. No "Copy & paste".
        self.assertIn('"One click"', self.source)
        self.assertIn('"Connector"', self.source)


class ConnectionsSheetExternalAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

    def test_browser_assistant_row_routes_to_live_connector(self) -> None:
        # The web-chat row no longer copies a memory blob out of Cortex; it opens the
        # live-connector wizard so ChatGPT/Claude web read memory on demand, in place.
        self.assertIn("browserAssistantRow", self.source)
        self.assertIn("Add \\(DistributionMode.appDisplayName) as a live connector", self.source)
        self.assertIn("never copied out", self.source)
        self.assertIn("state.presentConnectToolsWizard()", self.source)
        self.assertNotIn("state.copyMemoryPack()", self.source)


if __name__ == "__main__":
    unittest.main()
