from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import init_db
from backend.app.storage import CortexStore, connect


class WorkingCanvasTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.user_id = "canvas-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, name: str, args: dict):
        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def test_record_node_offloads_raw_evidence_and_returns_compact_canvas(self) -> None:
        raw = "tool stdout line 1\n" + "important evidence " * 200
        node = self.store.record_working_canvas_node(
            self.user_id,
            session_id="sess-1",
            node_id="001-N3",
            label="Search repo",
            summary="Found the relevant storage path",
            raw_text=raw,
        )
        self.assertTrue(node["verified"])
        self.assertNotIn("raw_text", node)
        self.assertEqual(node["raw_sha256"], hashlib.sha256(raw.encode()).hexdigest())
        self.assertTrue(self.store.vault.working_canvas_evidence_path(node["raw_sha256"]).exists())

        canvas = self.store.get_working_canvas(self.user_id, session_id="sess-1")
        self.assertEqual(canvas["node_count"], 1)
        self.assertIn('001_N3["Search repo"]', canvas["canvas"])
        self.assertEqual(canvas["nodes"][0]["receipt_event_id"], node["receipt_event_id"])
        self.assertNotIn(raw, canvas["canvas"])

    def test_drill_down_recovers_raw_evidence_and_verifies_hash(self) -> None:
        raw = "full browser trace with exact DOM evidence"
        self.store.record_working_canvas_node(
            self.user_id,
            session_id="sess-2",
            node_id="browser-result",
            raw_text=raw,
        )
        node = self.store.get_working_canvas_node(self.user_id, session_id="sess-2", node_id="browser-result")
        self.assertTrue(node["verified"])
        self.assertEqual(node["raw_text"], raw)

    def test_predecessor_edges_render_mermaid_chain(self) -> None:
        self.store.record_working_canvas_node(self.user_id, session_id="sess-3", node_id="A", raw_text="first")
        self.store.record_working_canvas_node(
            self.user_id,
            session_id="sess-3",
            node_id="B",
            predecessor_node_id="A",
            raw_text="second",
        )
        canvas = self.store.get_working_canvas(self.user_id, session_id="sess-3")
        self.assertIn("A --> B", canvas["canvas"])

    def test_event_receipt_is_in_integrity_chain(self) -> None:
        before = self.store.integrity_digest(self.user_id)["event_count"]
        node = self.store.record_working_canvas_node(self.user_id, session_id="sess-4", node_id="N", raw_text="receipt me")
        digest = self.store.integrity_digest(self.user_id)
        self.assertEqual(digest["event_count"], before + 1)
        with connect(self.db_path) as conn:
            event = conn.execute(
                "SELECT * FROM memory_events WHERE user_id = ? AND id = ?",
                (self.user_id, node["receipt_event_id"]),
            ).fetchone()
        self.assertIsNotNone(event)
        self.assertEqual(event["object_type"], "working_canvas_node")
        self.assertTrue(self.store.verify_integrity(self.user_id, digest["chain_head"])["matches"])

    def test_tampered_raw_evidence_is_rejected(self) -> None:
        node = self.store.record_working_canvas_node(self.user_id, session_id="sess-5", node_id="N", raw_text="original")
        self.store.vault.working_canvas_evidence_path(node["raw_sha256"]).write_text("tampered")
        with self.assertRaises(ValueError):
            self.store.get_working_canvas_node(self.user_id, session_id="sess-5", node_id="N")

    def test_mcp_scope_and_metadata(self) -> None:
        self.assertIn("write", mcp_tools.tool_required_capabilities("record_working_canvas_node"))
        self.assertEqual(mcp_tools.tool_required_capabilities("get_working_canvas"), ["read"])
        tools = {tool["name"]: tool for tool in mcp_tools.TOOLS}
        self.assertFalse(tools["record_working_canvas_node"]["annotations"]["readOnlyHint"])
        self.assertTrue(tools["get_working_canvas"]["annotations"]["readOnlyHint"])

    def test_mcp_round_trip(self) -> None:
        recorded = self._call(
            "record_working_canvas_node",
            {"session_id": "sess-6", "node_id": "tool-1", "label": "Tool 1", "raw_text": "raw mcp payload"},
        )
        self.assertEqual(recorded["node_id"], "tool_1")
        canvas = self._call("get_working_canvas", {"session_id": "sess-6"})
        self.assertEqual(canvas["node_count"], 1)
        node = self._call("get_working_canvas_node", {"session_id": "sess-6", "node_id": "tool-1"})
        self.assertEqual(node["raw_text"], "raw mcp payload")

    def test_delete_user_data_removes_canvas_rows_and_unshared_evidence(self) -> None:
        # "Delete my data" must take canvas rows AND their raw evidence blobs with it,
        # but never a blob another tenant still references (content-addressed = shared).
        mine = self.store.record_working_canvas_node(
            self.user_id, session_id="sess-7", node_id="mine", raw_text="private evidence only mine"
        )
        shared_raw = "evidence two tenants both recorded"
        shared = self.store.record_working_canvas_node(
            self.user_id, session_id="sess-7", node_id="shared", raw_text=shared_raw
        )
        self.store.record_working_canvas_node(
            "other-tenant", session_id="their-sess", node_id="theirs", raw_text=shared_raw
        )

        deleted = self.store.delete_user_data(self.user_id, include_backups=False)
        self.assertEqual(deleted["sqlite"]["working_canvas_nodes"], 2)
        with connect(self.db_path) as conn:
            remaining = conn.execute(
                "SELECT user_id FROM working_canvas_nodes"
            ).fetchall()
        self.assertEqual([row["user_id"] for row in remaining], ["other-tenant"])
        self.assertFalse(self.store.vault.working_canvas_evidence_path(mine["raw_sha256"]).exists())
        # The other tenant's identical blob survives and still verifies.
        other = self.store.get_working_canvas_node("other-tenant", session_id="their-sess", node_id="theirs")
        self.assertTrue(other["verified"])
        self.assertEqual(other["raw_sha256"], shared["raw_sha256"])


if __name__ == "__main__":
    unittest.main()
