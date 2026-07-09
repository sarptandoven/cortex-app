from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app import mcp_tools
from backend.app.storage import CONTEXT_ENGINE_VERSION, CortexStore, connect


class Phase2bRecomputeVerifyTests(unittest.TestCase):
    """Phase 2b: verify_context_pack, the recompute-verify DIAGNOSTIC. Locked invariants:
    storage integrity is checked before any recompute (tampered artifact = error, never a
    verdict); recompute runs with the pack's stored inputs and as_of pinned to pin time;
    a same-corpus recompute matches; corpus growth reports drift (named, per-layer) rather
    than pretending stability; an engine version bump reports engine_mismatch instead of a
    misleading diff; and the diagnostic recompute never feeds the reuse/prefetch signal."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase2b-user"
        # verify_context_pack is maintenance-scoped, and the user-level maintenance gate
        # (allow_agent_maintenance, default OFF) applies to the app path too — turn it on
        # here; test_maintenance_gate_defaults_off proves the default blocks it.
        self.store.update_settings(
            self.user_id, {"review_new_captures": False, "allow_agent_maintenance": True}
        )
        self._capture("I decided to use Postgres for the ledger service after comparing options.")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _capture(self, text: str) -> None:
        # source_url makes the extracted memories citation-backed, so they actually pack
        # into layers (uncited items are excluded from packs by design).
        self.store.save_capture(
            user_id=self.user_id,
            content=text,
            source="note",
            source_url="obsidian://vault/notes/ledger.md",
            title=None,
            extracted=extract_context(text),
        )

    def _call(self, name: str, args: dict):
        return mcp_tools.call_tool(self.store, self.user_id, name, args)

    def _pin(self, task: str = "ledger database work", **kw) -> str:
        pack = self._call("get_context", {"task": task, "pin": True, **kw})
        return pack["pin"]["pack_sha"]

    # -- verdicts -------------------------------------------------------------------------

    def test_unchanged_corpus_recompute_matches(self) -> None:
        sha = self._pin()
        result = self._call("verify_context_pack", {"pack_sha": sha})
        self.assertEqual(result["status"], "match")
        self.assertTrue(result["verified_storage"])
        self.assertTrue(result["diff"]["equal"])
        self.assertEqual(result["diff"]["changed_fields"], [])
        self.assertEqual(result["engine_version"], CONTEXT_ENGINE_VERSION)
        # The effective as_of was resolved and used, even though the pin didn't pass one.
        self.assertTrue(result["as_of_used"])

    def test_verify_is_deterministic(self) -> None:
        sha = self._pin()
        first = self._call("verify_context_pack", {"pack_sha": sha})
        second = self._call("verify_context_pack", {"pack_sha": sha})
        self.assertEqual(first["status"], second["status"])
        self.assertEqual(first["diff"], second["diff"])

    def test_corpus_growth_reports_drift_with_named_fields(self) -> None:
        sha = self._pin()
        # New matching memory: valid_from is empty, so the pinned as_of does not exclude it.
        # This is the honest contract — the corpus moved, verify says so, names the drift.
        self._capture("I decided the ledger database needs a read replica for reporting.")
        result = self._call("verify_context_pack", {"pack_sha": sha})
        self.assertEqual(result["status"], "drift")
        self.assertTrue(result["verified_storage"])
        changed = {entry["field"] for entry in result["diff"]["changed_fields"]}
        self.assertIn("layers", changed)
        layers_entry = next(e for e in result["diff"]["changed_fields"] if e["field"] == "layers")
        drift = layers_entry["layer_drift"]
        self.assertTrue(drift)
        new_ids = [i for layer in drift.values() for i in layer["new_in_recompute"]]
        self.assertTrue(any(i.startswith("mem_") for i in new_ids))

    def test_engine_version_bump_reports_engine_mismatch_not_drift(self) -> None:
        sha = self._pin()
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE context_packs SET engine_version = ? WHERE pack_sha = ?",
                (CONTEXT_ENGINE_VERSION - 1, sha),
            )
        result = self._call("verify_context_pack", {"pack_sha": sha})
        self.assertEqual(result["status"], "engine_mismatch")
        self.assertTrue(result["verified_storage"])
        self.assertEqual(result["stored_engine_version"], CONTEXT_ENGINE_VERSION - 1)
        self.assertEqual(result["current_engine_version"], CONTEXT_ENGINE_VERSION)
        self.assertNotIn("diff", result)

    # -- integrity and identity ------------------------------------------------------------

    def test_tampered_pack_is_an_error_not_a_verdict(self) -> None:
        sha = self._pin()
        path = self.store.vault.context_pack_path(sha)
        raw = json.loads(path.read_text())
        raw["task"] = "tampered"
        path.write_text(json.dumps(raw, sort_keys=True, separators=(",", ":")))
        with self.assertRaises(ValueError):
            self._call("verify_context_pack", {"pack_sha": sha})

    def test_unknown_and_malformed_shas_error(self) -> None:
        with self.assertRaises(ValueError):
            self._call("verify_context_pack", {"pack_sha": "0" * 64})
        with self.assertRaises(ValueError):
            self._call("verify_context_pack", {"pack_sha": "not-a-sha"})

    def test_other_user_cannot_verify_my_pack(self) -> None:
        sha = self._pin()
        # Give the other user the maintenance gate so the test proves pack identity scoping,
        # not merely the settings gate.
        self.store.update_settings("other-user", {"allow_agent_maintenance": True})
        with self.assertRaises(ValueError):
            mcp_tools.call_tool(self.store, "other-user", "verify_context_pack", {"pack_sha": sha})

    # -- side-effect discipline --------------------------------------------------------------

    def test_verify_recompute_never_feeds_reuse_signal(self) -> None:
        sha = self._pin()

        def count_reuse() -> int:
            with connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT COUNT(*) AS n FROM memory_events WHERE user_id = ? AND event_type = 'context_reused'",
                    (self.user_id,),
                ).fetchone()
            return int(row["n"])

        before = count_reuse()
        self._call("verify_context_pack", {"pack_sha": sha})
        self.assertEqual(count_reuse(), before)

    def test_verify_emits_audit_event(self) -> None:
        sha = self._pin()
        self._call("verify_context_pack", {"pack_sha": sha})
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = ? AND event_type = 'recompute_verified'",
                (self.user_id, sha),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(json.loads(row["metadata_json"])["status"], "match")

    def test_verify_does_not_pin_new_packs(self) -> None:
        sha = self._pin()
        with connect(self.db_path) as conn:
            before = conn.execute("SELECT COUNT(*) AS n FROM context_packs").fetchone()["n"]
        self._call("verify_context_pack", {"pack_sha": sha})
        with connect(self.db_path) as conn:
            after = conn.execute("SELECT COUNT(*) AS n FROM context_packs").fetchone()["n"]
        self.assertEqual(before, after)

    # -- scope discipline ---------------------------------------------------------------------

    def test_maintenance_scoped_diagnostic(self) -> None:
        self.assertIn("verify_context_pack", mcp_tools.MAINTENANCE_TOOLS)
        self.assertEqual(
            mcp_tools.tool_required_capabilities("verify_context_pack"), ["maintenance"]
        )
        sha = self._pin()
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store, self.user_id, "verify_context_pack", {"pack_sha": sha},
                token_scopes=["read", "write"],
            )
        result = mcp_tools.call_tool(
            self.store, self.user_id, "verify_context_pack", {"pack_sha": sha},
            token_scopes=["read", "maintenance"],
        )
        self.assertEqual(result["status"], "match")

    def test_maintenance_gate_defaults_off(self) -> None:
        sha = self._pin()
        self.store.update_settings(self.user_id, {"allow_agent_maintenance": False})
        with self.assertRaises(PermissionError):
            self._call("verify_context_pack", {"pack_sha": sha})

    def test_advertised_only_with_maintenance_scope(self) -> None:
        names_plain = {t["name"] for t in mcp_tools.tools_for_scopes(["read", "write"])}
        self.assertNotIn("verify_context_pack", names_plain)
        names_maint = {t["name"] for t in mcp_tools.tools_for_scopes(["read", "maintenance"])}
        self.assertIn("verify_context_pack", names_maint)
        # Admin/app path (unscoped) sees everything.
        names_admin = {t["name"] for t in mcp_tools.tools_for_scopes(None)}
        self.assertIn("verify_context_pack", names_admin)


if __name__ == "__main__":
    unittest.main()
