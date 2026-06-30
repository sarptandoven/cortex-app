from __future__ import annotations

import tempfile
import unittest
import shutil
import sqlite3
import json
import os
import zipfile
from pathlib import Path

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
        self.assertGreater(stats["edges"], 0)

        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertEqual(self.store.stats(self.user_id)["pending_captures"], 0)

        stats = self.store.stats(self.user_id)
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

        self.assertTrue(self.store.archive_capture(self.user_id, result["capture_id"]))
        with connect(self.db_path) as conn:
            relation_count_after_archive = conn.execute(
                "SELECT COUNT(*) FROM memory_relations WHERE user_id = ?",
                (self.user_id,),
            ).fetchone()[0]
        self.assertEqual(relation_count_after_archive, 0)
        self.assertEqual(self.store.answer_query(self.user_id, "risk budget local-first path", limit=2)["citations"], [])

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
        self.assertEqual(answer["citations"][0]["source_url"], "slack://channel/C123/p202606291200")
        self.assertIn("API keys rotation", answer["citations"][0]["excerpt"])
        self.assertEqual(answer["results"][0]["result_type"], "task")
        self.assertEqual(answer["results"][0]["source_url"], "slack://channel/C123/p202606291200")

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
        self.assertEqual(approved_answer["citations"][0]["source_url"], "message://api-key-policy")

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
        self.assertEqual(connected_gmail["status"], "connected")
        self.assertEqual(connected_gmail["beta_status"], "planned")
        self.assertFalse(connected_gmail["primary_beta"])
        self.assertFalse(connected_gmail["show_in_primary_ui"])

        self.assertTrue(account["id"].startswith("sacct_"))
        self.assertEqual(account["source"], "gmail")
        self.assertEqual(account["account_label"], "Work Gmail")
        self.assertEqual(account["policy"]["sync"], "metadata_and_content")
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
        self.assertEqual(synced_gmail["status"], "synced")
        self.assertEqual(synced_gmail["beta_status"], "planned")
        self.assertFalse(synced_gmail["primary_beta"])
        self.assertFalse(synced_gmail["show_in_primary_ui"])
        self.assertEqual(synced_gmail["primary_beta_path"], "account-sign-in-planned")

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
                SELECT id, status, superseded_by
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
        self.assertEqual(stale_memory["superseded_by"], replacement_memory["id"])
        self.assertEqual(replacement_memory["status"], "active")

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

    def test_mcp_connected_source_tools_register_and_sync_cited_records(self) -> None:
        connectors = call_tool(self.store, self.user_id, "list_source_connectors", {"include_accounts": False})
        connector_ids = {item["id"] for item in connectors["results"]}
        self.assertIn("notion", connector_ids)

        account_payload = call_tool(
            self.store,
            self.user_id,
            "connect_source_account",
            {
                "source": "notion",
                "account_label": "Demo Notion",
                "account_identifier": "workspace-demo",
                "connection_type": "mcp",
                "policy": {"sync": "pages_and_comments"},
                "metadata": {"workspace": "first-100"},
            },
            token_scopes=["write"],
        )
        account = account_payload["account"]
        self.assertEqual(account["source"], "notion")
        self.assertEqual(account["status"], "connected")

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
                        "content": "I decided Notion should become the canonical project memory source for Project Helix.",
                        "title": "Project Helix memory decision",
                        "external_id": "page-helix",
                        "captured_at": "2026-06-30T09:30:00Z",
                        "metadata": {
                            "workspace": "first-100",
                            "page_id": "page-helix",
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
        self.assertTrue(synced["records"][0]["source_url"].startswith(f"source-account://notion/{account['id']}/page-helix"))
        self.assertEqual(synced["cursor"]["cursor_name"], "pages")
        self.assertTrue(account["policy"]["review_required"])

        capture_id = synced["capture_ids"][0]
        self.assertEqual([item["id"] for item in self.store.inbox(self.user_id, limit=10)], [capture_id])
        inbox = self.store.inbox(self.user_id)
        self.assertEqual(len(inbox), 1)
        self.assertEqual(inbox[0]["source"], "notion")
        self.assertTrue(inbox[0]["preview_memories"])
        self.assertTrue(any("canonical project memory source" in item["content"] for item in inbox[0]["preview_memories"]))
        self.assertEqual(self.store.search(self.user_id, "canonical project memory source", limit=5), [])
        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))

        found = self.store.search(self.user_id, "canonical project memory source", limit=5)
        self.assertTrue(found)
        self.assertEqual(found[0]["source"], "notion")
        self.assertTrue(found[0]["source_url"].startswith(f"source-account://notion/{account['id']}/page-helix"))
        self.assertEqual(found[0]["sector"], "first-100")
        self.assertEqual(found[0]["provenance"]["record_metadata"]["page_id"], "page-helix")
        self.assertIn("project helix", [topic.casefold() for topic in found[0]["topics"]])
        self.assertIn("memory source", [topic.casefold() for topic in found[0]["topics"]])

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
        for leaked in ("tags:", "#todo", "[[", "]]", "dataview", "Template block", "TABLE file.mtime"):
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
                SELECT id, status, superseded_by
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
        self.assertEqual(stale_memory["superseded_by"], replacement_memory["id"])
        self.assertEqual(replacement_memory["status"], "active")
        self.assertEqual(capture["source_account_id"], account["id"])
        self.assertEqual(capture["external_id"], "msg-async-update")

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
        mcp_filtered = call_tool(self.store, self.user_id, "search_memory", {"query": "short direct sentences", "layer": "style"})
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
