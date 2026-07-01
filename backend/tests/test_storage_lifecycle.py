from __future__ import annotations

import base64
import tempfile
import unittest
import shutil
import sqlite3
import json
import os
import zipfile
from pathlib import Path
from unittest.mock import patch

from backend.app.database import connect, init_db
from backend.app.extractor import extract_context
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore
from scripts.export_support_bundle import validate_content_free_bundle


DUMMY_OPENAI_KEY = "sk-" + ("0" * 24)


class CortexStorageLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "test-user"
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def capture(self, text: str):
        return self.store.save_capture(
            user_id=self.user_id,
            content=text,
            source="unit-test",
            source_url=None,
            title="Unit test capture",
            extracted=extract_context(text, "unit-test"),
        )

    def test_capture_search_approve_archive_export(self) -> None:
        result = self.capture(
            "Vamika decided Cortex should use SQLite locally and Supabase later. "
            "Cortex needs MCP tools for ChatGPT and Claude. "
            "The next step is to test backup and export."
        )
        capture_id = result["capture_id"]

        self.assertGreaterEqual(len(result["memories"]), 2)
        inbox_item = self.store.inbox(self.user_id)[0]
        self.assertEqual(inbox_item["id"], capture_id)
        self.assertTrue(inbox_item["preview_memories"])
        self.assertTrue(any("SQLite locally" in item["content"] for item in inbox_item["preview_memories"]))
        self.assertTrue(inbox_item["preview_tasks"])
        self.assertTrue(any("backup and export" in item["content"] for item in inbox_item["preview_tasks"]))
        self.assertNotIn("raw_text", inbox_item)
        self.assertTrue(self.store.search(self.user_id, "Supabase later?!"))

        stats = self.store.stats(self.user_id)
        self.assertEqual(stats["captures"], 1)
        self.assertEqual(stats["pending_captures"], 1)
        self.assertEqual(stats["memories"], 0)
        self.assertEqual(stats["decisions"], 0)
        self.assertEqual(stats["tasks"], 0)
        self.assertEqual(stats["entities"], 0)
        self.assertEqual(stats["edges"], 0)
        self.assertEqual(stats["by_kind"], [])
        self.assertEqual(stats["by_layer"], [])
        self.assertEqual(stats["top_topics"], [])
        self.assertEqual(stats["top_entities"], [])
        mcp_stats = call_tool(self.store, self.user_id, "get_memory_stats", {})
        self.assertEqual(mcp_stats["memories"], 0)
        self.assertEqual(mcp_stats["top_topics"], [])

        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertEqual(self.store.stats(self.user_id)["pending_captures"], 0)

        stats = self.store.stats(self.user_id)
        self.assertGreater(stats["memories"], 0)
        self.assertGreater(stats["edges"], 0)
        self.assertTrue(stats["by_layer"])
        profile = self.store.personal_profile(self.user_id, query="Supabase", limit=4)
        self.assertEqual(profile["name"], "Cortex Personal Adaptation Profile")
        self.assertGreater(profile["readiness"], 0)
        self.assertIn("Decision memory", profile["markdown"])
        self.assertIn("fine-tuned model", profile["markdown"])
        self.store.update_settings(self.user_id, {"allow_agent_exports": True})
        self.assertTrue(call_tool(self.store, self.user_id, "get_personal_profile", {"format": "markdown"}).startswith("# Cortex Personal Adaptation Profile"))
        self.assertFalse(call_tool(self.store, self.user_id, "get_personal_profile", {"include_pending": "false"})["include_pending"])
        adaptation = self.store.agent_adaptation(self.user_id, query="Supabase", target="Claude", limit=4)
        self.assertEqual(adaptation["name"], "Cortex Agent Adaptation Layer")
        self.assertEqual(adaptation["target"], "Claude")
        self.assertTrue(adaptation["operating_principles"])
        self.assertTrue(adaptation["rules"])
        self.assertTrue(adaptation["evidence"])
        self.assertIn("Respect this prior decision", adaptation["markdown"])
        self.assertFalse(call_tool(self.store, self.user_id, "get_agent_adaptation", {"include_pending": "false"})["include_pending"])
        self.assertTrue(call_tool(self.store, self.user_id, "get_agent_adaptation", {"format": "markdown", "target": "Claude"}).startswith("# Cortex Agent Adaptation Layer"))

        export_before_archive = self.store.export_markdown(self.user_id)
        self.assertIn("Supabase", export_before_archive)

        self.assertTrue(self.store.archive_capture(self.user_id, capture_id))
        stats_after_archive = self.store.stats(self.user_id)
        self.assertEqual(stats_after_archive["memories"], 0)
        self.assertEqual(stats_after_archive["tasks"], 0)
        self.assertEqual(stats_after_archive["edges"], 0)
        self.assertEqual(self.store.search(self.user_id, "Supabase"), [])
        self.assertEqual(self.store.graph(self.user_id)["nodes"], [])

        export_after_archive = self.store.export_markdown(self.user_id)
        self.assertIn("Supabase", export_after_archive)

    def test_search_handles_hyphenated_user_queries(self) -> None:
        result = self.capture(
            "I prefer source-backed answers with direct caveats. "
            "Cortex should retrieve hyphenated phrases when users type them naturally."
        )

        self.assertTrue(result["memories"])
        hits = self.store.search(self.user_id, "source-backed answers direct caveats", limit=5)

        self.assertTrue(hits)
        self.assertTrue(any("source-backed answers" in hit["content"] for hit in hits))

    def test_ask_includes_bounded_related_memory_and_archives_relations(self) -> None:
        extracted = {
            "_timestamp": "2026-06-30T10:00:00+00:00",
            "summary": "Project Atlas beta release memory.",
            "records": [
                {
                    "id": "mem_atlas_beta_decision",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Atlas decision: pick the local-first beta path because the risk budget is tight.",
                    "summary": "Project Atlas chose the local-first beta path.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "topics": ["Project Atlas", "beta"],
                    "entity_ids": ["project_atlas"],
                },
                {
                    "id": "mem_atlas_release_procedure",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": "Before the Project Atlas beta release, run backend smoke, build the app, and verify codesign.",
                    "summary": "Project Atlas beta release checks.",
                    "confidence": "confirmed",
                    "importance": 3,
                    "topics": ["Project Atlas", "beta"],
                    "entity_ids": ["project_atlas"],
                },
            ],
            "tasks": [],
            "entities": [
                {"id": "project_atlas", "kind": "project", "name": "Project Atlas", "aliases": ["Atlas"], "context": ""}
            ],
        }
        result = self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas beta release memory.",
            source="obsidian",
            source_url="file:///tmp/Project%20Atlas.md",
            title="Project Atlas",
            extracted=extracted,
        )

        with connect(self.db_path) as conn:
            relation_count = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(relation_count, 1)

        answer = self.store.answer_query(self.user_id, "risk budget local-first path", limit=2)
        citation_ids = [citation["id"] for citation in answer["citations"]]

        self.assertEqual(citation_ids[0], "mem_atlas_beta_decision")
        self.assertIn("mem_atlas_release_procedure", citation_ids)
        related = next(citation for citation in answer["citations"] if citation["id"] == "mem_atlas_release_procedure")
        self.assertEqual(related["relationship"]["kind"], "shared_entity")
        self.assertEqual(related["relationship"]["related_to_id"], "mem_atlas_beta_decision")

        context_pack = self.store.context_pack(self.user_id, query="risk budget local-first path", limit=2)
        self.assertIn("mem_atlas_beta_decision", context_pack)
        self.assertIn("mem_atlas_release_procedure", context_pack)
        self.assertIn("Related: shared_entity to mem_atlas_beta_decision.", context_pack)

        profile = self.store.personal_profile(self.user_id, query="risk budget local-first path", limit=2, include_pending=True)
        focus_ids = [item["id"] for item in profile["focus"]]
        self.assertEqual(focus_ids[0], "mem_atlas_beta_decision")
        self.assertIn("mem_atlas_release_procedure", focus_ids)
        related_focus = next(item for item in profile["focus"] if item["id"] == "mem_atlas_release_procedure")
        self.assertEqual(related_focus["relationship"]["kind"], "shared_entity")
        self.assertIn("Related: shared_entity to mem_atlas_beta_decision.", profile["markdown"])

        adaptation = self.store.agent_adaptation(self.user_id, query="risk budget local-first path", limit=2, include_pending=True)
        evidence_ids = [item["id"] for item in adaptation["evidence"]]
        self.assertIn("mem_atlas_release_procedure", evidence_ids)
        self.assertIn("Related: shared_entity to mem_atlas_beta_decision.", adaptation["markdown"])

        self.assertTrue(self.store.archive_capture(self.user_id, result["capture_id"]))
        with connect(self.db_path) as conn:
            relation_count_after_archive = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(relation_count_after_archive, 0)
        self.assertEqual(self.store.answer_query(self.user_id, "risk budget local-first path", limit=2)["citations"], [])

    def test_related_memory_links_across_source_records_by_entity(self) -> None:
        decision = self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas cross-record decision.",
            source="obsidian",
            source_url="file:///tmp/Atlas%20Decision.md",
            title="Atlas Decision",
            extracted={
                "_timestamp": "2026-06-29T10:00:00Z",
                "summary": "Cross-record decision fixture.",
                "records": [
                    {
                        "id": "mem_cross_atlas_decision",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Atlas decision: keep the local-first beta because the budget cap is strict.",
                        "summary": "Project Atlas local-first beta decision.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["Project Atlas", "beta"],
                        "entity_ids": ["project_atlas"],
                    }
                ],
                "tasks": [],
                "entities": [{"id": "project_atlas", "kind": "project", "name": "Project Atlas", "aliases": [], "context": ""}],
            },
        )
        procedure = self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas cross-record procedure.",
            source="obsidian",
            source_url="file:///tmp/Atlas%20Procedure.md",
            title="Atlas Procedure",
            extracted={
                "_timestamp": "2026-06-29T10:05:00Z",
                "summary": "Cross-record procedure fixture.",
                "records": [
                    {
                        "id": "mem_cross_atlas_procedure",
                        "kind": "procedure",
                        "layer": "procedural",
                        "content": "Before the Project Atlas beta release, run backend smoke, build the app, verify codesign, and confirm source citations.",
                        "summary": "Project Atlas beta release procedure.",
                        "confidence": "confirmed",
                        "importance": 3,
                        "topics": ["Project Atlas", "beta"],
                        "entity_ids": ["project_atlas"],
                    }
                ],
                "tasks": [],
                "entities": [{"id": "project_atlas", "kind": "project", "name": "Project Atlas", "aliases": [], "context": ""}],
            },
        )

        with connect(self.db_path) as conn:
            relation = conn.execute(
                """
                SELECT kind, source_memory_id, target_memory_id, metadata_json
                FROM memory_relations
                WHERE user_id = ?
                """,
                (self.user_id,),
            ).fetchone()
        self.assertIsNotNone(relation)
        self.assertEqual(relation["kind"], "shared_entity")
        self.assertEqual({relation["source_memory_id"], relation["target_memory_id"]}, {"mem_cross_atlas_decision", "mem_cross_atlas_procedure"})
        relation_metadata = json.loads(relation["metadata_json"])
        self.assertEqual(relation_metadata["shared_entities"], ["project_atlas"])
        self.assertEqual(relation_metadata["related_capture_id"], decision["capture_id"])

        answer = self.store.answer_query(self.user_id, "budget cap local-first beta", limit=2)
        citation_ids = [citation["id"] for citation in answer["citations"]]
        self.assertEqual(citation_ids[0], "mem_cross_atlas_decision")
        self.assertIn("mem_cross_atlas_procedure", citation_ids)
        related = next(citation for citation in answer["citations"] if citation["id"] == "mem_cross_atlas_procedure")
        self.assertEqual(related["relationship"]["kind"], "shared_entity")
        self.assertEqual(related["relationship"]["related_to_id"], "mem_cross_atlas_decision")

        context_pack = self.store.context_pack(self.user_id, query="budget cap local-first beta", limit=2)
        self.assertIn("mem_cross_atlas_decision", context_pack)
        self.assertIn("mem_cross_atlas_procedure", context_pack)
        self.assertIn("Related: shared_entity to mem_cross_atlas_decision.", context_pack)

        self.assertTrue(self.store.archive_capture(self.user_id, procedure["capture_id"]))
        with connect(self.db_path) as conn:
            relation_count = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(relation_count, 0)

    def test_entities_allow_same_id_across_users(self) -> None:
        for index, user_id in enumerate(("entity-user-a", "entity-user-b")):
            result = self.store.save_capture(
                user_id=user_id,
                content=f"{user_id} mentioned Shared Project.",
                source="unit-test",
                source_url=None,
                title="Shared entity",
                extracted={
                    "_timestamp": f"2026-06-29T12:00:0{index}Z",
                    "summary": "Shared entity test.",
                    "records": [],
                    "tasks": [],
                    "entities": [
                        {
                            "id": "project_shared",
                            "kind": "project",
                            "name": "Shared Project",
                            "aliases": [],
                            "context": f"{user_id} context",
                        }
                    ],
                },
            )
            self.assertEqual(result["entities"][0]["id"], "project_shared")

        with connect(self.db_path) as conn:
            table_info = conn.execute("PRAGMA table_info(entities)").fetchall()
            primary_key_columns = [
                row[1]
                for row in sorted((row for row in table_info if row[5]), key=lambda row: row[5])
            ]
            rows = conn.execute(
                "SELECT id, user_id, context FROM entities WHERE id = ? ORDER BY user_id",
                ("project_shared",),
            ).fetchall()

        self.assertEqual(primary_key_columns, ["user_id", "id"])
        self.assertEqual([row["user_id"] for row in rows], ["entity-user-a", "entity-user-b"])
        self.assertEqual({row["context"] for row in rows}, {"entity-user-a context", "entity-user-b context"})

    def test_ask_starter_queries_return_layer_intent_memories(self) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=(
                "We decided Project Starter should use five product tabs. "
                "I prefer source-backed answers with direct caveats. "
                "My writing style uses terse project notes. "
                "On June 29, 2026, Project Starter shipped the importer."
            ),
            source="docs",
            source_url="/tmp/starter-memory.md",
            title="Starter memory",
            extracted=extract_context(
                "We decided Project Starter should use five product tabs. "
                "I prefer source-backed answers with direct caveats. "
                "My writing style uses terse project notes. "
                "On June 29, 2026, Project Starter shipped the importer.",
                "docs",
            ),
        )

        cases = [
            ("What decisions should I remember?", "decision", "five product tabs"),
            ("What preferences have I stated?", "preference", "source-backed answers"),
            ("How do I usually write?", "style", "terse project notes"),
            ("What changed recently?", "episodic", "shipped the importer"),
        ]
        for query, expected_layer, expected_text in cases:
            with self.subTest(query=query):
                answer = self.store.answer_query(self.user_id, query, limit=3)
                self.assertTrue(answer["citations"])
                self.assertEqual(answer["citations"][0]["layer"], expected_layer)
                self.assertIn(expected_text, answer["citations"][0]["excerpt"])

    def test_task_intent_ask_returns_open_loop_citation(self) -> None:
        saved = self.store.save_capture(
            user_id=self.user_id,
            content="Meeting notes: Follow up with Dana about the API keys rotation before Friday.",
            source="slack",
            source_url="slack://channel/C123/p202606291200",
            title="Platform sync",
            extracted={
                "_timestamp": "2026-06-29T12:00:00Z",
                "summary": "Platform sync follow-up.",
                "records": [],
                "tasks": [
                    {
                        "id": "task_api_key_rotation",
                        "kind": "action",
                        "content": "Follow up with Dana about the API keys rotation before Friday.",
                        "status": "open",
                        "importance": 4,
                        "topics": ["api-keys", "platform"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )
        self.assertTrue(self.store.approve_capture(self.user_id, saved["capture_id"]))

        answer = self.store.answer_query(self.user_id, "What open loops do I have about API keys?", limit=5)

        self.assertTrue(answer["citations"])
        self.assertEqual(answer["citations"][0]["result_type"], "task")
        self.assertEqual(answer["citations"][0]["layer"], "task")
        self.assertEqual(answer["citations"][0]["status"], "open")
        self.assertEqual(answer["citations"][0]["source"], "slack")
        self.assertTrue(answer["citations"][0]["source_url"].startswith("slack://channel/C123/p202606291200"))
        self.assertIn("line=1", answer["citations"][0]["source_url"])
        self.assertIn("excerpt=", answer["citations"][0]["source_url"])
        self.assertIn("API keys rotation", answer["citations"][0]["excerpt"])
        self.assertEqual(answer["results"][0]["result_type"], "task")
        self.assertTrue(answer["results"][0]["source_url"].startswith("slack://channel/C123/p202606291200"))
        self.assertIn("line=1", answer["results"][0]["source_url"])
        self.assertIn("excerpt=", answer["results"][0]["source_url"])

    def test_task_results_do_not_pad_non_task_memory_search(self) -> None:
        memory = self.store.save_capture(
            user_id=self.user_id,
            content="I prefer source-backed answers with direct caveats.",
            source="notes",
            source_url="file:///tmp/preferences.md",
            title="Answer preferences",
            extracted={
                "_timestamp": "2026-06-29T12:10:00Z",
                "summary": "Answer preferences.",
                "records": [
                    {
                        "id": "mem_source_backed_answers",
                        "kind": "preference",
                        "layer": "preference",
                        "content": "I prefer source-backed answers with direct caveats.",
                        "summary": "Source-backed answers with caveats.",
                        "confidence": "confirmed",
                        "importance": 4,
                        "topics": ["answers"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )
        task = self.store.save_capture(
            user_id=self.user_id,
            content="Follow up on source-backed answers in the onboarding QA list.",
            source="notes",
            source_url="file:///tmp/tasks.md",
            title="QA tasks",
            extracted={
                "_timestamp": "2026-06-29T12:11:00Z",
                "summary": "QA tasks.",
                "records": [],
                "tasks": [
                    {
                        "id": "task_source_backed_answers",
                        "kind": "action",
                        "content": "Follow up on source-backed answers in the onboarding QA list.",
                        "status": "open",
                        "importance": 5,
                        "topics": ["answers"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )
        self.assertTrue(self.store.approve_capture(self.user_id, memory["capture_id"]))
        self.assertTrue(self.store.approve_capture(self.user_id, task["capture_id"]))

        results = self.store.search(self.user_id, "source-backed answers direct caveats", limit=5)

        self.assertTrue(results)
        self.assertTrue(any(item["id"] == "mem_source_backed_answers" for item in results))
        self.assertFalse(any(item.get("result_type") == "task" for item in results))

    def test_task_ask_obeys_pending_review_policy(self) -> None:
        self.store.update_settings(self.user_id, {"allow_pending_in_context": False})
        saved = self.store.save_capture(
            user_id=self.user_id,
            content="Open question: should Cortex rotate API keys monthly?",
            source="email",
            source_url="message://api-key-policy",
            title="API key policy",
            extracted={
                "_timestamp": "2026-06-29T12:20:00Z",
                "summary": "API key policy question.",
                "records": [],
                "tasks": [
                    {
                        "id": "task_pending_api_key_policy",
                        "kind": "question",
                        "content": "Should Cortex rotate API keys monthly?",
                        "status": "open",
                        "importance": 3,
                        "topics": ["api-keys", "security"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )

        pending_answer = self.store.answer_query(self.user_id, "What open questions are there about API keys?", limit=5)
        self.assertEqual(pending_answer["citations"], [])

        self.assertTrue(self.store.approve_capture(self.user_id, saved["capture_id"]))
        approved_answer = self.store.answer_query(self.user_id, "What open questions are there about API keys?", limit=5)

        self.assertTrue(approved_answer["citations"])
        self.assertEqual(approved_answer["citations"][0]["result_type"], "task")
        self.assertTrue(approved_answer["citations"][0]["source_url"].startswith("message://api-key-policy"))
        self.assertIn("line=1", approved_answer["citations"][0]["source_url"])
        self.assertIn("excerpt=", approved_answer["citations"][0]["source_url"])

    def test_api_and_mcp_tokens_are_audience_scoped(self) -> None:
        api_token = "cxa_storage_lifecycle_token_123456789"
        mcp_token = "cxm_storage_lifecycle_token_123456789"

        api_metadata = self.store.ensure_api_token(self.user_id, api_token, label="Unit REST token", scopes=["read", "write"])
        mcp_metadata = self.store.ensure_mcp_token(self.user_id, mcp_token, label="Unit MCP token", scopes=["read"])

        self.assertEqual(api_metadata["audience"], "api")
        self.assertEqual(mcp_metadata["audience"], "mcp")
        self.assertEqual(self.store.authenticate_api_token(api_token)["user_id"], self.user_id)
        self.assertEqual(self.store.authenticate_mcp_token(mcp_token)["user_id"], self.user_id)
        self.assertIsNone(self.store.authenticate_api_token(mcp_token))
        self.assertIsNone(self.store.authenticate_mcp_token(api_token))

        active_api_tokens = self.store.list_tokens(self.user_id, audience="api")
        self.assertEqual([token["token_id"] for token in active_api_tokens], [api_metadata["token_id"]])
        self.assertNotIn("token_hash", active_api_tokens[0])

        revoked = self.store.revoke_token(self.user_id, api_metadata["token_id"])
        self.assertTrue(revoked["revoked"])
        self.assertIsNone(self.store.authenticate_api_token(api_token))
        self.assertEqual(self.store.list_tokens(self.user_id, audience="api"), [])
        self.assertEqual(self.store.list_tokens(self.user_id, audience="api", include_revoked=True)[0]["revoked_at"], revoked["revoked_at"])

    def test_source_accounts_and_sync_cursors_track_connector_health(self) -> None:
        catalog = {item["id"]: item for item in self.store.source_connector_catalog()}
        self.assertIn("gmail", catalog)
        self.assertIn("notion", catalog)
        self.assertIn(catalog["chatgpt"]["import_status"], {"native", "generic", "export_only"})
        for source_id in ("gemini", "perplexity", "copilot", "grok", "poe", "notebooklm"):
            self.assertIn(source_id, catalog)
            self.assertEqual(catalog[source_id]["category"], "AI chats")
            self.assertEqual(catalog[source_id]["source_ids"], [source_id])
            self.assertTrue(catalog[source_id]["supports_import"])
            self.assertEqual(catalog[source_id]["import_status"], "generic")
            self.assertFalse(catalog[source_id]["primary_beta"])
            self.assertEqual(catalog[source_id]["beta_status"], "needs-connector")

        advertised_source_ids = {source_id for item in catalog.values() for source_id in item["source_ids"]}
        for source_id in (
            "chatgpt",
            "claude",
            "gemini",
            "perplexity",
            "copilot",
            "grok",
            "poe",
            "notebooklm",
            "email",
            "docs",
            "cloud-docs",
            "notion",
            "slack",
            "calendar",
            "github",
        ):
            self.assertIn(source_id, advertised_source_ids)
        self.assertEqual(catalog["gmail"]["source_ids"], ["email"])
        self.assertEqual(catalog["gmail"]["export_status"], "native_via_email")
        self.assertTrue(catalog["gmail"]["supports_import"])
        self.assertFalse(catalog["gmail"]["primary_beta"])
        self.assertEqual(catalog["gmail"]["beta_status"], "planned")
        self.assertEqual(catalog["gmail"]["primary_beta_path"], "account-sign-in-planned")
        self.assertFalse(catalog["gmail"]["show_in_primary_ui"])
        self.assertTrue(catalog["obsidian"]["primary_beta"])
        self.assertEqual(catalog["obsidian"]["beta_status"], "ready")
        self.assertEqual(catalog["obsidian"]["primary_beta_path"], "native-local-connector")
        self.assertTrue(catalog["obsidian"]["show_in_primary_ui"])
        self.assertIn("Gmail account records", catalog["gmail"]["import_label"])
        self.assertIn("cloud-docs", catalog["google-drive"]["source_ids"])
        self.assertIn("docs", catalog["google-drive"]["source_ids"])
        self.assertEqual(catalog["google-drive"]["live_status"], "planned")
        self.assertEqual(catalog["google-drive"]["export_status"], "generic")
        self.assertTrue(catalog["github"]["formats"])
        catalog_display_text = "\n".join(
            str(value)
            for item in catalog.values()
            for value in [
                item.get("name"),
                item.get("notes"),
                item.get("first_100_note"),
                item.get("import_label"),
                *(item.get("permissions_required") or []),
            ]
            if value
        ).lower()
        for manual_intake_term in (
            "takeout",
            "selected export",
            "manual import",
            "file upload",
            "files or folders",
            "selected files",
            "choose file",
            "choose folder",
            "upload",
            "user-selected",
        ):
            self.assertNotIn(manual_intake_term, catalog_display_text)

        readiness = self.store.source_readiness_report(self.user_id)
        self.assertGreaterEqual(readiness["summary"]["primary_beta_ready"], 1)
        self.assertGreaterEqual(readiness["summary"]["planned_connectors"], 1)
        self.assertGreaterEqual(readiness["summary"]["advanced_fallback_only"], 1)
        self.assertGreaterEqual(readiness["summary"]["connector_needed"], 1)
        gmail_readiness = next(item for item in readiness["sources"] if item["source"] == "gmail")
        self.assertEqual(gmail_readiness["status"], "planned")
        self.assertEqual(gmail_readiness["beta_status"], "planned")
        self.assertFalse(gmail_readiness["primary_beta"])
        self.assertFalse(gmail_readiness["show_in_primary_ui"])
        self.assertEqual(gmail_readiness["sync_plan"]["mode"], "planned_account_sync")
        self.assertEqual(gmail_readiness["sync_plan"]["managed_sync_status"], "planned")
        self.assertIsNone(gmail_readiness["sync_plan"]["credential_ref"])
        self.assertEqual(gmail_readiness["source_ids"], ["email"])
        self.assertEqual(gmail_readiness["export_status"], "native_via_email")
        self.assertIn("Account sign-in sync is planned", gmail_readiness["next_action"])
        for source_id in ("gemini", "perplexity", "copilot", "grok", "poe", "notebooklm"):
            ai_readiness = next(item for item in readiness["sources"] if item["source"] == source_id)
            self.assertEqual(ai_readiness["status"], "connector_needed")
            self.assertEqual(ai_readiness["beta_status"], "needs-connector")
            self.assertFalse(ai_readiness["primary_beta"])
            self.assertEqual(ai_readiness["source_ids"], [source_id])
            self.assertIn("direct connector", ai_readiness["next_action"].lower())
        obsidian_readiness = next(item for item in readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(obsidian_readiness["status"], "import_ready")
        self.assertEqual(obsidian_readiness["beta_status"], "ready")
        self.assertTrue(obsidian_readiness["primary_beta"])
        self.assertTrue(obsidian_readiness["show_in_primary_ui"])
        readiness_display_text = "\n".join(
            str(value)
            for item in readiness["sources"]
            for value in [
                item.get("next_action"),
                item.get("first_100_note"),
                item.get("import_label"),
                *(item.get("permissions_required") or []),
            ]
            if value
        ).lower()
        for manual_intake_term in (
            "takeout",
            "selected export",
            "manual import",
            "file upload",
            "files or folders",
            "selected files",
            "choose file",
            "choose folder",
            "upload",
            "user-selected",
        ):
            self.assertNotIn(manual_intake_term, readiness_display_text)

        account = self.store.upsert_source_account(
            self.user_id,
            source="Gmail",
            account_label="Work Gmail",
            account_identifier="user@example.com",
            connection_type="oauth",
            status="connected",
            auth_state="healthy",
            policy={"sync": "metadata_and_content", "include_attachments": False},
            metadata={"workspace": "doppl"},
        )
        connected_readiness = self.store.source_readiness_report(self.user_id)
        connected_gmail = next(item for item in connected_readiness["sources"] if item["source"] == "gmail")
        self.assertEqual(connected_gmail["status"], "planned")
        self.assertEqual(connected_gmail["beta_status"], "planned")
        self.assertFalse(connected_gmail["primary_beta"])
        self.assertFalse(connected_gmail["show_in_primary_ui"])

        self.assertTrue(account["id"].startswith("sacct_"))
        self.assertEqual(account["source"], "gmail")
        self.assertEqual(account["status"], "planned")
        self.assertEqual(account["auth_state"], "not_configured")
        self.assertEqual(account["account_label"], "Work Gmail")
        self.assertEqual(account["policy"]["sync"], "metadata_and_content")
        self.assertEqual(account["metadata"]["requested_status"], "connected")
        self.assertEqual(account["metadata"]["requested_auth_state"], "healthy")
        self.assertEqual(account["metadata"]["connector_state"], "planned_until_records_sync")
        self.assertIsNone(account["last_sync_at"])
        self.assertEqual([item["id"] for item in self.store.list_source_accounts(self.user_id)], [account["id"]])

        cursor = self.store.upsert_sync_cursor(
            self.user_id,
            source="gmail",
            source_account_id=account["id"],
            cursor_name="messages",
            cursor_value="page-token-1",
            high_water_mark="2026-06-29T10:00:00Z",
            state={"page": 1},
            completed=True,
        )
        self.assertTrue(cursor["id"].startswith("sync_"))
        self.assertEqual(cursor["source_account_id"], account["id"])
        self.assertEqual(cursor["source"], "gmail")
        self.assertEqual(cursor["cursor_name"], "messages")
        self.assertEqual(cursor["state"]["page"], 1)
        self.assertIsNotNone(cursor["last_completed_at"])

        synced_account = self.store.list_source_accounts(self.user_id)[0]
        self.assertIsNotNone(synced_account["last_sync_at"])
        self.assertIsNone(synced_account["last_error"])
        synced_readiness = self.store.source_readiness_report(self.user_id)
        synced_gmail = next(item for item in synced_readiness["sources"] if item["source"] == "gmail")
        self.assertEqual(synced_gmail["status"], "planned")
        self.assertEqual(synced_gmail["beta_status"], "planned")
        self.assertFalse(synced_gmail["primary_beta"])
        self.assertFalse(synced_gmail["show_in_primary_ui"])
        self.assertEqual(synced_gmail["primary_beta_path"], "account-sign-in-planned")
        self.assertEqual(synced_gmail["sync_plan"]["mode"], "planned_account_sync")
        self.assertEqual(synced_gmail["sync_plan"]["managed_sync_status"], "planned")
        self.assertEqual(synced_gmail["sync_plan"]["credential_ref"], f"source_account:{account['id']}")
        self.assertIsNotNone(synced_gmail["sync_plan"]["last_completed_at"])

        failed = self.store.upsert_sync_cursor(
            self.user_id,
            source="gmail",
            source_account_id=account["id"],
            cursor_name="messages",
            cursor_value="page-token-1",
            high_water_mark="2026-06-29T10:00:00Z",
            last_error="rate limited",
            completed=False,
        )
        self.assertEqual(failed["last_error"], "rate limited")
        self.assertIsNone(failed["last_completed_at"])
        self.assertEqual(self.store.list_source_accounts(self.user_id)[0]["last_error"], "rate limited")

        sync_result = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided connected Gmail sync should feed Cortex without manual file import.",
                    "title": "Gmail decision",
                    "external_id": "msg-001",
                    "captured_at": "2026-06-29T12:30:00Z",
                }
            ],
            cursor_name="messages",
            cursor_value="page-token-2",
            high_water_mark="2026-06-29T12:30:00Z",
            state={"page": 2},
            processing="sync",
        )
        self.assertEqual(sync_result["status"], "complete")
        self.assertEqual(sync_result["saved"], 1)
        self.assertEqual(sync_result["queued"], 0)
        self.assertEqual(sync_result["skipped"], 0)
        self.assertTrue(sync_result["records"][0]["source_url"].startswith(f"source-account://gmail/{account['id']}/msg-001"))
        self.assertEqual(sync_result["cursor"]["cursor_value"], "page-token-2")
        self.assertEqual(sync_result["cursor"]["state"]["last_batch_saved"], 1)
        recovered_account = self.store.list_source_accounts(self.user_id)[0]
        self.assertEqual(recovered_account["status"], "connected")
        self.assertIsNone(recovered_account["last_error"])

        duplicate_result = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided connected Gmail sync should feed Cortex without manual file import.",
                    "title": "Gmail decision",
                    "external_id": "msg-001",
                    "captured_at": "2026-06-29T12:30:00Z",
                }
            ],
            cursor_name="messages",
            processing="sync",
        )
        self.assertEqual(duplicate_result["status"], "complete")
        self.assertEqual(duplicate_result["saved"], 0)
        self.assertEqual(duplicate_result["skipped"], 1)
        self.assertEqual(duplicate_result["records"][0]["status"], "duplicate")

        updated_result = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided connected Gmail sync should update the same Cortex record when remote content changes.",
                    "title": "Gmail decision updated",
                    "external_id": "msg-001",
                    "captured_at": "2026-06-29T12:45:00Z",
                }
            ],
            cursor_name="messages",
            processing="sync",
        )
        self.assertEqual(updated_result["status"], "complete")
        self.assertEqual(updated_result["saved"], 1)
        self.assertEqual(updated_result["records"][0]["status"], "updated")
        self.assertEqual(updated_result["records"][0]["capture_id"], sync_result["records"][0]["capture_id"])
        self.assertEqual(self.store.search(self.user_id, "manual file import"), [])
        self.assertEqual(self.store.search(self.user_id, "remote content changes", limit=5), [])
        self.assertTrue(self.store.approve_capture(self.user_id, updated_result["records"][0]["capture_id"]))
        self.assertTrue(self.store.search(self.user_id, "remote content changes", limit=5))
        with connect(self.db_path) as conn:
            capture_rows = conn.execute(
                """
                SELECT id, source_account_id, external_id, raw_hash
                FROM captures
                WHERE user_id = ? AND source = ? AND external_id = ?
                """,
                (self.user_id, "gmail", "msg-001"),
            ).fetchall()
            stale_memory = conn.execute(
                """
                SELECT id, status, valid_to, superseded_by
                FROM memories
                WHERE user_id = ?
                  AND capture_id = ?
                  AND content LIKE '%manual file import%'
                LIMIT 1
                """,
                (self.user_id, sync_result["records"][0]["capture_id"]),
            ).fetchone()
            replacement_memory = conn.execute(
                """
                SELECT id, status
                FROM memories
                WHERE user_id = ?
                  AND capture_id = ?
                  AND content LIKE '%remote content changes%'
                LIMIT 1
                """,
                (self.user_id, sync_result["records"][0]["capture_id"]),
            ).fetchone()
        self.assertEqual(len(capture_rows), 1)
        self.assertEqual(capture_rows[0]["source_account_id"], account["id"])
        self.assertIsNotNone(stale_memory)
        self.assertIsNotNone(replacement_memory)
        self.assertEqual(stale_memory["status"], "archived")
        self.assertEqual(stale_memory["valid_to"], "2026-06-29T12:45:00Z")
        self.assertEqual(stale_memory["superseded_by"], replacement_memory["id"])
        self.assertEqual(replacement_memory["status"], "active")

        metadata_refresh_result = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided connected Gmail sync should update the same Cortex record when remote content changes.",
                    "title": "Gmail decision retitled",
                    "external_id": "msg-001",
                    "source_url": "service://gmail/messages/msg-001?thread=updated",
                    "captured_at": "2026-06-29T12:50:00Z",
                    "metadata": {"relative_path": "Gmail/Updated Thread.eml", "line_start": 12, "tags": ["connector-refresh"]},
                }
            ],
            cursor_name="messages",
            processing="sync",
        )
        self.assertEqual(metadata_refresh_result["status"], "complete")
        self.assertEqual(metadata_refresh_result["saved"], 1)
        self.assertEqual(metadata_refresh_result["skipped"], 0)
        self.assertEqual(metadata_refresh_result["records"][0]["status"], "updated")
        self.assertEqual(metadata_refresh_result["records"][0]["capture_id"], sync_result["records"][0]["capture_id"])
        refreshed_results = self.store.search(self.user_id, "remote content changes", limit=5)
        self.assertTrue(refreshed_results)
        self.assertIn("thread=updated", refreshed_results[0]["source_url"])
        self.assertIn("line=", refreshed_results[0]["source_url"])
        with connect(self.db_path) as conn:
            refreshed_capture = conn.execute(
                "SELECT title, source_url, review_status, approved_at FROM captures WHERE user_id = ? AND id = ?",
                (self.user_id, sync_result["records"][0]["capture_id"]),
            ).fetchone()
            refreshed_memory_rows = conn.execute(
                """
                SELECT source_url, status, valid_to, provenance_json, topics_json
                FROM memories
                WHERE user_id = ?
                  AND capture_id = ?
                  AND content LIKE '%remote content changes%'
                """,
                (self.user_id, sync_result["records"][0]["capture_id"]),
            ).fetchall()
        self.assertEqual(refreshed_capture["title"], "Gmail decision retitled")
        self.assertEqual(refreshed_capture["source_url"], "service://gmail/messages/msg-001?thread=updated")
        self.assertEqual(refreshed_capture["review_status"], "approved")
        self.assertIsNotNone(refreshed_capture["approved_at"])
        self.assertEqual(len(refreshed_memory_rows), 1)
        self.assertEqual(refreshed_memory_rows[0]["status"], "active")
        self.assertFalse(refreshed_memory_rows[0]["valid_to"])
        self.assertIn("thread=updated", refreshed_memory_rows[0]["source_url"])
        refreshed_provenance = json.loads(refreshed_memory_rows[0]["provenance_json"])
        self.assertEqual(refreshed_provenance["record_metadata"]["relative_path"], "Gmail/Updated Thread.eml")
        self.assertEqual(refreshed_provenance["record_metadata"]["line_start"], 12)
        self.assertIn("connector refresh", json.loads(refreshed_memory_rows[0]["topics_json"]))

        repeated_text_result = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "Repeated boilerplate can appear in two separate service records.",
                    "title": "Repeated A",
                    "external_id": "msg-repeat-a",
                },
                {
                    "content": "Repeated boilerplate can appear in two separate service records.",
                    "title": "Repeated B",
                    "external_id": "msg-repeat-b",
                },
            ],
            cursor_name="messages",
            processing="sync",
        )
        self.assertEqual(repeated_text_result["saved"], 2)
        self.assertEqual([record["status"] for record in repeated_text_result["records"]], ["saved", "saved"])
        self.assertNotEqual(repeated_text_result["records"][0]["capture_id"], repeated_text_result["records"][1]["capture_id"])
        self.assertEqual([item["id"] for item in self.store.list_sync_cursors(self.user_id, source_account_id=account["id"])], [cursor["id"]])
        with self.assertRaises(ValueError):
            self.store.upsert_sync_cursor(
                self.user_id,
                source="gmail",
                source_account_id="sacct_missing",
                cursor_name="messages",
            )

        backup = self.store.create_backup(self.user_id)
        self.assertTrue(Path(backup["backup_path"]).is_file())

        deleted = self.store.delete_user_data(self.user_id, include_backups=False)
        self.assertEqual(deleted["sqlite"]["source_accounts"], 1)
        self.assertEqual(deleted["sqlite"]["sync_cursors"], 1)
        self.assertEqual(deleted["vault"]["source_accounts"], 1)
        self.assertEqual(deleted["vault"]["sync_cursors"], 1)
        self.assertEqual(self.store.list_source_accounts(self.user_id, include_disconnected=True), [])
        self.assertEqual(self.store.list_sync_cursors(self.user_id), [])

        restored = self.store.restore_latest_backup(self.user_id)
        self.assertEqual(restored["rebuild"]["source_accounts"], 1)
        self.assertEqual(restored["rebuild"]["sync_cursors"], 1)
        self.assertEqual(self.store.list_source_accounts(self.user_id)[0]["id"], account["id"])
        self.assertEqual(self.store.list_sync_cursors(self.user_id)[0]["id"], cursor["id"])
        with connect(self.db_path) as conn:
            restored_capture = conn.execute(
                """
                SELECT source_account_id, external_id
                FROM captures
                WHERE user_id = ? AND source = ? AND external_id = ?
                """,
                (self.user_id, "gmail", "msg-001"),
            ).fetchone()
        self.assertIsNotNone(restored_capture)
        self.assertEqual(restored_capture["source_account_id"], account["id"])
        self.assertEqual(restored_capture["external_id"], "msg-001")

        disconnected = self.store.disconnect_source_account(self.user_id, account["id"])
        self.assertEqual(disconnected["status"], "disconnected")
        self.assertEqual(disconnected["auth_state"], "revoked")
        self.assertEqual(self.store.list_source_accounts(self.user_id), [])
        self.assertEqual(self.store.list_source_accounts(self.user_id, include_disconnected=True)[0]["id"], account["id"])

        rebuilt = self.store.rebuild_index_from_vault(self.user_id)
        self.assertEqual(rebuilt["source_accounts"], 1)
        self.assertEqual(rebuilt["sync_cursors"], 1)
        self.assertEqual(self.store.list_source_accounts(self.user_id), [])
        self.assertEqual(self.store.list_source_accounts(self.user_id, include_disconnected=True)[0]["status"], "disconnected")
        with connect(self.db_path) as conn:
            rebuilt_capture = conn.execute(
                """
                SELECT source_account_id, external_id
                FROM captures
                WHERE user_id = ? AND source = ? AND external_id = ?
                """,
                (self.user_id, "gmail", "msg-001"),
            ).fetchone()
        self.assertIsNotNone(rebuilt_capture)
        self.assertEqual(rebuilt_capture["source_account_id"], account["id"])
        self.assertEqual(rebuilt_capture["external_id"], "msg-001")

        events = self.store.audit_log(self.user_id, limit=20)
        event_pairs = {(event["object_type"], event["event_type"]) for event in events}
        self.assertIn(("source_account", "upserted"), event_pairs)
        self.assertIn(("sync_cursor", "updated"), event_pairs)

    def test_direct_source_sync_uses_current_account_identity_only(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        sarpt = self.store.upsert_source_account(
            self.user_id,
            source="slack",
            account_label="Sarpt Tandoven",
            account_identifier="U1",
            connection_type="api-token",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
            metadata={"real_name": "Sarpt Tandoven", "user_name": "sarpt", "email": "sarpt@example.com"},
        )
        self.store.upsert_source_account(
            self.user_id,
            source="slack",
            account_label="Dana Partner",
            account_identifier="U2",
            connection_type="api-token",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
            metadata={"real_name": "Dana Partner", "user_name": "dana", "email": "dana@example.com"},
        )

        synced = self.store.sync_source_account_records(
            self.user_id,
            sarpt["id"],
            records=[
                {
                    "content": (
                        "Sarpt Tandoven: I prefer source-account memory to use crisp cited answers. "
                        "Dana Partner: I prefer sprawling stakeholder recaps for everyone."
                    ),
                    "title": "Slack mixed-account identity",
                    "external_id": "slack-mixed-account-identity",
                    "source_url": "https://doppl.slack.com/archives/C123/p1782739200000100",
                    "captured_at": "2026-06-30T10:00:00Z",
                    "metadata": {"connector": "slack", "line_start": 9, "line_end": 10},
                }
            ],
            cursor_name="messages",
            processing="sync",
        )

        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["saved"], 1)
        user_hits = self.store.search(self.user_id, "source-account memory crisp cited answers", limit=5)
        user_hit = next((hit for hit in user_hits if hit["kind"] == "preference"), None)
        self.assertIsNotNone(user_hit)
        self.assertEqual(user_hit["provenance"]["source_account_id"], sarpt["id"])
        self.assertEqual(user_hit["provenance"]["external_id"], "slack-mixed-account-identity")

        teammate_hits = self.store.search(self.user_id, "sprawling stakeholder recaps for everyone", limit=5)
        self.assertFalse(any(hit["kind"] == "preference" for hit in teammate_hits))

        answer = self.store.answer_query(self.user_id, "How should source-account memory answer?", limit=5)
        citation = next((item for item in answer["citations"] if item.get("source_account_id") == sarpt["id"]), None)
        self.assertIsNotNone(citation)
        self.assertEqual(citation["external_id"], "slack-mixed-account-identity")
        self.assertEqual(citation["source_record_id"], "slack-mixed-account-identity")
        self.assertEqual(citation["line_start"], 9)

    def test_source_account_memory_ids_are_scoped_per_account(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        first = self.store.upsert_source_account(
            self.user_id,
            source="slack",
            account_label="First Workspace User",
            account_identifier="U-first",
            connection_type="api-token",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
            metadata={"real_name": "First Workspace User", "user_name": "first"},
        )
        second = self.store.upsert_source_account(
            self.user_id,
            source="slack",
            account_label="Second Workspace User",
            account_identifier="U-second",
            connection_type="api-token",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
            metadata={"real_name": "Second Workspace User", "user_name": "second"},
        )
        record_payload = {
            "title": "Shared connector memory collision",
            "external_id": "shared-connector-record",
            "captured_at": "2026-06-30T10:05:00Z",
        }

        self.store.sync_source_account_records(
            self.user_id,
            first["id"],
            records=[
                {
                    **record_payload,
                    "content": "First Workspace User: I decided shared connector memory collision should preserve account provenance.",
                    "source_url": "https://doppl.slack.com/archives/C123/p1782739500000100",
                }
            ],
            cursor_name="messages",
            processing="sync",
        )
        self.store.sync_source_account_records(
            self.user_id,
            second["id"],
            records=[
                {
                    **record_payload,
                    "content": "Second Workspace User: I decided shared connector memory collision should preserve account provenance.",
                    "source_url": "https://other.slack.com/archives/C999/p1782739500000100",
                }
            ],
            cursor_name="messages",
            processing="sync",
        )

        with connect(self.db_path) as conn:
            captures = conn.execute(
                """
                SELECT id, source_account_id, external_id
                FROM captures
                WHERE user_id = ? AND source = ? AND external_id = ?
                ORDER BY source_account_id
                """,
                (self.user_id, "slack", "shared-connector-record"),
            ).fetchall()
            rows = conn.execute(
                """
                SELECT id, capture_id, source_url, provenance_json
                FROM memories
                WHERE user_id = ?
                  AND content LIKE '%shared connector memory collision%'
                  AND status = 'active'
                ORDER BY source_url
                """,
                (self.user_id,),
            ).fetchall()

        self.assertEqual(len(captures), 2)
        self.assertEqual({capture["source_account_id"] for capture in captures}, {first["id"], second["id"]})
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row["id"] for row in rows}), 2)
        provenances = [json.loads(row["provenance_json"]) for row in rows]
        self.assertEqual({item["source_account_id"] for item in provenances}, {first["id"], second["id"]})
        self.assertEqual({item["external_id"] for item in provenances}, {"shared-connector-record"})

    def test_same_extracted_memory_id_from_two_source_accounts_does_not_overwrite_provenance(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        first = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="First Gmail",
            account_identifier="first@example.com",
            connection_type="mcp",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
        )
        second = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Second Gmail",
            account_identifier="second@example.com",
            connection_type="mcp",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
        )
        extracted = {
            "summary": "Shared extracted memory id",
            "_timestamp": "2026-06-30T10:10:00Z",
            "records": [
                {
                    "id": "mem_shared_extracted_id",
                    "kind": "decision",
                    "content": "I decided source-account memory IDs must preserve account-specific provenance.",
                    "summary": "Source account provenance must survive memory id collisions.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "topics": ["source-sync"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        }

        first_result = self.store.save_capture(
            user_id=self.user_id,
            content="First account: I decided source-account memory IDs must preserve account-specific provenance.",
            source="gmail",
            source_url="source-account://gmail/first/msg-collision",
            title="First collision",
            extracted=extracted,
            source_account_id=first["id"],
            external_id="msg-collision",
        )
        second_result = self.store.save_capture(
            user_id=self.user_id,
            content="Second account: I decided source-account memory IDs must preserve account-specific provenance.",
            source="gmail",
            source_url="source-account://gmail/second/msg-collision",
            title="Second collision",
            extracted=extracted,
            source_account_id=second["id"],
            external_id="msg-collision",
        )

        self.assertNotEqual(first_result["memories"][0]["id"], "mem_shared_extracted_id")
        self.assertNotEqual(second_result["memories"][0]["id"], "mem_shared_extracted_id")
        self.assertNotEqual(first_result["memories"][0]["id"], second_result["memories"][0]["id"])
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT id, source_url, provenance_json
                FROM memories
                WHERE user_id = ?
                  AND content = ?
                  AND status = 'active'
                ORDER BY source_url
                """,
                (
                    self.user_id,
                    "I decided source-account memory IDs must preserve account-specific provenance.",
                ),
            ).fetchall()
        self.assertEqual(len(rows), 2)
        provenances = [json.loads(row["provenance_json"]) for row in rows]
        self.assertEqual({item["source_account_id"] for item in provenances}, {first["id"], second["id"]})
        self.assertEqual({item["external_id"] for item in provenances}, {"msg-collision"})
        first_answer = self.store.answer_query(
            self.user_id,
            "What did I decide about source-account memory IDs?",
            source_account_id=first["id"],
        )
        self.assertEqual(first_answer["citations"][0]["source_account_id"], first["id"])
        second_answer = self.store.answer_query(
            self.user_id,
            "What did I decide about source-account memory IDs?",
            source_account_id=second["id"],
        )
        self.assertEqual(second_answer["citations"][0]["source_account_id"], second["id"])

    def test_disconnect_retains_local_memory_credentials_and_citations_but_stops_sync(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        account_id = "sacct_disconnect_github"
        account = self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="doppl-tech/cortex-app",
            account_identifier="doppl-tech/cortex-app",
            connection_type="api-token",
            status="connected",
            auth_state="authorized",
            account_id=account_id,
            policy={"review_required": False},
            metadata={
                "credential_ref": f"source_credential:{account_id}",
                "repositories": ["doppl-tech/cortex-app"],
                "sync_interval_seconds": 60,
            },
        )
        self.store.store_source_account_credential(
            self.user_id,
            account["id"],
            source="github",
            payload={"token": "github_disconnect_secret", "repositories": ["doppl-tech/cortex-app"]},
        )
        synced = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided disconnected source accounts should retain local Cortex memory for Ask.",
                    "title": "Disconnect retention issue",
                    "external_id": "issue-101",
                    "source_url": "https://github.com/doppl-tech/cortex-app/issues/101",
                    "captured_at": "2026-06-30T11:00:00Z",
                    "metadata": {"repository": "doppl-tech/cortex-app", "line_start": 4},
                }
            ],
            cursor_name="issues",
            cursor_value="issue-cursor-101",
            processing="sync",
        )
        self.assertEqual(synced["saved"], 1)
        self.assertTrue(self.store.search(self.user_id, "retain local Cortex memory", source_account_id=account["id"]))

        queued = self.store.enqueue_source_account_sync(
            self.user_id,
            account["id"],
            cursor_name="issues",
            schedule_token="pre-disconnect",
        )
        self.assertEqual(queued["job_type"], "source_account_sync")

        disconnected = self.store.disconnect_source_account(self.user_id, account["id"])
        self.assertEqual(disconnected["status"], "disconnected")
        self.assertEqual(disconnected["retention"]["disconnect_action"], "pause_sync")
        self.assertIn("memories", disconnected["retention"]["disconnect_retains"])
        self.assertIn("local_credentials", disconnected["retention"]["disconnect_retains"])
        self.assertEqual(self.store.list_source_accounts(self.user_id), [])
        self.assertEqual(self.store.list_source_accounts(self.user_id, include_disconnected=True)[0]["id"], account["id"])

        credential = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=account["id"])
        self.assertEqual(credential["payload"]["token"], "github_disconnect_secret")
        self.assertTrue(self.store.search(self.user_id, "retain local Cortex memory", source_account_id=account["id"]))
        answer = self.store.answer_query(self.user_id, "What did I decide about disconnected source accounts?", source_account_id=account["id"])
        citation = next((item for item in answer["citations"] if item.get("source_account_id") == account["id"]), None)
        self.assertIsNotNone(citation)
        self.assertEqual(citation["external_id"], "issue-101")
        self.assertEqual(citation["source_record_id"], "issue-101")

        with self.assertRaisesRegex(ValueError, "source account is disconnected"):
            self.store.sync_source_account_records(
                self.user_id,
                account["id"],
                records=[
                    {
                        "content": "This should not be read after disconnect.",
                        "external_id": "issue-102",
                    }
                ],
                processing="sync",
            )
        with self.assertRaisesRegex(ValueError, "source account is disconnected"):
            self.store.enqueue_source_account_sync(self.user_id, account["id"], cursor_name="issues")

        scheduled = self.store.enqueue_due_source_syncs(self.user_id, limit=10)
        self.assertEqual(scheduled["scheduled"], 0)
        ran = self.store.run_due_source_sync_jobs(self.user_id, limit=10)
        self.assertEqual(ran["scheduled_source_syncs"]["scheduled"], 0)
        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(ran["jobs"][0]["result"]["reason"], "source_account_disconnected")
        self.assertTrue(self.store.search(self.user_id, "retain local Cortex memory", source_account_id=account["id"]))

    def test_source_account_missing_snapshot_archive_is_account_scoped(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        first = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="First Gmail",
            account_identifier="first@example.com",
            connection_type="mcp",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
        )
        second = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Second Gmail",
            account_identifier="second@example.com",
            connection_type="mcp",
            status="connected",
            auth_state="authorized",
            policy={"review_required": False},
        )

        first_initial = self.store.sync_source_account_records(
            self.user_id,
            first["id"],
            records=[
                {
                    "content": "I decided first account archive scope should disappear after its missing snapshot.",
                    "title": "Shared Gmail record",
                    "external_id": "shared-msg",
                    "captured_at": "2026-06-30T12:00:00Z",
                }
            ],
            cursor_name="messages",
            processing="sync",
        )
        second_initial = self.store.sync_source_account_records(
            self.user_id,
            second["id"],
            records=[
                {
                    "content": "I decided second account archive scope must remain after first account snapshot.",
                    "title": "Shared Gmail record",
                    "external_id": "shared-msg",
                    "captured_at": "2026-06-30T12:01:00Z",
                }
            ],
            cursor_name="messages",
            processing="sync",
        )
        self.assertEqual(first_initial["saved"], 1)
        self.assertEqual(second_initial["saved"], 1)
        self.assertTrue(self.store.search(self.user_id, "first account archive scope", source_account_id=first["id"]))
        self.assertTrue(self.store.search(self.user_id, "second account archive scope", source_account_id=second["id"]))

        snapshot = self.store.sync_source_account_records(
            self.user_id,
            first["id"],
            records=[
                {
                    "content": "I decided first account replacement record is the only current snapshot item.",
                    "title": "Replacement Gmail record",
                    "external_id": "replacement-msg",
                    "captured_at": "2026-06-30T12:05:00Z",
                }
            ],
            cursor_name="messages",
            processing="sync",
            archive_missing=True,
            complete_snapshot=True,
        )

        self.assertEqual(snapshot["archived_missing"], 1)
        self.assertEqual(self.store.search(self.user_id, "first account archive scope", source_account_id=first["id"]), [])
        self.assertTrue(self.store.search(self.user_id, "first account replacement record", source_account_id=first["id"]))
        self.assertTrue(self.store.search(self.user_id, "second account archive scope", source_account_id=second["id"]))
        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT source_account_id, external_id, review_status
                FROM captures
                WHERE user_id = ?
                  AND source = 'gmail'
                  AND external_id = 'shared-msg'
                ORDER BY source_account_id
                """,
                (self.user_id,),
            ).fetchall()
        self.assertEqual(
            {(row["source_account_id"], row["review_status"]) for row in rows},
            {(first["id"], "archived"), (second["id"], "approved")},
        )

    def test_baseline_ten_catalog_services_do_not_make_fake_primary_ui_promises(self) -> None:
        catalog = {item["id"]: item for item in self.store.source_connector_catalog()}
        wired_source_paths = {
            "obsidian": "native-local-connector",
            "slack": "native-token-connector",
            "github": "native-token-connector",
            "readwise": "native-token-connector",
            "linear": "native-token-connector",
            "notion": "native-token-connector",
            "jira": "native-token-connector",
            "raindrop": "native-token-connector",
            "calendar": "native-local-connector",
            "zotero": "native-local-connector",
        }
        wired_source_ids = tuple(wired_source_paths)

        self.assertEqual(len(wired_source_ids), 10)
        for source_id, primary_beta_path in wired_source_paths.items():
            with self.subTest(source_id=source_id):
                entry = catalog[source_id]
                baseline = entry["service_baseline"]
                self.assertTrue(entry["baseline_10k"])
                self.assertTrue(baseline["included"])
                self.assertTrue(baseline["records_supported"])
                self.assertTrue(baseline["live_sync"])
                self.assertTrue(baseline["local_app_autosync"])
                self.assertFalse(baseline["manual_direct_sync"])
                self.assertFalse(baseline["hosted_managed_sync"])
                self.assertTrue(entry["supports_import"])
                self.assertTrue(entry["source_ids"])
                self.assertTrue(entry["formats"])
                self.assertEqual(entry["beta_status"], "ready")
                self.assertEqual(entry["primary_beta_path"], primary_beta_path)
                self.assertEqual(baseline["primary_ui"], source_id == "obsidian")
                self.assertEqual(entry["show_in_primary_ui"], source_id == "obsidian")
                self.assertEqual(entry["primary_beta"], source_id == "obsidian")
                self.assertNotEqual(entry["primary_beta_path"], "advanced-fallback-only")
                self.assertNotEqual(baseline["path"], "connector-records-supported")

        readiness = self.store.source_readiness_report(self.user_id)
        self.assertEqual(readiness["summary"]["baseline_10k_services"], len(wired_source_ids))
        self.assertEqual(readiness["summary"]["baseline_10k_records_supported"], len(wired_source_ids))
        self.assertEqual(readiness["summary"]["baseline_10k_live_sync"], len(wired_source_ids))
        self.assertEqual(readiness["summary"]["baseline_10k_local_app_autosync"], len(wired_source_ids))
        self.assertEqual(readiness["summary"]["baseline_10k_manual_direct_sync"], 0)
        self.assertEqual(readiness["summary"]["baseline_10k_hosted_managed_sync"], 0)
        readiness_by_source = {item["source"]: item for item in readiness["sources"]}
        for source_id, primary_beta_path in wired_source_paths.items():
            with self.subTest(readiness_source_id=source_id):
                entry = readiness_by_source[source_id]
                self.assertEqual(entry["beta_status"], "ready")
                self.assertEqual(entry["primary_beta_path"], primary_beta_path)
                self.assertEqual(entry["show_in_primary_ui"], source_id == "obsidian")
                self.assertEqual(entry["sync_plan"]["mode"], "local_app_autosync")
                self.assertIsNone(entry["sync_plan"]["hosted_credential_ref"])
                self.assertIn(entry["sync_plan"]["managed_sync_status"], {"not_configured", "waiting_for_first_sync", "healthy"})
                self.assertNotEqual(entry["status"], "advanced_fallback")
                self.assertNotIn("Advanced/Fallback", entry["next_action"])

    def test_source_sync_plan_marks_due_and_backing_off_accounts(self) -> None:
        vault_path = Path(self.tmp.name) / "due-plan-vault"
        vault_path.mkdir()
        account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Doppl Vault",
            account_identifier="vault-due-plan",
            connection_type="local_folder",
            status="connected",
            auth_state="healthy",
            metadata={
                "vault_path": str(vault_path),
                "sync_interval_seconds": 900,
                "next_sync_due_at": "2000-01-01T00:00:00Z",
            },
        )

        readiness = self.store.source_readiness_report(self.user_id)
        obsidian = next(item for item in readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(obsidian["sync_plan"]["mode"], "local_app_autosync")
        self.assertEqual(obsidian["sync_plan"]["credential_ref"], f"source_account:{account['id']}")
        self.assertEqual(obsidian["sync_plan"]["managed_sync_status"], "due")
        self.assertEqual(obsidian["sync_plan"]["sync_interval_seconds"], 900)
        self.assertTrue(obsidian["sync_plan"]["due_now"])
        self.assertTrue(obsidian["sync_plan"]["scheduler_supported"])
        self.assertIsNone(obsidian["sync_plan"]["blocked_reason"])
        self.assertEqual(obsidian["sync_plan"]["next_sync_due_at"], "2000-01-01T00:00:00Z")

        self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Doppl Vault",
            account_identifier="vault-due-plan",
            connection_type="local_folder",
            status="connected",
            auth_state="healthy",
            metadata={
                "vault_path": str(vault_path),
                "sync_interval_seconds": 900,
                "next_sync_due_at": "2000-01-01T00:00:00Z",
                "retry_after": "2999-01-01T00:00:00Z",
            },
            account_id=account["id"],
        )
        backoff_readiness = self.store.source_readiness_report(self.user_id)
        backed_off = next(item for item in backoff_readiness["sources"] if item["source"] == "obsidian")
        self.assertEqual(backed_off["sync_plan"]["managed_sync_status"], "backing_off")
        self.assertFalse(backed_off["sync_plan"]["due_now"])
        self.assertEqual(backed_off["sync_plan"]["retry_after"], "2999-01-01T00:00:00Z")

        unsupported = self.store.upsert_source_account(
            self.user_id,
            source="github",
            account_label="Doppl GitHub",
            account_identifier="doppl-tech/cortex-app",
            connection_type="api_token",
            status="connected",
            auth_state="healthy",
            metadata={
                "sync_interval_seconds": 900,
                "next_sync_due_at": "2000-01-01T00:00:00Z",
            },
        )
        unsupported_readiness = self.store.source_readiness_report(self.user_id)
        unsupported_github = next(item for item in unsupported_readiness["sources"] if item["source"] == "github")
        self.assertEqual(unsupported_github["sync_plan"]["credential_ref"], f"source_account:{unsupported['id']}")
        self.assertFalse(unsupported_github["sync_plan"]["due_now"])
        self.assertFalse(unsupported_github["sync_plan"]["scheduler_supported"])
        self.assertEqual(unsupported_github["sync_plan"]["blocked_reason"], "stored_sync_configuration_required")

    def test_due_obsidian_source_sync_job_rescans_changed_vault(self) -> None:
        vault_path = Path(self.tmp.name) / "scheduled-vault"
        note_path = vault_path / "Scheduled Sync.md"
        note_path.parent.mkdir(parents=True)
        note_path.write_text(
            "# Scheduled Sync\n\n"
            "Decision: Project Sable oldsyncmarker should be replaced by scheduled source sync.\n",
            encoding="utf-8",
        )

        initial = self.store.sync_obsidian_vault(
            self.user_id,
            vault_path=str(vault_path),
            processing="async",
        )
        self.assertEqual(initial["status"], "complete")
        self.assertEqual(initial["queued"], 1)
        capture_id = initial["capture_ids"][0]

        initial_jobs = self.store.run_due_jobs(self.user_id, limit=10)
        self.assertGreaterEqual(initial_jobs["processed"], 1)
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertTrue(self.store.search(self.user_id, "oldsyncmarker", limit=5))

        note_path.write_text(
            "# Scheduled Sync\n\n"
            "Decision: Project Sable newsyncmarker should replace stale scheduled source memory.\n",
            encoding="utf-8",
        )
        account = self.store.list_source_accounts(self.user_id)[0]
        self.store.upsert_source_account(
            self.user_id,
            source=account["source"],
            account_label=account["account_label"],
            account_identifier=account["account_identifier"],
            connection_type=account["connection_type"],
            status="connected",
            auth_state="healthy",
            policy=account["policy"],
            metadata={
                **account["metadata"],
                "sync_interval_seconds": 60,
                "next_sync_due_at": "2000-01-01T00:00:00Z",
            },
            account_id=account["id"],
        )

        due_report = self.store.source_readiness_report(self.user_id)
        due_obsidian = next(item for item in due_report["sources"] if item["source"] == "obsidian")
        self.assertTrue(due_obsidian["sync_plan"]["due_now"])
        self.assertEqual(due_obsidian["sync_plan"]["managed_sync_status"], "due")

        ran_sync = self.store.run_due_jobs(self.user_id, limit=1)
        self.assertEqual(ran_sync["processed"], 1)
        self.assertEqual(ran_sync["jobs"][0]["job_type"], "source_account_sync")
        self.assertEqual(ran_sync["jobs"][0]["status"], "succeeded")
        self.assertEqual(ran_sync["jobs"][0]["result"]["sync_status"], "complete")
        self.assertEqual(ran_sync["jobs"][0]["result"]["queued"], 1)
        self.assertEqual(ran_sync["jobs"][0]["result"]["capture_ids"], [capture_id])
        self.assertEqual(self.store.search(self.user_id, "oldsyncmarker", limit=5), [])
        self.assertEqual(self.store.search(self.user_id, "newsyncmarker", limit=5), [])

        ran_extraction = self.store.run_due_jobs(self.user_id, limit=10)
        self.assertGreaterEqual(ran_extraction["processed"], 1)
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        found = self.store.search(self.user_id, "newsyncmarker", limit=5)
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "obsidian")
        self.assertIn("Scheduled%20Sync.md", found[0]["source_url"])

        refreshed_account = self.store.list_source_accounts(self.user_id)[0]
        self.assertNotIn("next_sync_due_at", refreshed_account["metadata"])
        self.assertEqual(refreshed_account["metadata"]["last_scheduler_status"], "complete")
        refreshed_report = self.store.source_readiness_report(self.user_id)
        refreshed_obsidian = next(item for item in refreshed_report["sources"] if item["source"] == "obsidian")
        self.assertFalse(refreshed_obsidian["sync_plan"]["due_now"])

    def test_mcp_connected_source_tools_register_and_sync_cited_records(self) -> None:
        connectors = call_tool(self.store, self.user_id, "list_source_connectors", {"include_accounts": False})
        connector_ids = {item["id"] for item in connectors["results"]}
        self.assertIn("google-drive", connector_ids)

        account_payload = call_tool(
            self.store,
            self.user_id,
            "connect_source_account",
            {
                "source": "google-drive",
                "account_label": "Demo Drive",
                "account_identifier": "drive-demo",
                "connection_type": "mcp",
                "policy": {"sync": "docs_and_files"},
                "metadata": {"workspace": "first-100"},
            },
            token_scopes=["write"],
        )
        account = account_payload["account"]
        self.assertEqual(account["source"], "google-drive")
        self.assertEqual(account["status"], "planned")
        self.assertEqual(account["auth_state"], "not_configured")
        self.assertEqual(account["metadata"]["requested_status"], "connected")

        readiness_before_records = self.store.source_readiness_report(self.user_id)
        drive_before_records = next(item for item in readiness_before_records["sources"] if item["source"] == "google-drive")
        self.assertEqual(drive_before_records["status"], "planned")
        self.assertEqual(drive_before_records["beta_status"], "planned")

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_source_records",
                {"source_account_id": account["id"], "records": [{"content": "Read-only MCP tokens cannot sync records."}]},
                token_scopes=["read"],
            )

        synced = call_tool(
            self.store,
            self.user_id,
            "sync_source_records",
            {
                "source_account_id": account["id"],
                "records": [
                    {
                        "content": "I decided Google Drive should become the canonical project memory source for Project Helix.",
                        "title": "Project Helix memory decision",
                        "external_id": "drive-doc-helix",
                        "captured_at": "2026-06-30T09:30:00Z",
                        "metadata": {
                            "workspace": "first-100",
                            "document_id": "drive-doc-helix",
                            "tags": ["project-helix", "memory-source"],
                            "wikilinks": [{"target": "Project Helix", "display": "Project Helix"}],
                        },
                    }
                ],
                "cursor_name": "pages",
                "cursor_value": "cursor-2",
                "high_water_mark": "2026-06-30T09:30:00Z",
                "processing": "sync",
            },
            token_scopes=["write"],
        )

        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["saved"], 1)
        self.assertTrue(synced["records"][0]["source_url"].startswith(f"source-account://google-drive/{account['id']}/drive-doc-helix"))
        self.assertEqual(synced["cursor"]["cursor_name"], "pages")
        self.assertTrue(account["policy"]["review_required"])
        readiness_after_records = self.store.source_readiness_report(self.user_id)
        drive_after_records = next(item for item in readiness_after_records["sources"] if item["source"] == "google-drive")
        self.assertEqual(drive_after_records["status"], "needs_review")
        self.assertEqual(drive_after_records["beta_status"], "planned")

        capture_id = synced["capture_ids"][0]
        self.assertEqual([item["id"] for item in self.store.inbox(self.user_id, limit=10)], [capture_id])
        inbox = self.store.inbox(self.user_id)
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["source"], "google-drive")
        self.assertTrue(inbox[0]["preview_memories"])
        self.assertTrue(any("canonical project memory source" in item["content"] for item in inbox[0]["preview_memories"]))
        self.assertEqual(self.store.search(self.user_id, "canonical project memory source", limit=5), [])
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        readiness_after_review = self.store.source_readiness_report(self.user_id)
        drive_after_review = next(item for item in readiness_after_review["sources"] if item["source"] == "google-drive")
        self.assertEqual(drive_after_review["status"], "imported")
        self.assertEqual(drive_after_review["beta_status"], "planned")
        self.assertEqual(drive_after_review["sync_plan"]["managed_sync_status"], "planned")

        found = self.store.search(self.user_id, "canonical project memory source", limit=5)
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "google-drive")
        self.assertTrue(found[0]["source_url"].startswith(f"source-account://google-drive/{account['id']}/drive-doc-helix"))
        self.assertEqual(found[0]["sector"], "first-100")
        self.assertEqual(found[0]["provenance"]["record_metadata"]["document_id"], "drive-doc-helix")
        self.assertIn("project helix", [topic.casefold() for topic in found[0]["topics"]])
        self.assertIn("memory source", [topic.casefold() for topic in found[0]["topics"]])

    def test_github_account_sync_fetches_records_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/repos/doppl-tech/cortex-app/issues", url)
            self.assertEqual(headers["Authorization"], "Bearer ghp_test")
            return [
                {
                    "number": 17,
                    "title": "Ship cited Ask for GitHub memory",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/17",
                    "created_at": "2026-06-30T09:00:00Z",
                    "updated_at": "2026-06-30T10:00:00Z",
                    "user": {"login": "sarp"},
                    "labels": [{"name": "first-100"}],
                    "body": "We decided Cortex should retrieve GitHub issue memory with exact citations.",
                }
            ]

        result = self.store.sync_github_account(
            self.user_id,
            token="ghp_test",
            repositories=["doppl-tech/cortex-app"],
            processing="sync",
            max_records=20,
            include_comments=False,
            max_comments_per_item=0,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "github")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "github")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["status"], "connected")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertFalse(result["source_account"]["metadata"]["include_comments"])
        self.assertEqual(result["source_account"]["metadata"]["max_comments_per_item"], 0)
        self.assertFalse(result["sync"]["include_comments"])
        self.assertEqual(result["sync"]["max_comments_per_item"], 0)
        self.assertFalse(result["cursor"]["state"]["include_comments"])
        self.assertEqual(result["cursor"]["state"]["max_comments_per_item"], 0)
        credential = self.store.vault.read_source_credential(user_id=self.user_id, source_account_id=result["source_account_id"])
        self.assertEqual(credential["payload"]["include_comments"], False)
        self.assertEqual(credential["payload"]["max_comments_per_item"], 0)
        self.assertEqual(result["records"][0]["source_url"], "https://github.com/doppl-tech/cortex-app/issues/17")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "GitHub issue exact citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "github")
        self.assertTrue(search[0]["source_url"].startswith("https://github.com/doppl-tech/cortex-app/issues/17"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertIn("doppl-tech", search[0]["topics"])
        self.assertIn("cortex-app", search[0]["topics"])
        answer = self.store.answer_query(self.user_id, "What did we decide about GitHub issue memory?", limit=3)
        self.assertTrue(answer["citations"])
        self.assertTrue(answer["citations"][0]["source_url"].startswith("https://github.com/doppl-tech/cortex-app/issues/17"))

        duplicate = self.store.sync_github_account(
            self.user_id,
            token="ghp_test",
            repositories=["doppl-tech/cortex-app"],
            processing="sync",
            max_records=20,
            include_comments=False,
            max_comments_per_item=0,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_github_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/repos/doppl-tech/cortex-app/issues", url)
            self.assertEqual(headers["Authorization"], "Bearer ghp_mcp_test")
            return [
                {
                    "number": 31,
                    "title": "MCP GitHub sync keeps citations",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/31",
                    "created_at": "2026-06-30T09:00:00Z",
                    "updated_at": "2026-06-30T10:00:00Z",
                    "user": {"login": "sarp"},
                    "labels": [{"name": "mcp"}],
                    "body": "We decided MCP GitHub sync should write source-account records.",
                }
            ]

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_github",
                {"token": "ghp_mcp_test", "repositories": ["doppl-tech/cortex-app"]},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.github._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_github",
                {
                    "token": "ghp_mcp_test",
                    "repositories": ["doppl-tech/cortex-app"],
                    "processing": "sync",
                    "max_records": 10,
                    "include_comments": False,
                    "max_comments_per_item": 0,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "github")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("ghp_mcp_test", json.dumps(synced))
        self.assertFalse(synced["sync"]["include_comments"])
        self.assertEqual(synced["sync"]["max_comments_per_item"], 0)
        self.assertEqual(synced["records"][0]["source_url"], "https://github.com/doppl-tech/cortex-app/issues/31")

    def test_slack_account_sync_fetches_messages_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            if "/auth.test" in url:
                self.assertEqual(headers["Authorization"], "Bearer xoxb_test")
                return {"ok": True, "user_id": "U123", "user": "sarpt", "team_id": "T123", "team": "Doppl"}
            self.assertIn("/conversations.history", url)
            self.assertIn("channel=C123ABC", url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb_test")
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "We decided Cortex should retrieve Slack channel memory with exact citations.",
                        "ts": "1782739200.000100",
                    }
                ],
            }

        result = self.store.sync_slack_account(
            self.user_id,
            token="xoxb_test",
            channels=["C123ABC|general"],
            workspace_url="https://doppl.slack.com",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "slack")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "slack")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["account_identifier"], "U123")
        self.assertEqual(result["source_account"]["metadata"]["slack_user_id"], "U123")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertEqual(result["records"][0]["source_url"], "https://doppl.slack.com/archives/C123ABC/p1782739200000100")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Slack channel exact citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "slack")
        self.assertTrue(search[0]["source_url"].startswith("https://doppl.slack.com/archives/C123ABC/p1782739200000100"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["channel"], "general")
        answer = self.store.answer_query(self.user_id, "What did we decide about Slack channel memory?", limit=3)
        self.assertTrue(answer["citations"])
        self.assertTrue(answer["citations"][0]["source_url"].startswith("https://doppl.slack.com/archives/C123ABC/p1782739200000100"))

        duplicate = self.store.sync_slack_account(
            self.user_id,
            token="xoxb_test",
            channels=["C123ABC|general"],
            workspace_url="https://doppl.slack.com",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_slack_account_sync_uses_auth_identity_for_user_authored_preferences(self) -> None:
        self.store.update_settings(self.user_id, {"review_new_captures": False})

        def fake_request(url: str, headers: dict[str, str]):
            if "/auth.test" in url:
                self.assertEqual(headers["Authorization"], "Bearer xoxb_identity")
                return {"ok": True, "user_id": "U123", "user": "sarpt", "team_id": "T123", "team": "Doppl"}
            self.assertIn("/conversations.history", url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb_identity")
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "I prefer live Slack sync to preserve concise source-backed memory.",
                        "ts": "1782739300.000100",
                    },
                    {
                        "type": "message",
                        "user": "U456",
                        "text": "I prefer teammate-only Slack preferences to be ignored by Cortex.",
                        "ts": "1782739301.000200",
                    },
                ],
            }

        result = self.store.sync_slack_account(
            self.user_id,
            token="xoxb_identity",
            channels=["C123ABC|general"],
            workspace_url="https://doppl.slack.com",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["saved"], 2)
        self.assertEqual(result["source_account"]["account_identifier"], "U123")
        self.assertEqual(result["source_account"]["metadata"]["user_id"], "U123")
        for capture_id in result["capture_ids"]:
            self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        user_hits = self.store.search(self.user_id, "concise source-backed memory", limit=5)
        user_preference = next((hit for hit in user_hits if hit["kind"] == "preference"), None)
        self.assertIsNotNone(user_preference)
        self.assertEqual(user_preference["provenance"]["source_account_id"], result["source_account_id"])
        with connect(self.db_path) as conn:
            teammate_preference_count = conn.execute(
                """
                SELECT COUNT(*)
                FROM memories
                WHERE user_id = ?
                  AND kind = 'preference'
                  AND status = 'active'
                  AND content LIKE '%teammate-only Slack preferences%'
                """,
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(teammate_preference_count, 0)

    def test_mcp_slack_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            if "/auth.test" in url:
                self.assertEqual(headers["Authorization"], "Bearer xoxb_mcp_test")
                return {"ok": True, "user_id": "U123", "user": "sarpt"}
            self.assertIn("/conversations.history", url)
            self.assertEqual(headers["Authorization"], "Bearer xoxb_mcp_test")
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "We decided MCP Slack sync should write source-account records.",
                        "ts": "1782739200.000300",
                    }
                ],
            }

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_slack",
                {"token": "xoxb_mcp_test", "channels": ["C123ABC|general"]},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.slack._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_slack",
                {
                    "token": "xoxb_mcp_test",
                    "channels": ["C123ABC|general"],
                    "workspace_url": "https://doppl.slack.com",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "slack")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("xoxb_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "https://doppl.slack.com/archives/C123ABC/p1782739200000300")

    def test_readwise_account_sync_fetches_highlights_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/export/", url)
            self.assertEqual(headers["Authorization"], "Token readwise_test")
            return {
                "results": [
                    {
                        "user_book_id": 111,
                        "title": "Retrieval Systems",
                        "author": "A. Researcher",
                        "category": "books",
                        "source_url": "https://readwise.io/bookreview/111",
                        "highlights": [
                            {
                                "id": 222,
                                "text": "We decided Cortex should retrieve Readwise highlights with exact source citations.",
                                "note": "Useful for the first 100 user memory loop.",
                                "highlighted_at": "2026-06-29T10:00:00Z",
                                "updated": "2026-06-30T10:30:00Z",
                                "tags": [{"name": "retrieval"}],
                            }
                        ],
                    }
                ]
            }

        result = self.store.sync_readwise_account(
            self.user_id,
            token="readwise_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "readwise")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "readwise")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertNotIn("readwise_test", json.dumps(result))
        self.assertEqual(result["records"][0]["source_url"], "https://readwise.io/bookreview/111")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Readwise highlights exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "readwise")
        self.assertTrue(search[0]["source_url"].startswith("https://readwise.io/bookreview/111"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["highlight_id"], "222")
        answer = self.store.answer_query(self.user_id, "What did we decide about Readwise highlights?", limit=3)
        self.assertTrue(answer["citations"])
        self.assertTrue(answer["citations"][0]["source_url"].startswith("https://readwise.io/bookreview/111"))

        duplicate = self.store.sync_readwise_account(
            self.user_id,
            token="readwise_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_readwise_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/export/", url)
            self.assertEqual(headers["Authorization"], "Token readwise_mcp_test")
            return {
                "results": [
                    {
                        "user_book_id": 333,
                        "title": "Cortex Field Notes",
                        "highlights": [
                            {
                                "id": 444,
                                "text": "We decided MCP Readwise sync should write source-account records.",
                                "updated": "2026-06-30T10:00:00Z",
                            }
                        ],
                    }
                ]
            }

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_readwise",
                {"token": "readwise_mcp_test"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.readwise._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_readwise",
                {
                    "token": "readwise_mcp_test",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "readwise")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("readwise_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "readwise://book/333/highlight/444")

    def test_calendar_account_sync_fetches_events_with_stable_citations(self) -> None:
        private_path = Path(self.tmp.name) / "Private Calendar.ics"
        ics_text = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-storage@example.com
DTSTAMP:20260630T100000Z
DTSTART:20260701T160000Z
DTEND:20260701T170000Z
SUMMARY:Calendar exact citations
DESCRIPTION:We decided Cortex should retrieve Calendar events with exact source citations.
LOCATION:Doppl HQ
ATTENDEE;CN=Ada:mailto:ada@example.com
END:VEVENT
END:VCALENDAR
"""
        private_path.write_text(ics_text, encoding="utf-8")

        result = self.store.sync_calendar_account(
            self.user_id,
            ics_path=str(private_path),
            processing="sync",
            max_records=20,
        )

        self.assertEqual(result["source"], "calendar")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "calendar")
        self.assertEqual(result["source_account"]["connection_type"], "local-file")
        self.assertEqual(result["source_account"]["metadata"]["path_redacted"], True)
        self.assertNotIn(str(private_path), json.dumps(result))
        self.assertTrue(result["records"][0]["source_url"].startswith("source-account://calendar/"))
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Calendar events exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "calendar")
        self.assertTrue(search[0]["source_url"].startswith("source-account://calendar/"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["uid"], "event-storage@example.com")
        self.assertNotIn(str(private_path), json.dumps(search))

        duplicate = self.store.sync_calendar_account(
            self.user_id,
            ics_path=str(private_path),
            processing="sync",
            max_records=20,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_calendar_sync_tool_fetches_records_without_exposing_path(self) -> None:
        private_path = Path(self.tmp.name) / "MCP Private Calendar.ics"
        private_path.write_text(
            """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-mcp@example.com
DTSTAMP:20260630T100000Z
DTSTART:20260701T160000Z
SUMMARY:MCP Calendar sync
DESCRIPTION:We decided MCP Calendar sync should write source-account records.
END:VEVENT
END:VCALENDAR
""",
            encoding="utf-8",
        )

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_calendar",
                {"ics_path": str(private_path)},
                token_scopes=["read"],
            )

        synced = call_tool(
            self.store,
            self.user_id,
            "sync_calendar",
            {
                "ics_path": str(private_path),
                "processing": "sync",
                "max_records": 10,
            },
            token_scopes=["write"],
        )

        self.assertEqual(synced["source"], "calendar")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn(str(private_path), json.dumps(synced))
        self.assertTrue(synced["records"][0]["source_url"].startswith("source-account://calendar/"))

    def test_raindrop_account_sync_fetches_bookmarks_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/raindrops/0", url)
            self.assertEqual(headers["Authorization"], "Bearer raindrop_test")
            return {
                "result": True,
                "items": [
                    {
                        "_id": 123,
                        "title": "Raindrop exact citations",
                        "link": "https://example.com/raindrop-citations",
                        "excerpt": "We decided Cortex should retrieve Raindrop bookmarks with exact source citations.",
                        "note": "Useful for research recall.",
                        "tags": ["research"],
                        "lastUpdate": "2026-06-30T10:30:00Z",
                        "highlights": [{"text": "Raindrop highlights should appear in the cited record."}],
                    }
                ],
            }

        result = self.store.sync_raindrop_account(
            self.user_id,
            token="raindrop_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "raindrop")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "raindrop")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertNotIn("raindrop_test", json.dumps(result))
        self.assertEqual(result["records"][0]["source_url"], "https://example.com/raindrop-citations")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Raindrop bookmarks exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "raindrop")
        self.assertTrue(search[0]["source_url"].startswith("https://example.com/raindrop-citations"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["raindrop_id"], "123")

        duplicate = self.store.sync_raindrop_account(
            self.user_id,
            token="raindrop_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_raindrop_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertEqual(headers["Authorization"], "Bearer raindrop_mcp_test")
            return {
                "result": True,
                "items": [
                    {
                        "_id": 456,
                        "title": "MCP Raindrop sync",
                        "link": "https://example.com/mcp-raindrop",
                        "excerpt": "We decided MCP Raindrop sync should write source-account records.",
                        "lastUpdate": "2026-06-30T10:00:00Z",
                    }
                ],
            }

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_raindrop",
                {"token": "raindrop_mcp_test"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.raindrop._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_raindrop",
                {
                    "token": "raindrop_mcp_test",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "raindrop")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("raindrop_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "https://example.com/mcp-raindrop")

    def test_zotero_account_sync_fetches_items_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/users/0/items", url)
            self.assertEqual(headers["Zotero-API-Version"], "3")
            self.assertNotIn("Zotero-API-Key", headers)
            return [
                {
                    "key": "ZTITEM1",
                    "version": 42,
                    "data": {
                        "key": "ZTITEM1",
                        "itemType": "annotation",
                        "parentItem": "ZTPARENT",
                        "title": "",
                        "annotationType": "highlight",
                        "annotationText": "We decided Cortex should retrieve Zotero annotations with exact source citations.",
                        "annotationComment": "Useful for research recall.",
                        "dateModified": "2026-06-30T10:30:00Z",
                        "tags": [{"tag": "research"}],
                    },
                },
                {
                    "key": "ZTPDF1",
                    "version": 43,
                    "data": {
                        "key": "ZTPDF1",
                        "itemType": "attachment",
                        "title": "Skipped PDF",
                        "contentType": "application/pdf",
                    },
                },
            ]

        result = self.store.sync_zotero_account(
            self.user_id,
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "zotero")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "zotero")
        self.assertEqual(result["source_account"]["connection_type"], "local-api")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], False)
        self.assertEqual(result["source_account"]["metadata"]["attachment_content_imported"], False)
        self.assertEqual(result["sync"]["records_found"], 2)
        self.assertEqual(result["sync"]["records_returned"], 1)
        self.assertNotIn("ZTPDF1", json.dumps(result))
        self.assertEqual(result["records"][0]["source_url"], "zotero://select/library/items/ZTITEM1")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Zotero annotations exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "zotero")
        self.assertTrue(search[0]["source_url"].startswith("zotero://select/library/items/ZTITEM1"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["item_key"], "ZTITEM1")
        self.assertEqual(search[0]["provenance"]["record_metadata"]["record_kind"], "annotation")

        duplicate = self.store.sync_zotero_account(
            self.user_id,
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_zotero_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str]):
            self.assertIn("/groups/12345/items", url)
            self.assertEqual(headers["Zotero-API-Key"], "zotero_mcp_test")
            return [
                {
                    "key": "ZTMCP1",
                    "version": 50,
                    "data": {
                        "key": "ZTMCP1",
                        "itemType": "note",
                        "note": "<p>We decided MCP Zotero sync should write source-account records.</p>",
                        "dateModified": "2026-06-30T10:00:00Z",
                    },
                }
            ]

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_zotero",
                {"token": "zotero_mcp_test"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.zotero._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_zotero",
                {
                    "token": "zotero_mcp_test",
                    "library_type": "group",
                    "library_id": "12345",
                    "api_base_url": "https://api.zotero.org",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "zotero")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("zotero_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "zotero://select/groups/12345/items/ZTMCP1")

    def test_linear_account_sync_fetches_issues_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str], body: dict):
            self.assertEqual(headers["Authorization"], "lin_api_test")
            self.assertIn("issues", body["query"])
            return {
                "data": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "lin-1",
                                "identifier": "COR-42",
                                "title": "Linear exact citations",
                                "description": "We decided Cortex should retrieve Linear issues with exact source citations.",
                                "url": "https://linear.app/doppl/issue/COR-42/linear-exact-citations",
                                "createdAt": "2026-06-29T10:00:00Z",
                                "updatedAt": "2026-06-30T10:30:00Z",
                                "state": {"name": "In Progress", "type": "started"},
                                "team": {"key": "COR", "name": "Cortex"},
                                "labels": {"nodes": [{"name": "retrieval"}]},
                            }
                        ],
                    }
                }
            }

        result = self.store.sync_linear_account(
            self.user_id,
            token="lin_api_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "linear")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "linear")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertNotIn("lin_api_test", json.dumps(result))
        self.assertEqual(result["records"][0]["source_url"], "https://linear.app/doppl/issue/COR-42/linear-exact-citations")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Linear issues exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "linear")
        self.assertTrue(search[0]["source_url"].startswith("https://linear.app/doppl/issue/COR-42/linear-exact-citations"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["identifier"], "COR-42")

        duplicate = self.store.sync_linear_account(
            self.user_id,
            token="lin_api_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_linear_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str], body: dict):
            self.assertEqual(headers["Authorization"], "lin_mcp_test")
            return {
                "data": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "lin-mcp",
                                "identifier": "COR-99",
                                "title": "MCP Linear sync",
                                "description": "We decided MCP Linear sync should write source-account records.",
                                "updatedAt": "2026-06-30T10:00:00Z",
                            }
                        ],
                    }
                }
            }

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_linear",
                {"token": "lin_mcp_test"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.linear._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_linear",
                {
                    "token": "lin_mcp_test",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "linear")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("lin_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "linear://issue/COR-99")

    def test_jira_account_sync_fetches_issues_with_stable_citations(self) -> None:
        expected_auth = base64.b64encode(b"sarp@example.com:jira_api_test").decode("ascii")

        def fake_request(url: str, headers: dict[str, str], body: dict):
            self.assertEqual(url, "https://doppl.atlassian.net/rest/api/3/search/jql")
            self.assertEqual(headers["Authorization"], f"Basic {expected_auth}")
            self.assertEqual(body["jql"], "project = COR ORDER BY updated DESC")
            return {
                "isLast": True,
                "issues": [
                    {
                        "id": "10001",
                        "key": "COR-42",
                        "fields": {
                            "summary": "Jira exact citations",
                            "description": {
                                "type": "doc",
                                "version": 1,
                                "content": [
                                    {
                                        "type": "paragraph",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "We decided Cortex should retrieve Jira issues with exact source citations.",
                                            }
                                        ],
                                    }
                                ],
                            },
                            "project": {"key": "COR", "name": "Cortex"},
                            "issuetype": {"name": "Task"},
                            "status": {"name": "In Progress"},
                            "created": "2026-06-29T10:00:00.000+0000",
                            "updated": "2026-06-30T10:30:00.000+0000",
                        },
                    }
                ],
            }

        result = self.store.sync_jira_account(
            self.user_id,
            email="sarp@example.com",
            api_token="jira_api_test",
            site_url="https://doppl.atlassian.net",
            jql="project = COR ORDER BY updated DESC",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "jira")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "jira")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["metadata"]["api_token_configured"], True)
        self.assertNotIn("jira_api_test", json.dumps(result))
        self.assertEqual(result["records"][0]["source_url"], "https://doppl.atlassian.net/browse/COR-42")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Jira issues exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "jira")
        self.assertTrue(search[0]["source_url"].startswith("https://doppl.atlassian.net/browse/COR-42"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["issue_key"], "COR-42")

        duplicate = self.store.sync_jira_account(
            self.user_id,
            email="sarp@example.com",
            api_token="jira_api_test",
            site_url="https://doppl.atlassian.net",
            jql="project = COR ORDER BY updated DESC",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_jira_sync_tool_fetches_records_without_exposing_token(self) -> None:
        expected_auth = base64.b64encode(b"sarp@example.com:jira_mcp_test").decode("ascii")

        def fake_request(url: str, headers: dict[str, str], body: dict):
            self.assertEqual(headers["Authorization"], f"Basic {expected_auth}")
            return {
                "isLast": True,
                "issues": [
                    {
                        "id": "10099",
                        "key": "COR-99",
                        "fields": {
                            "summary": "MCP Jira sync",
                            "description": "We decided MCP Jira sync should write source-account records.",
                            "updated": "2026-06-30T10:00:00.000+0000",
                        },
                    }
                ],
            }

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_jira",
                {
                    "email": "sarp@example.com",
                    "api_token": "jira_mcp_test",
                    "site_url": "https://doppl.atlassian.net",
                },
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.jira._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_jira",
                {
                    "email": "sarp@example.com",
                    "api_token": "jira_mcp_test",
                    "site_url": "https://doppl.atlassian.net",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "jira")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("jira_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "https://doppl.atlassian.net/browse/COR-99")

    def test_notion_account_sync_fetches_pages_with_stable_citations(self) -> None:
        def fake_request(url: str, headers: dict[str, str], body: dict | None, method: str):
            self.assertEqual(headers["Authorization"], "Bearer notion_test")
            if method == "POST":
                return {
                    "has_more": False,
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "created_time": "2026-06-29T10:00:00Z",
                            "last_edited_time": "2026-06-30T10:30:00Z",
                            "url": "https://www.notion.so/doppl/page-1",
                            "properties": {
                                "Name": {"type": "title", "title": [{"plain_text": "Notion exact citations"}]},
                                "Status": {"type": "status", "status": {"name": "Ready"}},
                            },
                        }
                    ],
                }
            return {
                "has_more": False,
                "results": [
                    {
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {"plain_text": "We decided Cortex should retrieve Notion pages with exact source citations."}
                            ]
                        },
                    }
                ],
            }

        result = self.store.sync_notion_account(
            self.user_id,
            token="notion_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )

        self.assertEqual(result["source"], "notion")
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["saved"], 1)
        self.assertEqual(result["source_account"]["source"], "notion")
        self.assertEqual(result["source_account"]["connection_type"], "api-token")
        self.assertEqual(result["source_account"]["metadata"]["token_configured"], True)
        self.assertNotIn("notion_test", json.dumps(result))
        self.assertEqual(result["records"][0]["source_url"], "https://www.notion.so/doppl/page-1")
        capture_id = result["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        search = self.store.search(self.user_id, "Notion pages exact source citations", limit=3)
        self.assertTrue(search)
        self.assertEqual(search[0]["source"], "notion")
        self.assertTrue(search[0]["source_url"].startswith("https://www.notion.so/doppl/page-1"))
        self.assertIn("line=", search[0]["source_url"])
        self.assertIn("excerpt=", search[0]["source_url"])
        self.assertEqual(search[0]["provenance"]["record_metadata"]["page_id"], "page-1")

        duplicate = self.store.sync_notion_account(
            self.user_id,
            token="notion_test",
            processing="sync",
            max_records=20,
            request_json=fake_request,
        )
        self.assertEqual(duplicate["saved"], 0)
        self.assertEqual(duplicate["skipped"], 1)
        self.assertEqual(duplicate["records"][0]["status"], "duplicate")

    def test_mcp_notion_sync_tool_fetches_records_without_exposing_token(self) -> None:
        def fake_request(url: str, headers: dict[str, str], body: dict | None, method: str):
            self.assertEqual(headers["Authorization"], "Bearer notion_mcp_test")
            if method == "POST":
                return {
                    "has_more": False,
                    "results": [
                        {
                            "object": "page",
                            "id": "page-mcp",
                            "last_edited_time": "2026-06-30T10:00:00Z",
                            "properties": {"Name": {"type": "title", "title": [{"plain_text": "MCP Notion sync"}]}},
                        }
                    ],
                }
            return {"has_more": False, "results": []}

        with self.assertRaises(PermissionError):
            call_tool(
                self.store,
                self.user_id,
                "sync_notion",
                {"token": "notion_mcp_test"},
                token_scopes=["read"],
            )

        with patch("backend.app.connectors.notion._request_json", side_effect=fake_request):
            synced = call_tool(
                self.store,
                self.user_id,
                "sync_notion",
                {
                    "token": "notion_mcp_test",
                    "processing": "sync",
                    "max_records": 10,
                },
                token_scopes=["write"],
            )

        self.assertEqual(synced["source"], "notion")
        self.assertEqual(synced["saved"], 1)
        self.assertNotIn("notion_mcp_test", json.dumps(synced))
        self.assertEqual(synced["records"][0]["source_url"], "notion://page/page-mcp")

    def test_source_account_policy_blocks_ai_context_after_approval(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Private Gmail",
            account_identifier="private@example.com",
            connection_type="mcp",
            policy={"allow_ai_context": False},
        )
        self.assertFalse(account["policy"]["allow_ai_context"])
        self.assertTrue(account["policy"]["review_required"])

        synced = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "Decision: Private Gmail Alpha should remain blocked from AI context.",
                    "title": "Private Gmail Alpha",
                    "external_id": "gmail-alpha",
                    "captured_at": "2026-06-30T10:00:00Z",
                }
            ],
            processing="sync",
        )
        self.assertEqual(synced["saved"], 1)
        capture_id = synced["capture_ids"][0]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertEqual(self.store.search(self.user_id, "Private Gmail Alpha", limit=5), [])
        self.assertNotIn(
            "remain blocked from AI context",
            self.store.context_pack(self.user_id, query="Private Gmail Alpha"),
        )

        updated = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Private Gmail",
            account_identifier="private@example.com",
            connection_type="mcp",
            policy={"allow_ai_context": True, "review_required": True},
            account_id=account["id"],
        )
        self.assertTrue(updated["policy"]["allow_ai_context"])
        self.assertTrue(self.store.search(self.user_id, "Private Gmail Alpha", limit=5))

    def test_obsidian_source_sync_cleans_markdown_and_preserves_file_citation(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Demo Vault",
            account_identifier="demo-vault",
            connection_type="local_folder",
            status="connected",
            auth_state="healthy",
        )
        synced = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": """---
title: Project Atlas
tags: #cortex #todo
---
# Project Atlas

> [!NOTE] Template block
> This callout should not become memory.

```dataview
TABLE file.mtime
FROM #cortex
```

- [ ] Follow up with Dana about [[Project Atlas|Atlas]] review.
I decided [[Project Atlas|Atlas]] should use [source-backed retrieval](https://example.com) for MCP memory.
I prefer #cortex notes that keep [[People/Dana|Dana]] citations clean.
Never use [[Templates/Marketing]] boilerplate in memory.
""",
                    "title": "Project Atlas.md",
                    "source_url": "file:///Users/example/Obsidian/Project%20Atlas.md",
                    "external_id": "Project Atlas.md",
                }
            ],
            cursor_name="vault",
            cursor_value="1",
            processing="sync",
        )

        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["saved"], 1)

        with connect(self.db_path) as conn:
            rows = conn.execute(
                """
                SELECT kind, layer, content, raw_excerpt, source_url
                FROM memories
                WHERE user_id = ? AND source = ?
                ORDER BY kind, content
                """,
                (self.user_id, "obsidian"),
            ).fetchall()

        self.assertTrue(rows)
        joined = "\n".join((row["content"] or "") + "\n" + (row["raw_excerpt"] or "") for row in rows)
        for leaked in ("tags:", "#todo", "[[", "]]", "dataview", "Template block", "This callout should not become memory.", "TABLE file.mtime"):
            self.assertNotIn(leaked, joined)
        self.assertTrue(all((row["source_url"] or "").startswith("file:///Users/example/Obsidian/Project%20Atlas.md") for row in rows))
        self.assertTrue(any(row["kind"] == "decision" and "Atlas should use source-backed retrieval" in row["content"] for row in rows))
        self.assertTrue(any(row["kind"] == "preference" and "cortex notes" in row["content"] for row in rows))
        self.assertTrue(any(row["kind"] == "negative" and "Marketing boilerplate" in row["content"] for row in rows))

        self.assertTrue(self.store.approve_capture(self.user_id, synced["capture_ids"][0]))
        found = self.store.search(self.user_id, "source-backed retrieval MCP memory", limit=3)
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "obsidian")
        self.assertIn("source-backed retrieval", found[0]["content"])

    def test_source_readiness_report_combines_import_review_and_sync_health(self) -> None:
        capture = self.store.save_capture(
            user_id=self.user_id,
            content="Gmail export: We decided Cortex should keep source citations attached to imported messages.",
            source="gmail",
            source_url="gmail://message/msg-1",
            title="Gmail readiness capture",
            extracted=extract_context(
                "Gmail export: We decided Cortex should keep source citations attached to imported messages.",
                "gmail",
            ),
        )
        self.assertGreaterEqual(len(capture["memories"]), 1)

        account = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Primary Gmail",
            account_identifier="user@example.com",
            connection_type="oauth",
            status="connected",
            auth_state="healthy",
        )
        self.store.upsert_sync_cursor(
            self.user_id,
            source="gmail",
            source_account_id=account["id"],
            cursor_name="messages",
            cursor_value="page-token-1",
            high_water_mark="2026-06-29T10:00:00Z",
            last_error="token expired",
            completed=False,
        )

        report = self.store.source_readiness_report(self.user_id)
        self.assertIn("generated_at", report)
        self.assertGreaterEqual(report["summary"]["sources_total"], 1)
        self.assertGreaterEqual(report["summary"]["active_memories"], 1)
        self.assertEqual(report["summary"]["needs_attention"], 1)
        self.assertTrue(report["recommendations"])

        gmail = next(source for source in report["sources"] if source["source"] == "gmail")
        self.assertEqual(gmail["status"], "needs_attention")
        self.assertEqual(gmail["accounts"], 1)
        self.assertEqual(gmail["cursors"], 1)
        self.assertEqual(gmail["captures"], 1)
        self.assertEqual(gmail["pending"], 1)
        self.assertGreater(gmail["active_memories"], 0)
        self.assertGreater(gmail["citation_coverage"], 0)
        self.assertIn("token expired", gmail["warnings"])
        self.assertIn("token expired", gmail["next_action"])

    def test_markdown_handoffs_include_source_urls(self) -> None:
        capture = self.store.save_capture(
            user_id=self.user_id,
            content="We decided Project Atlas handoffs must include source locators for cited memory.",
            source="notion",
            source_url="notion://workspace/project-atlas#block-7",
            title="Project Atlas decision",
            extracted=extract_context(
                "We decided Project Atlas handoffs must include source locators for cited memory.",
                "notion",
            ),
        )
        self.assertTrue(self.store.approve_capture(self.user_id, capture["capture_id"]))

        context_pack = self.store.context_pack(self.user_id, query="Project Atlas", limit=5)
        profile = self.store.personal_profile(self.user_id, query="Project Atlas", limit=5)
        adaptation = self.store.agent_adaptation(self.user_id, query="Project Atlas", target="Claude", limit=5)

        self.assertIn("notion://workspace/project-atlas#block-7", context_pack)
        self.assertIn("notion://workspace/project-atlas#block-7", profile["markdown"])
        self.assertIn("notion://workspace/project-atlas#block-7", adaptation["markdown"])

    def test_sync_change_feed_is_cursorable_and_content_free(self) -> None:
        secret_phrase = "Sync Feed Secret Raw Content"
        capture = self.store.save_capture(
            user_id=self.user_id,
            content=f"We decided the {secret_phrase} must never appear in sync manifests.",
            source="unit-test",
            source_url="/tmp/sync-feed.md",
            title="Sync feed capture",
            extracted=extract_context(f"We decided the {secret_phrase} must never appear in sync manifests.", "unit-test"),
        )
        self.assertTrue(self.store.approve_capture(self.user_id, capture["capture_id"]))

        feed = self.store.sync_change_feed(self.user_id, limit=1)
        self.assertEqual(feed["sync_contract"], 1)
        self.assertFalse(feed["content_included"])
        self.assertTrue(feed["changes"])
        self.assertTrue(feed["has_more"])
        self.assertEqual(feed["counts"]["captures"], 1)
        self.assertGreaterEqual(feed["counts"]["events"], 2)
        self.assertNotIn(secret_phrase, json.dumps(feed))

        next_feed = self.store.sync_change_feed(self.user_id, after=feed["next_cursor"], limit=10)
        self.assertNotEqual(next_feed["next_cursor"], feed["next_cursor"])
        self.assertNotIn(secret_phrase, json.dumps(next_feed))

        invalid = self.store.sync_change_feed(self.user_id, after="evt_missing", limit=10)
        self.assertEqual(invalid["changes"], [])
        self.assertEqual(invalid["warnings"], ["cursor_not_found"])
        self.assertEqual(invalid["next_cursor"], "evt_missing")

    def test_sync_devices_register_revoke_and_sign_change_feed(self) -> None:
        device = self.store.register_sync_device(
            self.user_id,
            device_name="MacBook Pro",
            platform="macOS",
            capabilities=["manifest", "upload"],
        )
        self.assertTrue(device["id"].startswith("sdev_"))
        self.assertEqual(device["platform"], "macos")
        self.assertIn("device_key", device)
        self.assertEqual(len(device["fingerprint"]), 16)

        listed = self.store.list_sync_devices(self.user_id)
        self.assertEqual([item["id"] for item in listed], [device["id"]])
        self.assertNotIn("device_key", json.dumps(listed))

        phrase = "Signed Sync Device Secret Phrase"
        capture = self.store.save_capture(
            user_id=self.user_id,
            content=f"We decided {phrase} must stay out of signed manifests.",
            source="unit-test",
            source_url="/tmp/signed-sync.md",
            title="Signed sync capture",
            extracted=extract_context(f"We decided {phrase} must stay out of signed manifests.", "unit-test"),
        )
        self.assertTrue(self.store.approve_capture(self.user_id, capture["capture_id"]))

        feed = self.store.sync_change_feed(self.user_id, device_id=device["id"], signing_key="unit-secret")
        self.assertEqual(feed["device"]["id"], device["id"])
        self.assertEqual(feed["counts"]["sync_devices"], 1)
        self.assertTrue(feed["signature"]["configured"])
        self.assertEqual(feed["signature"]["device_id"], device["id"])
        self.assertTrue(feed["signature"]["payload_hash"].startswith("sha256:"))
        self.assertTrue(feed["signature"]["value"].startswith("hmac-sha256:"))
        self.assertNotIn("device_key", json.dumps(feed))
        self.assertNotIn(phrase, json.dumps(feed))

        refreshed = self.store.list_sync_devices(self.user_id)[0]
        self.assertEqual(refreshed["last_cursor"], feed["next_cursor"])
        self.assertIsNotNone(refreshed["last_seen_at"])

        receipt = self.store.record_sync_receipt(
            self.user_id,
            device["id"],
            cursor=feed["next_cursor"],
            status="uploaded",
            manifest_hash=feed["signature"]["payload_hash"],
            remote_ref="local-sync://unit-test/upload-1",
            stats={"changes": len(feed["changes"])},
        )
        self.assertTrue(receipt["id"].startswith("srec_"))
        self.assertEqual(receipt["device_id"], device["id"])
        self.assertEqual(receipt["cursor"], feed["next_cursor"])
        self.assertEqual(receipt["status"], "uploaded")
        self.assertEqual(receipt["stats"]["changes"], len(feed["changes"]))
        receipts = self.store.list_sync_receipts(self.user_id, device["id"])
        self.assertEqual([item["id"] for item in receipts], [receipt["id"]])

        revoked = self.store.revoke_sync_device(self.user_id, device["id"])
        self.assertEqual(revoked["id"], device["id"])
        self.assertIsNotNone(revoked["revoked_at"])
        self.assertEqual(self.store.list_sync_devices(self.user_id), [])
        self.assertEqual(self.store.list_sync_devices(self.user_id, include_revoked=True)[0]["id"], device["id"])

        revoked_feed = self.store.sync_change_feed(self.user_id, device_id=device["id"], signing_key="unit-secret")
        self.assertIn("device_revoked", revoked_feed["warnings"])
        self.assertFalse(revoked_feed["signature"]["configured"])
        with self.assertRaisesRegex(ValueError, "revoked"):
            self.store.record_sync_receipt(self.user_id, device["id"], cursor=revoked_feed["next_cursor"])

        deleted = self.store.delete_user_data(self.user_id, include_backups=False)
        self.assertEqual(deleted["sqlite"]["sync_devices"], 1)
        self.assertEqual(deleted["sqlite"]["sync_receipts"], 1)
        self.assertEqual(deleted["vault"]["sync_devices"], 1)
        self.assertEqual(deleted["vault"]["sync_receipts"], 1)
        rebuilt = self.store.rebuild_index_from_vault(self.user_id)
        self.assertEqual(rebuilt["sync_devices"], 0)
        self.assertEqual(rebuilt["sync_receipts"], 0)
        self.assertEqual(self.store.list_sync_devices(self.user_id, include_revoked=True), [])
        self.assertEqual(self.store.list_sync_receipts(self.user_id, device["id"]), [])

    def test_memory_quality_report_tracks_citations_review_and_layers(self) -> None:
        uncited = self.capture("We decided uncited quality memory should warn about missing source paths.")
        cited = self.store.save_capture(
            user_id=self.user_id,
            content="On June 29, 2026, cited quality memory should preserve a source URL.",
            source="unit-test",
            source_url="/tmp/cited-quality-note.md",
            title="Cited quality",
            extracted=extract_context("On June 29, 2026, cited quality memory should preserve a source URL.", "unit-test"),
        )

        report = self.store.memory_quality_report(self.user_id)

        self.assertEqual(report["totals"]["captures"], 2)
        self.assertEqual(report["totals"]["pending_captures"], 2)
        self.assertGreater(report["totals"]["active_memories"], 0)
        self.assertGreater(report["totals"]["uncited_memories"], 0)
        self.assertGreater(report["totals"]["dated_memories"], 0)
        self.assertGreater(report["totals"]["temporal_memories"], 0)
        self.assertGreater(report["totals"]["dated_temporal_memories"], 0)
        self.assertLess(report["citation_coverage"], 1.0)
        self.assertGreater(report["date_coverage"], 0.0)
        self.assertLess(report["date_coverage"], 1.0)
        self.assertTrue(report["layers_present"])
        self.assertTrue(any("citations" in warning for warning in report["warnings"]))
        self.assertTrue(any(source["source"] == "unit-test" for source in report["source_health"]))
        unit_health = next(source for source in report["source_health"] if source["source"] == "unit-test")
        self.assertIn("date_coverage", unit_health)
        self.assertGreater(unit_health["dated_temporal_memories"], 0)
        self.assertTrue(uncited["capture_id"])
        self.assertTrue(cited["capture_id"])

    def test_delete_memory_purges_memory_record_and_indexes(self) -> None:
        result = self.capture(
            "Delete the Zephyr memory but keep the surrounding capture for audit context. "
            "The next step is to verify memory-level forgetting."
        )
        memory = result["memories"][0]
        memory_id = memory["id"]

        self.assertTrue(list((self.store.vault.root / "memories").rglob(f"{memory_id}.json")))
        self.assertTrue(self.store.search(self.user_id, memory["content"].split()[0]))

        self.assertTrue(self.store.delete_memory(self.user_id, memory_id))

        with connect(self.db_path) as conn:
            self.assertIsNone(conn.execute("SELECT id FROM memories WHERE id = ?", (memory_id,)).fetchone())
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_fts WHERE memory_id = ?", (memory_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_entities WHERE memory_id = ?", (memory_id,)).fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM memory_topics WHERE memory_id = ?", (memory_id,)).fetchone()[0], 0)
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM graph_edges WHERE source_id = ? OR target_id = ? OR evidence_id = ?",
                    (memory_id, memory_id, memory_id),
                ).fetchone()[0],
                0,
            )

        self.assertFalse(list((self.store.vault.root / "memories").rglob(f"{memory_id}.json")))
        self.assertNotIn(memory["content"], self.store.export_markdown(self.user_id))
        self.assertTrue(any(event["event_type"] == "deleted" and event["object_id"] == memory_id for event in self.store.audit_log(self.user_id, limit=10)))

    def test_delete_capture_purges_raw_capture_and_cannot_rebuild_from_vault(self) -> None:
        phrase = "Purge Nimbus raw source text should disappear from current storage."
        result = self.capture(
            f"{phrase} "
            "Nimbus has an action item to remove all derived tasks and graph links."
        )
        capture_id = result["capture_id"]
        memory_ids = [memory["id"] for memory in result["memories"]]
        self.assertTrue(memory_ids)
        self.assertIn("Nimbus", self.store.export_markdown(self.user_id))
        self.assertTrue(list((self.store.vault.root / "captures").rglob(f"{capture_id}.json")))

        self.assertTrue(self.store.delete_capture(self.user_id, capture_id))

        payload = json.dumps(self.store.export_json(self.user_id), sort_keys=True)
        self.assertNotIn(phrase, payload)
        self.assertNotIn(capture_id, payload)
        for memory_id in memory_ids:
            self.assertNotIn(memory_id, payload)
            self.assertFalse(list((self.store.vault.root / "memories").rglob(f"{memory_id}.json")))
        self.assertFalse(list((self.store.vault.root / "captures").rglob(f"{capture_id}.json")))
        self.assertEqual(self.store.search(self.user_id, "Nimbus raw source"), [])
        self.assertEqual(self.store.diagnostics(self.user_id)["relation_orphans"], 0)

        rebuild = self.store.rebuild_index_from_vault(self.user_id)
        self.assertEqual(rebuild["captures"], 0)
        self.assertEqual(rebuild["memories"], 0)
        self.assertEqual(self.store.search(self.user_id, "Nimbus raw source"), [])

    def test_async_capture_queue_materializes_after_worker_run(self) -> None:
        phrase = "Queued Aurora capture should become searchable only after job processing."
        queued = self.store.enqueue_capture(
            user_id=self.user_id,
            content=phrase,
            source="unit-test-async",
            source_url=None,
            title="Queued capture",
        )
        capture_id = queued["capture_id"]
        job_id = queued["jobs"][0]["id"]

        self.assertEqual(queued["status"], "queued")
        self.assertEqual(queued["processing"]["processing"]["extraction_status"], "queued")
        self.assertEqual(self.store.search(self.user_id, "Aurora capture"), [])
        self.assertEqual(self.store.get_job(self.user_id, job_id)["status"], "queued")

        ran = self.store.run_due_jobs(self.user_id, limit=1)
        status = self.store.capture_status(self.user_id, capture_id)

        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")
        self.assertEqual(status["processing"]["extraction_status"], "succeeded")
        self.assertGreaterEqual(status["processing"]["memory_count"], 1)
        self.assertTrue(self.store.search(self.user_id, "Aurora capture"))
        with connect(self.db_path) as conn:
            vector_ready = self.store._vector_ready(conn)
        if vector_ready:
            self.assertIn(status["processing"]["embedding_status"], {"queued", "available", "failed"})
            if any(job["job_type"] == "embed_memory" and job["status"] == "queued" for job in status["jobs"]):
                embedded = self.store.run_due_jobs(self.user_id, limit=10)
                self.assertGreaterEqual(embedded["processed"], 1)
                status_after_embedding = self.store.capture_status(self.user_id, capture_id)
                self.assertIn(status_after_embedding["processing"]["embedding_status"], {"available", "failed"})
        else:
            self.assertEqual(status["processing"]["embedding_status"], "not_available")

    def test_source_readiness_reports_syncing_until_async_source_records_materialize(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Demo Vault",
            account_identifier="vault-demo",
            connection_type="local_folder",
            status="connected",
            auth_state="healthy",
        )
        synced = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "Decision: Cortex async source readiness should wait for materialized memory.",
                    "title": "Async readiness",
                    "external_id": "readiness-note",
                }
            ],
            processing="async",
        )
        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["queued"], 1)
        self.assertIsNotNone(synced["cursor"]["last_completed_at"])

        queued_report = self.store.source_readiness_report(self.user_id)
        queued_obsidian = next(source for source in queued_report["sources"] if source["source"] == "obsidian")
        self.assertEqual(queued_obsidian["status"], "syncing")
        self.assertEqual(queued_obsidian["processing"], 1)
        self.assertEqual(queued_obsidian["pending"], 1)
        self.assertEqual(queued_report["summary"]["syncing"], 1)
        self.assertEqual(queued_report["summary"]["processing"], 1)
        self.assertIn("Processing 1 source record", queued_obsidian["next_action"])
        self.assertTrue(any("source processing" in item for item in queued_report["recommendations"]))

        ran = self.store.run_due_jobs(self.user_id, limit=10)
        self.assertGreaterEqual(ran["processed"], 1)

        materialized_report = self.store.source_readiness_report(self.user_id)
        materialized_obsidian = next(source for source in materialized_report["sources"] if source["source"] == "obsidian")
        self.assertEqual(materialized_obsidian["status"], "needs_review")
        self.assertEqual(materialized_obsidian["processing"], 0)
        self.assertEqual(materialized_obsidian["pending"], 1)
        self.assertEqual(materialized_report["summary"]["syncing"], 0)
        self.assertEqual(materialized_report["summary"]["processing"], 0)

    def test_async_source_account_job_uses_deterministic_extraction_even_with_anthropic_key(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="obsidian",
            account_label="Async Local Vault",
            account_identifier="async-local-vault",
            connection_type="local_folder",
            status="connected",
            auth_state="healthy",
        )
        synced = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "Decision: Cortex async connector jobs must keep local-first extraction.",
                    "title": "Async local extraction",
                    "external_id": "Async/Local.md",
                }
            ],
            processing="async",
        )
        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["queued"], 1)
        capture_id = synced["capture_ids"][0]

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}), patch(
            "backend.app.extractor._extract_with_claude",
            side_effect=AssertionError("async connector extraction must stay local"),
        ):
            ran = self.store.run_due_jobs(self.user_id, limit=10)

        self.assertGreaterEqual(ran["processed"], 1)
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        found = self.store.search(self.user_id, "async connector jobs local-first extraction", limit=5)
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "obsidian")

    def test_async_source_record_update_replaces_stale_memory(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Demo Gmail",
            account_identifier="demo@example.com",
            connection_type="mcp",
            status="connected",
            auth_state="authorized",
        )
        first = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided async connector sync should clear the stale draft policy.",
                    "title": "Async update",
                    "external_id": "msg-async-update",
                }
            ],
            processing="async",
        )
        self.assertEqual(first["queued"], 1)
        self.assertEqual(first["records"][0]["status"], "queued")
        first_capture_id = first["records"][0]["capture_id"]

        ran_first = self.store.run_due_jobs(self.user_id, limit=10)
        self.assertGreaterEqual(ran_first["processed"], 1)
        self.assertEqual(self.store.search(self.user_id, "stale draft policy", limit=5), [])
        self.assertTrue(self.store.approve_capture(self.user_id, first_capture_id))
        self.assertTrue(self.store.search(self.user_id, "stale draft policy", limit=5))

        updated = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided async connector sync should preserve only the current remote policy.",
                    "title": "Async update changed",
                    "external_id": "msg-async-update",
                }
            ],
            processing="async",
        )
        self.assertEqual(updated["queued"], 1)
        self.assertEqual(updated["records"][0]["status"], "updated")
        self.assertEqual(updated["records"][0]["capture_id"], first_capture_id)
        self.assertEqual(self.store.search(self.user_id, "stale draft policy", limit=5), [])

        ran_updated = self.store.run_due_jobs(self.user_id, limit=10)
        self.assertGreaterEqual(ran_updated["processed"], 1)
        self.assertEqual(self.store.search(self.user_id, "current remote policy", limit=5), [])
        self.assertTrue(self.store.approve_capture(self.user_id, first_capture_id))
        self.assertTrue(self.store.search(self.user_id, "current remote policy", limit=5))
        with connect(self.db_path) as conn:
            memory_count = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ?",
                (self.user_id, first_capture_id),
            ).fetchone()[0]
            stale_memory = conn.execute(
                """
                SELECT id, status, valid_to, superseded_by
                FROM memories
                WHERE user_id = ?
                  AND capture_id = ?
                  AND content LIKE '%stale draft policy%'
                LIMIT 1
                """,
                (self.user_id, first_capture_id),
            ).fetchone()
            replacement_memory = conn.execute(
                """
                SELECT id, status
                FROM memories
                WHERE user_id = ?
                  AND capture_id = ?
                  AND content LIKE '%current remote policy%'
                LIMIT 1
                """,
                (self.user_id, first_capture_id),
            ).fetchone()
            capture = conn.execute(
                "SELECT source_account_id, external_id FROM captures WHERE user_id = ? AND id = ?",
                (self.user_id, first_capture_id),
            ).fetchone()
        self.assertGreaterEqual(memory_count, 1)
        self.assertIsNotNone(stale_memory)
        self.assertIsNotNone(replacement_memory)
        self.assertEqual(stale_memory["status"], "archived")
        self.assertTrue(stale_memory["valid_to"])
        self.assertEqual(stale_memory["superseded_by"], replacement_memory["id"])
        self.assertEqual(replacement_memory["status"], "active")
        self.assertEqual(capture["source_account_id"], account["id"])
        self.assertEqual(capture["external_id"], "msg-async-update")

        metadata_refresh = self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "I decided async connector sync should preserve only the current remote policy.",
                    "title": "Async update retitled",
                    "external_id": "msg-async-update",
                    "source_url": "service://gmail/messages/msg-async-update?thread=refreshed",
                    "metadata": {"relative_path": "Gmail/Async Refreshed.eml", "line_start": 8, "tags": ["async-refresh"]},
                }
            ],
            processing="async",
        )
        self.assertEqual(metadata_refresh["queued"], 1)
        self.assertEqual(metadata_refresh["records"][0]["status"], "updated")
        self.assertEqual(metadata_refresh["records"][0]["capture_id"], first_capture_id)
        ran_refresh = self.store.run_due_jobs(self.user_id, limit=10)
        self.assertGreaterEqual(ran_refresh["processed"], 1)
        refreshed_results = self.store.search(self.user_id, "current remote policy", limit=5)
        self.assertTrue(refreshed_results)
        self.assertIn("thread=refreshed", refreshed_results[0]["source_url"])
        with connect(self.db_path) as conn:
            refreshed_capture = conn.execute(
                "SELECT title, source_url, review_status FROM captures WHERE user_id = ? AND id = ?",
                (self.user_id, first_capture_id),
            ).fetchone()
            refreshed_memory = conn.execute(
                """
                SELECT provenance_json, topics_json
                FROM memories
                WHERE user_id = ?
                  AND capture_id = ?
                  AND status = 'active'
                  AND content LIKE '%current remote policy%'
                LIMIT 1
                """,
                (self.user_id, first_capture_id),
            ).fetchone()
        self.assertEqual(refreshed_capture["title"], "Async update retitled")
        self.assertEqual(refreshed_capture["source_url"], "service://gmail/messages/msg-async-update?thread=refreshed")
        self.assertEqual(refreshed_capture["review_status"], "approved")
        refreshed_provenance = json.loads(refreshed_memory["provenance_json"])
        self.assertEqual(refreshed_provenance["record_metadata"]["relative_path"], "Gmail/Async Refreshed.eml")
        self.assertIn("async refresh", json.loads(refreshed_memory["topics_json"]))

    def test_strict_embedding_failure_keeps_keyword_search_usable(self) -> None:
        previous_provider = os.environ.get("CORTEX_EMBEDDING_PROVIDER")
        previous_strict = os.environ.get("CORTEX_EMBEDDING_STRICT")
        previous_key = os.environ.get("OPENAI_API_KEY")
        os.environ["CORTEX_EMBEDDING_PROVIDER"] = "openai"
        os.environ["CORTEX_EMBEDDING_STRICT"] = "1"
        os.environ.pop("OPENAI_API_KEY", None)
        try:
            phrase = "Strict vector outage should leave FTS keyword search usable."
            result = self.capture(phrase)
            capture_id = result["capture_id"]
            self.assertTrue(self.store.search(self.user_id, "Strict vector outage"))
            status = self.store.capture_status(self.user_id, capture_id)
            with connect(self.db_path) as conn:
                vector_ready = self.store._vector_ready(conn)
            if vector_ready:
                self.assertIn(status["processing"]["embedding_status"], {"queued", "not_available"})
                if any(job["job_type"] == "embed_memory" and job["status"] == "queued" for job in status["jobs"]):
                    ran = self.store.run_due_jobs(self.user_id, limit=10)
                    self.assertGreaterEqual(ran["processed"], 1)
                self.assertTrue(self.store.search(self.user_id, "Strict vector outage"))
                status_after_failure = self.store.capture_status(self.user_id, capture_id)
                self.assertIn(status_after_failure["processing"]["embedding_status"], {"queued", "failed", "not_available"})
            else:
                self.assertEqual(status["processing"]["embedding_status"], "not_available")
        finally:
            if previous_provider is None:
                os.environ.pop("CORTEX_EMBEDDING_PROVIDER", None)
            else:
                os.environ["CORTEX_EMBEDDING_PROVIDER"] = previous_provider
            if previous_strict is None:
                os.environ.pop("CORTEX_EMBEDDING_STRICT", None)
            else:
                os.environ["CORTEX_EMBEDDING_STRICT"] = previous_strict
            if previous_key is None:
                os.environ.pop("OPENAI_API_KEY", None)
            else:
                os.environ["OPENAI_API_KEY"] = previous_key

    def test_search_uses_occurred_at_for_temporal_queries(self) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas launch decision kept Home, Review, Ask, and Connections & Privacy for beta onboarding.",
            source="notion",
            source_url="notion://page/atlas-2026",
            title="Project Atlas launch decision",
            extracted={
                "_timestamp": "2026-06-30T09:00:00Z",
                "summary": "Project Atlas launch decision.",
                "records": [
                    {
                        "id": "mem_atlas_2026",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Atlas launch decision kept Home, Review, Ask, and Connections & Privacy for beta onboarding.",
                        "summary": "Project Atlas kept the simple product loop.",
                        "topics": ["project-atlas", "launch"],
                        "entity_ids": [],
                        "confidence": "confirmed",
                        "importance": 4,
                        "occurred_at": "2026-06-29",
                    }
                ],
            },
        )
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas launch decision used the archive flow for the internal prototype.",
            source="notion",
            source_url="notion://page/atlas-2025",
            title="Project Atlas launch decision archive",
            extracted={
                "_timestamp": "2026-07-01T09:00:00Z",
                "summary": "Project Atlas launch decision archive.",
                "records": [
                    {
                        "id": "mem_atlas_2025",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Project Atlas launch decision used the archive flow for the internal prototype.",
                        "summary": "Project Atlas used the archive flow.",
                        "topics": ["project-atlas", "launch"],
                        "entity_ids": [],
                        "confidence": "confirmed",
                        "importance": 4,
                        "occurred_at": "2025-06-29",
                    }
                ],
            },
        )

        by_year = self.store.search(self.user_id, "Project Atlas launch 2026", limit=2)
        self.assertEqual(by_year[0]["id"], "mem_atlas_2026")
        self.assertEqual(by_year[0]["occurred_at"], "2026-06-29")

        date_only = self.store.search(self.user_id, "what happened in 2026", limit=5)
        self.assertIn("mem_atlas_2026", [memory["id"] for memory in date_only])

        by_month = self.store.search(self.user_id, "Project Atlas June 2026", limit=1)
        self.assertEqual(by_month[0]["id"], "mem_atlas_2026")

        answer = self.store.answer_query(self.user_id, "Project Atlas launch 2026", limit=1)
        self.assertEqual(answer["citations"][0]["id"], "mem_atlas_2026")
        self.assertEqual(answer["citations"][0]["occurred_at"], "2026-06-29")
        self.assertEqual(answer["citations"][0]["source_url"], "notion://page/atlas-2026")

    def test_deleting_queued_capture_removes_pending_job(self) -> None:
        queued = self.store.enqueue_capture(
            user_id=self.user_id,
            content="Queued deletion should not resurrect after worker run.",
            source="unit-test-async",
            source_url=None,
            title="Queued delete",
        )
        capture_id = queued["capture_id"]
        self.assertTrue(self.store.list_jobs(self.user_id, status="queued"))

        self.assertTrue(self.store.delete_capture(self.user_id, capture_id))
        ran = self.store.run_due_jobs(self.user_id, limit=5)

        self.assertEqual(ran["processed"], 0)
        self.assertEqual(self.store.search(self.user_id, "Queued deletion"), [])
        self.assertEqual(self.store.list_jobs(self.user_id), [])

    def test_job_health_reports_queued_failed_and_stale_running_jobs(self) -> None:
        first = self.store.enqueue_capture(
            user_id=self.user_id,
            content="Queue health should report queued work.",
            source="unit-test-async",
            source_url=None,
            title="Queued health",
        )
        queued_health = self.store.job_health(self.user_id)
        self.assertEqual(queued_health["status"], "attention")
        self.assertEqual(queued_health["counts"]["queued"], 1)
        self.assertEqual(queued_health["due_queued"], 1)
        self.assertIsNotNone(queued_health["oldest_queued_age_seconds"])

        second = self.store.enqueue_capture(
            user_id=self.user_id,
            content="Queue health should report failed work.",
            source="unit-test-async",
            source_url=None,
            title="Failed health",
        )
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = 'failed',
                    attempts = max_attempts,
                    last_error = 'Synthetic failure for queue health.',
                    updated_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
                """,
                (second["jobs"][0]["id"],),
            )
            conn.execute(
                """
                UPDATE memory_jobs
                SET status = 'running',
                    locked_by = 'stale-worker',
                    updated_at = '2000-01-01T00:00:00+00:00'
                WHERE id = ?
                """,
                (first["jobs"][0]["id"],),
            )

        blocked = self.store.job_health(self.user_id, stale_after_seconds=60)
        self.assertEqual(blocked["status"], "blocked")
        self.assertEqual(blocked["counts"]["failed"], 1)
        self.assertEqual(blocked["counts"]["running"], 1)
        self.assertEqual(blocked["recent_failures"][0]["last_error"], "Synthetic failure for queue health.")
        self.assertEqual(blocked["stale_running"][0]["locked_by"], "stale-worker")
        self.assertNotIn("payload", blocked["recent_failures"][0])
        self.assertNotIn("result", blocked["stale_running"][0])

    def test_backup_prune_removes_archives_that_can_retain_deleted_content(self) -> None:
        phrase = "Backup retention phrase should vanish after backup pruning."
        result = self.capture(phrase)
        backup = self.store.create_backup(self.user_id)
        backup_path = Path(backup["backup_path"])
        self.assertTrue(backup_path.exists())
        with zipfile.ZipFile(backup_path) as archive:
            backup_text = "\n".join(
                archive.read(name).decode("utf-8", errors="ignore")
                for name in archive.namelist()
                if name.endswith(".json") or name.endswith(".jsonl")
            )
        self.assertIn(phrase, backup_text)

        self.assertTrue(self.store.delete_capture(self.user_id, result["capture_id"]))
        self.assertTrue(backup_path.exists())

        pruned = self.store.delete_backups(self.user_id)
        self.assertEqual(pruned["deleted"], 1)
        self.assertGreater(pruned["bytes_deleted"], 0)
        self.assertFalse(backup_path.exists())
        self.assertEqual(self.store.diagnostics(self.user_id)["vault"]["record_counts"]["backups"], 0)

    def test_backup_excludes_local_credentials_logs_and_config_backups(self) -> None:
        self.capture("Backup allowlist should still include normal Cortex vault records.")
        sensitive_files = {
            "credentials.json": "{\"localBetaAPIKey.v1\":\"cx_secret\"}",
            "cortex-app.log": "Authorization: Bearer cx_secret",
            ".env": "CORTEX_API_KEY=cx_secret",
            ".cortex-backup-mcp.json": "{\"CORTEX_API_KEY\":\"cx_secret\"}",
            "exports/export.json": "{\"token\":\"cx_secret\"}",
            "attachments/credentials.json": "{\"token\":\"cx_secret\"}",
            "attachments/.cortex-backup-config.json": "{\"token\":\"cx_secret\"}",
        }
        for relative, content in sensitive_files.items():
            path = self.store.vault.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        normal_attachment = self.store.vault.root / "attachments" / "safe-note.txt"
        normal_attachment.write_text("safe attachment", encoding="utf-8")

        backup = self.store.create_backup(self.user_id)
        with zipfile.ZipFile(backup["backup_path"]) as archive:
            names = set(archive.namelist())
            backup_text = "\n".join(
                archive.read(name).decode("utf-8", errors="ignore")
                for name in names
                if name.endswith((".json", ".jsonl", ".txt", ".env"))
            )

        self.assertTrue(any(name.startswith("captures/") for name in names))
        self.assertIn("attachments/safe-note.txt", names)
        for relative in sensitive_files:
            self.assertNotIn(relative, names)
        self.assertNotIn("cx_secret", backup_text)

    def test_backup_retention_prunes_older_archives_by_count(self) -> None:
        self.capture("Backup retention should keep the newest recovery archive.")
        backup_paths: list[Path] = []
        for index in range(3):
            backup = self.store.create_backup(self.user_id)
            path = Path(backup["backup_path"])
            os.utime(path, (1_700_000_000 + index, 1_700_000_000 + index))
            backup_paths.append(path)

        pruned = self.store.prune_backups(self.user_id, keep_latest=1, max_age_days=0)

        self.assertEqual(pruned["deleted"], 2)
        self.assertGreater(pruned["bytes_deleted"], 0)
        self.assertFalse(backup_paths[0].exists())
        self.assertFalse(backup_paths[1].exists())
        self.assertTrue(backup_paths[2].exists())
        self.assertEqual(self.store.latest_backup()["backup_path"], str(backup_paths[2]))

    def test_restore_latest_backup_restores_vault_records_and_rebuilds_index(self) -> None:
        phrase = "Restore latest backup should recover the Orion memory."
        self.store.update_settings(self.user_id, {"allow_pending_in_context": False, "context_pack_limit": 7})
        result = self.capture(phrase)
        self.assertTrue(self.store.approve_capture(self.user_id, result["capture_id"]))
        backup = self.store.create_backup(self.user_id)
        backup_path = Path(backup["backup_path"])
        self.assertTrue(backup_path.exists())

        self.assertTrue(self.store.delete_user_data(self.user_id, include_backups=False))
        self.assertEqual(self.store.search(self.user_id, "Orion memory"), [])
        self.assertTrue(backup_path.exists())

        restored = self.store.restore_latest_backup(self.user_id)

        self.assertEqual(restored["backup_path"], str(backup_path))
        self.assertEqual(restored["rebuild"]["captures"], 1)
        self.assertGreaterEqual(restored["rebuild"]["memories"], 1)
        self.assertTrue(self.store.search(self.user_id, "Orion memory"))
        self.assertFalse(self.store.settings(self.user_id)["allow_pending_in_context"])
        self.assertEqual(self.store.settings(self.user_id)["context_pack_limit"], 7)
        self.assertTrue(any(event["event_type"] == "restored" for event in self.store.audit_log(self.user_id, limit=10)))

    def test_restore_latest_backup_honors_deleted_capture_tombstone(self) -> None:
        phrase = "Restore should not resurrect this deleted Solstice capture."
        result = self.capture(
            f"{phrase} "
            "Solstice has a derived memory and task that should stay deleted after restore."
        )
        capture_id = result["capture_id"]
        backup = self.store.create_backup(self.user_id)
        backup_path = Path(backup["backup_path"])
        self.assertTrue(backup_path.exists())

        self.assertTrue(self.store.delete_capture(self.user_id, capture_id))
        self.assertTrue(list(self.store.vault.iter_tombstones(self.user_id)))

        restored = self.store.restore_latest_backup(self.user_id)

        self.assertEqual(restored["backup_path"], str(backup_path))
        self.assertEqual(restored["tombstones"]["captures"], 1)
        self.assertEqual(restored["rebuild"]["captures"], 0)
        self.assertEqual(restored["rebuild"]["memories"], 0)
        self.assertEqual(self.store.search(self.user_id, "Solstice capture"), [])
        self.assertNotIn(phrase, json.dumps(self.store.export_json(self.user_id), sort_keys=True))

    def test_restore_latest_backup_honors_deleted_memory_tombstone(self) -> None:
        result = self.capture(
            "Restore should not resurrect the deleted Meridian memory. "
            "Meridian chose the retrieval plan because latency matters."
        )
        memory_id = result["memories"][0]["id"]
        backup = self.store.create_backup(self.user_id)
        backup_path = Path(backup["backup_path"])
        self.assertTrue(backup_path.exists())

        self.assertTrue(self.store.delete_memory(self.user_id, memory_id))
        self.assertTrue(list(self.store.vault.iter_tombstones(self.user_id)))

        restored = self.store.restore_latest_backup(self.user_id)
        memory_ids = {memory["id"] for memory in self.store.export_json(self.user_id)["memories"]}

        self.assertEqual(restored["backup_path"], str(backup_path))
        self.assertEqual(restored["tombstones"]["memories"], 1)
        self.assertNotIn(memory_id, memory_ids)
        self.assertFalse(list((self.store.vault.root / "memories").rglob(f"{memory_id}.json")))

    def test_restore_rejects_unsafe_backup_member_paths(self) -> None:
        backup_path = self.store.vault.backups_dir / "cortex-vault-unsafe.zip"
        self.store.vault.backups_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup_path, "w") as archive:
            archive.writestr("../evil.json", "{}")

        with self.assertRaises(ValueError):
            self.store.restore_latest_backup(self.user_id)

    def test_restore_rejects_denylisted_backup_member_paths(self) -> None:
        backup_path = self.store.vault.backups_dir / "cortex-vault-denylisted.zip"
        self.store.vault.backups_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(backup_path, "w") as archive:
            archive.writestr("attachments/credentials.json", "{\"token\":\"cx_secret\"}")

        with self.assertRaises(ValueError):
            self.store.restore_latest_backup(self.user_id)

    def test_delete_user_data_purges_current_vault_index_events_and_backups(self) -> None:
        phrase = "Delete all local Cortex user data phrase."
        self.capture(phrase)
        backups = [self.store.create_backup(self.user_id) for _ in range(2)]
        backup_paths = [Path(backup["backup_path"]) for backup in backups]
        self.assertTrue(all(path.exists() for path in backup_paths))
        self.assertTrue(self.store.search(self.user_id, "local Cortex user data"))
        self.assertTrue(list(self.store.vault.iter_events(self.user_id)))

        deleted = self.store.delete_user_data(self.user_id)

        self.assertTrue(deleted["include_backups"])
        self.assertEqual(deleted["vault"]["backups"], 2)
        self.assertGreater(deleted["vault"]["backup_bytes_deleted"], 0)
        self.assertGreaterEqual(deleted["sqlite"]["captures"], 1)
        self.assertGreaterEqual(deleted["sqlite"]["memories"], 1)
        self.assertGreaterEqual(deleted["vault"]["captures"], 1)
        self.assertGreaterEqual(deleted["vault"]["memories"], 1)
        self.assertGreaterEqual(deleted["vault"]["events"], 1)
        self.assertEqual(self.store.search(self.user_id, "local Cortex user data"), [])
        self.assertEqual(self.store.export_json(self.user_id)["captures"], [])
        self.assertFalse(list((self.store.vault.root / "captures").rglob("*.json")))
        self.assertFalse(list((self.store.vault.root / "memories").rglob("*.json")))
        self.assertFalse(list(self.store.vault.backups_dir.glob("*")))
        self.assertTrue(all(not path.exists() for path in backup_paths))
        self.assertIsNone(self.store.latest_backup())
        with self.assertRaises(FileNotFoundError):
            self.store.restore_latest_backup(self.user_id)
        self.assertEqual(list(self.store.vault.iter_events(self.user_id)), [])

        rebuilt = self.store.rebuild_index_from_vault(self.user_id)
        self.assertEqual(rebuilt["captures"], 0)
        self.assertEqual(rebuilt["memories"], 0)

    def test_init_db_upgrades_legacy_indexes_after_columns(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.executescript(
                """
                CREATE TABLE captures (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  source TEXT NOT NULL,
                  source_url TEXT,
                  title TEXT,
                  raw_text TEXT NOT NULL,
                  summary TEXT,
                  captured_at TEXT NOT NULL
                );
                CREATE TABLE memories (
                  id TEXT PRIMARY KEY,
                  capture_id TEXT,
                  user_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  content TEXT NOT NULL,
                  summary TEXT,
                  source TEXT NOT NULL,
                  source_url TEXT,
                  confidence TEXT NOT NULL DEFAULT 'confirmed',
                  importance INTEGER NOT NULL DEFAULT 3,
                  status TEXT NOT NULL DEFAULT 'active',
                  topics_json TEXT NOT NULL DEFAULT '[]',
                  entity_ids_json TEXT NOT NULL DEFAULT '[]',
                  occurred_at TEXT,
                  captured_at TEXT NOT NULL,
                  raw_excerpt TEXT
                );
                """
            )
            conn.commit()
        finally:
            conn.close()

        init_db(legacy_path)

        upgraded = sqlite3.connect(legacy_path)
        try:
            capture_columns = {row[1] for row in upgraded.execute("PRAGMA table_info(captures)")}
            memory_columns = {row[1] for row in upgraded.execute("PRAGMA table_info(memories)")}
            indexes = {row[1] for row in upgraded.execute("PRAGMA index_list(memories)")}
        finally:
            upgraded.close()

        self.assertIn("raw_hash", capture_columns)
        self.assertIn("review_status", capture_columns)
        self.assertIn("layer", memory_columns)
        self.assertIn("idx_memories_layer", indexes)
        self.assertIn("idx_memories_active_layer_rank", indexes)

    def test_init_db_migrates_legacy_entities_primary_key_preserving_rows(self) -> None:
        legacy_path = Path(self.tmp.name) / "legacy-entities.db"
        conn = sqlite3.connect(legacy_path)
        try:
            conn.executescript(
                """
                CREATE TABLE entities (
                  id TEXT PRIMARY KEY,
                  user_id TEXT NOT NULL,
                  kind TEXT NOT NULL,
                  name TEXT NOT NULL,
                  aliases_json TEXT NOT NULL DEFAULT '[]',
                  context TEXT,
                  first_seen TEXT NOT NULL,
                  last_seen TEXT NOT NULL
                );
                INSERT INTO entities
                (id, user_id, kind, name, aliases_json, context, first_seen, last_seen)
                VALUES
                ('project_taipei', 'legacy-user-a', 'project', 'Taipei', '["Taipei"]', 'legacy project context', '2026-06-01T00:00:00Z', '2026-06-02T00:00:00Z'),
                ('person_vamika', 'legacy-user-b', 'person', 'Vamika', '[]', 'legacy person context', '2026-06-03T00:00:00Z', '2026-06-04T00:00:00Z');
                """
            )
            conn.commit()
        finally:
            conn.close()

        init_db(legacy_path)

        upgraded = sqlite3.connect(legacy_path)
        upgraded.row_factory = sqlite3.Row
        try:
            table_info = upgraded.execute("PRAGMA table_info(entities)").fetchall()
            primary_key_columns = [
                row[1]
                for row in sorted((row for row in table_info if row[5]), key=lambda row: row[5])
            ]
            rows = upgraded.execute("SELECT * FROM entities ORDER BY user_id, id").fetchall()
            upgraded.execute(
                """
                INSERT INTO entities
                (id, user_id, kind, name, aliases_json, context, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "project_taipei",
                    "legacy-user-b",
                    "project",
                    "Taipei",
                    "[]",
                    "second user context",
                    "2026-06-05T00:00:00Z",
                    "2026-06-05T00:00:00Z",
                ),
            )
            upgraded.commit()
            shared_id_count = upgraded.execute(
                "SELECT COUNT(*) FROM entities WHERE id = ?",
                ("project_taipei",),
            ).fetchone()[0]
        finally:
            upgraded.close()

        self.assertEqual(primary_key_columns, ["user_id", "id"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["id"], "project_taipei")
        self.assertEqual(rows[0]["context"], "legacy project context")
        self.assertEqual(rows[1]["id"], "person_vamika")
        self.assertEqual(rows[1]["context"], "legacy person context")
        self.assertEqual(shared_id_count, 2)

    def test_backup_diagnostics_and_rebuild_search(self) -> None:
        self.capture("Cortex should create recoverable local backups before users trust it.")

        diagnostics = self.store.diagnostics(self.user_id)
        self.assertEqual(diagnostics["status"], "ok")
        self.assertEqual(diagnostics["quick_check"], "ok")
        self.assertEqual(diagnostics["fts_orphans"], 0)
        self.assertEqual(diagnostics["relation_orphans"], 0)

        rebuild = self.store.rebuild_search_index(self.user_id)
        self.assertGreaterEqual(rebuild["indexed_memories"], 1)

        backup = self.store.create_backup(self.user_id)
        self.assertTrue(Path(backup["backup_path"]).exists())
        self.assertGreater(backup["size_bytes"], 0)

    def test_reliability_report_and_repair_storage(self) -> None:
        result = self.capture(
            "Cortex reliability hardening should repair stale search rows and relationship drift. "
            "The next step is to make recovery understandable for everyday users."
        )
        memory_id = result["memories"][0]["id"]

        report_before = self.store.reliability_report(self.user_id)
        self.assertEqual(report_before["health_contract"], 3)
        self.assertTrue(any(check["name"] == "sqlite_quick_check" for check in report_before["checks"]))

        conn = sqlite3.connect(self.db_path)
        try:
            conn.execute("PRAGMA foreign_keys=OFF")
            conn.execute(
                "INSERT INTO memory_fts(memory_id, content, summary, source, topics) VALUES (?, ?, ?, ?, ?)",
                ("missing-memory", "orphan search text", "", "unit-test", "reliability"),
            )
            conn.execute(
                "INSERT OR REPLACE INTO memory_entities(memory_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                ("missing-memory", "missing-entity", self.user_id, "2026-01-01T00:00:00Z"),
            )
            conn.execute(
                """
                INSERT INTO memory_relations
                (id, user_id, source_memory_id, target_memory_id, kind, weight, metadata_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "rel_missing_target",
                    self.user_id,
                    memory_id,
                    "missing-memory",
                    "shared_entity",
                    1.0,
                    "{}",
                    "2026-01-01T00:00:00Z",
                ),
            )
            conn.execute("UPDATE memories SET status = 'archived' WHERE id = ?", (memory_id,))
            conn.commit()
        finally:
            conn.close()

        broken = self.store.diagnostics(self.user_id)
        self.assertEqual(broken["status"], "needs_maintenance")
        self.assertGreaterEqual(broken["fts_orphans"], 1)
        self.assertGreaterEqual(broken["inactive_fts_rows"], 1)
        self.assertGreaterEqual(broken["relation_orphans"], 1)

        repaired = self.store.repair_storage(self.user_id)
        self.assertTrue(Path(repaired["backup_path"]).exists())
        self.assertEqual(repaired["before"]["status"], "needs_maintenance")
        self.assertEqual(repaired["after"]["quick_check"], "ok")
        self.assertEqual(repaired["after"]["fts_orphans"], 0)
        self.assertEqual(repaired["after"]["inactive_fts_rows"], 0)
        self.assertEqual(repaired["after"]["relation_orphans"], 0)
        self.assertTrue(any(action["name"] == "remove_memory_relation_orphans" and action["rows"] >= 1 for action in repaired["actions"]))
        self.assertTrue(any(action["name"] == "rebuild_search_index" for action in repaired["actions"]))

        report_after = self.store.reliability_report(self.user_id)
        self.assertIsNotNone(report_after["latest_backup"])
        self.assertFalse(any(check["status"] == "critical" for check in report_after["checks"]))

    def test_sanitized_support_bundle_omits_user_content(self) -> None:
        self.capture(
            "Support bundle smoke text should never be shared with support. "
            f"The secret is password=supersecret123 and the API key is {DUMMY_OPENAI_KEY}."
        )
        self.store.record_agent_event(
            self.user_id,
            "search_memory",
            {"query": "Support bundle smoke text"},
            success=True,
        )
        bundle = self.store.support_bundle(self.user_id)
        payload = json.dumps(bundle, sort_keys=True)
        validate_content_free_bundle(bundle)

        self.assertEqual(bundle["bundle_schema"], 1)
        self.assertEqual(bundle["backend"]["health_contract"], 3)
        self.assertIn("operational-readiness", bundle["backend"]["features"])
        self.assertFalse(bundle["privacy"]["contains_raw_capture_text"])
        self.assertEqual(bundle["summary"]["counts"]["captures"], 1)
        self.assertIn("diagnostics", bundle)
        self.assertIn("reliability", bundle)
        self.assertIn("trust", bundle)
        self.assertTrue(bundle["recent_events"])
        self.assertNotIn("Support bundle smoke text should never be shared", payload)
        self.assertNotIn("supersecret123", payload)
        self.assertNotIn(DUMMY_OPENAI_KEY, payload)
        self.assertNotIn('"query": "Support bundle smoke text"', payload)

        mcp_bundle = call_tool(self.store, self.user_id, "get_support_bundle", {})
        self.assertEqual(mcp_bundle["bundle_schema"], 1)
        self.assertFalse(mcp_bundle["privacy"]["contains_memory_content"])
        validate_content_free_bundle(mcp_bundle)

        compromised = json.loads(json.dumps(bundle))
        compromised["captures"] = [{"raw_text": "private support text"}]
        with self.assertRaisesRegex(ValueError, "content-free"):
            validate_content_free_bundle(compromised)

        compromised_query = json.loads(json.dumps(bundle))
        compromised_query["recent_events"][0]["safe_metadata"]["query"] = "Support bundle smoke text"
        with self.assertRaisesRegex(ValueError, "content-free"):
            validate_content_free_bundle(compromised_query)

    def test_data_lifecycle_report_explains_storage_backup_export_and_delete(self) -> None:
        self.capture(
            "Lifecycle report should explain where local memory lives and how deletion works. "
            "We decided privacy receipts should preserve citations and backup state."
        )
        self.store.create_backup(self.user_id)

        report = self.store.data_lifecycle_report(self.user_id)

        self.assertEqual(report["storage"]["mode"], "local_first")
        self.assertEqual(report["storage"]["database_path"], str(self.db_path))
        self.assertEqual(report["storage"]["vault_path"], str(self.store.vault.root))
        self.assertGreaterEqual(report["record_counts"]["captures"], 1)
        self.assertGreaterEqual(report["record_counts"]["active_memories"], 1)
        self.assertGreaterEqual(report["backups"]["count"], 1)
        self.assertIsNotNone(report["backups"]["latest_backup"])
        self.assertTrue(report["export"]["redaction_enabled"])
        self.assertEqual(report["export"]["json_endpoint"], "/v1/export.json")
        self.assertTrue(report["deletion"]["include_backups_default"])
        self.assertIn("api_tokens", report["deletion"]["covered_sqlite"])
        self.assertIn("credentials", report["deletion"]["covered_vault"])
        self.assertIn("backups when include_backups=true", report["deletion"]["covered_vault"])
        self.assertEqual(report["deletion"]["tombstone_policy"], "block_restore")
        self.assertTrue(report["deletion"]["restore_preserves_tombstones"])
        self.assertEqual(report["ai_access"]["mode"], report["ai_access"]["mode"].lower())
        self.assertIn("audit", report)
        self.assertTrue(report["recommended_actions"])

    def test_daily_review_and_context_pack(self) -> None:
        self.capture(
            "Vamika decided Cortex should become a daily memory cockpit for ChatGPT and Claude. "
            "Cortex needs a cited adaptation profile before every AI session. "
            "The next step is to review open loops and test retention workflows."
        )

        review = self.store.daily_review(self.user_id)
        self.assertEqual(review["captured_today"], 1)
        self.assertEqual(review["stats"]["pending_captures"], 1)
        self.assertGreaterEqual(review["momentum_score"], 0)
        self.assertTrue(review["pending"])
        self.assertTrue(review["recent_memories"])
        self.assertTrue(review["recent_decisions"])
        self.assertTrue(review["open_tasks"])
        self.assertTrue(any("Review 1 pending item" in item for item in review["recommended_actions"]))
        self.assertIn("# Cortex Memory View", review["context_pack"])
        self.assertIn("Suggested Assistant Instruction", review["context_pack"])

        focused_pack = self.store.context_pack(self.user_id, query="ChatGPT Claude", limit=5)
        self.assertIn("Focus: ChatGPT Claude", focused_pack)
        self.assertIn("memory cockpit", focused_pack)
        self.assertIn("## Open Loops", focused_pack)
        self.assertIn("test retention workflows", focused_pack)

    def test_layered_memory_extraction_and_context_pack(self) -> None:
        result = self.capture(
            "I prefer concise technical answers. "
            "My writing style uses short direct sentences. "
            "I don't like fluffy introductions or vague summaries. "
            "Yesterday I met with Alex about Cortex onboarding. "
            "Vamika decided Cortex should group memory layers for retrieval."
        )

        layers = {item["layer"] for item in result["memories"]}
        self.assertIn("preference", layers)
        self.assertIn("style", layers)
        self.assertIn("negative", layers)
        self.assertIn("decision", layers)
        self.assertTrue(any(item["kind"] == "event" and item["layer"] == "episodic" for item in result["memories"]))
        self.assertEqual(self.store.search(self.user_id, "fluffy", limit=1)[0]["layer"], "negative")
        self.assertEqual(self.store.search(self.user_id, "writing style", limit=1)[0]["layer"], "style")
        filtered = self.store.search(self.user_id, "fluffy introductions", limit=5, layer="negative")
        self.assertTrue(filtered)
        self.assertTrue(all(item["layer"] == "negative" for item in filtered))
        mcp_filtered = call_tool(self.store, self.user_id, "search_memory", {"query": "short direct sentences", "layer": "style"})["results"]
        self.assertTrue(mcp_filtered)
        self.assertTrue(all(item["layer"] == "style" for item in mcp_filtered))

        pack = self.store.context_pack(self.user_id, limit=10)
        self.assertIn("### Preference Memory", pack)
        self.assertIn("### Style Memory", pack)
        self.assertIn("### Negative Memory", pack)
        self.assertIn("### Decision Memory", pack)

    def test_simple_product_loop_tracks_capture_review_and_reuse(self) -> None:
        empty_loop = self.store.product_loop(self.user_id)
        self.assertEqual(empty_loop["primary_action"]["action"], "capture")
        self.assertEqual(empty_loop["counts"]["pending_captures"], 0)

        capture = self.capture(
            "Cortex should make the daily loop obvious: capture, review, reuse, and return. "
            "The next step is to copy context into Claude before planning."
        )
        pending_loop = self.store.product_loop(self.user_id)
        self.assertEqual(pending_loop["primary_action"]["action"], "review")
        self.assertEqual(pending_loop["counts"]["pending_captures"], 1)
        self.assertTrue(any(step["key"] == "capture" and step["status"] == "done" for step in pending_loop["steps"]))
        self.assertTrue(any(step["key"] == "review" and step["status"] == "current" for step in pending_loop["steps"]))

        self.assertTrue(self.store.approve_capture(self.user_id, capture["capture_id"]))
        reuse_loop = self.store.product_loop(self.user_id)
        self.assertEqual(reuse_loop["primary_action"]["action"], "reuse")
        self.assertEqual(reuse_loop["counts"]["pending_captures"], 0)

        answer = self.store.answer_query(self.user_id, "daily loop", limit=5)
        self.assertTrue(answer["citations"])
        ask_loop = self.store.product_loop(self.user_id)
        self.assertEqual(ask_loop["primary_action"]["action"], "done")
        self.assertGreaterEqual(ask_loop["counts"]["used_today"], 1)
        self.assertTrue(any(step["key"] == "reuse" and step["status"] == "done" for step in ask_loop["steps"]))

        recorded = self.store.record_context_reuse(self.user_id, surface="unit-test", query="daily loop", target="Claude")
        self.assertTrue(recorded["recorded"])
        done_loop = recorded["product_loop"]
        self.assertEqual(done_loop["primary_action"]["action"], "done")
        self.assertGreaterEqual(done_loop["counts"]["reused_today"], 1)
        self.assertTrue(any(step["key"] == "reuse" and step["status"] == "done" for step in done_loop["steps"]))

        mcp_loop = call_tool(self.store, self.user_id, "get_product_loop", {})
        self.assertEqual(mcp_loop["primary_action"]["action"], "done")

    def test_settings_control_pending_context_visibility(self) -> None:
        strict_user = "strict-default-user"
        defaults = self.store.settings(strict_user)
        self.assertTrue(defaults["review_new_captures"])
        self.assertFalse(defaults["allow_pending_in_context"])
        self.assertTrue(defaults["allow_agent_reads"])
        self.assertTrue(defaults["allow_agent_writes"])
        self.assertFalse(defaults["allow_agent_exports"])
        self.assertFalse(defaults["allow_agent_maintenance"])
        self.assertFalse(defaults["allow_agent_destructive_actions"])
        self.assertTrue(defaults["redact_sensitive_context"])

        result = self.store.save_capture(
            user_id=strict_user,
            content=(
                "Vamika decided strict mode should hide pending Cortex captures from assistants. "
                "Cortex needs approval before this memory appears in strict search."
            ),
            source="unit-test",
            source_url=None,
            title="Unit test capture",
            extracted=extract_context(
                "Vamika decided strict mode should hide pending Cortex captures from assistants. "
                "Cortex needs approval before this memory appears in strict search.",
                "unit-test",
            ),
        )
        capture_id = result["capture_id"]

        self.assertEqual(self.store.search(strict_user, "strict mode pending"), [])
        self.assertNotIn("strict mode should hide", self.store.context_pack(strict_user, query="strict mode"))

        updated = self.store.update_settings(strict_user, {"allow_pending_in_context": True, "context_pack_limit": 6})
        self.assertTrue(updated["allow_pending_in_context"])
        self.assertEqual(updated["context_pack_limit"], 6)
        self.assertTrue(self.store.search(strict_user, "strict mode pending"))
        self.assertIn("strict mode should hide", self.store.context_pack(strict_user, query="strict mode"))

        updated = self.store.update_settings(strict_user, {"allow_pending_in_context": False})
        self.assertFalse(updated["allow_pending_in_context"])
        self.assertEqual(self.store.search(strict_user, "strict mode pending"), [])

        self.assertTrue(self.store.approve_capture(strict_user, capture_id))
        self.assertTrue(self.store.search(strict_user, "strict mode pending"))
        self.assertIn("strict mode should hide", self.store.context_pack(strict_user, query="strict mode"))

        self.store.update_settings(strict_user, {"review_new_captures": False})
        auto_text = "Cortex should auto approve captures when review is turned off."
        auto = self.store.save_capture(
            user_id=strict_user,
            content=auto_text,
            source="unit-test",
            source_url=None,
            title="Unit test capture",
            extracted=extract_context(auto_text, "unit-test"),
        )
        self.assertEqual(self.store.inbox(strict_user), [])
        self.assertTrue(self.store.search(strict_user, "auto approve captures"))
        self.assertTrue(self.store.archive_capture(strict_user, auto["capture_id"]))

    def test_source_policies_control_retrieval_visibility(self) -> None:
        result = self.store.save_capture(
            user_id=self.user_id,
            content="Private Source Alpha should only appear when its source policy allows it.",
            source="gmail",
            source_url="gmail://message/alpha",
            title="Private source policy",
            extracted=extract_context("Private Source Alpha should only appear when its source policy allows it.", "gmail"),
        )
        capture_id = result["capture_id"]

        self.assertTrue(self.store.search(self.user_id, "Private Source Alpha"))

        excluded = self.store.update_settings(
            self.user_id,
            {"source_policies": {"gmail": {"mode": "excluded"}}},
        )
        self.assertEqual(excluded["source_policies"]["gmail"]["mode"], "excluded")
        self.assertEqual(self.store.search(self.user_id, "Private Source Alpha"), [])
        self.assertNotIn("only appear when its source policy allows it", self.store.context_pack(self.user_id, query="Private Source Alpha"))

        review_first = self.store.update_settings(
            self.user_id,
            {"source_policies": {"gmail": {"mode": "review"}}},
        )
        self.assertTrue(review_first["source_policies"]["gmail"]["review_required"])
        self.assertEqual(self.store.search(self.user_id, "Private Source Alpha"), [])

        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertTrue(self.store.search(self.user_id, "Private Source Alpha"))

        cleared = self.store.update_settings(self.user_id, {"source_policies": {"gmail": {"mode": "default"}}})
        self.assertEqual(cleared["source_policies"], {})

    def test_graph_honors_pending_review_visibility(self) -> None:
        pending = self.store.save_capture(
            user_id=self.user_id,
            content="Pending Graph Alpha should not appear in graph while pending context is disabled.",
            source="docs",
            source_url="/tmp/pending-graph-alpha.md",
            title="Pending graph alpha",
            extracted=extract_context("Pending Graph Alpha should not appear in graph while pending context is disabled.", "docs"),
        )
        self.assertTrue(pending["memories"])

        self.store.update_settings(self.user_id, {"allow_pending_in_context": False})
        graph_text = json.dumps(self.store.graph(self.user_id))
        self.assertNotIn("Pending Graph Alpha", graph_text)
        self.assertNotIn("Pending graph alpha", graph_text)

        self.assertTrue(self.store.approve_capture(self.user_id, pending["capture_id"]))
        approved_graph_text = json.dumps(self.store.graph(self.user_id))
        self.assertIn("Pending Graph Alpha", approved_graph_text)

    def test_source_policy_exclusion_filters_exports_and_mcp_export(self) -> None:
        private = self.store.save_capture(
            user_id=self.user_id,
            content="Private Export Alpha should never leave through full memory export.",
            source="email",
            source_url="/tmp/mail.mbox#service=email&subject=Private%20Export%20Alpha",
            title="Private export source policy",
            extracted=extract_context("Private Export Alpha should never leave through full memory export.", "email"),
        )
        public = self.store.save_capture(
            user_id=self.user_id,
            content="Public Export Beta should remain available in full memory export.",
            source="docs",
            source_url="file:///PublicExportBeta.md",
            title="Public export source policy",
            extracted=extract_context("Public Export Beta should remain available in full memory export.", "docs"),
        )
        self.assertTrue(private["memories"])
        self.assertTrue(public["memories"])
        self.assertTrue(self.store.approve_capture(self.user_id, private["capture_id"]))
        self.assertTrue(self.store.approve_capture(self.user_id, public["capture_id"]))

        self.store.update_settings(
            self.user_id,
            {
                "allow_agent_exports": True,
                "source_policies": {"gmail": {"mode": "excluded"}},
            },
        )
        self.assertEqual(self.store.search(self.user_id, "Private Export Alpha"), [])
        excluded_pack = self.store.context_pack(self.user_id, query="Private Export Alpha")
        self.assertNotIn("should never leave through full memory export", excluded_pack)
        self.assertNotIn("service=email", excluded_pack)

        exported = self.store.export_json(self.user_id)
        exported_text = json.dumps(exported)
        self.assertNotIn("Private Export Alpha", exported_text)
        self.assertNotIn("Private%20Export%20Alpha", exported_text)
        self.assertIn("Public Export Beta", exported_text)
        self.assertTrue(all(capture["source"] != "email" for capture in exported["captures"]))
        self.assertTrue(all(memory["source"] != "email" for memory in exported["memories"]))

        markdown = self.store.export_markdown(self.user_id)
        self.assertNotIn("Private Export Alpha", markdown)
        self.assertIn("Public Export Beta", markdown)

        mcp_json = call_tool(self.store, self.user_id, "export_memory", {"format": "json"})
        self.assertNotIn("Private Export Alpha", json.dumps(mcp_json))
        self.assertIn("Public Export Beta", json.dumps(mcp_json))
        mcp_markdown = call_tool(self.store, self.user_id, "export_memory", {"format": "markdown"})
        self.assertNotIn("Private Export Alpha", mcp_markdown)
        self.assertIn("Public Export Beta", mcp_markdown)

        profile = self.store.personal_profile(self.user_id, query="Export", limit=5)
        self.assertNotIn("Private Export Alpha", json.dumps(profile))
        self.assertNotIn("email", json.dumps(profile["coverage"]))
        self.assertIn("Public Export Beta", json.dumps(profile))
        adaptation = self.store.agent_adaptation(self.user_id, query="Export", target="Claude", limit=5)
        self.assertNotIn("Private Export Alpha", json.dumps(adaptation))
        self.assertNotIn("email", json.dumps(adaptation["coverage"]))
        self.assertIn("Public Export Beta", json.dumps(adaptation))

        graph = self.store.graph(self.user_id)
        graph_text = json.dumps(graph)
        self.assertNotIn("Private Export Alpha", graph_text)
        self.assertNotIn("Private export source policy", graph_text)
        self.assertIn("Public Export Beta", graph_text)

    def test_trust_controls_redact_shared_context_and_exports(self) -> None:
        self.capture(
            f"Cortex should never leak password=supersecret123 or {DUMMY_OPENAI_KEY} "
            "or vamika@example.com or ghp_abcdefghijklmnopqrstuvwxyz123456 "
            "or xoxb-12345678901234567890 or 4111 1111 1111 1111 to agent context after testing."
        )

        pack = self.store.context_pack(self.user_id, query="leak", limit=5)
        self.assertIn("[REDACTED_SECRET]", pack)
        self.assertIn("[REDACTED_OPENAI_KEY]", pack)
        self.assertIn("[REDACTED_EMAIL]", pack)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", pack)
        self.assertIn("[REDACTED_SLACK_TOKEN]", pack)
        self.assertIn("[REDACTED_NUMBER]", pack)
        self.assertNotIn("supersecret123", pack)
        self.assertNotIn("vamika@example.com", pack)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", pack)
        self.assertNotIn("xoxb-12345678901234567890", pack)
        self.assertNotIn("4111 1111 1111 1111", pack)

        export = self.store.export_markdown(self.user_id)
        self.assertIn("[REDACTED_SECRET]", export)
        self.assertNotIn(DUMMY_OPENAI_KEY, export)
        self.assertNotIn("supersecret123", export)
        self.assertNotIn("vamika@example.com", export)

        json_export = self.store.export_json(self.user_id)
        json_export_text = json.dumps(json_export, sort_keys=True)
        self.assertIn("[REDACTED_SECRET]", json_export_text)
        self.assertIn("[REDACTED_OPENAI_KEY]", json_export_text)
        self.assertIn("[REDACTED_EMAIL]", json_export_text)
        self.assertIn("[REDACTED_GITHUB_TOKEN]", json_export_text)
        self.assertIn("[REDACTED_SLACK_TOKEN]", json_export_text)
        self.assertIn("[REDACTED_NUMBER]", json_export_text)
        self.assertNotIn("supersecret123", json_export_text)
        self.assertNotIn(DUMMY_OPENAI_KEY, json_export_text)
        self.assertNotIn("vamika@example.com", json_export_text)
        self.assertNotIn("ghp_abcdefghijklmnopqrstuvwxyz123456", json_export_text)
        self.assertNotIn("xoxb-12345678901234567890", json_export_text)
        self.assertNotIn("4111 1111 1111 1111", json_export_text)

        self.store.update_settings(self.user_id, {"allow_agent_exports": True})
        mcp_json = call_tool(self.store, self.user_id, "export_memory", {"format": "json"})
        mcp_json_text = json.dumps(mcp_json, sort_keys=True)
        self.assertIn("[REDACTED_SECRET]", mcp_json_text)
        self.assertNotIn("supersecret123", mcp_json_text)
        self.assertNotIn(DUMMY_OPENAI_KEY, mcp_json_text)

        mcp_markdown = call_tool(self.store, self.user_id, "export_memory", {"format": "markdown"})
        self.assertIn("[REDACTED_SECRET]", mcp_markdown)
        self.assertNotIn("supersecret123", mcp_markdown)
        self.assertNotIn(DUMMY_OPENAI_KEY, mcp_markdown)

        self.store.update_settings(self.user_id, {"redact_sensitive_context": False})
        unredacted = self.store.context_pack(self.user_id, query="leak", limit=5)
        self.assertIn("supersecret123", unredacted)

    def test_trust_controls_redact_local_paths_from_shared_citations(self) -> None:
        local_path = "/Users/vamika/Documents/Cortex Private/Project Atlas.md#line=12"
        capture = self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas should hide local citation paths before agent sharing.",
            source="docs",
            source_url=local_path,
            title=local_path,
            extracted=extract_context("Project Atlas should hide local citation paths before agent sharing.", "docs"),
        )
        self.assertTrue(self.store.approve_capture(self.user_id, capture["capture_id"]))

        search_results = self.store.search(self.user_id, "Project Atlas citation paths", limit=5)
        self.assertTrue(search_results)
        self.assertEqual(search_results[0]["source_url"], local_path)

        answer = self.store.answer_query(self.user_id, "Project Atlas citation paths", limit=5)
        answer_text = json.dumps(answer)
        self.assertNotIn("/Users/vamika", answer_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", answer_text)

        context_pack = self.store.context_pack(self.user_id, query="Project Atlas", limit=5)
        self.assertNotIn("/Users/vamika", context_pack)
        self.assertIn("local-file://Project%20Atlas.md#line=12", context_pack)

        profile = self.store.personal_profile(self.user_id, query="Project Atlas", limit=5)
        profile_text = json.dumps(profile)
        self.assertNotIn("/Users/vamika", profile_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", profile["markdown"])

        adaptation = self.store.agent_adaptation(self.user_id, query="Project Atlas", target="Claude", limit=5)
        adaptation_text = json.dumps(adaptation)
        self.assertNotIn("/Users/vamika", adaptation_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", adaptation["markdown"])

        exported = self.store.export_json(self.user_id)
        exported_text = json.dumps(exported)
        self.assertNotIn("/Users/vamika", exported_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", exported_text)

        markdown = self.store.export_markdown(self.user_id)
        self.assertNotIn("/Users/vamika", markdown)
        self.assertIn("local-file://Project%20Atlas.md#line=12", markdown)

        mcp_search = call_tool(self.store, self.user_id, "search_memory", {"query": "Project Atlas", "top_k": 5})
        mcp_search_text = json.dumps(mcp_search)
        self.assertNotIn("/Users/vamika", mcp_search_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", mcp_search_text)

        graph_text = json.dumps(self.store.graph(self.user_id))
        self.assertNotIn("/Users/vamika", graph_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", graph_text)

        self.store.update_settings(self.user_id, {"redact_sensitive_context": False})
        unredacted_answer = self.store.answer_query(self.user_id, "Project Atlas citation paths", limit=5)
        unredacted_answer_text = json.dumps(unredacted_answer)
        self.assertNotIn("/Users/vamika", unredacted_answer_text)
        self.assertIn("local-file://Project%20Atlas.md#line=12", unredacted_answer_text)

    def test_trust_controls_redact_local_paths_inside_service_citation_parameters(self) -> None:
        service_locator = (
            "notion://page/project-atlas?workspace=beta"
            "&path=/Users/vamika/Documents/Cortex Private/Project Atlas.md"
            "#anchor=/Users/vamika/Library/Application Support/Cortex/Project Atlas Notes.md"
            "&encoded=%2FUsers%2Fvamika%2FDocuments%2FCortex%20Private%2FEncoded%20Plan.md%23line%3D44"
            "&line=12"
        )
        capture = self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas service locator parameters should preserve service citations without local paths.",
            source="notion",
            source_url=service_locator,
            title="Project Atlas service locator",
            extracted=extract_context(
                "Project Atlas service locator parameters should preserve service citations without local paths.",
                "notion",
            ),
        )
        self.assertTrue(self.store.approve_capture(self.user_id, capture["capture_id"]))
        self.store.update_settings(self.user_id, {"allow_agent_exports": True})

        shared_outputs = [
            json.dumps(self.store.answer_query(self.user_id, "Project Atlas service locator", limit=5)),
            self.store.context_pack(self.user_id, query="Project Atlas service locator", limit=5),
            json.dumps(self.store.personal_profile(self.user_id, query="Project Atlas service locator", limit=5)),
            json.dumps(self.store.export_json(self.user_id)),
            self.store.export_markdown(self.user_id),
            json.dumps(call_tool(self.store, self.user_id, "search_memory", {"query": "Project Atlas service locator", "top_k": 5})),
            json.dumps(call_tool(self.store, self.user_id, "export_memory", {"format": "json"})),
            call_tool(self.store, self.user_id, "build_context_pack", {"query": "Project Atlas service locator", "limit": 5}),
        ]

        for output in shared_outputs:
            with self.subTest(output=output[:80]):
                self.assertNotIn("/Users/vamika", output)
                self.assertNotIn("%2FUsers%2Fvamika", output)
                self.assertIn("notion://page/project-atlas", output)
                self.assertIn("workspace=beta", output)
                self.assertIn("path=local-file://Project%20Atlas.md", output)
                self.assertIn("anchor=local-file://Project%20Atlas%20Notes.md", output)
                self.assertIn("encoded=local-file://Encoded%20Plan.md%23line%3D44", output)
                self.assertIn("line=12", output)

    def test_agent_permission_gates_and_audit_summary(self) -> None:
        self.store.update_settings(self.user_id, {"allow_agent_writes": False})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "remember_this", {"content": "Agents should not write right now."})

        self.store.update_settings(self.user_id, {"allow_agent_writes": True})
        saved = call_tool(self.store, self.user_id, "remember_this", {"content": "Agents can save after permission is restored."})
        self.assertIn("capture_id", saved)

        self.store.update_settings(self.user_id, {"allow_agent_reads": False})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "search_memory", {"query": "permission"})

        self.store.update_settings(self.user_id, {"allow_agent_reads": True, "allow_agent_exports": False})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "build_context_pack", {"query": "permission"})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "get_personal_profile", {})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "get_agent_adaptation", {})

        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "create_memory_backup", {})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "get_memory_diagnostics", {})
        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "get_reliability_report", {})
        self.store.update_settings(self.user_id, {"allow_agent_maintenance": True})
        backup = call_tool(self.store, self.user_id, "create_memory_backup", {})
        self.assertIn("backup_path", backup)
        diagnostics = call_tool(self.store, self.user_id, "get_memory_diagnostics", {})
        self.assertIn("quick_check", diagnostics)

        with self.assertRaises(PermissionError):
            call_tool(self.store, self.user_id, "forget_memory", {"id": saved["memories"][0]["id"]})
        self.store.update_settings(self.user_id, {"allow_agent_destructive_actions": True})
        deleted = call_tool(self.store, self.user_id, "forget_memory", {"id": saved["memories"][0]["id"]})
        self.assertTrue(deleted["deleted"])

        self.store.record_agent_event(self.user_id, "search_memory", {"query": "permission"}, success=False, error="blocked")
        audit = self.store.audit_log(self.user_id, limit=5)
        self.assertTrue(any(item["object_type"] == "agent" and item["event_type"] == "tool_call" for item in audit))
        trust = self.store.trust_summary(self.user_id)
        self.assertIn("trust_score", trust)
        self.assertTrue(any("Connected agents" in item or "exports" in item.lower() for item in trust["risk_flags"]))

    def test_local_vault_files_rebuild_the_index(self) -> None:
        self.store.update_settings(self.user_id, {"allow_pending_in_context": False, "context_pack_limit": 9})
        result = self.capture(
            "Cortex local vault files should be the durable source of truth. "
            "The SQLite index should be rebuildable from disk if it is lost."
        )
        capture_id = result["capture_id"]
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertTrue(self.store.search(self.user_id, "durable source of truth"))

        vault = self.store.vault
        self.assertTrue(vault.manifest_path.exists())
        self.assertTrue(vault.settings_path.exists())
        self.assertTrue(vault.events_path.exists())
        self.assertTrue(list((vault.root / "captures").rglob("*.json")))
        self.assertTrue(list((vault.root / "memories").rglob("*.json")))

        diagnostics = self.store.diagnostics(self.user_id)
        self.assertEqual(diagnostics["vault"]["record_counts"]["captures"], 1)
        self.assertGreaterEqual(diagnostics["vault"]["record_counts"]["memories"], 1)
        self.assertGreaterEqual(diagnostics["vault"]["event_count"], 3)

        with connect(self.db_path) as conn:
            conn.execute("DELETE FROM memory_fts")
            conn.execute("DELETE FROM memory_entities WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM memory_topics WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM task_entities WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM task_topics WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM graph_edges WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM tasks WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM memories WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM entities WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM captures WHERE user_id = ?", (self.user_id,))
            conn.execute("DELETE FROM user_settings WHERE user_id = ?", (self.user_id,))

        self.assertEqual(self.store.search(self.user_id, "durable source of truth"), [])

        rebuild = self.store.rebuild_index_from_vault(self.user_id)
        self.assertEqual(rebuild["captures"], 1)
        self.assertGreaterEqual(rebuild["memories"], 1)
        self.assertTrue(self.store.search(self.user_id, "durable source of truth"))
        self.assertFalse(self.store.settings(self.user_id)["allow_pending_in_context"])
        self.assertEqual(self.store.settings(self.user_id)["context_pack_limit"], 9)

    def test_legacy_index_backfills_empty_vault(self) -> None:
        account = self.store.upsert_source_account(
            self.user_id,
            source="gmail",
            account_label="Legacy Gmail",
            account_identifier="legacy@example.com",
            connection_type="mcp",
            status="connected",
            auth_state="authorized",
        )
        self.store.sync_source_account_records(
            self.user_id,
            account["id"],
            records=[
                {
                    "content": "Existing local SQLite connector records should keep source identity after upgrade.",
                    "title": "Legacy source identity",
                    "external_id": "legacy-msg-001",
                }
            ],
            processing="sync",
        )

        for directory in ("captures", "memories", "tasks", "entities", "graph_edges"):
            shutil.rmtree(self.store.vault.root / directory)
            (self.store.vault.root / directory).mkdir()
        self.store.vault.events_path.unlink()

        backfill = self.store.ensure_vault_backfilled(self.user_id)
        self.assertTrue(backfill["backfilled"])
        self.assertEqual(backfill["captures"], 1)
        self.assertTrue(list((self.store.vault.root / "captures").rglob("*.json")))
        self.assertTrue(list((self.store.vault.root / "memories").rglob("*.json")))
        self.assertTrue(self.store.vault.events_path.exists())
        capture_payload = json.loads(next((self.store.vault.root / "captures").rglob("*.json")).read_text())
        self.assertEqual(capture_payload["source_account_id"], account["id"])
        self.assertEqual(capture_payload["external_id"], "legacy-msg-001")


if __name__ == "__main__":
    unittest.main()
