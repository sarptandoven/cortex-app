from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.connectors.obsidian import stable_section_external_id
from backend.app.database import connect, init_db
from backend.app.mcp_tools import TOOLS, call_tool, tool_required_capabilities
from backend.app.storage import CortexStore


class First100AcceptanceLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "First 100 Vault"
        self.vault.mkdir()
        self.db_path = self.root / "cortex.sqlite"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "first-100-user"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def write_note(self, relative_path: str, content: str) -> Path:
        path = self.vault / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path

    def memory_contents_for_capture(self, capture_id: str) -> list[str]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT content
                FROM memories
                WHERE user_id = ? AND capture_id = ? AND status = 'active'
                ORDER BY id
                """,
                (self.user_id, capture_id),
            ).fetchall()
        return [row["content"] for row in rows]

    def test_obsidian_review_update_approval_retrieval_and_mcp_tool_surface(self) -> None:
        note = self.write_note(
            "Loops/Memory Loop.md",
            """---
title: First 100 Memory Loop
tags: [first-100, cortex]
---
# First 100 Memory Loop

Decision: We decided the first-100 Cortex MVP will use Obsidian as the primary local connector for source-backed memory.
Procedure: Before shipping a beta build, run the backend unittest subset and verify the MCP memory loop.
I prefer Cortex answers that include citations back to the originating note.
""",
        )

        first_sync = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(first_sync["status"], "complete")
        self.assertEqual(first_sync["source"], "obsidian")
        self.assertEqual(first_sync["saved"], 1)
        self.assertEqual(first_sync["records"][0]["status"], "saved")
        capture_id = first_sync["records"][0]["capture_id"]
        self.assertEqual(first_sync["source_account"]["source"], "obsidian")
        self.assertEqual(first_sync["source_account"]["connection_type"], "local-folder")
        self.assertTrue(first_sync["records"][0]["source_url"].startswith(note.resolve().as_uri()))

        inbox = call_tool(self.store, self.user_id, "get_memory_inbox", {"limit": 10})
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["id"], capture_id)
        self.assertEqual(inbox[0]["review_status"], "pending")
        self.assertGreaterEqual(inbox[0]["memory_count"], 1)
        self.assertTrue(inbox[0]["preview_memories"])
        self.assertTrue(any("first-100 Cortex MVP" in item["content"] for item in inbox[0]["preview_memories"]))
        self.assertNotIn("raw_text", inbox[0])

        loop_after_sync = call_tool(self.store, self.user_id, "get_product_loop", {})
        self.assertEqual(loop_after_sync["primary_action"]["action"], "review")
        self.assertEqual(loop_after_sync["counts"]["pending_captures"], 1)
        self.assertEqual({step["key"]: step["status"] for step in loop_after_sync["steps"]}["capture"], "done")
        self.assertEqual(
            call_tool(self.store, self.user_id, "search_memory", {"query": "primary local connector", "top_k": 5}),
            [],
        )

        note.write_text(
            """---
title: First 100 Memory Loop
tags: [first-100, cortex]
---
# First 100 Memory Loop

