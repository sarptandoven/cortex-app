from __future__ import annotations

import tempfile
import unittest
import shutil
import sqlite3
import json
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

        with sqlite3.connect(self.db_path) as conn:
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

    def test_daily_review_and_context_pack(self) -> None:
        self.capture(
            "Vamika decided Cortex should become a daily memory cockpit for ChatGPT and Claude. "
            "Cortex needs a copy-ready context pack before every AI session. "
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
        self.assertIn("# Cortex Context Pack", review["context_pack"])
        self.assertIn("Suggested Assistant Instruction", review["context_pack"])

        focused_pack = self.store.context_pack(self.user_id, query="ChatGPT Claude", limit=5)
        self.assertIn("Focus: ChatGPT Claude", focused_pack)
        self.assertIn("memory cockpit", focused_pack)
        self.assertIn("## Open Loops", focused_pack)
        self.assertIn("test retention workflows", focused_pack)

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
        self.assertTrue(defaults["allow_agent_writes"])
        self.assertTrue(defaults["allow_agent_exports"])
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

    def test_trust_controls_redact_shared_context_and_exports(self) -> None:
        self.capture(
            f"Cortex should never leak password=supersecret123 or {DUMMY_OPENAI_KEY} "
            "to agent context. Email me at vamika@example.com after testing."
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
