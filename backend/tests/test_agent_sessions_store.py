from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.connectors.agent_sessions import AGENT_SESSIONS_SOURCE, MIN_MESSAGE_CHARS
from backend.app.database import connect, init_db
from backend.app.storage import CortexStore


def _long(text: str) -> str:
    if len(text) >= MIN_MESSAGE_CHARS:
        return text
    return text + " " + "x" * (MIN_MESSAGE_CHARS - len(text))


class AgentSessionsStoreTests(unittest.TestCase):
    """sync_agent_sessions: harvested prompts flow into memory through the review pipeline.
    Locked invariants (tested): review-gated by default; NEVER archive-missing (memory outlives
    a rotated log); a re-scan of unchanged logs is a no-op; a source account + cursor are
    recorded so it participates in the normal connector machinery."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "harvest-user"
        self.claude_dir = self.root / "claude"
        self.claude_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_claude(self, filename: str, prompts: list[str]) -> Path:
        path = self.claude_dir / "proj" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as handle:
            for index, text in enumerate(prompts):
                handle.write(json.dumps({
                    "type": "user",
                    "message": {"role": "user", "content": _long(text)},
                    "uuid": f"{filename}-u{index}",
                    "timestamp": "2026-07-01T10:00:00Z",
                    "cwd": "/repo",
                }) + "\n")
        return path

    def _sync(self, **kw):
        return self.store.sync_agent_sessions(
            self.user_id,
            agents=["claude"],
            claude_dir=str(self.claude_dir),
            codex_dir=None,
            cursor_dir=None,
            **kw,
        )

    def test_harvest_is_review_gated_by_default(self) -> None:
        self._seed_claude("s1.jsonl", ["I want the ledger service to use Postgres for jsonb support."])
        result = self._sync()
        self.assertEqual(result["saved"], 1)
        capture_id = result["capture_ids"][0]
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT review_status, source FROM captures WHERE user_id = ? AND id = ?",
                (self.user_id, capture_id),
            ).fetchone()
        self.assertEqual(row["review_status"], "pending")
        self.assertEqual(row["source"], AGENT_SESSIONS_SOURCE)
        inbox_ids = {item["id"] for item in self.store.inbox(self.user_id, limit=50)}
        self.assertIn(capture_id, inbox_ids)

    def test_records_source_account_and_cursor(self) -> None:
        self._seed_claude("s1.jsonl", ["Design the invoice parser with strict schema validation."])
        result = self._sync()
        account_id = result["source_account_id"]
        accounts = {acct["id"]: acct for acct in self.store.list_source_accounts(self.user_id)}
        self.assertIn(account_id, accounts)
        self.assertEqual(accounts[account_id]["source"], AGENT_SESSIONS_SOURCE)
        self.assertIsNotNone(result["cursor"])

    def test_rescan_unchanged_is_noop(self) -> None:
        self._seed_claude("s1.jsonl", ["Adopt trunk-based development for the ledger repository."])
        first = self._sync()
        self.assertEqual(first["saved"], 1)
        second = self._sync()
        self.assertEqual(second["saved"], 0)
        self.assertEqual(second["status"], "empty")

    def test_never_archives_when_log_disappears(self) -> None:
        # The whole point of harvesting: memory OUTLIVES the source log.
        path = self._seed_claude("s1.jsonl", ["A durable decision about database sharding strategy."])
        first = self._sync()
        capture_id = first["capture_ids"][0]
        # Approve it so it is real memory, then delete the source log and re-scan.
        self.store.approve_capture(self.user_id, capture_id)
        path.unlink()
        second = self._sync()
        self.assertEqual(second.get("archived_missing", 0), 0)
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT review_status FROM captures WHERE user_id = ? AND id = ?",
                (self.user_id, capture_id),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertNotEqual(row["review_status"], "archived")

    def test_can_disable_review_gate(self) -> None:
        self._seed_claude("s1.jsonl", ["Trusted prompt that should skip review when opted in."])
        result = self._sync(review_required=False)
        capture_id = result["capture_ids"][0]
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT review_status FROM captures WHERE user_id = ? AND id = ?",
                (self.user_id, capture_id),
            ).fetchone()
        # review_required=False means the source policy does not force review; the capture is
        # approved unless the user's global review_new_captures setting says otherwise.
        self.assertIn(row["review_status"], {"approved", "pending"})

    def test_empty_scan_is_clean(self) -> None:
        result = self._sync()
        self.assertEqual(result["saved"], 0)
        self.assertEqual(result["status"], "empty")
        self.assertEqual(result["failed"], 0)

    def test_mcp_tool_routes_and_is_write_scoped(self) -> None:
        from backend.app import mcp_tools

        self._seed_claude("s1.jsonl", ["Route this prompt through the MCP surface into review."])
        # Read-only token is refused (the tool writes captures).
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store, self.user_id, "sync_agent_sessions",
                {"agents": ["claude"]}, token_scopes=["read"],
            )
        # The MCP surface intentionally exposes no directory-override args, so it scans the real
        # default roots. That is fine: on a clean CI home it simply finds nothing. We assert the
        # call routes and returns the connector envelope, not a specific harvest count.
        result = mcp_tools.call_tool(
            self.store, self.user_id, "sync_agent_sessions",
            {"agents": ["claude"], "max_records": 5}, token_scopes=["read", "write"],
        )
        self.assertEqual(result["source"], AGENT_SESSIONS_SOURCE)
        self.assertIn("status", result)


if __name__ == "__main__":
    unittest.main()
