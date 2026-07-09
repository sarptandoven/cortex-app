from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


class SourceReputationTests(unittest.TestCase):
    """source_reputation (Phase C): a pure read-model over the capture review ledger that learns
    which sources earn trust at review and RECOMMENDS promote/demote, never auto-flips a toggle.

    Locked invariants (tested here):
      - approvals come from capture.approved, rejections only from capture.archived(reason=user_review);
      - automated reconciliation archives (reason=reconcile) are NEVER counted as rejections;
      - each capture folds to its LAST decision (re-approve/re-reject churn can't inflate totals);
      - a recommendation requires REPUTATION_MIN_DECISIONS decided captures (min-evidence gate);
      - promote is only offered for an untrusted source, demote only for a trusted one;
      - the window is respected (decisions older than `days` fall out)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "rep-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # -- helpers ---------------------------------------------------------
    def _account(self, *, source: str = "slack", trusted: bool = False, label: str = "Acct") -> str:
        acct = self.store.upsert_source_account(
            self.user_id,
            source=source,
            account_label=label,
            policy={"review_required": not trusted, "allow_ai_context": True},
        )
        return acct["id"]

    def _capture(self, i: int, *, source: str = "slack", account_id: str | None = None) -> str:
        result = self.store.save_capture(
            user_id=self.user_id,
            content=f"Decision {i}: we standardized on the new deploy cadence and rollout policy.",
            source=source,
            source_url=f"{source}://thread/{i}",
            title=None,
            extracted=extract_context("We standardized on the new deploy cadence."),
            source_account_id=account_id,
            external_id=f"ext-{source}-{i}",
        )
        return result["capture_id"]

    def _by_source(self, report: dict, source: str) -> dict:
        return next(entry for entry in report["sources"] if entry["source"] == source)

    # -- tests -----------------------------------------------------------
    def test_approval_rate_and_reliable_verdict(self) -> None:
        """9 approvals + 1 user-rejection over the min-decisions floor => 0.9 rate, reliable."""
        ids = [self._capture(i) for i in range(10)]
        for cid in ids[:9]:
            self.store.approve_capture(self.user_id, cid)
        self.store.archive_capture(self.user_id, ids[9])  # default reason=user_review

        report = self.store.source_reputation(self.user_id)
        slack = self._by_source(report, "slack")
        self.assertEqual(slack["approved"], 9)
        self.assertEqual(slack["rejected"], 1)
        self.assertEqual(slack["decided"], 10)
        self.assertAlmostEqual(slack["approval_rate"], 0.9, places=3)
        self.assertEqual(slack["verdict"], "reliable")

    def test_reconcile_archive_is_not_a_rejection(self) -> None:
        """The core signal-hygiene invariant: a reconciliation archive (source file vanished) must
        NOT count against the source, and must not even overwrite an earlier genuine approval."""
        aid = self._account(trusted=False)
        ids = [self._capture(i, account_id=aid) for i in range(10)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)
        # Two captures later reconciled away (their source files disappeared).
        self.store.archive_capture(self.user_id, ids[0], reason="reconcile")
        self.store.archive_capture(self.user_id, ids[1], reason="reconcile")

        report = self.store.source_reputation(self.user_id)
        slack = self._by_source(report, "slack")
        self.assertEqual(slack["rejected"], 0, "reconcile archives must never be rejections")
        self.assertEqual(slack["approved"], 10, "the earlier approval must survive a reconcile archive")
        self.assertAlmostEqual(slack["approval_rate"], 1.0, places=3)

    def test_folds_to_last_decision_per_capture(self) -> None:
        """A capture approved then genuinely rejected at review counts once, as its final decision."""
        ids = [self._capture(i) for i in range(9)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)
        # One approval is later reversed by the user (a real user_review archive).
        self.store.archive_capture(self.user_id, ids[0], reason="user_review")

        report = self.store.source_reputation(self.user_id)
        slack = self._by_source(report, "slack")
        self.assertEqual(slack["decided"], 9, "fold to last decision: no double counting")
        self.assertEqual(slack["approved"], 8)
        self.assertEqual(slack["rejected"], 1)

    def test_min_decisions_gate_holds_recommendation(self) -> None:
        """Below REPUTATION_MIN_DECISIONS a perfect record is still insufficient_evidence/hold —
        one lucky streak must not auto-trust a whole connector."""
        aid = self._account(trusted=False)
        few = self.store.REPUTATION_MIN_DECISIONS - 1
        ids = [self._capture(i, account_id=aid) for i in range(few)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)

        report = self.store.source_reputation(self.user_id)
        account = next(a for a in report["accounts"] if a["source_account_id"] == aid)
        self.assertEqual(account["verdict"], "insufficient_evidence")
        self.assertEqual(account["recommendation"], "hold")
        self.assertEqual(report["recommendations"], [])

    def test_promote_only_for_untrusted_account(self) -> None:
        """A reliable but UNtrusted account earns a promote recommendation."""
        aid = self._account(trusted=False)
        ids = [self._capture(i, account_id=aid) for i in range(self.store.REPUTATION_MIN_DECISIONS)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)

        report = self.store.source_reputation(self.user_id)
        account = next(a for a in report["accounts"] if a["source_account_id"] == aid)
        self.assertEqual(account["verdict"], "reliable")
        self.assertFalse(account["trusted"])
        self.assertEqual(account["recommendation"], "promote")
        self.assertIn("slack", [r["source"] for r in report["recommendations"]])

    def test_already_trusted_reliable_account_holds(self) -> None:
        """A reliable account that is ALREADY trusted has nothing to promote => hold, no rec."""
        aid = self._account(trusted=True)
        ids = [self._capture(i, account_id=aid) for i in range(self.store.REPUTATION_MIN_DECISIONS)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)

        report = self.store.source_reputation(self.user_id)
        account = next(a for a in report["accounts"] if a["source_account_id"] == aid)
        self.assertEqual(account["verdict"], "reliable")
        self.assertTrue(account["trusted"])
        self.assertEqual(account["recommendation"], "hold")
        self.assertEqual(report["recommendations"], [])

    def test_demote_only_for_trusted_noisy_account(self) -> None:
        """A TRUSTED account the user keeps rejecting (<=0.5 approval) earns a demote recommendation."""
        aid = self._account(trusted=True)
        ids = [self._capture(i, account_id=aid) for i in range(10)]
        # 3 approved, 7 user-rejected => 0.3 approval rate, noisy.
        for cid in ids[:3]:
            self.store.approve_capture(self.user_id, cid)
        for cid in ids[3:]:
            self.store.archive_capture(self.user_id, cid, reason="user_review")

        report = self.store.source_reputation(self.user_id)
        account = next(a for a in report["accounts"] if a["source_account_id"] == aid)
        self.assertEqual(account["verdict"], "noisy")
        self.assertTrue(account["trusted"])
        self.assertEqual(account["recommendation"], "demote")
        self.assertEqual(
            [r["recommendation"] for r in report["recommendations"] if r["source_account_id"] == aid],
            ["demote"],
        )

    def test_untrusted_noisy_account_holds(self) -> None:
        """A noisy account that is not trusted has nothing to demote => hold (no phantom rec)."""
        aid = self._account(trusted=False)
        ids = [self._capture(i, account_id=aid) for i in range(10)]
        for cid in ids[:3]:
            self.store.approve_capture(self.user_id, cid)
        for cid in ids[3:]:
            self.store.archive_capture(self.user_id, cid, reason="user_review")

        report = self.store.source_reputation(self.user_id)
        account = next(a for a in report["accounts"] if a["source_account_id"] == aid)
        self.assertEqual(account["verdict"], "noisy")
        self.assertFalse(account["trusted"])
        self.assertEqual(account["recommendation"], "hold")
        self.assertEqual(report["recommendations"], [])

    def test_mixed_rate_holds(self) -> None:
        """An approval rate between the demote and promote thresholds is 'mixed' => hold."""
        aid = self._account(trusted=False)
        ids = [self._capture(i, account_id=aid) for i in range(10)]
        # 7 approved, 3 rejected => 0.7, between 0.5 and 0.9.
        for cid in ids[:7]:
            self.store.approve_capture(self.user_id, cid)
        for cid in ids[7:]:
            self.store.archive_capture(self.user_id, cid, reason="user_review")

        report = self.store.source_reputation(self.user_id)
        account = next(a for a in report["accounts"] if a["source_account_id"] == aid)
        self.assertEqual(account["verdict"], "mixed")
        self.assertEqual(account["recommendation"], "hold")

    def test_window_excludes_old_decisions(self) -> None:
        """days= bounds the ledger scan: a decision outside the window does not count."""
        ids = [self._capture(i) for i in range(9)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)
        # A 1-day window still sees today's decisions...
        fresh = self.store.source_reputation(self.user_id, days=1)
        self.assertEqual(self._by_source(fresh, "slack")["approved"], 9)

        # ...but if we backdate every approved event beyond the window, they fall out.
        from backend.app.database import connect

        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_events SET created_at = '2000-01-01T00:00:00Z' "
                "WHERE user_id = ? AND event_type = 'approved'",
                (self.user_id,),
            )
        aged = self.store.source_reputation(self.user_id, days=30)
        self.assertEqual(aged["sources"], [], "decisions older than the window must not count")

    def test_report_shape_and_caveats(self) -> None:
        """The read-model advertises its own honesty caveats and echoes the evidence floor."""
        report = self.store.source_reputation(self.user_id)
        self.assertEqual(report["min_decisions"], self.store.REPUTATION_MIN_DECISIONS)
        self.assertIn("window_days", report)
        self.assertTrue(any("never changes" in c.lower() for c in report["caveats"]))
        self.assertTrue(any("reconciliation" in c.lower() for c in report["caveats"]))

    def test_mcp_tool_is_read_scoped_and_returns_recommendations(self) -> None:
        """MCP parity: get_source_reputation is a READ tool (a write-only token is refused) and
        its dispatch returns the same promote/demote read-model the REST/store layers expose."""
        from backend.app import mcp_tools

        self.assertIn("get_source_reputation", mcp_tools.READ_TOOLS)
        self.assertNotIn("get_source_reputation", mcp_tools.WRITE_TOOLS)

        aid = self._account(trusted=False)
        ids = [self._capture(i, account_id=aid) for i in range(self.store.REPUTATION_MIN_DECISIONS)]
        for cid in ids:
            self.store.approve_capture(self.user_id, cid)

        # A read-scoped token gets the report...
        allowed = mcp_tools.call_tool(
            self.store, self.user_id, "get_source_reputation", {"days": 90}, token_scopes=["read"]
        )
        recs = {r["source"]: r["recommendation"] for r in allowed["recommendations"]}
        self.assertEqual(recs.get("slack"), "promote")

        # ...a write-only token is refused: reputation is a read-model, not a mutation.
        with self.assertRaises(PermissionError):
            mcp_tools.call_tool(
                self.store, self.user_id, "get_source_reputation", {"days": 90}, token_scopes=["write"]
            )


if __name__ == "__main__":
    unittest.main()