Decision: We decided the first-100 Cortex MVP will use edited Obsidian notes as the refreshed source for approved cited memory.
Procedure: Before shipping a beta build, run unittest backend acceptance tests and verify the MCP high-value tools.
I prefer Cortex answers that cite the edited Obsidian note when memory changes.
""",
            encoding="utf-8",
        )
        changed_sync = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")

        self.assertEqual(changed_sync["status"], "complete")
        self.assertEqual(changed_sync["saved"], 1)
        self.assertEqual(changed_sync["records"][0]["status"], "updated")
        self.assertEqual(changed_sync["records"][0]["capture_id"], capture_id)
        memory_text = "\n".join(self.memory_contents_for_capture(capture_id))
        self.assertIn("edited Obsidian notes", memory_text)
        self.assertIn("MCP high-value tools", memory_text)
        self.assertNotIn("primary local connector", memory_text)
        self.assertNotIn("backend unittest subset", memory_text)

        approved = call_tool(self.store, self.user_id, "approve_memory_capture", {"capture_id": capture_id})
        self.assertEqual(approved, {"approved": True})
        self.assertEqual(call_tool(self.store, self.user_id, "get_memory_inbox", {"limit": 10}), [])

        search_results = call_tool(
            self.store,
            self.user_id,
            "search_memory",
            {"query": "edited Obsidian note approved cited memory", "top_k": 5},
        )
        self.assertTrue(search_results)
        self.assertTrue(any("edited Obsidian" in item["content"] for item in search_results))
        self.assertTrue(all(item["source"] == "obsidian" for item in search_results))
        self.assertTrue(all((item["source_url"] or "").startswith("local-file://Memory%20Loop.md#") for item in search_results))
        self.assertTrue(all("line=" in (item["source_url"] or "") and "excerpt=" in (item["source_url"] or "") for item in search_results))
        self.assertTrue(
            any(
                item["provenance"]["external_id"]
                == stable_section_external_id(self.vault, note, "first-100-memory-loop")
                for item in search_results
            )
        )

        cited_answer = self.store.answer_query(self.user_id, "approved cited memory from edited Obsidian note", limit=5)
        self.assertTrue(cited_answer["citations"])
        self.assertTrue(any("edited Obsidian" in citation["excerpt"] for citation in cited_answer["citations"]))
        self.assertTrue(any(citation["source"] == "obsidian" for citation in cited_answer["citations"]))
        self.assertTrue(any((citation["source_url"] or "").startswith("local-file://Memory%20Loop.md#") for citation in cited_answer["citations"]))
        self.assertTrue(any("line=" in (citation["source_url"] or "") and "excerpt=" in (citation["source_url"] or "") for citation in cited_answer["citations"]))

        loop_after_retrieval = call_tool(self.store, self.user_id, "get_product_loop", {})
        self.assertEqual(loop_after_retrieval["primary_action"]["action"], "done")
        self.assertEqual(loop_after_retrieval["counts"]["pending_captures"], 0)
        self.assertGreaterEqual(loop_after_retrieval["counts"]["reused_today"], 1)

        tool_names = {tool["name"] for tool in TOOLS}
        expected_high_value_tools = {
            "search_memory",
            "get_memory_inbox",
            "approve_memory_capture",
            "get_product_loop",
            "list_source_connectors",
            "connect_source_account",
            "sync_source_records",
            "get_personal_profile",
            "get_agent_adaptation",
            "get_style_profile",
            "get_project_context",
            "get_procedure",
        }
        self.assertTrue(expected_high_value_tools.issubset(tool_names))
        self.assertEqual(tool_required_capabilities("search_memory"), ["read"])
        self.assertEqual(tool_required_capabilities("sync_source_records"), ["write"])
        self.assertEqual(tool_required_capabilities("approve_memory_capture"), ["write"])
        self.assertEqual(tool_required_capabilities("get_agent_adaptation"), ["read", "export"])

        connectors = call_tool(self.store, self.user_id, "list_source_connectors", {})
        obsidian = next(source for source in connectors["readiness"]["sources"] if source["source"] == "obsidian")
        self.assertEqual(obsidian["status"], "synced")
        self.assertEqual(obsidian["beta_status"], "active")
        self.assertEqual(obsidian["pending"], 0)
        self.assertGreaterEqual(obsidian["approved"], 1)
        self.assertGreaterEqual(obsidian["active_memories"], 1)
        self.assertEqual(obsidian["citation_coverage"], 1.0)

    def test_obsidian_review_required_policy_overrides_global_pending_access(self) -> None:
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})
        self.write_note(
            "Safety/Review Gate.md",
            """# Review Gate

Decision: Project Gate keeps Obsidian source-account memory unavailable to Ask until explicit approval.
Procedure: Before using synced note memory, approve the review item.
""",
        )

        synced = self.store.sync_obsidian_vault(self.user_id, vault_path=str(self.vault), processing="sync")
        self.assertEqual(synced["saved"], 1)
        capture_id = synced["records"][0]["capture_id"]

        search_before_approval = self.store.search(self.user_id, "Project Gate explicit approval", limit=5)
        self.assertEqual(search_before_approval, [])
        answer_before_approval = self.store.answer_query(self.user_id, "Project Gate explicit approval", limit=5)
        self.assertEqual(answer_before_approval["citations"], [])
        self.assertIn("did not find a cited item", answer_before_approval["answer"])

        approved = call_tool(self.store, self.user_id, "approve_memory_capture", {"capture_id": capture_id})
        self.assertEqual(approved, {"approved": True})

        search_after_approval = self.store.search(self.user_id, "Project Gate explicit approval", limit=5)
        self.assertTrue(search_after_approval)
        self.assertTrue(any("Project Gate" in item["content"] for item in search_after_approval))
        answer_after_approval = self.store.answer_query(self.user_id, "Project Gate explicit approval", limit=5)
        self.assertTrue(answer_after_approval["citations"])


if __name__ == "__main__":
    unittest.main()
