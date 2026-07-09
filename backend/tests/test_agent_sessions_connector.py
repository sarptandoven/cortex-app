from __future__ import annotations

import json
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path

from backend.app.connectors.agent_sessions import (
    AGENT_SESSIONS_SOURCE,
    MIN_MESSAGE_CHARS,
    scan_agent_sessions,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")


def _long(text: str) -> str:
    """Pad text past the min-length gate while keeping it readable."""
    if len(text) >= MIN_MESSAGE_CHARS:
        return text
    return text + " " + "x" * (MIN_MESSAGE_CHARS - len(text))


class AgentSessionScannerTests(unittest.TestCase):
    """Session harvesting: the user's OWN messages from local Claude/Codex/Cursor logs flow into
    the review pipeline. Locked invariants (each tested): user words only (agent replies, meta,
    sidechain, subagent traffic, and short steering never harvested); read-only scanning; stable
    external ids so re-scan is a dedupe no-op; incremental by file-mtime high-water mark; one bad
    file is an error entry, not a crash."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.claude_dir = self.root / "claude"
        self.codex_dir = self.root / "codex"
        self.cursor_dir = self.root / "cursor"
        for directory in (self.claude_dir, self.codex_dir, self.cursor_dir):
            directory.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _scan(self, **kw):
        return scan_agent_sessions(
            claude_dir=str(self.claude_dir),
            codex_dir=str(self.codex_dir),
            cursor_dir=str(self.cursor_dir),
            **kw,
        )

    # -- Claude ----------------------------------------------------------------------------

    def test_claude_harvests_user_only(self) -> None:
        _write_jsonl(
            self.claude_dir / "proj" / "s1.jsonl",
            [
                {"type": "user", "message": {"role": "user", "content": _long("I want the ledger to use Postgres for jsonb.")},
                 "uuid": "u1", "timestamp": "2026-07-01T10:00:00Z", "cwd": "/repo", "gitBranch": "main"},
                {"type": "assistant", "message": {"role": "assistant", "content": _long("Here is a very long assistant reply that must never be harvested.")}, "uuid": "a1"},
                {"type": "user", "isMeta": True, "message": {"role": "user", "content": _long("meta plumbing line that is plenty long")}, "uuid": "u2"},
                {"type": "user", "isSidechain": True, "message": {"role": "user", "content": _long("sidechain subagent traffic that is plenty long")}, "uuid": "u3"},
                {"type": "user", "message": {"role": "user", "content": "ok"}, "uuid": "u4"},
                {"type": "user", "message": {"role": "user", "content": "<command-name>/login</command-name> wrapper plumbing that is long enough"}, "uuid": "u5"},
            ],
        )
        scan = self._scan()
        self.assertEqual(len(scan.records), 1)
        record = scan.records[0]
        self.assertIn("Postgres", record.content)
        self.assertEqual(record.metadata["agent"], "claude")
        self.assertEqual(record.metadata["cwd"], "/repo")
        self.assertEqual(record.metadata["git_branch"], "main")
        self.assertEqual(record.captured_at, "2026-07-01T10:00:00Z")

    def test_claude_content_blocks_joined(self) -> None:
        _write_jsonl(
            self.claude_dir / "proj" / "s2.jsonl",
            [
                {"type": "user", "message": {"role": "user", "content": [
                    {"type": "text", "text": "First I need to migrate the schema."},
                    {"type": "tool_result", "content": "noise"},
                    {"type": "text", "text": "Then wire the audit trail in Postgres jsonb."},
                ]}, "uuid": "u1", "timestamp": "2026-07-01T11:00:00Z"},
            ],
        )
        scan = self._scan()
        self.assertEqual(len(scan.records), 1)
        self.assertIn("migrate the schema", scan.records[0].content)
        self.assertIn("audit trail", scan.records[0].content)
        self.assertNotIn("noise", scan.records[0].content)

    # -- Codex -----------------------------------------------------------------------------

    def test_codex_harvests_user_message_events(self) -> None:
        _write_jsonl(
            self.codex_dir / "2026" / "07" / "01" / "rollout-a.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-07-01T09:00:00Z",
                 "payload": {"id": "sess-a", "cwd": "/proj", "source": {"vscode": {}}}},
                {"type": "event_msg", "timestamp": "2026-07-01T09:01:00Z",
                 "payload": {"type": "user_message", "message": _long("Build the invoice parser with strict validation.")}},
                {"type": "event_msg", "timestamp": "2026-07-01T09:02:00Z",
                 "payload": {"type": "agent_message", "message": _long("Assistant output that must never be harvested here.")}},
                {"type": "response_item", "payload": {"type": "message", "role": "assistant",
                 "content": [{"type": "output_text", "text": _long("also assistant output to ignore")}]}},
            ],
        )
        scan = self._scan()
        self.assertEqual(len(scan.records), 1)
        record = scan.records[0]
        self.assertIn("invoice parser", record.content)
        self.assertEqual(record.metadata["agent"], "codex")
        self.assertEqual(record.metadata["session_id"], "sess-a")
        self.assertEqual(record.metadata["cwd"], "/proj")

    def test_codex_subagent_rollups_excluded(self) -> None:
        _write_jsonl(
            self.codex_dir / "2026" / "07" / "01" / "rollout-guardian.jsonl",
            [
                {"type": "session_meta", "timestamp": "2026-07-01T09:00:00Z",
                 "payload": {"id": "guard-1", "cwd": "/proj", "source": {"subagent": {"other": "guardian"}}}},
                {"type": "event_msg", "timestamp": "2026-07-01T09:01:00Z",
                 "payload": {"type": "user_message", "message": _long("The following is the agent history you are assessing.")}},
            ],
        )
        scan = self._scan()
        self.assertEqual(scan.records, [])

    # -- Cursor ----------------------------------------------------------------------------

    def _seed_cursor(self, workspace: str, prompts: list[dict]) -> Path:
        ws_dir = self.cursor_dir / workspace
        ws_dir.mkdir()
        db_path = ws_dir / "state.vscdb"
        conn = sqlite3.connect(db_path)
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value BLOB)")
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            ("aiService.prompts", json.dumps(prompts)),
        )
        conn.commit()
        conn.close()
        return db_path

    def test_cursor_harvests_prompts(self) -> None:
        self._seed_cursor(
            "abc123workspace",
            [
                {"text": _long("Make the headshot generator production ready with retries.")},
                {"text": "yes"},  # too short
                {"text": _long("Add webhook signature verification for Smartsheet events.")},
            ],
        )
        scan = self._scan()
        self.assertEqual(len(scan.records), 2)
        self.assertTrue(all(record.metadata["agent"] == "cursor" for record in scan.records))
        self.assertTrue(all(record.metadata["session_id"] == "abc123workspace" for record in scan.records))

    def test_cursor_opened_read_only(self) -> None:
        db_path = self._seed_cursor("ws-ro", [{"text": _long("A genuinely long enough prompt about database indexing.")}])
        self._scan()
        # The scan must not have created a WAL/journal or otherwise mutated the db dir.
        siblings = {p.name for p in db_path.parent.iterdir()}
        self.assertEqual(siblings, {"state.vscdb"})

    # -- cross-cutting ----------------------------------------------------------------------

    def test_stable_external_ids_make_rescan_idempotent(self) -> None:
        _write_jsonl(
            self.claude_dir / "proj" / "s1.jsonl",
            [{"type": "user", "message": {"role": "user", "content": _long("A stable durable prompt about ledger design.")}, "uuid": "u1", "timestamp": "2026-07-01T10:00:00Z"}],
        )
        first = self._scan()
        second = self._scan()
        self.assertEqual(
            [r.external_id for r in first.records],
            [r.external_id for r in second.records],
        )

    def test_incremental_cursor_skips_old_files(self) -> None:
        old = self.claude_dir / "proj" / "old.jsonl"
        _write_jsonl(old, [{"type": "user", "message": {"role": "user", "content": _long("Old prompt about the first milestone.")}, "uuid": "o1", "timestamp": "2026-06-01T10:00:00Z"}])
        # Age the old file well into the past.
        old_time = time.time() - 86_400
        import os
        os.utime(old, (old_time, old_time))
        first = self._scan()
        self.assertEqual(len(first.records), 1)
        cursor = first.cursor_value
        self.assertTrue(cursor and cursor.startswith("hwm="))

        # A brand-new file after the cursor is picked up; the old one is skipped.
        new = self.claude_dir / "proj" / "new.jsonl"
        _write_jsonl(new, [{"type": "user", "message": {"role": "user", "content": _long("New prompt about the second milestone.")}, "uuid": "n1", "timestamp": "2026-07-02T10:00:00Z"}])
        second = scan_agent_sessions(
            claude_dir=str(self.claude_dir), codex_dir=str(self.codex_dir), cursor_dir=str(self.cursor_dir),
            cursor_value=cursor,
        )
        self.assertEqual(len(second.records), 1)
        self.assertIn("second milestone", second.records[0].content)
        self.assertGreaterEqual(second.skipped_files, 1)

    def test_truncation_holds_high_water_mark(self) -> None:
        # Two files; max_records=1 lands the first and truncates before the second.
        import os
        early = self.claude_dir / "proj" / "a.jsonl"
        _write_jsonl(early, [{"type": "user", "message": {"role": "user", "content": _long("Early single prompt about setup.")}, "uuid": "e1", "timestamp": "2026-06-01T10:00:00Z"}])
        early_time = time.time() - 100_000
        os.utime(early, (early_time, early_time))
        later = self.claude_dir / "proj" / "b.jsonl"
        _write_jsonl(later, [{"type": "user", "message": {"role": "user", "content": _long("Later prompt about the second file.")}, "uuid": "l1", "timestamp": "2026-07-01T10:00:00Z"}])
        scan = self._scan(max_records=1)
        self.assertTrue(scan.truncated)
        self.assertEqual(len(scan.records), 1)
        self.assertIn("setup", scan.records[0].content)  # oldest file first
        # The high-water mark advanced only past the fully-processed first file, so a resume
        # with the returned cursor still harvests the second file.
        resume = scan_agent_sessions(
            claude_dir=str(self.claude_dir), codex_dir=str(self.codex_dir), cursor_dir=str(self.cursor_dir),
            cursor_value=scan.cursor_value,
        )
        self.assertEqual(len(resume.records), 1)
        self.assertIn("second file", resume.records[0].content)

    def test_bad_file_is_error_not_crash(self) -> None:
        good = self.claude_dir / "proj" / "good.jsonl"
        _write_jsonl(good, [{"type": "user", "message": {"role": "user", "content": _long("A good and valid prompt about caching.")}, "uuid": "g1", "timestamp": "2026-07-01T10:00:00Z"}])
        # A corrupt cursor db raises inside _cursor_records -> becomes an error entry.
        ws = self.cursor_dir / "corruptws"
        ws.mkdir()
        (ws / "state.vscdb").write_bytes(b"not a sqlite database at all")
        scan = self._scan()
        self.assertEqual(len(scan.records), 1)  # the good claude prompt still lands
        self.assertTrue(any(err["agent"] == "cursor" for err in scan.errors))

    def test_unknown_agent_rejected(self) -> None:
        with self.assertRaises(ValueError):
            scan_agent_sessions(agents=["claude", "not-an-agent"])

    def test_empty_when_no_logs(self) -> None:
        scan = self._scan()
        self.assertEqual(scan.records, [])
        self.assertEqual(scan.records_found, 0)
        self.assertEqual(scan.to_summary()["connector"], AGENT_SESSIONS_SOURCE)


if __name__ == "__main__":
    unittest.main()
