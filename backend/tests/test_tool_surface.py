from __future__ import annotations

import unittest

from backend.app.mcp_tools import (
    CORE_TOOL_NAMES,
    DESTRUCTIVE_TOOLS,
    MAINTENANCE_TOOLS,
    TOOLS,
    tool_required_capabilities,
    tools_for_scopes,
)


def _names(tools: list[dict]) -> set[str]:
    return {str(tool.get("name") or "") for tool in tools}


class CuratedToolSurfaceTests(unittest.TestCase):
    """The MCP tool collapse: scoped tokens are ADVERTISED a curated core surface so agents
    face a handful of well-chosen tools instead of ~60. Hiding is never authorization —
    call_tool dispatch and scope enforcement are untouched for every existing tool."""

    def test_core_tools_exist_in_catalog_with_schemas(self) -> None:
        catalog = _names(TOOLS)
        self.assertEqual(
            set(CORE_TOOL_NAMES),
            {"get_context", "ask_memory", "search_memory", "get_entity_context", "get_person_map", "remember_this", "list_capabilities"},
        )
        for name in CORE_TOOL_NAMES:
            self.assertIn(name, catalog)
            tool = next(tool for tool in TOOLS if tool.get("name") == name)
            self.assertIn("inputSchema", tool, name)

    def test_read_token_sees_core_read_tools_including_person_map(self) -> None:
        # get_person_map is a distilled-profile READ (holistic picture of the user), so a plain
        # read token sees it. Write-only tools (remember_this) stay hidden until write is granted.
        names = _names(tools_for_scopes(["read"]))
        self.assertEqual(
            names,
            {"get_context", "ask_memory", "search_memory", "get_entity_context", "get_person_map", "list_capabilities"},
        )

    def test_person_map_is_read_and_write_adds_remember(self) -> None:
        self.assertIn("get_person_map", _names(tools_for_scopes(["read", "export"])))
        self.assertIn("get_person_map", _names(tools_for_scopes(["read"])))
        self.assertEqual(tool_required_capabilities("get_person_map"), ["read"])
        self.assertIn("remember_this", _names(tools_for_scopes(["read", "write"])))
        self.assertNotIn("remember_this", _names(tools_for_scopes(["read"])))

    def test_maintenance_and_destructive_scopes_keep_their_tools_visible(self) -> None:
        # A token deliberately minted with these scopes must keep seeing its tools, or
        # list-then-call automations built on them would silently break.
        maintenance_names = _names(tools_for_scopes(["read", "maintenance"]))
        self.assertTrue(MAINTENANCE_TOOLS <= maintenance_names, MAINTENANCE_TOOLS - maintenance_names)
        destructive_names = _names(tools_for_scopes(["read", "write", "destructive"]))
        self.assertTrue(DESTRUCTIVE_TOOLS <= destructive_names, DESTRUCTIVE_TOOLS - destructive_names)

    def test_admin_and_full_surface_preserve_the_legacy_list(self) -> None:
        # Admin auth (the macOS app's own path) is byte-identical to before the collapse.
        self.assertEqual(tools_for_scopes(None), TOOLS)
        # surface="full" restores the legacy scope-filtered list exactly.
        full = _names(tools_for_scopes(["read"], surface="full"))
        expected = {
            str(tool.get("name") or "")
            for tool in TOOLS
            if all(cap in {"read"} for cap in tool_required_capabilities(str(tool.get("name") or ""), scoped=True))
        }
        self.assertEqual(full, expected)
        self.assertGreater(len(full), len(_names(tools_for_scopes(["read"]))))

    def test_advertise_full_marker_widens_advertisement_but_grants_nothing(self) -> None:
        via_marker = _names(tools_for_scopes(["read", "advertise_full"]))
        via_surface = _names(tools_for_scopes(["read"], surface="full"))
        self.assertEqual(via_marker, via_surface)
        # The marker must NEVER be a capability: no tool may require it.
        for tool in TOOLS:
            name = str(tool.get("name") or "")
            self.assertNotIn("advertise_full", tool_required_capabilities(name, scoped=True), name)

    def test_every_catalog_tool_still_dispatches(self) -> None:
        # The collapse is advertisement-only: call_tool must still know every name.
        from backend.app import mcp_tools
        import inspect

        source = inspect.getsource(mcp_tools.call_tool)
        for tool in TOOLS:
            name = str(tool.get("name") or "")
            self.assertIn(f'"{name}"', source, f"call_tool lost the {name} branch")


if __name__ == "__main__":
    unittest.main()
