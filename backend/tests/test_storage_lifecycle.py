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


DUMMY_OPENAI_KEY = "sk-" + ("0" * 24)


class CortexStorageLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "test-user"

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
        self.assertEqual(self.store.inbox(self.user_id)[0]["id"], capture_id)
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

        events = self.store.audit_log(self.user_id, limit=20)
        event_pairs = {(event["object_type"], event["event_type"]) for event in events}
        self.assertIn(("source_account", "upserted"), event_pairs)
        self.assertIn(("sync_cursor", "updated"), event_pairs)

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
        self.assertLess(report["citation_coverage"], 1.0)
        self.assertTrue(report["layers_present"])
        self.assertTrue(any("citations" in warning for warning in report["warnings"]))
        self.assertTrue(any(source["source"] == "unit-test" for source in report["source_health"]))
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
            self.assertEqual(status["processing"]["embedding_status"], "queued")
            self.assertTrue(any(job["job_type"] == "embed_memory" for job in status["jobs"]))
            embedded = self.store.run_due_jobs(self.user_id, limit=10)
            self.assertGreaterEqual(embedded["processed"], 1)
            status_after_embedding = self.store.capture_status(self.user_id, capture_id)
            self.assertEqual(status_after_embedding["processing"]["embedding_status"], "available")
        else:
            self.assertEqual(status["processing"]["embedding_status"], "not_available")

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
                self.assertEqual(status["processing"]["embedding_status"], "queued")
                ran = self.store.run_due_jobs(self.user_id, limit=10)
                self.assertGreaterEqual(ran["processed"], 1)
                self.assertTrue(self.store.search(self.user_id, "Strict vector outage"))
                status_after_failure = self.store.capture_status(self.user_id, capture_id)
                self.assertIn(status_after_failure["processing"]["embedding_status"], {"queued", "failed"})
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
        backup = self.store.create_backup(self.user_id)
        self.assertTrue(Path(backup["backup_path"]).exists())
        self.assertTrue(self.store.search(self.user_id, "local Cortex user data"))
        self.assertTrue(list(self.store.vault.iter_events(self.user_id)))

        deleted = self.store.delete_user_data(self.user_id)

        self.assertTrue(deleted["include_backups"])
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
        self.assertTrue(any("Review 1 pending capture" in item for item in review["recommended_actions"]))
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

        recorded = self.store.record_context_reuse(self.user_id, surface="unit-test", query="daily loop", target="Claude")
        self.assertTrue(recorded["recorded"])
        done_loop = recorded["product_loop"]
        self.assertEqual(done_loop["primary_action"]["action"], "done")
        self.assertGreaterEqual(done_loop["counts"]["reused_today"], 1)
        self.assertTrue(any(step["key"] == "reuse" and step["status"] == "done" for step in done_loop["steps"]))

        mcp_loop = call_tool(self.store, self.user_id, "get_product_loop", {})
        self.assertEqual(mcp_loop["primary_action"]["action"], "done")

    def test_settings_control_pending_context_visibility(self) -> None:
        defaults = self.store.settings(self.user_id)
        self.assertTrue(defaults["review_new_captures"])
        self.assertTrue(defaults["allow_pending_in_context"])
        self.assertTrue(defaults["allow_agent_reads"])
        self.assertFalse(defaults["allow_agent_writes"])
        self.assertFalse(defaults["allow_agent_exports"])
        self.assertFalse(defaults["allow_agent_maintenance"])
        self.assertFalse(defaults["allow_agent_destructive_actions"])
        self.assertTrue(defaults["redact_sensitive_context"])

        result = self.capture(
            "Vamika decided strict mode should hide pending Cortex captures from assistants. "
            "Cortex needs approval before this memory appears in strict search."
        )
        capture_id = result["capture_id"]

        self.assertTrue(self.store.search(self.user_id, "strict mode pending"))

        updated = self.store.update_settings(self.user_id, {"allow_pending_in_context": False, "context_pack_limit": 6})
        self.assertFalse(updated["allow_pending_in_context"])
        self.assertEqual(updated["context_pack_limit"], 6)
        self.assertEqual(self.store.search(self.user_id, "strict mode pending"), [])
        self.assertNotIn("strict mode should hide", self.store.context_pack(self.user_id, query="strict mode"))

        self.assertTrue(self.store.approve_capture(self.user_id, capture_id))
        self.assertTrue(self.store.search(self.user_id, "strict mode pending"))
        self.assertIn("strict mode should hide", self.store.context_pack(self.user_id, query="strict mode"))

        self.store.update_settings(self.user_id, {"review_new_captures": False})
        auto = self.capture("Cortex should auto approve captures when review is turned off.")
        self.assertEqual(self.store.inbox(self.user_id), [])
        self.assertTrue(self.store.search(self.user_id, "auto approve captures"))
        self.assertTrue(self.store.archive_capture(self.user_id, auto["capture_id"]))

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

    def test_trust_controls_redact_shared_context_and_exports(self) -> None:
        self.capture(
            f"Cortex should never leak password=supersecret123 or {DUMMY_OPENAI_KEY} "
            "or vamika@example.com to agent context after testing."
        )

        pack = self.store.context_pack(self.user_id, query="leak", limit=5)
        self.assertIn("[REDACTED_SECRET]", pack)
        self.assertIn("[REDACTED_OPENAI_KEY]", pack)
        self.assertIn("[REDACTED_EMAIL]", pack)
        self.assertNotIn("supersecret123", pack)
        self.assertNotIn("vamika@example.com", pack)

        export = self.store.export_markdown(self.user_id)
        self.assertIn("[REDACTED_SECRET]", export)
        self.assertNotIn(DUMMY_OPENAI_KEY, export)

        self.store.update_settings(self.user_id, {"redact_sensitive_context": False})
        unredacted = self.store.context_pack(self.user_id, query="leak", limit=5)
        self.assertIn("supersecret123", unredacted)

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
        self.store.update_settings(self.user_id, {"allow_agent_maintenance": True})
        backup = call_tool(self.store, self.user_id, "create_memory_backup", {})
        self.assertIn("backup_path", backup)

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
        self.capture("Existing local SQLite users should get vault JSON files after upgrade.")

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


if __name__ == "__main__":
    unittest.main()
