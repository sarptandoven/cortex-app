from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
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

    def test_trusted_source_account_policy_reranks_near_tie(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        untrusted = self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="Untrusted GitHub",
            account_identifier="untrusted-github",
            connection_type="api_token",
            status="connected",
            auth_state="connected",
            policy={"mode": "default", "review_required": False, "allow_ai_context": True},
        )
        trusted = self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="Trusted GitHub",
            account_identifier="trusted-github",
            connection_type="api_token",
            status="connected",
            auth_state="connected",
            policy={"mode": "trusted", "review_required": False, "allow_ai_context": True},
        )
        self.store.sync_source_account_records(
            self.user_id,
            untrusted["id"],
            records=[
                {
                    "external_id": "untrusted-reliability-ranking",
                    "title": "Untrusted reliability ranking",
                    "content": "Atlas reliability source-account trust ranking chooses the generic issue note.",
                    "source_url": "cortex-source://github#service=github&repository=untrusted&line=5&excerpt=trust-ranking",
                    "metadata": {"fixture": "trusted-source-account-ranking"},
                }
            ],
            processing="sync",
        )
        self.store.sync_source_account_records(
            self.user_id,
            trusted["id"],
            records=[
                {
                    "external_id": "trusted-reliability-ranking",
                    "title": "Trusted reliability ranking",
                    "content": "Atlas reliability source-account trust ranking chooses the canonical issue note.",
                    "source_url": "cortex-source://github#service=github&repository=trusted&line=8&excerpt=trust-ranking",
                    "metadata": {"fixture": "trusted-source-account-ranking"},
                }
            ],
            processing="sync",
        )

        results = self.store.search(self.user_id, "Atlas reliability source-account trust ranking issue note", limit=2)

        self.assertTrue(results)
        self.assertIn("canonical issue note", results[0]["content"])
        self.assertEqual(results[0]["provenance"]["source_account_id"], trusted["id"])
        self.assertEqual(results[0]["provenance"]["source_account_policy"]["mode"], "trusted")

    def test_trusted_source_survives_noisy_duplicate_corpus(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        untrusted = self.store.upsert_source_account(
            self.user_id,
            source="slack",
            account_label="Noisy Slack",
            account_identifier="noisy-slack",
            connection_type="api_token",
            status="connected",
            auth_state="connected",
            policy={"mode": "default", "review_required": False, "allow_ai_context": True},
        )
        trusted = self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="Trusted GitHub",
            account_identifier="trusted-github",
            connection_type="api_token",
            status="connected",
            auth_state="connected",
            policy={"mode": "trusted", "review_required": False, "allow_ai_context": True},
        )
        for index in range(18):
            self.store.sync_source_account_records(
                self.user_id,
                untrusted["id"],
                records=[
                    {
                        "external_id": f"noisy-aurora-ranking-{index}",
                        "title": f"Noisy Aurora duplicate {index}",
                        "content": (
                            "Project Aurora source trust ranking duplicate corpus repeats generic launch notes "
                            f"without the canonical rollback constraint. Duplicate {index}."
                        ),
                        "source_url": f"https://slack.example.com/archives/CNOISY/p{index:04d}",
                    }
                ],
                processing="sync",
            )
        self.store.sync_source_account_records(
            self.user_id,
            trusted["id"],
            records=[
                {
                    "external_id": "trusted-aurora-ranking",
                    "title": "Trusted Aurora launch issue",
                    "content": "Project Aurora source trust ranking canonical rollback constraint: keep the previous DMG available before inviting testers.",
                    "source_url": "https://github.com/doppl-tech/cortex-app/issues/424",
                    "metadata": {"canonical": True},
                }
            ],
            processing="sync",
        )

        results = self.store.search(
            self.user_id,
            "Project Aurora source trust ranking canonical rollback constraint previous DMG testers",
            limit=5,
        )
        result_ids = [item["id"] for item in results]

        self.assertTrue(results)
        self.assertTrue(result_ids[0])
        self.assertEqual(results[0]["provenance"]["external_id"], "trusted-aurora-ranking")
        self.assertEqual(results[0]["provenance"]["source_account_id"], trusted["id"])
        self.assertEqual(results[0]["provenance"]["source_account_policy"]["mode"], "trusted")
        duplicate_results = [item for item in results if str(item["provenance"].get("external_id") or "").startswith("noisy-aurora-ranking")]
        self.assertLessEqual(len(duplicate_results), 2)

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

        historical_results = self.store.search(
            self.user_id,
            "deployment process old Fabric script",
            limit=10,
            layer="procedural",
            as_of="2019-12-31",
        )
        historical_contents = "\n".join(item["content"] for item in historical_results)
        self.assertIn("Expired deployment process", historical_contents)
        self.assertNotIn("Current deployment process", historical_contents)
        self.assertNotIn("Future deployment process", historical_contents)
        self.assertNotIn("Superseded deployment process", historical_contents)

        historical_answer = self.store.answer_query(
            self.user_id,
            "deployment process old Fabric script",
            limit=10,
            as_of="2019-12-31",
        )
        self.assertEqual(historical_answer["filters"]["as_of"], "2019-12-31T23:59:59+00:00")
        historical_answer_text = json.dumps(historical_answer)
        self.assertIn("Expired deployment process", historical_answer_text)
        self.assertNotIn("Current deployment process", historical_answer_text)
        self.assertNotIn("Future deployment process", historical_answer_text)

        mcp_search = call_tool(
            self.store,
            self.user_id,
            "search_memory",
            {"query": "deployment process old Fabric script", "top_k": 10, "layer": "procedural", "as_of": "2019-12-31"},
        )
        self.assertEqual(mcp_search["filters"]["as_of"], "2019-12-31T23:59:59+00:00")
        self.assertTrue(any("Expired deployment process" in item["content"] for item in mcp_search["results"]))

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
                    "id": "mem_preference_recommend_first",
                    "kind": "preference",
                    "layer": "preference",
                    "content": "I prefer Project Atlas answers that lead with the recommended path before caveats.",
                    "importance": 4,
                    "topics": ["Project Atlas", "preference"],
                },
                {
                    "id": "mem_negative_no_hype",
                    "kind": "negative",
                    "layer": "negative",
                    "content": "Do not use hype-heavy launch language when answering Project Atlas questions.",
                    "importance": 4,
                    "topics": ["Project Atlas", "negative"],
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

        style = call_tool(
            self.store,
            self.user_id,
            "get_style_profile",
            {
                "query": "Project Atlas questions",
                "draft": "This is a game-changing update for Project Atlas, and it is incredibly powerful because it unlocks many possibilities before we explain the actual recommendation.",
            },
        )
        self.assertTrue(style["style"])
        self.assertEqual(style["style"][0]["layer"], "style")
        self.assertIn("rewrite_checklist", style)
        self.assertIn("style_contract", style)
        self.assertEqual(style["style_contract"]["confidence"], "strong")
        self.assertTrue(style["style_contract"]["must_do"])
        self.assertTrue(style["style_contract"]["must_avoid"])
        self.assertTrue(any(rule["memory_id"] == "mem_preference_recommend_first" for rule in style["style_contract"]["must_do"]))
        self.assertTrue(any(rule["memory_id"] == "mem_negative_no_hype" for rule in style["style_contract"]["must_avoid"]))
        self.assertTrue(style["guidance"]["style_directives"])
        self.assertEqual(style["guidance"]["style_directives"][0]["memory_id"], "mem_style_direct")
        self.assertIn("Project Atlas questions", style["guidance"]["style_directives"][0]["instruction"])
        self.assertTrue(style["guidance"]["preference_directives"])
        self.assertTrue(style["guidance"]["negative_directives"])
        self.assertTrue(style["rewrite_examples"])
        self.assertEqual(style["rewrite_examples"][0]["name"], "recommended_path_first")
        self.assertIn("Recommended path: Project Atlas questions", style["rewrite_examples"][0]["after"])
        self.assertTrue(style["rewrite_examples"][0]["citations"])
        self.assertTrue(style["rewrite_examples"][0]["rules_applied"])
        self.assertTrue(style["rewrite_examples"][0]["quality_checks"])
        self.assertTrue(any(citation["memory_id"] == "mem_preference_recommend_first" for citation in style["rewrite_examples"][0]["citations"]))
        self.assertTrue(any(example["name"] == "remove_rejected_pattern" for example in style["rewrite_examples"]))
        self.assertEqual(style["draft_review"]["status"], "needs_revision")
        self.assertTrue(any(finding["memory_id"] == "mem_negative_no_hype" for finding in style["draft_review"]["findings"]))
        self.assertTrue(any(finding["memory_id"] == "mem_preference_recommend_first" for finding in style["draft_review"]["findings"]))
        self.assertIn("Recommended path: Project Atlas questions", style["draft_review"]["recommended_opening"])

        style_markdown = call_tool(
            self.store,
            self.user_id,
            "get_style_profile",
            {
                "query": "Project Atlas questions",
                "format": "markdown",
                "draft": "This is a game-changing update for Project Atlas, and it is incredibly powerful.",
            },
        )
        self.assertIn("## Rewrite Checklist", style_markdown)
        self.assertIn("## Style Contract", style_markdown)
        self.assertIn("## Draft Review", style_markdown)
        self.assertIn("## Style Directives", style_markdown)
        self.assertIn("## Negative Constraints", style_markdown)
        self.assertIn("## Rewrite Examples", style_markdown)
        self.assertIn("recommended_path_first", style_markdown)
        self.assertIn("remove_rejected_pattern", style_markdown)
        self.assertIn("mem_style_direct", style_markdown)
        self.assertIn("mem_negative_no_hype", style_markdown)

        project = call_tool(self.store, self.user_id, "get_project_context", {"name": "Project Atlas"})
        self.assertTrue(project["memories"])
        self.assertTrue(any(memory["sector"] == "Project Atlas" for memory in project["memories"]))
        self.assertIn("agent_brief", project)
        self.assertEqual(project["agent_brief"]["project"], "Project Atlas")
        self.assertTrue(project["agent_brief"]["citations"])
        self.assertIn("procedures", project["agent_brief"]["sections"])
        self.assertTrue(any("Follow the cited procedure" in action for action in project["agent_brief"]["next_actions"]))

        procedure = call_tool(self.store, self.user_id, "get_procedure", {"query": "Project Atlas release health"})
        self.assertTrue(procedure["procedures"])
        self.assertEqual(procedure["procedures"][0]["layer"], "procedural")
        self.assertTrue(procedure["execution_checklist"])
        self.assertEqual(procedure["procedure_contract"]["status"], "strong")
        self.assertTrue(any("run backend tests" in item["step"] for item in procedure["execution_checklist"]))

        procedure_markdown = call_tool(
            self.store,
            self.user_id,
            "get_procedure",
            {"query": "Project Atlas release health", "format": "markdown"},
        )
        self.assertIn("## Execution Checklist", procedure_markdown)
        self.assertIn("Source:", procedure_markdown)

        recent_procedures = self.store.search(self.user_id, "", limit=5, layer="procedural")
        self.assertEqual([item["id"] for item in recent_procedures], ["mem_project_atlas_procedure"])
        default_procedure = call_tool(self.store, self.user_id, "get_procedure", {"query": "", "limit": 5})
        self.assertEqual([item["id"] for item in default_procedure["procedures"]], ["mem_project_atlas_procedure"])
        self.assertTrue(default_procedure["execution_checklist"])

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

    def _seed_holistic_corpus(self) -> None:
        """Cited memories in EVERY layer (preference, style, negative, decision,
        procedural, episodic w/ occurred_at, semantic) plus an open task."""
        extracted = {
            "_timestamp": "2026-07-02T10:00:00+00:00",
            "summary": "Holistic profile fixture",
            "records": [
                {
                    "id": "mem_holistic_pref_a",
                    "kind": "preference",
                    "layer": "preference",
                    "content": "Prefers concise release notes that lead with the recommendation.",
                    "importance": 4,
                    "topics": ["writing"],
                },
                {
                    "id": "mem_holistic_pref_b",
                    "kind": "preference",
                    "layer": "preference",
                    "content": "Prefers concise release notes that lead with the recommendation always.",
                    "importance": 4,
                    "topics": ["writing"],
                },
                {
                    "id": "mem_holistic_style",
                    "kind": "style",
                    "layer": "style",
                    "content": "Writing style: short direct sentences, tradeoff first, no filler.",
                    "importance": 4,
                    "topics": ["style"],
                },
                {
                    "id": "mem_holistic_negative",
                    "kind": "negative",
                    "layer": "negative",
                    "content": "Rejected hype-heavy launch language for product updates.",
                    "importance": 4,
                },
                {
                    "id": "mem_holistic_decision",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Decided to keep the beta local-first because the risk budget is strict.",
                    "importance": 5,
                },
                {
                    "id": "mem_holistic_procedure",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Procedure: before release, run backend tests, build the app, verify health.",
                    "importance": 4,
                },
                {
                    "id": "mem_holistic_epi_old",
                    "kind": "event",
                    "layer": "episodic",
                    "content": "Kickoff review with Riley about the launch checklist.",
                    "importance": 4,
                    "occurred_at": "2026-06-20",
                },
                {
                    "id": "mem_holistic_epi_recent",
                    "kind": "event",
                    "layer": "episodic",
                    "content": "Shipped the beta build to the first external tester.",
                    "importance": 4,
                    "occurred_at": "2026-07-01",
                },
                {
                    "id": "mem_holistic_fact",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Riley owns launch copy and reviews every release announcement.",
                    "importance": 4,
                },
                {
                    "id": "mem_holistic_fact_noise",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "/Users/example/vault/build/output.log",
                    "importance": 5,
                },
            ],
            "tasks": [
                {
                    "id": "task_holistic_open",
                    "kind": "action",
                    "content": "Send the beta invite list to Riley before Friday.",
                    "status": "open",
                    "importance": 4,
                    "topics": ["launch"],
                    "entity_ids": [],
                }
            ],
            "entities": [],
        }
        self.store.save_capture(
            user_id=self.user_id,
            content="Holistic profile fixture",
            source="unit-test",
            source_url="unit-test://holistic-profile",
            title="Holistic profile fixture",
            extracted=extracted,
        )

    def test_build_profile_represents_every_memory_layer_with_citations(self) -> None:
        self._seed_holistic_corpus()

        profile = self.store.build_profile(self.user_id, include_pending=True)

        # Contract: response keys unchanged.
        self.assertEqual(set(profile.keys()), {"generated_at", "readiness", "condensed", "sections", "limitations"})

        sections_by_id = {section["id"]: section for section in profile["sections"]}
        # Every seeded layer is represented in some section.
        for expected in ("how_you_work", "voice_style", "preferences", "dislikes", "decisions", "facts", "recent_timeline", "open_loops"):
            self.assertIn(expected, sections_by_id, f"expected section {expected} for a fully-seeded corpus")

        # Every section keeps the Section shape and is cited-or-abstain.
        for section in profile["sections"]:
            self.assertTrue(str(section.get("title") or "").strip())
            self.assertTrue(str(section.get("statement") or "").strip(), section["id"])
            self.assertIn(section.get("method"), {"template", "llm"})
            self.assertIn(section.get("confidence"), {"high", "medium", "low"})
            self.assertTrue(section.get("elements"))
            for element in section["elements"]:
                self.assertTrue(str(element.get("text") or "").strip())
                self.assertTrue(element.get("memory_ids") or str(element.get("source") or "").strip(), element)
                self.assertIn("source_url", element)
                self.assertIn("count", element)

        def cited_ids(section_id: str) -> set[str]:
            return {
                memory_id
                for element in sections_by_id[section_id]["elements"]
                for memory_id in element["memory_ids"]
            }

        # Layer-to-section evidence mapping, with citations.
        self.assertIn("mem_holistic_procedure", cited_ids("how_you_work"))
        self.assertIn("mem_holistic_style", cited_ids("voice_style"))
        self.assertTrue({"mem_holistic_pref_a", "mem_holistic_pref_b"} & cited_ids("preferences"))
        self.assertIn("mem_holistic_negative", cited_ids("dislikes"))
        self.assertIn("mem_holistic_decision", cited_ids("decisions"))
        # Facts: the semantic claim surfaces; the bare-path noise record does not.
        self.assertIn("mem_holistic_fact", cited_ids("facts"))
        self.assertNotIn("mem_holistic_fact_noise", cited_ids("facts"))
        # Recent timeline: episodic memories, most-recent-first by occurred_at.
        timeline = sections_by_id["recent_timeline"]["elements"]
        self.assertEqual(timeline[0]["memory_ids"], ["mem_holistic_epi_recent"])
        self.assertIn("2026-07-01", timeline[0]["text"])
        self.assertIn("mem_holistic_epi_old", cited_ids("recent_timeline"))
        # Open loops reuse the personal_profile open_loops (task-id cited).
        self.assertIn("task_holistic_open", cited_ids("open_loops"))

        # Honest limitations: with every layer present, none is reported missing.
        self.assertFalse(
            [item for item in profile["limitations"] if "Missing or weak layers" in item],
            profile["limitations"],
        )

        # The whole-person map carries the same sections (preference + style included).
        pmap = self.store.person_map(self.user_id, include_pending=True)
        pmap_ids = {section["id"] for section in pmap["profile"]}
        for expected in ("voice_style", "preferences", "facts", "recent_timeline", "open_loops"):
            self.assertIn(expected, pmap_ids)

        # The agent adaptation pack's persona carries the new sections too.
        adaptation = self.store.agent_adaptation(self.user_id, include_pending=True)
        persona_ids = {section["id"] for section in adaptation["persona"]}
        for expected in ("voice_style", "facts", "recent_timeline", "open_loops"):
            self.assertIn(expected, persona_ids)

    def test_build_profile_abstains_on_thin_corpus(self) -> None:
        # Empty corpus: nothing to say -> no fabricated sections.
        empty = self.store.build_profile(self.user_id, include_pending=True)
        self.assertEqual(empty["sections"], [])

        # A single low-importance signal is below every surfacing floor.
        self.store.save_capture(
            user_id=self.user_id,
            content="Thin corpus fixture",
            source="unit-test",
            source_url="unit-test://thin-profile",
            title="Thin corpus fixture",
            extracted={
                "_timestamp": "2026-07-02T10:00:00+00:00",
                "summary": "Thin corpus fixture",
                "records": [
                    {
                        "id": "mem_thin_pref",
                        "kind": "preference",
                        "layer": "preference",
                        "content": "Used a dark editor theme once.",
                        "importance": 2,
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        thin = self.store.build_profile(self.user_id, include_pending=True)
        self.assertEqual(thin["sections"], [])

    def _save_repeated_statement(self, times: int) -> None:
        """Save the exact same statement `times` times as genuinely separate captures
        (distinct timestamps -> distinct capture ids), the honest-repetition path."""
        for index in range(times):
            self.store.save_capture(
                user_id=self.user_id,
                content="I prefer dark mode in every editor and terminal.",
                source="unit-test",
                source_url="unit-test://repetition",
                title="Repetition fixture",
                extracted={
                    "_timestamp": f"2026-07-{index + 1:02d}T10:00:00+00:00",
                    "summary": "Repetition fixture",
                    "records": [
                        {
                            "id": "mem_repeat_dark_mode",
                            "kind": "preference",
                            "layer": "preference",
                            "content": "I prefer dark mode in every editor and terminal.",
                            # Below _IMPORTANT_FLOOR on purpose: only repetition-derived
                            # support can surface/raise this element.
                            "importance": 3,
                        }
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

    def test_repeated_statement_accrues_occurrences_and_profile_support(self) -> None:
        self._save_repeated_statement(3)

        # Content-derived ids collapse to ONE memory row, but honest repetition accrues.
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT id, occurrences FROM memories WHERE user_id = ?", (self.user_id,)
            ).fetchall()
        self.assertEqual([(row["id"], int(row["occurrences"])) for row in rows], [("mem_repeat_dark_mode", 3)])

        # The memory payload contract carries occurrences (additive).
        found = self.store.search(self.user_id, "dark mode editor terminal", limit=5)
        self.assertEqual([item["id"] for item in found], ["mem_repeat_dark_mode"])
        self.assertEqual(found[0]["occurrences"], 3)

        # Profile support is repetition-weighted: 3 saves of one sentence = support 3,
        # which clears the medium confidence band (support >= 3).
        profile = self.store.build_profile(self.user_id, include_pending=True)
        sections_by_id = {section["id"]: section for section in profile["sections"]}
        self.assertIn("preferences", sections_by_id)
        preferences = sections_by_id["preferences"]
        element = next(
            element
            for element in preferences["elements"]
            if "mem_repeat_dark_mode" in element["memory_ids"]
        )
        self.assertGreaterEqual(element["count"], 3)
        self.assertEqual(preferences["confidence"], "medium")

    def test_vault_rebuild_preserves_occurrences(self) -> None:
        self._save_repeated_statement(3)

        # A rebuild re-inserts every vault memory once; it must restore the accrued
        # count, never reset or inflate it — and stay idempotent across rebuilds.
        for _ in range(2):
            self.store.rebuild_index_from_vault(self.user_id)
            with connect(self.db_path) as conn:
                rows = conn.execute(
                    "SELECT occurrences FROM memories WHERE user_id = ? AND id = ?",
                    (self.user_id, "mem_repeat_dark_mode"),
                ).fetchall()
            self.assertEqual([int(row["occurrences"]) for row in rows], [3])

    def test_get_style_profile_prefers_current_trusted_style_over_stale_generated(self) -> None:
        # Conflicting style guidance with similar lexical overlap: a current,
        # trusted, user-authored concise style vs a stale, untrusted, generated,
        # verbose/salesy style that is given HIGHER importance. get_style_profile
        # must surface and cite the trusted one, proving the trusted-source
        # signal (not raw lexical overlap or importance) decides the style layer.
        extracted = {
            "_timestamp": "2026-06-30T10:00:00+00:00",
            "summary": "Conflicting style fixture",
            "records": [
                {
                    "id": "mem_style_stale_generated",
                    "kind": "style",
                    "layer": "style",
                    "content": (
                        "Writing style for Project Atlas status updates: long, enthusiastic, "
                        "salesy sentences that bury the tradeoff in Project Atlas status updates."
                    ),
                    "importance": 5,
                    "topics": ["Project Atlas", "style"],
                },
                {
                    "id": "mem_style_current_trusted",
                    "kind": "style",
                    "layer": "style",
                    "content": (
                        "Writing style for Project Atlas status updates: concise, direct "
                        "sentences that lead with the tradeoff in Project Atlas status updates."
                    ),
                    "importance": 3,
                    "topics": ["Project Atlas", "style"],
                    "metadata": {"trusted_source": True, "quality": "trusted"},
                },
            ],
            "tasks": [],
            "entities": [],
        }
        self.store.save_capture(
            user_id=self.user_id,
            content="Conflicting style fixture",
            source="unit-test",
            source_url="unit-test://style-conflict",
            title="Conflicting style fixture",
            extracted=extracted,
        )

        result = call_tool(
            self.store,
            self.user_id,
            "get_style_profile",
            {"query": "Project Atlas status update writing style"},
        )

        self.assertTrue(result["style"])
        # The trusted, current style must be the surfaced top style signal...
        self.assertEqual(result["style"][0]["id"], "mem_style_current_trusted")
        # ...and the cited style directive, not the stale generated one.
        self.assertTrue(result["guidance"]["style_directives"])
        self.assertEqual(
            result["guidance"]["style_directives"][0]["memory_id"], "mem_style_current_trusted"
        )
        style_ids = [item["id"] for item in result["style"]]
        if "mem_style_stale_generated" in style_ids:
            self.assertLess(
                style_ids.index("mem_style_current_trusted"),
                style_ids.index("mem_style_stale_generated"),
            )


if __name__ == "__main__":
    unittest.main()
