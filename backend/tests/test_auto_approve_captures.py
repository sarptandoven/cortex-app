"""Regression tests for CORTEX_AUTO_APPROVE_CAPTURES.

The Review inbox holds new captures as `pending` until approved; retrieval hides pending
content. For a low-friction beta a deployment can flip auto-approve so a first-run
capture -> ask returns a cited answer with no manual approval step. This must:
  1. approve a manual capture at save time and make it immediately retrievable,
  2. leave the default (review-first) behaviour unchanged when the flag is off, and
  3. NOT bypass a connected source's per-source review policy (connector trust is sacred).
Also covers the truthful embedding index-compatibility report (schema follows the model).
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from backend.app.config import _truthy_env, load_settings
from backend.app.database import connect, init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore
from backend.app.worker import run_worker_tick


class AutoApproveCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.user_id = "auto-approve-user"

    def _save(self, content: str, *, auto_approve: bool, source: str = "macos", source_account_id=None) -> str:
        cap = self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=None,
            title=None,
            extracted=extract_context(content, source),
            cite_capture_provenance=True,
            auto_approve=auto_approve,
            source_account_id=source_account_id,
        )
        return cap["capture_id"]

    def _review_status(self, capture_id: str) -> str:
        with connect(self.db_path) as conn:
            row = conn.execute("SELECT review_status FROM captures WHERE id = ?", (capture_id,)).fetchone()
        return row["review_status"]

    def test_manual_capture_auto_approved_and_retrievable_without_approve(self) -> None:
        cid = self._save("My favorite programming language is Rust.", auto_approve=True)
        self.assertEqual(self._review_status(cid), "approved")
        run_worker_tick(self.store, [self.user_id], limit_per_user=100)
        # Retrievable with NO explicit approve step (default settings keep review on).
        self.assertGreaterEqual(len(self.store.search(self.user_id, "programming language", limit=5)), 1)

    def test_default_still_routes_to_review(self) -> None:
        cid = self._save("I use Postgres as my primary database.", auto_approve=False)
        self.assertEqual(self._review_status(cid), "pending")
        # Pending content is not returned by default retrieval.
        self.assertEqual(self.store.search(self.user_id, "primary database", limit=5), [])

    def test_async_manual_capture_survives_worker_extraction(self) -> None:
        """Regression: an auto-approved MANUAL capture queued via enqueue_capture must stay
        approved after the worker extracts it (the worker re-calls save_capture) — otherwise
        the /v1/captures/queue path silently reverts to pending and hides the memory."""
        cap = self.store.enqueue_capture(
            user_id=self.user_id,
            content="My favorite programming language is Rust.",
            source="macos",
            source_url=None,
            title=None,
            cite_capture_provenance=True,
            auto_approve=True,
        )
        cid = cap["capture_id"]
        self.assertEqual(self._review_status(cid), "approved")
        run_worker_tick(self.store, [self.user_id], limit_per_user=100)  # extraction re-saves it
        self.assertEqual(self._review_status(cid), "approved")  # must NOT revert to pending
        self.assertGreaterEqual(len(self.store.search(self.user_id, "programming language", limit=5)), 1)

    def test_enqueue_respects_source_review_policy(self) -> None:
        """Regression: the ASYNC path (enqueue_capture) must enforce a connected source's
        review_required policy just like the sync path — auto_approve/user settings only govern
        manual captures, never connector captures."""
        account = self.store.upsert_source_account(
            self.user_id, source="obsidian", account_label="vault", policy={"review_required": True}
        )
        cap = self.store.enqueue_capture(
            user_id=self.user_id,
            content="Connector note that must be reviewed first.",
            source="obsidian",
            source_url=None,
            title=None,
            source_account_id=account["id"],
            external_id="note-1",
            cite_capture_provenance=True,
            auto_approve=True,  # even with the deployment flag on
        )
        self.assertEqual(self._review_status(cap["capture_id"]), "pending")

    def test_approve_all_clears_the_whole_backlog_in_one_call(self) -> None:
        """Regression for the 99+ review backlog: approve_all_captures approves EVERY pending
        capture (not just a 10-item page) and makes them retrievable, while leaving nothing
        pending. This is the one-decision escape from the 10-at-a-time batch cap."""
        pending_ids = [
            self._save(f"Pending memory number {i} about topic alpha.", auto_approve=False)
            for i in range(15)
        ]
        self.assertTrue(all(self._review_status(cid) == "pending" for cid in pending_ids))

        result = self.store.approve_all_captures(self.user_id)
        self.assertEqual(result["approved"], 15)
        self.assertIsNone(result["source"])
        self.assertTrue(all(self._review_status(cid) == "approved" for cid in pending_ids))
        run_worker_tick(self.store, [self.user_id], limit_per_user=100)
        self.assertGreaterEqual(len(self.store.search(self.user_id, "topic alpha", limit=20)), 1)

    def test_approve_all_can_scope_to_one_source(self) -> None:
        """approve_all_captures(source=...) approves only that source's pending captures,
        matching case-insensitively, and leaves other sources still pending."""
        keep = self._save("Note from vault A.", auto_approve=False, source="obsidian")
        other = self._save("Note from a chat export.", auto_approve=False, source="chatgpt")

        result = self.store.approve_all_captures(self.user_id, source="OBSIDIAN")
        self.assertEqual(result["approved"], 1)
        self.assertEqual(self._review_status(keep), "approved")
        self.assertEqual(self._review_status(other), "pending")

    def test_mcp_remember_this_honours_auto_approve(self) -> None:
        """Regression: the MCP remember_this tool (agent-written manual captures) auto-approves
        when the flag is on, so agent-saved memories are immediately retrievable."""
        from backend.app import mcp_tools

        prev = os.environ.get("CORTEX_AUTO_APPROVE_CAPTURES")
        os.environ["CORTEX_AUTO_APPROVE_CAPTURES"] = "1"
        try:
            result = mcp_tools.call_tool(
                self.store, self.user_id, "remember_this",
                {"content": "Agent fact: my favorite language is Rust.", "source": "ai-chat"},
            )
        finally:
            if prev is None:
                os.environ.pop("CORTEX_AUTO_APPROVE_CAPTURES", None)
            else:
                os.environ["CORTEX_AUTO_APPROVE_CAPTURES"] = prev
        self.assertIsNotNone(result)
        run_worker_tick(self.store, [self.user_id], limit_per_user=100)
        self.assertGreaterEqual(len(self.store.search(self.user_id, "favorite language", limit=5)), 1)

    def test_auto_approve_does_not_bypass_source_review_policy(self) -> None:
        """A connected source whose policy requires review keeps its captures pending even
        when auto_approve is on — connector trust overrides the deployment convenience flag."""
        account = self.store.upsert_source_account(
            self.user_id, source="obsidian", account_label="vault", policy={"review_required": True}
        )
        cid = self._save(
            "Decision: connector captures must stay review-first.",
            auto_approve=True,
            source="obsidian",
            source_account_id=account["id"],
        )
        self.assertEqual(self._review_status(cid), "pending")

    def test_connected_obsidian_vault_is_trusted_by_default_and_retrievable(self) -> None:
        """Regression: a folder the user explicitly connects (review_required=False, the
        endpoint default) auto-adds its notes to memory so they're immediately retrievable —
        instead of dumping every note into Review, which made a connected vault look like it
        'never synced'. review_required=True still routes notes through Review."""
        import tempfile
        from pathlib import Path as _P

        vault = _P(tempfile.mkdtemp())
        (vault / "note.md").write_text(
            "My favorite programming language is Rust and I prefer dark-roast coffee.", encoding="utf-8"
        )
        # trusted (endpoint default): notes become active, searchable memory with no approve step
        res = self.store.sync_obsidian_vault(self.user_id, vault_path=str(vault), processing="sync", review_required=False)
        self.assertEqual(res["status"], "complete")
        self.assertGreaterEqual(res["saved"], 1)
        run_worker_tick(self.store, [self.user_id], limit_per_user=100)
        self.assertGreaterEqual(len(self.store.search(self.user_id, "programming language", limit=5)), 1)

        # review_required=True keeps the review gate: a second vault's notes stay pending
        vault2 = _P(tempfile.mkdtemp())
        (vault2 / "secret.md").write_text("Reviewable note about Postgres tuning.", encoding="utf-8")
        res2 = self.store.sync_obsidian_vault(self.user_id, vault_path=str(vault2), processing="sync", review_required=True)
        cap2 = res2["records"][0]["capture_id"]
        self.assertEqual(self._review_status(cap2), "pending")

    def test_config_flag_parses(self) -> None:
        self.assertFalse(load_settings().auto_approve_captures)  # default off
        prev = os.environ.get("CORTEX_AUTO_APPROVE_CAPTURES")
        os.environ["CORTEX_AUTO_APPROVE_CAPTURES"] = "1"
        try:
            self.assertTrue(_truthy_env("CORTEX_AUTO_APPROVE_CAPTURES"))
            self.assertTrue(load_settings().auto_approve_captures)
        finally:
            if prev is None:
                os.environ.pop("CORTEX_AUTO_APPROVE_CAPTURES", None)
            else:
                os.environ["CORTEX_AUTO_APPROVE_CAPTURES"] = prev

    def test_embedding_status_index_compatible_is_truthful(self) -> None:
        """The health/ready embedding report uses the store's real vec-index dimension, so
        index_compatible is True on a healthy shard (schema follows the active model) instead
        of comparing against the static 384 build constant."""
        status = self.store._embedding_status()
        self.assertEqual(status["schema_dimensions"], status["dimensions"])
        self.assertTrue(status["index_compatible"])
        # Same truthful value must appear in the surfaces /health and /ready render.
        self.assertTrue(self.store.runtime_storage_status()["embedding"]["index_compatible"])
        self.assertTrue(self.store.diagnostics(self.user_id)["embedding"]["index_compatible"])


if __name__ == "__main__":
    unittest.main()
