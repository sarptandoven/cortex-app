from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app import mcp_tools
from backend.app.storage import CortexStore, connect


class _Phase6Fixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase6-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, content: str, *, user_authored: bool = True, import_id: str | None = None) -> dict:
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="note",
            source_url=f"cortex-capture://{memory_id}" if user_authored else None,
            title=None,
            import_id=import_id,
            extracted={
                "summary": content[:80],
                "records": [
                    {"id": memory_id, "kind": "decision", "layer": "decision", "content": content,
                     "confidence": "confirmed", "importance": 4,
                     "occurred_at": "2026-01-01T00:00:00Z", "topics": [], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )


class ContradictionInterruptTests(_Phase6Fixture):
    """Phase 6.1: write-path contradiction detection -> budget-gated alert -> warnings[]."""

    def test_conflicting_capture_raises_alert(self) -> None:
        self._seed("m-pg", "The storage backend is postgres for the hosted service")
        self._seed("m-lite", "The storage backend is sqlite going forward")
        alerts = self.store.list_proactive_alerts(self.user_id, status="pending")
        self.assertEqual(len(alerts), 1)
        alert = alerts[0]
        self.assertEqual(alert["kind"], "contradiction")
        self.assertEqual(alert["detail"]["field"], "storage backend")
        self.assertTrue(alert["detail"]["existing"]["memory_id"])
        self.assertTrue(alert["detail"]["new"]["memory_id"])

    def test_agreeing_capture_raises_nothing(self) -> None:
        self._seed("m-a", "The storage backend is postgres")
        self._seed("m-b", "Confirmed again that the storage backend is postgres")
        self.assertEqual(self.store.list_proactive_alerts(self.user_id, status="pending"), [])

    def test_low_trust_existing_memory_does_not_interrupt(self) -> None:
        # Existing side is agent-authored (trust 0.5 < 0.6 gate): stays in Review, no interrupt.
        self.store.save_capture(
            user_id=self.user_id,
            content="The storage backend is postgres",
            source="claude",
            source_url="cortex-session://asess_x",
            title=None,
            extracted={
                "summary": "agent claim",
                "records": [
                    {"id": "agent-m", "kind": "decision", "layer": "decision",
                     "content": "The storage backend is postgres", "confidence": "stated",
                     "importance": 3, "occurred_at": "2026-01-01T00:00:00Z", "topics": [], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )
        self._seed("m-user", "The storage backend is sqlite")
        self.assertEqual(self.store.list_proactive_alerts(self.user_id, status="pending"), [])

    def test_bulk_imports_never_alert(self) -> None:
        self._seed("m-pg2", "The storage backend is postgres")
        self._seed("m-import", "The storage backend is sqlite", import_id="imp_1")
        self.assertEqual(self.store.list_proactive_alerts(self.user_id, status="pending"), [])

    def test_same_conflict_pair_dedupes(self) -> None:
        self._seed("m-1", "The storage backend is postgres")
        self._seed("m-2", "The storage backend is sqlite")
        first = self.store.list_proactive_alerts(self.user_id, status=None)
        # Re-running detection over the same saved memories is a no-op.
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id = ? AND status = 'active'", (self.user_id,)
            ).fetchall()
        self.store._raise_write_contradiction_alerts(
            self.user_id, [self.store._memory_from_row(r) for r in rows if "sqlite" in r["content"]]
        )
        self.assertEqual(len(self.store.list_proactive_alerts(self.user_id, status=None)), len(first))

    def test_warnings_delivered_once_via_agent_payload(self) -> None:
        self._seed("m-x", "The storage backend is postgres")
        self._seed("m-y", "The storage backend is sqlite")
        first = self.store.agent_payload(self.user_id, {"ok": True})
        self.assertEqual(len(first["warnings"]), 1)
        self.assertTrue(first["warnings"][0]["alert_id"].startswith("alrt_"))
        # Exactly-once interrupt: the second response carries no warnings.
        second = self.store.agent_payload(self.user_id, {"ok": True})
        self.assertNotIn("warnings", second)
        # But the alert is still visible (delivered) until resolved.
        self.assertEqual(len(self.store.list_proactive_alerts(self.user_id, status="delivered")), 1)

    def test_existing_warnings_key_never_clobbered(self) -> None:
        self._seed("m-p", "The storage backend is postgres")
        self._seed("m-q", "The storage backend is sqlite")
        payload = self.store.agent_payload(self.user_id, {"warnings": ["original"]})
        self.assertEqual(payload["warnings"], ["original"])


class AnnoyanceBudgetTests(_Phase6Fixture):
    """Phase 6.3: the daily cap is a product-survival constraint, enforced and observable."""

    def test_budget_suppresses_and_logs(self) -> None:
        self.store.update_settings(self.user_id, {"proactive_alerts_daily_budget": 2})
        raised = [
            self.store.raise_proactive_alert(self.user_id, kind="test", title=f"alert {i}", dedupe_key=f"k{i}")
            for i in range(5)
        ]
        self.assertEqual(sum(1 for a in raised if a), 2)
        precision = self.store.get_alert_precision(self.user_id)
        self.assertEqual(precision["raised"], 2)
        self.assertEqual(precision["suppressed_by_budget"], 3)

    def test_budget_zero_disables_alerts(self) -> None:
        self.store.update_settings(self.user_id, {"proactive_alerts_daily_budget": 0})
        self.assertIsNone(self.store.raise_proactive_alert(self.user_id, kind="test", title="nope"))
        self.assertEqual(self.store.list_proactive_alerts(self.user_id, status=None), [])

    def test_dismissal_is_a_label(self) -> None:
        alert = self.store.raise_proactive_alert(self.user_id, kind="test", title="maybe useful")
        self.store.resolve_proactive_alert(self.user_id, alert["id"], "dismissed")
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_events WHERE user_id = ? AND object_type = 'proactive_alert' AND event_type = 'alert_dismissed'",
                (self.user_id,),
            ).fetchone()
        self.assertEqual(row["n"], 1)

    def test_invalid_resolution_rejected(self) -> None:
        alert = self.store.raise_proactive_alert(self.user_id, kind="test", title="x")
        with self.assertRaises(ValueError):
            self.store.resolve_proactive_alert(self.user_id, alert["id"], "maybe")
        with self.assertRaises(ValueError):
            self.store.resolve_proactive_alert(self.user_id, "alrt_nonexistent", "accepted")

    def test_precision_metric(self) -> None:
        self.store.update_settings(self.user_id, {"proactive_alerts_daily_budget": 10})
        ids = [
            self.store.raise_proactive_alert(self.user_id, kind="test", title=f"a{i}", dedupe_key=f"p{i}")["id"]
            for i in range(4)
        ]
        self.store.resolve_proactive_alert(self.user_id, ids[0], "accepted")
        self.store.resolve_proactive_alert(self.user_id, ids[1], "accepted")
        self.store.resolve_proactive_alert(self.user_id, ids[2], "dismissed")
        precision = self.store.get_alert_precision(self.user_id)
        self.assertEqual(precision["precision"], round(2 / 3, 4))
        self.assertEqual(precision["pending"], 1)


class PrefetchPredictorTests(_Phase6Fixture):
    """Phase 6.2: dumbest-model-first predictor with a measured hit-rate."""

    def _pin(self, task: str, session_id: str | None = None) -> str:
        pack = self.store.assemble_context(self.user_id, task, pin=True, session_id=session_id)
        return pack["pin"]["pack_sha"]

    def test_empty_corpus_predicts_nothing(self) -> None:
        self.assertIsNone(self.store.predict_next_pack(self.user_id))

    def test_most_recent_baseline(self) -> None:
        self._seed("m-ctx", "Decided to use postgres for the billing project")
        first = self._pin("billing database work")
        second = self._pin("frontend styling work")
        predicted = self.store.predict_next_pack(self.user_id)
        # Content-addressing: identical packs share a sha, so accept either recent sha.
        self.assertIn(predicted["pack_sha"], {first, second})
        self.assertEqual(predicted["predictor"], "most_recent")

    def test_host_scoped_prediction(self) -> None:
        self._seed("m-ctx2", "Decided to use postgres for the billing project")
        cursor_session = self.store.begin_agent_session(self.user_id, goal="billing", host_label="Cursor")
        claude_session = self.store.begin_agent_session(self.user_id, goal="frontend", host_label="Claude Desktop")
        cursor_sha = self._pin("billing database work", session_id=cursor_session["id"])
        self._pin("frontend styling work", session_id=claude_session["id"])
        predicted = self.store.predict_next_pack(self.user_id, host_label="Cursor")
        self.assertEqual(predicted["pack_sha"], cursor_sha)
        self.assertEqual(predicted["predictor"], "most_recent_host")

    def test_replayed_usage_simulation_hit_rate_beats_baseline(self) -> None:
        """The roadmap's Phase 6 objective: replayed usage where the predictor's hit-rate is
        measured and must beat the no-predictor baseline (0). The simulated user has a sticky
        working pattern (returns to the same task most of the time), which is exactly the
        regularity the most-recent baseline exploits."""
        self._seed("m-sim", "Decided to use postgres for the billing project")
        # Two recurring tasks; the user works in runs (sticky), switching occasionally.
        # Deterministic replay schedule: 12 requests, 3 switches.
        schedule = ["billing", "billing", "billing", "frontend", "frontend", "frontend",
                    "frontend", "billing", "billing", "billing", "billing", "billing"]
        task_text = {"billing": "billing database work", "frontend": "frontend styling work"}
        for task in schedule:
            predicted = self.store.predict_next_pack(self.user_id)
            requested_sha = self._pin(task_text[task])
            self.store.record_prefetch_outcome(
                self.user_id,
                predicted_sha=(predicted or {}).get("pack_sha", ""),
                requested_sha=requested_sha,
            )
        report = self.store.get_prefetch_hit_rate(self.user_id)
        self.assertEqual(report["trials"], len(schedule))
        # 12 trials: first has no history (miss), 3 switches (miss) -> 8 hits minimum.
        self.assertGreaterEqual(report["hit_rate"], 8 / 12)
        self.assertGreater(report["hit_rate"], 0.0)  # beats the no-predictor baseline


class Phase6ToolSurfaceTests(_Phase6Fixture):
    """Cross-cutting rules: registered specs, correct scopes, dispatch through call_tool."""

    def test_tools_registered_with_scopes(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        self.assertIn("get_proactive_alerts", names)
        self.assertIn("resolve_proactive_alert", names)
        self.assertEqual(mcp_tools.tool_required_capabilities("get_proactive_alerts"), ["read"])
        self.assertEqual(mcp_tools.tool_required_capabilities("resolve_proactive_alert"), ["write"])

    def test_call_tool_dispatch_roundtrip(self) -> None:
        self._seed("m-d1", "The storage backend is postgres")
        self._seed("m-d2", "The storage backend is sqlite")
        # The alert rides the next payload as a warning...
        listed = mcp_tools.call_tool(self.store, self.user_id, "get_proactive_alerts", {"status": "pending"})
        # ...which may already have consumed pending -> delivered via agent_payload; check both.
        alerts = listed["alerts"] or mcp_tools.call_tool(
            self.store, self.user_id, "get_proactive_alerts", {"status": "delivered"}
        )["alerts"]
        self.assertEqual(len(alerts), 1)
        resolved = mcp_tools.call_tool(
            self.store, self.user_id, "resolve_proactive_alert",
            {"alert_id": alerts[0]["id"], "resolution": "accepted"},
        )
        self.assertEqual(resolved["status"], "accepted")

    def test_settings_budget_roundtrip(self) -> None:
        merged = self.store.update_settings(self.user_id, {"proactive_alerts_daily_budget": 7})
        self.assertEqual(merged["proactive_alerts_daily_budget"], 7)
        self.assertEqual(self.store.settings(self.user_id)["proactive_alerts_daily_budget"], 7)
        # Clamped, never negative or unbounded.
        self.assertEqual(self.store.update_settings(self.user_id, {"proactive_alerts_daily_budget": -5})["proactive_alerts_daily_budget"], 0)
        self.assertEqual(self.store.update_settings(self.user_id, {"proactive_alerts_daily_budget": 999})["proactive_alerts_daily_budget"], 20)


if __name__ == "__main__":
    unittest.main()
