from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app import mcp_tools
from backend.app.provenance import (
    compute_trust_score,
    sign_authorship,
    verify_authorship,
)
from backend.app.storage import CortexStore, connect


class Phase3ProvenanceHygieneTests(unittest.TestCase):
    """Provenance ledger & hygiene: deterministic trust scoring, echo suppression,
    the agent-never-supersedes-user invariant, authorship HMAC signing, and the
    belief timeline. Locked invariants — trust is derived-only (write path, rescore
    job, and rebuild converge), corroboration never lowers a score, and hand-edited
    authorship in vault frontmatter is restored to the signed class."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase3-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _capture(self, text: str, *, source: str = "note", source_url: str | None = None) -> dict:
        return self.store.save_capture(
            user_id=self.user_id,
            content=text,
            source=source,
            source_url=source_url,
            title=None,
            extracted=extract_context(text),
        )

    def _memories(self) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM memories WHERE user_id = ? ORDER BY captured_at",
                (self.user_id,),
            ).fetchall()
        return [self.store._memory_from_row(row) for row in rows]

    def _events(self, event_type: str) -> list:
        with connect(self.db_path) as conn:
            return conn.execute(
                "SELECT * FROM memory_events WHERE user_id = ? AND event_type = ?",
                (self.user_id, event_type),
            ).fetchall()

    # -- compute_trust_score (pure function) ----------------------------------------------

    def test_trust_score_bases(self) -> None:
        self.assertEqual(compute_trust_score(author_class="user"), 1.0)
        self.assertEqual(compute_trust_score(author_class="connector", has_citation=True), 0.8)
        self.assertEqual(compute_trust_score(author_class="agent", has_citation=True), 0.5)
        self.assertEqual(compute_trust_score(author_class="agent", has_citation=False), 0.4)

    def test_uncited_penalty_never_applies_to_user(self) -> None:
        self.assertEqual(compute_trust_score(author_class="user", has_citation=False), 1.0)

    def test_corroboration_never_lowers_score(self) -> None:
        # Locked property: for every class/citation/supersession combination the score
        # is monotone non-decreasing in occurrences.
        for author in ("user", "connector", "agent", "unknown"):
            for cited in (True, False):
                for superseded in (True, False):
                    prev = -1.0
                    for occ in range(1, 12):
                        score = compute_trust_score(
                            author_class=author,
                            has_citation=cited,
                            occurrences=occ,
                            superseded=superseded,
                        )
                        self.assertGreaterEqual(score, prev, (author, cited, superseded, occ))
                        self.assertGreaterEqual(score, 0.0)
                        self.assertLessEqual(score, 1.0)
                        prev = score

    def test_corroboration_caps(self) -> None:
        self.assertEqual(
            compute_trust_score(author_class="agent", has_citation=True, occurrences=4), 0.65
        )
        # Capped: 100 occurrences no better than 4.
        self.assertEqual(
            compute_trust_score(author_class="agent", has_citation=True, occurrences=100), 0.65
        )

    def test_superseded_halves(self) -> None:
        self.assertEqual(
            compute_trust_score(author_class="agent", has_citation=True, superseded=True), 0.25
        )

    def test_score_is_deterministic(self) -> None:
        a = compute_trust_score(author_class="connector", has_citation=False, occurrences=3)
        b = compute_trust_score(author_class="connector", has_citation=False, occurrences=3)
        self.assertEqual(a, b)

    # -- write path -----------------------------------------------------------------------

    def test_user_capture_scores_full_trust(self) -> None:
        self._capture("I decided to use Postgres for the ledger service database.")
        memories = self._memories()
        self.assertTrue(memories)
        for memory in memories:
            self.assertEqual(memory["author_class"], "user")
            self.assertEqual(memory["trust_score"], 1.0)

    def test_agent_capture_without_citation_discounted(self) -> None:
        self._capture(
            "The deployment pipeline requires manual approval before production releases happen.",
            source="claude",
        )
        memories = self._memories()
        self.assertTrue(memories)
        for memory in memories:
            self.assertEqual(memory["author_class"], "agent")
            self.assertEqual(memory["trust_score"], 0.4)

    def test_occurrence_bump_raises_trust(self) -> None:
        text = "The analytics warehouse queries must always filter by tenant identifier first."
        self._capture(text, source="claude")
        before = self._memories()[0]
        self._capture(text + " ", source="claude")
        after = self._memories()[0]
        self.assertGreaterEqual(after["occurrences"], before["occurrences"])
        self.assertGreaterEqual(after["trust_score"], before["trust_score"])

    # -- echo suppression -------------------------------------------------------------------

    def test_agent_echo_of_user_memory_is_suppressed(self) -> None:
        # NOTE: not a personal-layer statement — those classify as user regardless of
        # transport (the extractor authorship gate), so they can never be agent echoes.
        text = "Sarah is leading the migration of the billing service to the new platform."
        self._capture(text)
        originals = self._memories()
        self.assertTrue(all(m["author_class"] == "user" for m in originals))
        count_before = len(originals)
        # Agent restates the same fact through an agent transport.
        self._capture(text, source="chatgpt")
        after = self._memories()
        self.assertEqual(len(after), count_before, "echo must not insert a new memory")
        self.assertTrue(all(m["author_class"] == "user" for m in after))
        # Original strengthened, not weakened.
        strengthened = [m for m in after if m["occurrences"] >= 2]
        self.assertTrue(strengthened)
        for memory in after:
            self.assertEqual(memory["trust_score"], 1.0)
        self.assertTrue(self._events("echo_suppressed"))

    def test_user_repeat_is_not_echo_suppressed(self) -> None:
        # Echo suppression only applies to agent-authored writes; the user restating
        # their own fact goes down the normal duplicate/occurrence path.
        text = "I decided that the mobile app will launch in March next year."
        self._capture(text)
        self._capture(text)
        self.assertFalse(self._events("echo_suppressed"))

    # -- supersession invariant ---------------------------------------------------------------

    def test_resolve_conflict_blocks_agent_over_user(self) -> None:
        self._capture("I decided we will standardize on PostgreSQL for all backend services.")
        user_memory = self._memories()[0]
        self._capture(
            "The team standardizes on MySQL for all backend services going forward now.",
            source="claude",
        )
        agent_memory = [m for m in self._memories() if m["author_class"] == "agent"][0]
        ok = self.store.resolve_conflict(
            self.user_id, stale_id=user_memory["id"], current_id=agent_memory["id"]
        )
        self.assertFalse(ok, "agent memory must not supersede user memory")
        refreshed = [m for m in self._memories() if m["id"] == user_memory["id"]][0]
        self.assertFalse(refreshed.get("superseded_by"))
        self.assertTrue(self._events("supersede_blocked"))

    def test_resolve_conflict_user_over_agent_allowed_and_rescores(self) -> None:
        self._capture(
            "The team is standardizing on MySQL for all future backend services.",
            source="claude",
        )
        agent_memory = [m for m in self._memories() if m["author_class"] == "agent"][0]
        self._capture("I decided we standardize on PostgreSQL for all our backend services.")
        user_memory = [m for m in self._memories() if m["author_class"] == "user"][0]
        ok = self.store.resolve_conflict(
            self.user_id, stale_id=agent_memory["id"], current_id=user_memory["id"]
        )
        self.assertTrue(ok)
        stale = [m for m in self._memories() if m["id"] == agent_memory["id"]][0]
        self.assertEqual(stale["superseded_by"], user_memory["id"])
        # Supersession halves the derived trust immediately.
        self.assertEqual(
            stale["trust_score"],
            compute_trust_score(
                author_class="agent",
                has_citation=bool(str(stale.get("source_url") or "").strip()),
                occurrences=stale["occurrences"],
                superseded=True,
            ),
        )

    # -- rescore job ---------------------------------------------------------------------------

    def test_rescore_trust_is_idempotent_and_converges(self) -> None:
        self._capture("I prefer short paragraphs and concrete examples in all documentation.")
        self._capture(
            "The build cache is stored on the shared network volume for CI runners.",
            source="claude",
        )
        # Corrupt scores out-of-band to prove rescore restores the derived values.
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memories SET trust_score = 0.123 WHERE user_id = ?", (self.user_id,)
            )
        first = self.store.rescore_trust(self.user_id)
        self.assertGreater(first["updated"], 0)
        second = self.store.rescore_trust(self.user_id)
        self.assertEqual(second["updated"], 0, "rescore must be idempotent")
        for memory in self._memories():
            expected = compute_trust_score(
                author_class=memory["author_class"],
                has_citation=bool(str(memory.get("source_url") or "").strip()),
                occurrences=memory["occurrences"],
                superseded=bool(str(memory.get("superseded_by") or "").strip()),
            )
            self.assertEqual(memory["trust_score"], expected)

    def test_rescore_job_enqueue_coalesces_and_runs(self) -> None:
        self._capture("I decided the invoicing service ships before the reporting dashboard.")
        from backend.app.storage import now_iso

        job1 = self.store.enqueue_trust_rescore(self.user_id, run_at=now_iso())
        job2 = self.store.enqueue_trust_rescore(self.user_id, run_at=now_iso())
        self.assertEqual(job1["id"], job2["id"], "same-day enqueues must coalesce")
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT job_type, status FROM memory_jobs WHERE id = ?", (job1["id"],)
            ).fetchone()
        self.assertEqual(row["job_type"], "rescore_trust")
        processed = self.store.run_due_jobs(self.user_id, limit=20)
        types = {job.get("job_type") for job in processed.get("jobs", [])} if isinstance(processed, dict) else set()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT status FROM memory_jobs WHERE id = ?", (job1["id"],)
            ).fetchone()
        self.assertEqual(row["status"], "succeeded", types)

    # -- signing ------------------------------------------------------------------------------

    def test_sign_and_verify_roundtrip(self) -> None:
        key = b"k" * 32
        sig = sign_authorship(key, memory_id="mem_1", author_class="user")
        self.assertTrue(verify_authorship(key, sig, memory_id="mem_1", author_class="user"))
        self.assertFalse(verify_authorship(key, sig, memory_id="mem_1", author_class="agent"))
        self.assertFalse(verify_authorship(key, sig, memory_id="mem_2", author_class="user"))
        self.assertFalse(verify_authorship(b"x" * 32, sig, memory_id="mem_1", author_class="user"))

    def test_save_records_authorship_signature(self) -> None:
        self._capture("I decided the api gateway timeout stays at thirty seconds exactly.")
        memory = self._memories()[0]
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM authorship_signatures WHERE user_id = ? AND memory_id = ?",
                (self.user_id, memory["id"]),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["author_class"], memory["author_class"])
        key = self.store.vault.authorship_key()
        self.assertTrue(
            verify_authorship(
                key, row["signature"], memory_id=memory["id"], author_class=memory["author_class"]
            )
        )

    def test_rebuild_restores_tampered_authorship(self) -> None:
        self._capture(
            "The migration scripts run inside a transaction with a rollback checkpoint step.",
            source="claude",
        )
        memory = [m for m in self._memories() if m["author_class"] == "agent"][0]
        # Attacker (or careless edit) promotes the agent memory to 'user' in the vault note.
        self.store.vault.patch_memory(memory["id"], {"author_class": "user"})
        self.store.rebuild_index_from_vault(self.user_id)
        rebuilt = [m for m in self._memories() if m["id"] == memory["id"]][0]
        self.assertEqual(rebuilt["author_class"], "agent", "signed class must win over frontmatter")
        self.assertTrue(self._events("authorship_tamper_detected"))
        # And the derived trust matches the signed class, not the forged one.
        self.assertEqual(
            rebuilt["trust_score"],
            compute_trust_score(
                author_class="agent",
                has_citation=bool(str(rebuilt.get("source_url") or "").strip()),
                occurrences=rebuilt["occurrences"],
                superseded=bool(str(rebuilt.get("superseded_by") or "").strip()),
            ),
        )

    def test_rebuild_converges_trust_scores(self) -> None:
        self._capture("I prefer meetings scheduled in the afternoon rather than the morning.")
        before = {m["id"]: m["trust_score"] for m in self._memories()}
        self.store.rebuild_index_from_vault(self.user_id)
        after = {m["id"]: m["trust_score"] for m in self._memories()}
        self.assertEqual(before, after)

    def test_authorship_key_is_stable(self) -> None:
        key1 = self.store.vault.authorship_key()
        key2 = self.store.vault.authorship_key()
        self.assertEqual(key1, key2)
        self.assertEqual(len(key1), 32)

    # -- belief timeline ------------------------------------------------------------------------

    def test_belief_timeline_traces_supersession_chain(self) -> None:
        self._capture(
            "The recommendation engine uses collaborative filtering for the ranking model.",
            source="claude",
        )
        old = [m for m in self._memories() if m["author_class"] == "agent"][0]
        self._capture(
            "I decided the recommendation engine now uses a transformer ranking model."
        )
        new = [m for m in self._memories() if m["author_class"] == "user"][0]
        self.assertTrue(
            self.store.resolve_conflict(self.user_id, stale_id=old["id"], current_id=new["id"])
        )
        timeline = self.store.get_belief_timeline(self.user_id, "recommendation engine ranking")
        self.assertEqual(timeline["topic"], "recommendation engine ranking")
        revised = [t for t in timeline["timelines"] if t["revised"]]
        self.assertTrue(revised, timeline)
        chain = revised[0]
        self.assertEqual(chain["current_memory_id"], new["id"])
        ids = [rev["memory_id"] for rev in chain["revisions"]]
        self.assertEqual(ids[0], new["id"], "current belief first")
        self.assertIn(old["id"], ids)
        stale_rev = [rev for rev in chain["revisions"] if rev["memory_id"] == old["id"]][0]
        self.assertEqual(stale_rev["superseded_by"], new["id"])
        self.assertEqual(stale_rev["author_class"], "agent")
        # Every revision carries authorship + trust for auditability.
        for rev in chain["revisions"]:
            self.assertIn("trust_score", rev)
            self.assertIn("captured_at", rev)

    def test_belief_timeline_empty_topic(self) -> None:
        result = self.store.get_belief_timeline(self.user_id, "   ")
        self.assertEqual(result["timelines"], [])

    def test_belief_timeline_mcp_tool(self) -> None:
        self._capture("I decided the search index rebuilds nightly at two in the morning.")
        result = mcp_tools.call_tool(
            self.store, self.user_id, "get_belief_timeline", {"topic": "search index rebuild"}
        )
        self.assertIn("timelines", result)
        self.assertIn("get_belief_timeline", mcp_tools.READ_TOOLS)
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        self.assertIn("get_belief_timeline", names)

    # -- pack surfacing ---------------------------------------------------------------------------

    def test_pack_items_expose_authorship_and_trust(self) -> None:
        self._capture(
            "We decided to use Postgres for the ledger service after comparing options.",
            source_url="https://example.com/notes/ledger-db",
        )
        pack = mcp_tools.call_tool(
            self.store, self.user_id, "get_context", {"task": "ledger database work"}
        )
        items = [item for layer in pack.get("layers", []) for item in layer.get("items", [])]
        self.assertTrue(items)
        for item in items:
            self.assertIn("author_class", item)
            self.assertIn("trust_score", item)


if __name__ == "__main__":
    unittest.main()
