from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.mcp_tools import TOOLS, call_tool
from backend.app.storage import CortexStore


class MemoryQualityLayerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "memory-quality.sqlite"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "memory-quality-user"
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_connector_sync_extracts_procedural_memory_with_sector_and_provenance(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Obsidian: Demo Vault",
            account_identifier="demo-vault",
            connection_type="local_folder",
            status="connected",
            auth_state="healthy",
            metadata={"vault_name": "Demo Vault"},
        )
        synced = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "Procedure: Before deploying Cortex, run the backend suite, run ./macos/build.sh, then verify codesign.",
                    "title": "Deployment Runbook",
                    "source_url": "file:///Users/example/Demo%20Vault/Deployment%20Runbook.md",
                    "external_id": "Deployment Runbook.md",
                }
            ],
            processing="sync",
        )

        self.assertEqual(synced["saved"], 1)
        self.assertTrue(self.store.approve_capture(self.user_id, synced["capture_ids"][0]))
        found = self.store.search(self.user_id, "how deploy Cortex run backend suite codesign", limit=3)

        self.assertTrue(found)
        self.assertEqual(found[0]["kind"], "procedure")
        self.assertEqual(found[0]["layer"], "procedural")
        self.assertEqual(found[0]["sector"], "Demo Vault")
        self.assertEqual(found[0]["source_type"], "local_file")
        self.assertEqual(found[0]["provenance"]["source_account_id"], account["id"])
        self.assertEqual(found[0]["provenance"]["external_id"], "Deployment Runbook.md")

        profile = self.store.personal_profile(self.user_id, query="deploy Cortex", include_pending=True)
        procedural = next(section for section in profile["sections"] if section["layer"] == "procedural")
        self.assertEqual(procedural["count"], 1)
        adaptation = self.store.agent_adaptation(self.user_id, query="deploy Cortex", include_pending=True)
        self.assertTrue(any(rule["layer"] == "procedural" for rule in adaptation["rules"]))

    def test_retrieval_filters_expired_future_and_superseded_memories(self) -> None:
        extracted = {
            "_timestamp": "2026-06-30T10:00:00+00:00",
            "summary": "Validity filter fixture",
            "records": [
                {
                    "id": "mem_current_process",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Current deployment process uses backend tests and codesign verification.",
                    "importance": 4,
                    "valid_from": "2020-01-01T00:00:00+00:00",
                },
                {
                    "id": "mem_expired_process",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Expired deployment process uses the old Fabric script.",
                    "importance": 5,
                    "valid_to": "2020-01-01T00:00:00+00:00",
                },
                {
                    "id": "mem_future_process",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Future deployment process uses hosted workers.",
                    "importance": 5,
                    "valid_from": "2999-01-01T00:00:00+00:00",
                },
                {
                    "id": "mem_superseded_process",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Superseded deployment process skips codesign.",
                    "importance": 5,
                    "superseded_by": "mem_current_process",
                },
            ],
            "tasks": [],
            "entities": [],
        }
        self.store.save_capture(
            user_id=self.user_id,
            content="Validity filter fixture",
            source="unit-test",
            source_url="unit-test://validity",
            title="Validity fixture",
            extracted=extracted,
        )

        results = self.store.search(self.user_id, "deployment process", limit=10, layer="procedural")
        contents = "\n".join(item["content"] for item in results)

        self.assertIn("Current deployment process", contents)
        self.assertNotIn("Expired deployment process", contents)
        self.assertNotIn("Future deployment process", contents)
        self.assertNotIn("Superseded deployment process", contents)
        self.assertTrue(all(item["layer"] == "procedural" for item in results))

        answer = json.dumps(self.store.answer_query(self.user_id, "deployment process", limit=10))
        self.assertIn("Current deployment process", answer)
        self.assertNotIn("Expired deployment process", answer)
        self.assertNotIn("Future deployment process", answer)
        self.assertNotIn("Superseded deployment process", answer)

        pack = self.store.context_pack(self.user_id, query="deployment process", limit=10)
        self.assertIn("Current deployment process", pack)
        self.assertNotIn("Expired deployment process", pack)
        self.assertNotIn("Future deployment process", pack)
        self.assertNotIn("Superseded deployment process", pack)

        profile = self.store.personal_profile(self.user_id, query="deployment process", limit=10, include_pending=True)
        profile_text = json.dumps(profile)
        self.assertIn("Current deployment process", profile_text)
        self.assertNotIn("Expired deployment process", profile_text)
        self.assertNotIn("Future deployment process", profile_text)
        self.assertNotIn("Superseded deployment process", profile_text)

    def test_mcp_high_value_tools_return_style_project_and_procedure_context(self) -> None:
        extracted = {
            "_timestamp": "2026-06-30T10:00:00+00:00",
            "summary": "MCP focused tool fixture",
            "records": [
                {
                    "id": "mem_style_direct",
                    "kind": "style",
                    "layer": "style",
                    "content": "Writing style: answer Project Atlas questions with direct tradeoffs and concise paragraphs.",
                    "importance": 4,
                    "topics": ["Project Atlas", "style"],
                },
                {
                    "id": "mem_project_atlas_fact",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Atlas uses Cortex as the cited memory layer for first-100 users.",
                    "importance": 4,
                    "topics": ["Project Atlas"],
                    "sector": "Project Atlas",
                },
                {
                    "id": "mem_project_atlas_procedure",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Procedure: for Project Atlas releases, run backend tests, build the app, and verify health.",
                    "importance": 4,
                    "topics": ["Project Atlas", "release"],
                    "sector": "Project Atlas",
                },
            ],
            "tasks": [],
            "entities": [{"id": "project_project-atlas", "kind": "project", "name": "Project Atlas", "aliases": [], "context": ""}],
        }
        self.store.save_capture(
            user_id=self.user_id,
            content="MCP focused tool fixture",
            source="unit-test",
            source_url="unit-test://mcp-tools",
            title="MCP focused tool fixture",
            extracted=extracted,
        )

        tool_names = {tool["name"] for tool in TOOLS}
        self.assertIn("get_style_profile", tool_names)
        self.assertIn("get_project_context", tool_names)
        self.assertIn("get_procedure", tool_names)

        style = call_tool(self.store, self.user_id, "get_style_profile", {"query": "Project Atlas questions"})
        self.assertTrue(style["style"])
        self.assertEqual(style["style"][0]["layer"], "style")

        project = call_tool(self.store, self.user_id, "get_project_context", {"name": "Project Atlas"})
        self.assertTrue(project["memories"])
        self.assertTrue(any(memory["sector"] == "Project Atlas" for memory in project["memories"]))

        procedure = call_tool(self.store, self.user_id, "get_procedure", {"query": "Project Atlas release health"})
        self.assertTrue(procedure["procedures"])
        self.assertEqual(procedure["procedures"][0]["layer"], "procedural")

        recent_procedures = self.store.search(self.user_id, "", limit=5, layer="procedural")
        self.assertEqual([item["id"] for item in recent_procedures], ["mem_project_atlas_procedure"])
        default_procedure = call_tool(self.store, self.user_id, "get_procedure", {"query": "", "limit": 5})
        self.assertEqual([item["id"] for item in default_procedure["procedures"]], ["mem_project_atlas_procedure"])

    def test_mcp_project_context_falls_back_to_entity_search_and_includes_related_citations(self) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas decision fixture",
            source="obsidian",
            source_url="file:///Users/example/Demo%20Vault/Atlas%20Decision.md",
            title="Atlas Decision",
            extracted={
                "_timestamp": "2026-06-30T10:00:00+00:00",
                "summary": "Project context decision fixture",
                "records": [
                    {
                        "id": "mem_mcp_atlas_budget_decision",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Atlas decision: keep the local-first beta because the risk budget is strict.",
                        "importance": 4,
                        "topics": ["Project Atlas", "beta"],
                        "entity_ids": ["project_atlas"],
                    }
                ],
                "tasks": [],
                "entities": [{"id": "project_atlas", "kind": "project", "name": "Project Atlas", "aliases": [], "context": ""}],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas procedure fixture",
            source="obsidian",
            source_url="file:///Users/example/Demo%20Vault/Atlas%20Procedure.md",
            title="Atlas Procedure",
            extracted={
                "_timestamp": "2026-06-30T10:05:00+00:00",
                "summary": "Project context procedure fixture",
                "records": [
                    {
                        "id": "mem_mcp_atlas_release_procedure",
                        "kind": "procedure",
                        "layer": "procedural",
                        "content": "Procedure: before beta release, run backend smoke, build the app, and verify source citations.",
                        "importance": 3,
                        "topics": ["release"],
                        "entity_ids": ["project_atlas"],
                    }
                ],
                "tasks": [],
                "entities": [{"id": "project_atlas", "kind": "project", "name": "Project Atlas", "aliases": [], "context": ""}],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Riley context fixture",
            source="notes",
            source_url="file:///Users/example/Demo%20Vault/People/Riley.md",
            title="Riley",
            extracted={
                "_timestamp": "2026-06-30T10:10:00+00:00",
                "summary": "Person context fixture",
                "records": [
                    {
                        "id": "mem_mcp_riley_launch_copy",
                        "kind": "claim",
                        "layer": "semantic",
                        "content": "Riley owns launch copy and prefers a direct approval checklist before release.",
                        "importance": 3,
                        "topics": ["launch"],
                        "entity_ids": ["person_riley"],
                    }
                ],
                "tasks": [],
                "entities": [{"id": "person_riley", "kind": "person", "name": "Riley", "aliases": [], "context": ""}],
            },
        )

        project = call_tool(
            self.store,
            self.user_id,
            "get_project_context",
            {"name": "Project Atlas", "query": "risk budget", "limit": 4},
        )
        project_ids = [item["id"] for item in project["memories"]]
        self.assertEqual(project_ids[0], "mem_mcp_atlas_budget_decision")
        self.assertIn("mem_mcp_atlas_release_procedure", project_ids)
        related = next(item for item in project["memories"] if item["id"] == "mem_mcp_atlas_release_procedure")
        self.assertEqual(related["relationship"]["kind"], "shared_entity")
        self.assertEqual(related["relationship"]["related_to_id"], "mem_mcp_atlas_budget_decision")
        self.assertTrue(all(item["source_url"].startswith("local-file://") for item in project["memories"]))

        person = call_tool(
            self.store,
            self.user_id,
            "get_project_context",
            {"name": "Riley", "query": "launch copy", "limit": 3},
        )
        person_ids = [item["id"] for item in person["memories"]]
        self.assertIn("mem_mcp_riley_launch_copy", person_ids)
        person_memory = next(item for item in person["memories"] if item["id"] == "mem_mcp_riley_launch_copy")
        self.assertNotEqual(person_memory.get("sector"), "Riley")
        self.assertTrue(person_memory["source_url"].startswith("local-file://"))

    def test_sector_scoped_retrieval_keeps_project_memory_separate(self) -> None:
        extracted = {
            "_timestamp": "2026-06-30T10:00:00+00:00",
            "summary": "Sector scoping fixture",
            "records": [
                {
                    "id": "mem_atlas_release_sector",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Release checklist: Project Atlas runs backend tests, app build, and codesign.",
                    "importance": 4,
                    "sector": "Project Atlas",
                    "topics": ["release", "Project Atlas"],
                },
                {
                    "id": "mem_boreal_release_sector",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Release checklist: Project Boreal runs web smoke tests and CDN purge.",
                    "importance": 4,
                    "sector": "Project Boreal",
                    "topics": ["release", "Project Boreal"],
                },
            ],
            "tasks": [],
            "entities": [],
        }
        self.store.save_capture(
            user_id=self.user_id,
            content="Sector scoping fixture",
            source="unit-test",
            source_url="unit-test://sector-scope",
            title="Sector scoping fixture",
            extracted=extracted,
        )

        atlas_results = self.store.search(self.user_id, "release checklist", limit=5, sector="Project Atlas")
        self.assertEqual([item["id"] for item in atlas_results], ["mem_atlas_release_sector"])

        boreal_results = self.store.search(self.user_id, "release checklist", limit=5, sector="Project Boreal")
        self.assertEqual([item["id"] for item in boreal_results], ["mem_boreal_release_sector"])

        atlas_pack = self.store.context_pack(self.user_id, query="release checklist", limit=5, sector="Project Atlas")
        self.assertIn("Sector: Project Atlas", atlas_pack)
        self.assertIn("Project Atlas runs backend tests", atlas_pack)
        self.assertNotIn("Project Boreal runs web smoke tests", atlas_pack)

        profile = self.store.personal_profile(self.user_id, query="release checklist", limit=5, include_pending=True, sector="Project Atlas")
        self.assertEqual(profile["sector"], "Project Atlas")
        self.assertEqual([item["id"] for item in profile["focus"]], ["mem_atlas_release_sector"])
        self.assertNotIn("Project Boreal", profile["markdown"])

        self.store.update_settings(self.user_id, {"allow_agent_exports": True})
        context_tool = call_tool(self.store, self.user_id, "build_context_pack", {"query": "release checklist", "sector": "Project Atlas", "limit": 5})
        self.assertIn("Project Atlas runs backend tests", context_tool)
        self.assertNotIn("Project Boreal runs web smoke tests", context_tool)

        project_tool = call_tool(self.store, self.user_id, "get_project_context", {"name": "Project Atlas", "query": "release checklist", "limit": 5})
        self.assertEqual([item["id"] for item in project_tool["memories"]], ["mem_atlas_release_sector"])


if __name__ == "__main__":
    unittest.main()
