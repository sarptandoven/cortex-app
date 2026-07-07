from __future__ import annotations

import unittest

from backend.app import mcp_tools


TOOL_NAMES = {str(t.get("name") or "") for t in mcp_tools.TOOLS}


class ToolAnnotationTests(unittest.TestCase):
    def test_every_tool_has_annotations_and_title(self):
        for tool in mcp_tools.TOOLS:
            name = tool.get("name")
            self.assertIn("title", tool, name)
            ann = tool.get("annotations")
            self.assertIsInstance(ann, dict, name)
            for key in ("title", "readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"):
                self.assertIn(key, ann, f"{name} missing annotation {key}")
            self.assertIsInstance(ann["readOnlyHint"], bool, name)

    def test_read_tools_are_read_only_writes_are_not(self):
        self.assertTrue(mcp_tools._tool_annotations("get_context")["readOnlyHint"])
        self.assertTrue(mcp_tools._tool_annotations("search_memory")["readOnlyHint"])
        self.assertFalse(mcp_tools._tool_annotations("remember_this")["readOnlyHint"])
        self.assertFalse(mcp_tools._tool_annotations("sync_github")["readOnlyHint"])

    def test_destructive_hint(self):
        self.assertTrue(mcp_tools._tool_annotations("delete_all_user_data")["destructiveHint"])
        self.assertFalse(mcp_tools._tool_annotations("get_context")["destructiveHint"])
        # destructive tools are never advertised idempotent
        self.assertFalse(mcp_tools._tool_annotations("delete_all_user_data")["idempotentHint"])

    def test_open_world_hint_only_for_external_reaching_tools(self):
        self.assertTrue(mcp_tools._tool_annotations("sync_github")["openWorldHint"])
        self.assertTrue(mcp_tools._tool_annotations("connect_source_account")["openWorldHint"])
        self.assertFalse(mcp_tools._tool_annotations("get_context")["openWorldHint"])
        self.assertFalse(mcp_tools._tool_annotations("search_memory")["openWorldHint"])

    def test_core_tools_declare_output_schema(self):
        by_name = {str(t.get("name") or ""): t for t in mcp_tools.TOOLS}
        for name in ("search_memory", "ask_memory", "get_context", "get_entity_context",
                     "get_person_map", "remember_this", "list_capabilities"):
            self.assertIn("outputSchema", by_name[name], name)
            self.assertEqual(by_name[name]["outputSchema"].get("type"), "object", name)


class SchemaExporterTests(unittest.TestCase):
    def test_openai_export_covers_all_tools_and_matches_input_schema(self):
        exported = mcp_tools.export_openai_tools(None, surface="full")
        self.assertEqual(len(exported), len(mcp_tools.TOOLS))
        by_name = {str(t.get("name") or ""): t for t in mcp_tools.TOOLS}
        for fn in exported:
            self.assertEqual(fn["type"], "function")
            spec = fn["function"]
            self.assertIn(spec["name"], TOOL_NAMES)
            self.assertEqual(spec["parameters"], mcp_tools._tool_input_schema(by_name[spec["name"]]))
            self.assertTrue(spec["description"])

    def test_anthropic_export_shape(self):
        exported = mcp_tools.export_anthropic_tools(None, surface="full")
        self.assertEqual(len(exported), len(mcp_tools.TOOLS))
        for tool in exported:
            self.assertIn("name", tool)
            self.assertIn("input_schema", tool)
            self.assertIn(tool["name"], TOOL_NAMES)

    def test_openapi_export_has_path_per_tool_with_operation_id(self):
        doc = mcp_tools.export_openapi("http://127.0.0.1:8766", None, surface="full")
        self.assertEqual(doc["openapi"], "3.1.0")
        paths = doc["paths"]
        self.assertEqual(len(paths), len(mcp_tools.TOOLS))
        for name in TOOL_NAMES:
            key = f"/v1/tools/{name}"
            self.assertIn(key, paths, name)
            self.assertEqual(paths[key]["post"]["operationId"], name)

    def test_scope_filter_restricts_exported_surface(self):
        read_only = {fn["function"]["name"] for fn in mcp_tools.export_openai_tools(["read"], surface="full")}
        full = {fn["function"]["name"] for fn in mcp_tools.export_openai_tools(None, surface="full")}
        self.assertTrue(read_only.issubset(full))
        # A write tool must NOT appear for a read-only token; a read tool must.
        self.assertNotIn("remember_this", read_only)
        self.assertNotIn("delete_all_user_data", read_only)
        self.assertIn("get_context", read_only)

    def test_export_tool_schema_dispatch(self):
        self.assertIsInstance(mcp_tools.export_tool_schema("openai"), list)
        self.assertIsInstance(mcp_tools.export_tool_schema("anthropic"), list)
        self.assertEqual(mcp_tools.export_tool_schema("openapi")["openapi"], "3.1.0")
        self.assertIsInstance(mcp_tools.export_tool_schema("mcp"), list)
        with self.assertRaises(ValueError):
            mcp_tools.export_tool_schema("graphql")

    def test_exported_names_are_all_dispatchable_via_call_tool_surface(self):
        # Parity: every exported tool name is a real tool the dispatcher knows about.
        exported = {fn["function"]["name"] for fn in mcp_tools.export_openai_tools(None, surface="full")}
        self.assertEqual(exported, TOOL_NAMES)


class UseCortexRouterTests(unittest.TestCase):
    def test_router_classifies_task(self):
        route = lambda task, intent=None: mcp_tools._route_use_cortex(task, intent, {})[0]
        self.assertEqual(route("What did I decide about Go?"), "ask_memory")
        self.assertEqual(route("who owns the billing service?"), "ask_memory")
        self.assertEqual(route("tell me about Alice"), "get_entity_context")
        self.assertEqual(route("brief me on Project Atlas"), "get_entity_context")
        self.assertEqual(route("search embeddings config"), "search_memory")
        self.assertEqual(route("draft a reply to the team", "draft"), "get_context")

    def test_router_carries_intent_into_context(self):
        target, args, _ = mcp_tools._route_use_cortex("plan the launch", "plan", {})
        self.assertEqual(target, "get_context")
        self.assertEqual(args.get("intent"), "plan")

    def test_use_cortex_is_core_read_tool(self):
        self.assertIn("use_cortex", mcp_tools.CORE_TOOL_NAMES)
        self.assertIn("use_cortex", mcp_tools.READ_TOOLS)
        self.assertTrue(mcp_tools._tool_annotations("use_cortex")["readOnlyHint"])
        self.assertIn("read", mcp_tools.tool_required_capabilities("use_cortex", scoped=True))


class SurfacePresetTests(unittest.TestCase):
    def _names(self, surface, scopes=("read",)):
        return {t["name"] for t in mcp_tools.tools_for_scopes(list(scopes), surface=surface)}

    def test_coding_and_chat_presets(self):
        coding = self._names("coding")
        self.assertIn("use_cortex", coding)
        self.assertIn("get_procedure", coding)
        self.assertNotIn("ask_memory", coding)  # coding preset omits the Q&A tool
        chat = self._names("chat")
        self.assertIn("ask_memory", chat)
        self.assertIn("get_person_map", chat)

    def test_unknown_surface_falls_back_to_core(self):
        self.assertEqual(self._names("does-not-exist"), self._names("core"))

    def test_presets_stay_under_client_caps(self):
        for surface in ("core", "coding", "chat"):
            self.assertLessEqual(len(self._names(surface, scopes=("read", "write"))), 40, surface)  # Cursor cap


if __name__ == "__main__":
    unittest.main()
