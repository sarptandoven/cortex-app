"""Contract tests for external AI connections: every tool must have a REAL action.

The user-facing complaint these pin: the AI tools list was full of "Browser reference"
dead ends — ChatGPT/Claude web/Gemini rows that did nothing, and no way to actually use
Cortex data in those assistants. These tests keep the connection paths honest:

- every integration declares a connection kind (mcp config / CLI command / memory pack /
  local HTTP API) and the card renders a matching action, never a "Reference only" label,
- browser assistants get a one-tap cited memory pack built from the same context engine
  MCP clients use (`/v1/context-pack`),
- CLI tools (Claude Code, VS Code) get the exact terminal command with a scoped token,
- self-hosted stacks get base URL + scoped token + schema endpoints,
- the Connections sheet surfaces the memory-pack path with equal footing to MCP installs.
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
        for kind in ("mcpConfig", "cliCommand", "memoryPack", "httpAPI"):
            with self.subTest(kind=kind):
                self.assertIn(f"case {kind}", self.source)

    def test_every_integration_action_dispatches_on_kind(self) -> None:
        self.assertIn("func connectIntegration(_ integration: AIIntegration)", self.source)
        self.assertIn("switch integration.connectionKind", self.source)

    def test_no_reference_only_dead_ends(self) -> None:
        self.assertNotIn("Reference only. Direct connections will appear here", self.source)
        # The badge no longer paints purple "Reference" chips.
        self.assertNotIn('"Reference"', self.source)

    def test_browser_assistants_are_memory_pack_tools(self) -> None:
        # ChatGPT and Claude Web must declare the memory-pack path explicitly.
        chatgpt = re.search(r'id: "chatgpt".*?\),\n', self.source, re.S)
        self.assertIsNotNone(chatgpt)
        self.assertIn("connectionKind: .memoryPack", chatgpt.group(0))
        claude_web = re.search(r'id: "claude-web".*?\),\n', self.source, re.S)
        self.assertIsNotNone(claude_web)
        self.assertIn("connectionKind: .memoryPack", claude_web.group(0))

    def test_cli_tools_carry_command_templates(self) -> None:
        claude_code = re.search(r'id: "claude-code".*?\),\n', self.source, re.S)
        self.assertIsNotNone(claude_code)
        self.assertIn("connectionKind: .cliCommand", claude_code.group(0))
        self.assertIn("claude mcp add-json cortex", claude_code.group(0))
        vscode = re.search(r'id: "vscode-copilot".*?\),\n', self.source, re.S)
        self.assertIsNotNone(vscode)
        self.assertIn("connectionKind: .cliCommand", vscode.group(0))
        self.assertIn("code --add-mcp", vscode.group(0))

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
        self.assertIn("Copy memory pack", self.source)
        self.assertIn("Copy API details", self.source)

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
        self.assertIn('"Copy & paste"', self.source)
        self.assertIn('"Local API"', self.source)


class ConnectionsSheetExternalAccessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.source = CONNECTIONS_SHEET.read_text(encoding="utf-8")

    def test_memory_pack_row_has_equal_footing(self) -> None:
        self.assertIn("browserAssistantRow", self.source)
        self.assertIn("Copy a memory pack", self.source)
        self.assertIn("state.copyMemoryPack()", self.source)


if __name__ == "__main__":
    unittest.main()
