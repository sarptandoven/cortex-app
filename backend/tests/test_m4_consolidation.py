from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from backend.app import mcp_tools
from backend.app.database import connect, init_db
from backend.app.storage import CortexStore


class MemoryConsolidationTests(unittest.TestCase):
    USER = "m4-consolidation-user"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.store.update_settings(
            self.USER,
            {
                "review_new_captures": False,
                "allow_pending_in_context": True,
                "allow_agent_maintenance": True,
            },
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, content: str, occurred_at: str) -> None:
        self.store.save_capture(
            user_id=self.USER,
            content=content,
            source="obsidian",
            source_url=f"local-file://m4/{memory_id}.md",
            title=memory_id,
            extracted={
                "_timestamp": occurred_at,
                "summary": content,
                "records": [
                    {
                        "id": memory_id,
                        "kind": "decision",
                        "layer": "decision",
                        "content": content,
                        "summary": content,
                        "confidence": "confirmed",
                        "importance": 4,
                        "occurred_at": occurred_at,
                        "topics": ["database"],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _seed_conflict(self) -> None:
        self._seed("mem_sqlite", "The database is sqlite for the launch.", "2026-01-01T00:00:00Z")
        self._seed("mem_postgres", "The database is postgres for the launch.", "2026-06-01T00:00:00Z")

    @staticmethod
    def _request() -> dict:
        return {
            "task": "What database should the launch use?",
            "surface": "agent",
            "token_budget": 2000,
            "intent": "answer",
        }

    def test_consolidation_resolves_safe_conflict_and_serves_verified_hot_pack(self) -> None:
        self._seed_conflict()

        result = self.store.run_memory_consolidation(
            self.USER,
            hot_requests=[self._request()],
        )

        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["conflicts_detected"], 1)
        self.assertEqual(result["conflicts_auto_resolved"], 1)
        self.assertEqual(result["conflicts_review_required"], 0)
        self.assertEqual(result["metrics"]["contradictions_after"], 0)
        self.assertEqual(result["metrics"]["cache_verification_rate"], 1.0)
        self.assertEqual(len(result["packs_warmed"]), 1)

        cached = self.store.assemble_context(self.USER, self._request()["task"])
        self.assertIsInstance(cached, dict)
        self.assertTrue(cached["cache"]["hit"])
        self.assertTrue(cached["cache"]["verified"])
        payload = str(cached)
        self.assertIn("postgres", payload)
        self.assertNotIn("sqlite for the launch", payload)

    def test_direct_sql_memory_change_invalidates_hot_pack(self) -> None:
        self._seed_conflict()
        self.store.run_memory_consolidation(self.USER, hot_requests=[self._request()])
        cached = self.store.assemble_context(self.USER, self._request()["task"])
        self.assertTrue(cached["cache"]["hit"])

        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET content = ?, summary = ? WHERE user_id = ? AND id = ?",
                (
                    "The database is mysql for the launch.",
                    "The database is mysql for the launch.",
                    self.USER,
                    "mem_postgres",
                ),
            )

        recomputed = self.store.assemble_context(self.USER, self._request()["task"])
        self.assertNotIn("cache", recomputed)
        status = self.store.hot_context_cache_status(self.USER)
        self.assertEqual(status["entries"][0]["status"], "stale")

    def test_tampered_pack_is_never_served(self) -> None:
        self._seed_conflict()
        result = self.store.run_memory_consolidation(self.USER, hot_requests=[self._request()])
        pack_sha = result["packs_warmed"][0]["pack_sha"]
        self.store.vault.context_pack_path(pack_sha).write_bytes(b"{}")

        recomputed = self.store.assemble_context(self.USER, self._request()["task"])
        self.assertNotIn("cache", recomputed)
        status = self.store.hot_context_cache_status(self.USER)
        self.assertEqual(status["entries"][0]["status"], "invalid")

    def test_agent_over_user_conflict_is_left_for_review(self) -> None:
        self._seed_conflict()
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET author_class = 'user', trust_score = 1.0 WHERE user_id = ? AND id = 'mem_sqlite'",
                (self.USER,),
            )
            conn.execute(
                "UPDATE memories SET author_class = 'agent', trust_score = 1.0 WHERE user_id = ? AND id = 'mem_postgres'",
                (self.USER,),
            )

        result = self.store.run_memory_consolidation(
            self.USER,
            hot_requests=[],
            max_hot_packs=0,
        )
        self.assertEqual(result["conflicts_auto_resolved"], 0)
        self.assertEqual(result["conflicts_review_required"], 1)
        self.assertEqual(result["decisions"][0]["decision"], "review_required")
        self.assertEqual(len(self.store.detect_conflicts(self.USER)), 1)

    def test_connector_authority_margin_cannot_supersede_user_memory(self) -> None:
        self._seed_conflict()
        with connect(self.db_path) as conn:
            conn.execute(
                """
                UPDATE memories
                SET author_class = 'user', author_principal_id = 'person:owner', trust_score = 0.6
                WHERE user_id = ? AND id = 'mem_sqlite'
                """,
                (self.USER,),
            )
            conn.execute(
                """
                UPDATE memories
                SET author_class = 'connector', author_principal_id = 'connector:canonical', trust_score = 1.0
                WHERE user_id = ? AND id = 'mem_postgres'
                """,
                (self.USER,),
            )

        safe, proof = self.store._consolidation_resolution_is_safe(
            self.USER,
            {
                "field": "database",
                "reason": "authority",
                "current": {"memory_id": "mem_postgres"},
                "stale": {"memory_id": "mem_sqlite"},
            },
        )
        self.assertFalse(safe)
        self.assertTrue(proof["same_claim_scope"])
        result = self.store.run_memory_consolidation(
            self.USER,
            hot_requests=[],
            max_hot_packs=0,
        )
        self.assertEqual(result["conflicts_auto_resolved"], 0)
        self.assertEqual(result["conflicts_review_required"], 1)
        self.assertEqual(len(self.store.detect_conflicts(self.USER)), 1)

    def test_authoritative_mutations_and_decisions_roll_back_together(self) -> None:
        self._seed_conflict()
        self._seed("mem_owner_dana", "The owner is Dana for the API.", "2026-01-01T00:00:00Z")
        self._seed("mem_owner_lee", "The owner is Lee for the API.", "2026-06-01T00:00:00Z")
        original = self.store._resolve_conflict_in_conn
        calls = 0

        def fail_second(conn, user_id, *, stale_id, current_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("injected second-resolution failure")
            return original(conn, user_id, stale_id=stale_id, current_id=current_id)

        with mock.patch.object(self.store, "_resolve_conflict_in_conn", side_effect=fail_second):
            with self.assertRaisesRegex(RuntimeError, "injected second-resolution failure"):
                self.store.run_memory_consolidation(
                    self.USER,
                    hot_requests=[],
                    max_hot_packs=0,
                )

        with connect(self.db_path) as conn:
            superseded = conn.execute(
                "SELECT COUNT(*) AS count FROM memories WHERE user_id = ? AND superseded_by IS NOT NULL",
                (self.USER,),
            ).fetchone()
            decisions = conn.execute(
                "SELECT COUNT(*) AS count FROM memory_consolidation_decisions WHERE user_id = ?",
                (self.USER,),
            ).fetchone()
            run = conn.execute(
                "SELECT status FROM memory_consolidation_runs WHERE user_id = ?",
                (self.USER,),
            ).fetchone()
        self.assertEqual(int(superseded["count"]), 0)
        self.assertEqual(int(decisions["count"]), 0)
        self.assertEqual(run["status"], "failed")

    def test_post_commit_cache_failure_is_reported_without_hiding_mutations(self) -> None:
        self._seed_conflict()
        with mock.patch.object(
            self.store,
            "_store_hot_context_pack",
            side_effect=RuntimeError("injected cache failure"),
        ):
            result = self.store.run_memory_consolidation(
                self.USER,
                hot_requests=[self._request()],
            )

        self.assertEqual(result["status"], "succeeded_with_warnings")
        self.assertEqual(result["conflicts_auto_resolved"], 1)
        self.assertEqual(len(result["metrics"]["cache_warnings"]), 1)
        self.assertEqual(self.store.detect_conflicts(self.USER), [])
        status = self.store.get_memory_consolidation(self.USER)["runs"][0]
        self.assertEqual(status["status"], "succeeded_with_warnings")
        self.assertEqual(status["decisions"][0]["decision"], "auto_resolved")

    def test_same_field_different_subjects_are_never_auto_resolved(self) -> None:
        self._seed_conflict()
        self._seed(
            "mem_analytics_mysql",
            "The database is now mysql for the analytics migration.",
            "2026-07-01T00:00:00Z",
        )

        result = self.store.run_memory_consolidation(
            self.USER,
            hot_requests=[],
            max_hot_packs=0,
        )

        self.assertEqual(result["conflicts_auto_resolved"], 1)
        cross_scope = [
            decision
            for decision in result["decisions"]
            if decision["proof"].get("same_claim_scope") is False
        ]
        self.assertTrue(cross_scope)
        self.assertTrue(all(decision["decision"] == "review_required" for decision in cross_scope))
        active = {item["id"] for item in self.store.search(self.USER, "database", limit=10)}
        self.assertIn("mem_postgres", active)
        self.assertIn("mem_analytics_mysql", active)

    def test_cache_is_tenant_scoped(self) -> None:
        self._seed_conflict()
        self.store.run_memory_consolidation(self.USER, hot_requests=[self._request()])
        other = "other-user"
        self.store.update_settings(other, {"review_new_captures": False})

        result = self.store.assemble_context(other, self._request()["task"])
        self.assertNotIn("cache", result)
        self.assertEqual(self.store.hot_context_cache_status(other)["entries"], [])

    def test_job_queue_coalesces_and_executes_sleep_pass(self) -> None:
        self._seed_conflict()
        first = self.store.enqueue_memory_consolidation(
            self.USER,
            run_at="2000-01-01T00:00:00Z",
            hot_requests=[self._request()],
        )
        second = self.store.enqueue_memory_consolidation(
            self.USER,
            run_at="2000-01-01T00:00:00Z",
            hot_requests=[self._request()],
        )
        self.assertEqual(first["id"], second["id"])

        ran = self.store.run_due_jobs(self.USER, limit=1, schedule_source_syncs=False)
        self.assertEqual(ran["processed"], 1)
        self.assertEqual(ran["jobs"][0]["job_type"], "sleep_consolidation")
        self.assertEqual(ran["jobs"][0]["status"], "succeeded")

    def test_job_coalescing_merges_requests_and_fails_closed(self) -> None:
        first = self.store.enqueue_memory_consolidation(
            self.USER,
            run_at="2000-01-01T12:00:00Z",
            hot_requests=[self._request()],
            auto_resolve_safe=True,
        )
        second_request = {**self._request(), "task": "Who owns the API?"}
        second = self.store.enqueue_memory_consolidation(
            self.USER,
            run_at="2000-01-01T00:00:00Z",
            hot_requests=[second_request],
            auto_resolve_safe=False,
        )

        self.assertEqual(first["id"], second["id"])
        self.assertEqual(second["run_at"], "2000-01-01T00:00:00Z")
        self.assertFalse(second["payload"]["auto_resolve_safe"])
        self.assertEqual(second["payload"]["hot_requests"], [self._request(), second_request])

    def test_mcp_scope_and_status_parity(self) -> None:
        self._seed_conflict()
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store,
                self.USER,
                "consolidate_memory",
                {"hot_requests": [self._request()]},
                token_scopes=["read"],
            )

        result = mcp_tools.call_tool(
            self.store,
            self.USER,
            "consolidate_memory",
            {"hot_requests": [self._request()]},
            token_scopes=["maintenance"],
        )
        self.assertEqual(result["status"], "succeeded")
        status = mcp_tools.call_tool(
            self.store,
            self.USER,
            "get_memory_consolidation",
            {},
            token_scopes=["read"],
        )
        self.assertTrue(status["runs"])
        self.assertEqual(status["runs"][0]["run_id"], result["run_id"])
        self.assertEqual(status["runs"][0]["decisions"][0]["decision"], "auto_resolved")
        self.assertTrue(status["runs"][0]["decisions"][0]["proof"]["same_claim_scope"])


if __name__ == "__main__":
    unittest.main()
