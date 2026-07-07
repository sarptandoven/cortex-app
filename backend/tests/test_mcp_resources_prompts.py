from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore

USER = "resprompt-user"


def _store() -> CortexStore:
    tmp = Path(tempfile.mkdtemp())
    db_path = tmp / "cortex.db"
    init_db(db_path)
    store = CortexStore(db_path, tmp / "vault")
    content = "Decided to use Go for the CLI because compile times are faster."
    store.save_capture(
        user_id=USER,
        content=content,
        source="notes",
        source_url="cortex-source://notes#doc=decisions",
        title="CLI language decision",
        extracted=extract_context(content, "notes"),
        cite_capture_provenance=True,
        auto_approve=True,
    )
    return store


class ResourceListingTests(unittest.TestCase):
    def test_list_resources_and_templates(self):
        uris = {r["uri"] for r in mcp_tools.list_resources()}
        self.assertIn("cortex://profile/person-map", uris)
        self.assertIn("cortex://schema/capabilities", uris)
        for resource in mcp_tools.list_resources():
            self.assertEqual(resource["mimeType"], "application/json")
        templates = {t["uriTemplate"] for t in mcp_tools.list_resource_templates()}
        self.assertIn("cortex://entity/{name}", templates)


class ResourceReadTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def test_read_static_resource_returns_json_contents(self):
        result = mcp_tools.read_resource(self.store, USER, "cortex://schema/capabilities", token_scopes=["read"])
        self.assertIn("contents", result)
        content = result["contents"][0]
        self.assertEqual(content["uri"], "cortex://schema/capabilities")
        self.assertEqual(content["mimeType"], "application/json")
        parsed = json.loads(content["text"])  # must be valid JSON
        self.assertIn("stats", parsed)
        self.assertIn("tools", parsed)

    def test_entity_template_resource(self):
        result = mcp_tools.read_resource(self.store, USER, "cortex://entity/Go", token_scopes=["read"])
        parsed = json.loads(result["contents"][0]["text"])
        self.assertEqual(parsed.get("entity"), "Go")

    def test_unknown_resource_raises(self):
        with self.assertRaises(ValueError):
            mcp_tools.read_resource(self.store, USER, "cortex://nope/x", token_scopes=["read"])

    def test_read_requires_read_scope(self):
        # A token without read scope cannot read a resource (advertisement is never authorization).
        with self.assertRaises(PermissionError):
            mcp_tools.read_resource(self.store, USER, "cortex://profile/personal", token_scopes=["write"])

    def test_admin_none_scopes_allowed(self):
        # token_scopes=None is the admin/app path — allowed.
        result = mcp_tools.read_resource(self.store, USER, "cortex://review/daily", token_scopes=None)
        self.assertIn("contents", result)


class PromptTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()

    def test_list_prompts(self):
        names = {p["name"] for p in mcp_tools.list_prompts()}
        self.assertEqual(names, {"summarize_recent_decisions", "extract_action_items", "brief_me_on"})
        brief = next(p for p in mcp_tools.list_prompts() if p["name"] == "brief_me_on")
        self.assertTrue(any(a["name"] == "subject" and a.get("required") for a in brief["arguments"]))

    def test_get_prompt_returns_grounded_messages(self):
        out = mcp_tools.get_prompt(self.store, USER, "summarize_recent_decisions", {}, token_scopes=["read"])
        self.assertIn("messages", out)
        msg = out["messages"][0]
        self.assertEqual(msg["role"], "user")
        self.assertIn("cited", msg["content"]["text"].lower())

    def test_brief_me_on_requires_subject(self):
        with self.assertRaises(ValueError):
            mcp_tools.get_prompt(self.store, USER, "brief_me_on", {}, token_scopes=["read"])

    def test_prompt_requires_read_scope(self):
        with self.assertRaises(PermissionError):
            mcp_tools.get_prompt(self.store, USER, "extract_action_items", {}, token_scopes=["write"])

    def test_unknown_prompt_raises(self):
        with self.assertRaises(ValueError):
            mcp_tools.get_prompt(self.store, USER, "nope", {}, token_scopes=["read"])


if __name__ == "__main__":
    unittest.main()
