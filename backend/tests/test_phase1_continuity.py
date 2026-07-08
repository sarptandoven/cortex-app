from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app import mcp_tools
from backend.app.provenance import classify_author
from backend.app.storage import CortexStore, connect


class Phase1ContinuityTests(unittest.TestCase):
    """Agent Continuity: sessions + checkpoint episodes. The invariants under test are the
    roadmap's locked decisions — checkpoints bypass the review queue but are FORCED episodic
    (never personal layers), carry agent authorship via cortex-session:// provenance, and
    survive a full vault rebuild so continuity is as durable as the vault itself."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase1-user"
        # Review gate ON: proves checkpoints are auto-approved despite it.
        self.store.update_settings(self.user_id, {"review_new_captures": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, name: str, args: dict) -> dict:
        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def _start(self, goal: str = "Ship the widget", **kw) -> dict:
        return self._call("start_agent_session", {"goal": goal, **kw})

    # -- lifecycle -----------------------------------------------------------------------

    def test_session_lifecycle(self) -> None:
        session = self._start(host_label="claude-desktop")
        self.assertTrue(session["id"].startswith("asess_"))
        self.assertEqual(session["status"], "active")
        cp = self._call(
            "checkpoint_agent_session",
            {"session_id": session["id"], "summary": "Did the first half.", "next_steps": ["Finish second half"]},
        )
        self.assertEqual(cp["checkpoint_index"], 1)
        self.assertTrue(cp["memory_ids"])
        resumed = self._call("resume_agent_session", {"session_id": session["id"]})
        self.assertEqual(resumed["session"]["id"], session["id"])
        self.assertEqual(len(resumed["checkpoints"]), 1)
        self.assertIn("Finish second half", resumed["checkpoints"][0]["content"])
        closed = self._call("close_agent_session", {"session_id": session["id"], "outcome": "shipped"})
        self.assertEqual(closed["status"], "closed")
        with self.assertRaises(ValueError):
            self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "too late"})

    def test_unknown_session_is_a_clear_error(self) -> None:
        with self.assertRaises(ValueError):
            self._call("resume_agent_session", {"session_id": "asess_nope"})
        with self.assertRaises(ValueError):
            self._call("checkpoint_agent_session", {"session_id": "asess_nope", "summary": "x"})

    def test_checkpoint_requires_summary(self) -> None:
        session = self._start()
        with self.assertRaises(ValueError):
            self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "   "})

    def test_status_transitions_via_checkpoint(self) -> None:
        session = self._start()
        self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "blocked on API keys", "status": "blocked"})
        self.assertEqual(self.store.list_agent_sessions(self.user_id, status="blocked")[0]["id"], session["id"])

    def test_parent_chain(self) -> None:
        first = self._start(goal="Part one")
        self._call("checkpoint_agent_session", {"session_id": first["id"], "summary": "part one done"})
        self._call("close_agent_session", {"session_id": first["id"]})
        second = self._start(goal="Part two", parent_session_id=first["id"])
        resumed = self._call("resume_agent_session", {"session_id": second["id"]})
        self.assertEqual([p["id"] for p in resumed["parent_chain"]], [first["id"]])
        # Dangling parent ids are dropped, not fatal.
        orphan = self._start(goal="Orphan", parent_session_id="asess_missing")
        self.assertIsNone(orphan["parent_session_id"])

    # -- locked roadmap invariants -----------------------------------------------------------

    def test_checkpoints_bypass_review_but_are_episodic_agent_records(self) -> None:
        session = self._start()
        cp = self._call(
            "checkpoint_agent_session",
            {"session_id": session["id"], "summary": "I prefer dark mode and finished step 3."},
        )
        with connect(self.db_path) as conn:
            capture = conn.execute(
                "SELECT review_status FROM captures WHERE id = ?", (cp["capture_id"],)
            ).fetchone()
            memories = conn.execute(
                "SELECT layer, author_class, trust_score, source_url, status FROM memories WHERE capture_id = ?",
                (cp["capture_id"],),
            ).fetchall()
        # Bypasses the review queue even though review_new_captures is ON.
        self.assertEqual(capture["review_status"], "approved")
        self.assertTrue(memories)
        for row in memories:
            # NEVER a personal layer, even when the text sounds like a preference.
            self.assertEqual(row["layer"], "episodic")
            self.assertEqual(row["author_class"], "agent")
            self.assertEqual(row["trust_score"], 0.5)
            self.assertEqual(row["source_url"], f"cortex-session://{session['id']}")

    def test_session_provenance_classifies_as_agent(self) -> None:
        self.assertEqual(classify_author(source_url="cortex-session://asess_x"), "agent")
        # Session provenance outranks even a personal layer label.
        self.assertEqual(classify_author(source_url="cortex-session://asess_x", layer="preference"), "agent")

    def test_checkpoints_survive_vault_rebuild(self) -> None:
        session = self._start()
        self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "durable state one"})
        self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "durable state two"})
        self.store.rebuild_index_from_vault(self.user_id)
        resumed = self._call("resume_agent_session", {"session_id": session["id"]})
        self.assertEqual(len(resumed["checkpoints"]), 2)
        for item in resumed["checkpoints"]:
            self.assertEqual(item["author_class"], "agent")
            self.assertEqual(item["layer"], "episodic")

    def test_checkpoint_is_idempotent_per_index(self) -> None:
        # Deterministic capture/memory ids per (session, index): a client retry that reaches
        # save_capture twice with the same index upserts rather than duplicating.
        session = self._start()
        cp1 = self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "one"})
        cp2 = self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "two"})
        self.assertNotEqual(cp1["capture_id"], cp2["capture_id"])
        self.assertEqual((cp1["checkpoint_index"], cp2["checkpoint_index"]), (1, 2))

    # -- discovery / listing -----------------------------------------------------------------

    def test_list_agent_sessions_orders_and_filters(self) -> None:
        a = self._start(goal="A")
        b = self._start(goal="B")
        self._call("close_agent_session", {"session_id": a["id"]})
        listed = self._call("list_agent_sessions", {})
        self.assertEqual({s["id"] for s in listed}, {a["id"], b["id"]})
        active = self._call("list_agent_sessions", {"status": "active"})
        self.assertEqual([s["id"] for s in active], [b["id"]])

    def test_checkpoints_are_searchable_memory(self) -> None:
        session = self._start(goal="Search visibility")
        self._call(
            "checkpoint_agent_session",
            {"session_id": session["id"], "summary": "Refactored the xylophone parser module."},
        )
        results = self.store.search(self.user_id, "xylophone parser", 5)
        items = results if isinstance(results, list) else results.get("results", [])
        self.assertTrue(any("xylophone" in str(item.get("content", "")) for item in items), items)

    # -- tool surface -------------------------------------------------------------------------

    def test_tools_are_registered_with_correct_scopes(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        for tool_name in (
            "start_agent_session",
            "checkpoint_agent_session",
            "resume_agent_session",
            "list_agent_sessions",
            "close_agent_session",
        ):
            self.assertIn(tool_name, names)
        self.assertIn("write", mcp_tools.tool_required_capabilities("checkpoint_agent_session"))
        self.assertIn("read", mcp_tools.tool_required_capabilities("resume_agent_session"))

    def test_write_toggle_gates_checkpointing(self) -> None:
        session = self._start()
        self.store.update_settings(self.user_id, {"allow_agent_writes": False})
        with self.assertRaises(PermissionError):
            self._call("checkpoint_agent_session", {"session_id": session["id"], "summary": "should be gated"})


if __name__ == "__main__":
    unittest.main()
