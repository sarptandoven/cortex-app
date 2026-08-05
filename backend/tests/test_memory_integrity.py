from __future__ import annotations

import copy
import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


class MemoryIntegrityTests(unittest.TestCase):
    """Phase D: a tamper-evident hash chain over the append-only event log, and a lossless,
    self-verifying portable export.

    Locked invariants (tested here):
      - the chain head is deterministic (same history -> same head);
      - a new event advances the head (continuity is observable);
      - the head self-verifies, and a SILENT edit to a past event is detected (the whole point);
      - the portable bundle round-trips (export -> verify == intact);
      - a modified bundle payload fails verification (payload hash + counts both catch it);
      - the manifest attests the same bytes the bundle carries (lossless)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "integrity-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, n: int, *, approve: int = 0) -> list[str]:
        ids = []
        for i in range(n):
            result = self.store.save_capture(
                user_id=self.user_id,
                content=f"Decision {i}: we standardized deploy cadence and the rollout policy.",
                source="note",
                source_url=f"note://x/{i}",
                title=None,
                extracted=extract_context("We standardized deploy cadence."),
                external_id=f"e{i}",
            )
            ids.append(result["capture_id"])
        for cid in ids[:approve]:
            self.store.approve_capture(self.user_id, cid)
        return ids

    # -- hash chain ------------------------------------------------------
    def test_chain_head_is_deterministic(self) -> None:
        self._seed(4, approve=2)
        first = self.store.integrity_digest(self.user_id)
        second = self.store.integrity_digest(self.user_id)
        self.assertEqual(first["chain_head"], second["chain_head"])
        self.assertEqual(first["event_count"], second["event_count"])
        self.assertGreater(first["event_count"], 0)

    def test_empty_history_folds_to_genesis_only(self) -> None:
        """A user with no events has a valid (genesis-seeded) chain of length 0 — not an error."""
        digest = self.store.integrity_digest("nobody-here")
        self.assertEqual(digest["event_count"], 0)
        # With zero events the head is genesis folded zero times: exactly the genesis constant.
        self.assertEqual(digest["chain_head"], self.store.INTEGRITY_CHAIN_GENESIS)

    def test_new_event_advances_the_head(self) -> None:
        ids = self._seed(3, approve=1)
        before = self.store.integrity_digest(self.user_id)
        self.store.approve_capture(self.user_id, ids[1])  # emits a capture.approved event
        after = self.store.integrity_digest(self.user_id)
        self.assertNotEqual(before["chain_head"], after["chain_head"])
        self.assertEqual(after["event_count"], before["event_count"] + 1)

    def test_head_self_verifies(self) -> None:
        self._seed(3, approve=2)
        head = self.store.integrity_digest(self.user_id)["chain_head"]
        result = self.store.verify_integrity(self.user_id, head)
        self.assertTrue(result["matches"])
        self.assertEqual(result["actual_head"], head)

    def test_verify_rejects_stale_head_after_new_event(self) -> None:
        ids = self._seed(3, approve=1)
        pinned = self.store.integrity_digest(self.user_id)["chain_head"]
        self.store.approve_capture(self.user_id, ids[2])
        result = self.store.verify_integrity(self.user_id, pinned)
        self.assertFalse(result["matches"], "a head from before a new event must not match")

    def test_silent_tamper_is_detected(self) -> None:
        """The core property: edit a past event's metadata directly in the DB and the recomputed
        head no longer matches the pinned one. Tamper-evidence, not tamper-proofing."""
        self._seed(3, approve=2)
        head = self.store.integrity_digest(self.user_id)["chain_head"]
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT id FROM memory_events WHERE user_id = ? AND event_type = 'approved' ORDER BY rowid LIMIT 1",
                (self.user_id,),
            ).fetchone()
            conn.execute(
                "UPDATE memory_events SET metadata_json = ? WHERE id = ?",
                ('{"tampered": true}', row["id"]),
            )
        result = self.store.verify_integrity(self.user_id, head)
        self.assertFalse(result["matches"], "a silent edit to the past must break the head")

    def test_verify_with_empty_expected_head_does_not_match(self) -> None:
        self._seed(2)
        result = self.store.verify_integrity(self.user_id, "")
        self.assertFalse(result["matches"])
        self.assertIsNone(result["expected_head"])

    # -- portable bundle -------------------------------------------------
    def test_bundle_round_trips(self) -> None:
        self._seed(4, approve=3)
        bundle = self.store.export_portable_bundle(self.user_id)
        verdict = self.store.verify_portable_bundle(bundle)
        self.assertTrue(verdict["verified"])
        self.assertTrue(verdict["payload_matches"])
        self.assertTrue(verdict["counts_match"])

    def test_modified_bundle_payload_fails_verification(self) -> None:
        self._seed(3, approve=2)
        bundle = self.store.export_portable_bundle(self.user_id)
        tampered = copy.deepcopy(bundle)
        tampered["payload"]["memories"].append({"id": "injected", "content": "not real"})
        verdict = self.store.verify_portable_bundle(tampered)
        self.assertFalse(verdict["verified"])
        self.assertFalse(verdict["payload_matches"])
        self.assertFalse(verdict["counts_match"])

    def test_bundle_manifest_attests_same_bytes_as_standalone_manifest(self) -> None:
        """Lossless: the standalone export_manifest and the bundle's embedded manifest describe the
        same export bytes (same payload sha256), and the chain head lines up with the digest."""
        self._seed(3, approve=2)
        manifest = self.store.export_manifest(self.user_id)
        bundle = self.store.export_portable_bundle(self.user_id)
        digest = self.store.integrity_digest(self.user_id)
        self.assertEqual(manifest["payload_sha256"], bundle["manifest"]["payload_sha256"])
        self.assertEqual(manifest["chain_head"], digest["chain_head"])
        self.assertEqual(bundle["manifest"]["chain_head"], digest["chain_head"])

    def test_verify_rejects_malformed_bundle(self) -> None:
        for bad in ({}, {"manifest": {}}, {"payload": {}}, "not-a-dict", 42):
            with self.assertRaises(ValueError):
                self.store.verify_portable_bundle(bad)  # type: ignore[arg-type]

    def test_bundle_counts_match_export_json(self) -> None:
        """The bundle payload IS export_json, so its manifest counts match the raw export."""
        self._seed(5, approve=4)
        export = self.store.export_json(self.user_id)
        bundle = self.store.export_portable_bundle(self.user_id)
        self.assertEqual(bundle["manifest"]["record_counts"]["captures"], len(export["captures"]))
        self.assertEqual(bundle["payload"]["captures"], export["captures"])

    # -- MCP parity ------------------------------------------------------
    def test_mcp_tool_scopes_and_dispatch(self) -> None:
        """Integrity digest/verify and bundle-verify are READ tools; the bundle EXPORT is export-
        scoped (needs the export capability). Dispatch returns the same read-models as the store."""
        from backend.app import mcp_tools

        for read_tool in ("get_memory_integrity", "verify_memory_integrity", "verify_memory_bundle"):
            self.assertIn(read_tool, mcp_tools.READ_TOOLS)
            self.assertNotIn(read_tool, mcp_tools.EXPORT_TOOLS)
        self.assertIn("export_memory_bundle", mcp_tools.EXPORT_TOOLS)
        self.assertNotIn("export_memory_bundle", mcp_tools.READ_TOOLS)

        self.store.update_settings(self.user_id, {"allow_agent_exports": True})
        self._seed(3, approve=2)

        dig = mcp_tools.call_tool(self.store, self.user_id, "get_memory_integrity", {}, token_scopes=["read"])
        head = dig["chain_head"]
        verified = mcp_tools.call_tool(
            self.store, self.user_id, "verify_memory_integrity", {"expected_head": head}, token_scopes=["read"]
        )
        self.assertTrue(verified["matches"])

        # The bundle export is refused with a read-only token, allowed with export.
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(self.store, self.user_id, "export_memory_bundle", {}, token_scopes=["read"])
        bundle = mcp_tools.call_tool(
            self.store, self.user_id, "export_memory_bundle", {}, token_scopes=["read", "export"]
        )
        verdict = mcp_tools.call_tool(
            self.store, self.user_id, "verify_memory_bundle", {"bundle": bundle}, token_scopes=["read"]
        )
        self.assertTrue(verdict["verified"])


if __name__ == "__main__":
    unittest.main()
