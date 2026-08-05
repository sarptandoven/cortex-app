from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app import mcp_tools
from backend.app.storage import CortexStore, connect


class Phase2ContextPackTests(unittest.TestCase):
    """Pinned context packs: immutable, content-addressed, replayable audit artifacts of exactly
    what context an agent was served. Locked invariants — sha256(vault file bytes) == pack_sha
    forever, the DB row is only an index (the vault file is truth), tampering is an error not a
    degraded payload, and session linkage must reference a real agent session."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase2-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        text = "I decided to use Postgres for the ledger service after comparing options."
        self.store.save_capture(
            user_id=self.user_id,
            content=text,
            source="note",
            source_url=None,
            title=None,
            extracted=extract_context(text),
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _call(self, name: str, args: dict):
        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def _pin(self, task: str = "ledger database work", **kw) -> dict:
        pack = self._call("get_context", {"task": task, "pin": True, **kw})
        self.assertIn("pin", pack)
        return pack

    # -- pinning ------------------------------------------------------------------------------

    def test_pin_writes_content_addressed_vault_file(self) -> None:
        pack = self._pin()
        sha = pack["pin"]["pack_sha"]
        path = self.store.vault.context_pack_path(sha)
        self.assertTrue(path.exists())
        raw = path.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), sha)
        # Sharded layout: context_packs/<sha[:2]>/<sha>.json
        self.assertEqual(path.parent.name, sha[:2])
        # The hashed body never contains the pin block (it describes the artifact).
        self.assertNotIn("pin", json.loads(raw.decode("utf-8")))

    def test_unpinned_get_context_pins_nothing(self) -> None:
        pack = self._call("get_context", {"task": "ledger database work"})
        self.assertNotIn("pin", pack)
        self.assertEqual(self._call("list_context_packs", {}), [])

    def test_repin_of_identical_bytes_is_idempotent(self) -> None:
        pack = self._pin()
        again = self.store.pin_context_pack(self.user_id, {k: v for k, v in pack.items() if k != "pin"})
        self.assertEqual(again["pack_sha"], pack["pin"]["pack_sha"])
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM context_packs WHERE user_id = ? AND pack_sha = ?",
                (self.user_id, again["pack_sha"]),
            ).fetchone()
        self.assertEqual(int(row["n"]), 1)

    def test_canonical_bytes_are_deterministic(self) -> None:
        body = {"b": 2, "a": [1, {"z": True, "y": None}], "task": "x"}
        first = CortexStore._canonical_pack_bytes(dict(body))
        second = CortexStore._canonical_pack_bytes({**body, "pin": {"pack_sha": "ignored"}})
        self.assertEqual(first, second)
        self.assertNotIn(b" ", first.split(b'"task"')[0])  # compact separators

    def test_markdown_format_still_pins_json_body(self) -> None:
        rendered = self._call("get_context", {"task": "ledger database work", "format": "markdown", "pin": True})
        self.assertIsInstance(rendered, str)
        packs = self._call("list_context_packs", {})
        self.assertEqual(len(packs), 1)
        replay = self._call("get_context_pack", {"pack_sha": packs[0]["pack_sha"]})
        self.assertEqual(replay["pack"]["task"], "ledger database work")

    # -- replay + integrity ---------------------------------------------------------------------

    def test_replay_returns_exact_pack_verified(self) -> None:
        pack = self._pin()
        sha = pack["pin"]["pack_sha"]
        replay = self._call("get_context_pack", {"pack_sha": sha})
        self.assertTrue(replay["verified"])
        self.assertEqual(replay["pack"]["task"], "ledger database work")
        self.assertEqual(replay["pack"]["citations"], pack["citations"])
        self.assertEqual(replay["resolution"]["citation_count"], len(pack["citations"]))

    def test_tampered_pack_is_an_error(self) -> None:
        sha = self._pin()["pin"]["pack_sha"]
        path = self.store.vault.context_pack_path(sha)
        path.write_text(path.read_text().replace("ledger", "hacked"))
        with self.assertRaises(ValueError):
            self._call("get_context_pack", {"pack_sha": sha})

    def test_missing_vault_file_is_an_error(self) -> None:
        sha = self._pin()["pin"]["pack_sha"]
        self.store.vault.context_pack_path(sha).unlink()
        with self.assertRaises(ValueError):
            self._call("get_context_pack", {"pack_sha": sha})

    def test_bad_or_unknown_sha_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self._call("get_context_pack", {"pack_sha": "not-a-sha"})
        with self.assertRaises(ValueError):
            self._call("get_context_pack", {"pack_sha": "0" * 64})

    def test_packs_are_user_scoped(self) -> None:
        sha = self._pin()["pin"]["pack_sha"]
        with self.assertRaises(ValueError):
            mcp_tools.call_tool(self.store, "other-user", "get_context_pack", {"pack_sha": sha})

    # -- session linkage ------------------------------------------------------------------------

    def test_session_linked_pins_are_listable_by_session(self) -> None:
        session = self.store.begin_agent_session(self.user_id, goal="ledger work")
        linked = self._pin(session_id=session["id"])
        self._pin(task="unrelated other task")
        by_session = self._call("list_context_packs", {"session_id": session["id"]})
        self.assertEqual([p["pack_sha"] for p in by_session], [linked["pin"]["pack_sha"]])
        self.assertEqual(len(self._call("list_context_packs", {})), 2)
        replay = self._call("get_context_pack", {"pack_sha": linked["pin"]["pack_sha"]})
        self.assertEqual(replay["session_id"], session["id"])

    def test_pin_with_unknown_session_fails_loudly(self) -> None:
        with self.assertRaises(ValueError):
            self._pin(session_id="asess_missing")

    # -- substrate invariants ---------------------------------------------------------------------

    def test_packs_are_excluded_from_vault_restore_and_reconcile(self) -> None:
        sha = self._pin()["pin"]["pack_sha"]
        raw = self.store.vault.context_pack_path(sha).read_bytes()
        self.store.rebuild_index_from_vault(self.user_id)
        # Rebuild neither deletes nor rewrites the pack file: byte identity survives.
        self.assertEqual(self.store.vault.context_pack_path(sha).read_bytes(), raw)
        # And the pack never becomes a memory.
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE user_id = ? AND id LIKE ?",
                (self.user_id, f"%{sha[:12]}%"),
            ).fetchone()
        self.assertEqual(int(row["n"]), 0)

    def test_pin_emits_audit_event(self) -> None:
        sha = self._pin()["pin"]["pack_sha"]
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memory_events WHERE user_id = ? AND object_type = 'context_pack' AND object_id = ? AND event_type = 'pinned'",
                (self.user_id, sha),
            ).fetchone()
        self.assertEqual(int(row["n"]), 1)

    # -- tool surface -----------------------------------------------------------------------------

    def test_tools_registered_as_reads(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        self.assertIn("get_context_pack", names)
        self.assertIn("list_context_packs", names)
        for tool_name in ("get_context_pack", "list_context_packs"):
            self.assertIn("read", mcp_tools.tool_required_capabilities(tool_name))
            self.assertNotIn(tool_name, mcp_tools.WRITE_TOOLS)


if __name__ == "__main__":
    unittest.main()
