from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app import mcp_tools
from backend.app.storage import CortexStore, connect


class Phase4EvalHarnessTests(unittest.TestCase):
    """Personal eval harness: request-side scorecard (pure read-model over the mcp:{tool}
    event log) and deterministic answer grading (claims checked against active memory with
    citations). The grader is regex-based on purpose — reproducible and golden-set testable
    before any LLM judging is ever considered (roadmap risk register, Phase 4)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase4-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_memory(self, memory_id: str, content: str, occurred_at: str = "2026-01-01T00:00:00Z") -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="note",
            source_url=None,
            title=None,
            extracted={
                "summary": content[:80],
                "records": [
                    {"id": memory_id, "kind": "decision", "layer": "decision", "content": content,
                     "confidence": "confirmed", "importance": 4, "occurred_at": occurred_at,
                     "topics": ["project"], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _token(self, token_id: str, label: str) -> dict:
        return {"token_id": token_id, "label": label, "audience": "mcp", "scopes": ["read", "write"]}

    # -- record_agent_event result enrichment ----------------------------------------------

    def test_coverage_status_and_conflicts_persisted_from_result(self) -> None:
        result = {"coverage": {"status": "strong"}, "conflicts": [{"field": "database"}]}
        self.store.record_agent_event(
            self.user_id, "get_context", {"task": "x"}, success=True,
            token=self._token("tok_a", "Claude"), result=result,
        )
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = 'mcp:get_context'",
                (self.user_id,),
            ).fetchone()
        metadata = self.store._json_or_empty(row["metadata_json"])
        self.assertEqual(metadata["coverage_status"], "strong")
        self.assertEqual(metadata["conflicts_served"], 1)

    def test_non_dict_result_is_harmless(self) -> None:
        self.store.record_agent_event(self.user_id, "get_context", {}, success=True, result="markdown text")
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = 'mcp:get_context'",
                (self.user_id,),
            ).fetchone()
        metadata = self.store._json_or_empty(row["metadata_json"])
        self.assertNotIn("coverage_status", metadata)

    # -- scorecard tool-kind classification stays in sync with mcp_tools -------------------

    def test_scorecard_classification_parity_with_mcp_tools(self) -> None:
        # storage must not import the tool layer, so the prefix classifier is duplicated
        # there. This parity test is the drift guard the comment in storage.py promises.
        for name in mcp_tools.READ_TOOLS:
            self.assertEqual(self.store._scorecard_tool_kind(name), "read", name)
        for name in mcp_tools.WRITE_TOOLS:
            self.assertEqual(self.store._scorecard_tool_kind(name), "write", name)

    # -- get_tool_scorecard ------------------------------------------------------------------

    def test_scorecard_groups_by_token_and_computes_usage_rate(self) -> None:
        token = self._token("tok_a", "Claude Desktop")
        # Read before write on the day -> memory_usage_rate 1.0 for tok_a.
        self.store.record_agent_event(self.user_id, "get_context", {}, success=True, token=token,
                                      result={"coverage": {"status": "strong"}, "conflicts": []})
        self.store.record_agent_event(self.user_id, "remember_this", {"content": "x"}, success=True, token=token)
        # tok_b writes without ever reading -> rate 0.0.
        token_b = self._token("tok_b", "Other Host")
        self.store.record_agent_event(self.user_id, "remember_this", {"content": "y"}, success=True, token=token_b)

        scorecard = self.store.get_tool_scorecard(self.user_id)
        hosts = {host["token_id"]: host for host in scorecard["hosts"]}
        self.assertEqual(hosts["tok_a"]["memory_usage_rate"], 1.0)
        self.assertEqual(hosts["tok_a"]["coverage_mix"], {"strong": 1})
        self.assertEqual(hosts["tok_a"]["read_calls"], 1)
        self.assertEqual(hosts["tok_a"]["write_calls"], 1)
        self.assertEqual(hosts["tok_b"]["memory_usage_rate"], 0.0)
        self.assertTrue(scorecard["caveats"])

    def test_scorecard_counts_conflict_exposure_and_failures(self) -> None:
        token = self._token("tok_a", "Claude")
        self.store.record_agent_event(self.user_id, "get_context", {}, success=True, token=token,
                                      result={"conflicts": [{"a": 1}, {"b": 2}]})
        self.store.record_agent_event(self.user_id, "get_context", {}, success=False, error="boom", token=token)
        host = self.store.get_tool_scorecard(self.user_id)["hosts"][0]
        self.assertEqual(host["conflict_packs_served"], 1)
        self.assertEqual(host["failed_calls"], 1)
        self.assertEqual(host["calls"], 2)

    def test_scorecard_token_filter_and_untokened_bucket(self) -> None:
        self.store.record_agent_event(self.user_id, "get_context", {}, success=True)
        self.store.record_agent_event(self.user_id, "get_context", {}, success=True, token=self._token("tok_a", "A"))
        all_hosts = self.store.get_tool_scorecard(self.user_id)["hosts"]
        self.assertEqual({host["token_id"] for host in all_hosts}, {None, "tok_a"})
        filtered = self.store.get_tool_scorecard(self.user_id, token_id="tok_a")["hosts"]
        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["token_id"], "tok_a")

    def test_scorecard_accrues_grading_results(self) -> None:
        token = self._token("tok_a", "Claude")
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        result = self.store.grade_answer(self.user_id, "The database is postgres. The launch channel is discord.")
        self.store.record_agent_event(
            self.user_id, "submit_answer_for_grading", {"answer_text": "..."},
            success=True, token=token, result=result,
        )
        host = self.store.get_tool_scorecard(self.user_id, token_id="tok_a")["hosts"][0]
        grading = host["grading"]
        self.assertEqual(grading["submissions"], 1)
        self.assertEqual(grading["consistent"], 1)
        self.assertEqual(grading["unsupported"], 1)
        self.assertEqual(grading["faithfulness_rate"], 0.5)

    # -- grade_answer -------------------------------------------------------------------------

    def test_grade_answer_consistent_claim_cites_memory(self) -> None:
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        result = self.store.grade_answer(self.user_id, "We decided the database is postgres.")
        self.assertEqual(len(result["consistent"]), 1)
        self.assertEqual(result["consistent"][0]["field"], "database")
        self.assertTrue(result["consistent"][0]["citations"][0]["memory_id"])
        self.assertEqual(result["faithfulness_rate"], 1.0)

    def test_grade_answer_contradicted_claim_cites_current_memory(self) -> None:
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        result = self.store.grade_answer(self.user_id, "The database is sqlite.")
        self.assertEqual(len(result["contradicted"]), 1)
        claim = result["contradicted"][0]
        self.assertEqual(claim["claimed"], "sqlite")
        self.assertEqual(claim["expected"], "postgres")
        self.assertTrue(claim["citations"])

    def test_grade_answer_unsupported_claim_has_no_citations(self) -> None:
        result = self.store.grade_answer(self.user_id, "The launch channel is discord.")
        self.assertEqual(len(result["unsupported"]), 1)
        self.assertEqual(result["consistent"], [])
        self.assertIsNone(result["faithfulness_rate"]) if result["claims_extracted"] == 0 else None

    def test_grade_answer_ignores_superseded_memories(self) -> None:
        self._seed_memory("mem_old", "The database is sqlite for the launch.", "2026-01-01T00:00:00Z")
        self._seed_memory("mem_new", "The database is postgres for the launch.", "2026-06-01T00:00:00Z")
        conflicts = self.store.detect_conflicts(self.user_id)
        self.assertTrue(conflicts)
        self.assertTrue(
            self.store.resolve_conflict(
                self.user_id,
                stale_id=conflicts[0]["stale"]["memory_id"],
                current_id=conflicts[0]["current"]["memory_id"],
            )
        )
        result = self.store.grade_answer(self.user_id, "The database is sqlite.")
        self.assertEqual(len(result["contradicted"]), 1)
        self.assertEqual(result["contradicted"][0]["expected"], "postgres")

    def test_grade_answer_requires_text(self) -> None:
        with self.assertRaises(ValueError):
            self.store.grade_answer(self.user_id, "   ")

    def test_grade_answer_writes_audit_event(self) -> None:
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        self.store.grade_answer(self.user_id, "The database is postgres.", session_id="asess_1", pack_sha="abc")
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND event_type = 'answer_graded'",
                (self.user_id,),
            ).fetchall()
        self.assertEqual(len(rows), 1)
        metadata = self.store._json_or_empty(rows[0]["metadata_json"])
        self.assertEqual(metadata["session_id"], "asess_1")
        self.assertEqual(metadata["pack_sha"], "abc")
        self.assertEqual(metadata["consistent"], 1)

    def test_grade_answer_deterministic(self) -> None:
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        answer = "The database is postgres. The owner is Dana. The budget is unknown to me."
        first = self.store.grade_answer(self.user_id, answer)
        second = self.store.grade_answer(self.user_id, answer)
        for key in ("consistent", "contradicted", "unsupported", "claims_extracted", "faithfulness_rate"):
            self.assertEqual(first[key], second[key], key)

    # -- MCP tool wiring ----------------------------------------------------------------------

    def test_tools_registered_with_correct_scopes(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        self.assertIn("get_tool_scorecard", names)
        self.assertIn("submit_answer_for_grading", names)
        self.assertIn("get_tool_scorecard", mcp_tools.READ_TOOLS)
        self.assertIn("submit_answer_for_grading", mcp_tools.WRITE_TOOLS)
        self.assertNotIn("submit_answer_for_grading", mcp_tools.EXPORT_TOOLS)
        self.assertNotIn("submit_answer_for_grading", mcp_tools.DESTRUCTIVE_TOOLS)

    def test_call_tool_scorecard_and_grading(self) -> None:
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        graded = mcp_tools.call_tool(
            self.store, self.user_id, "submit_answer_for_grading",
            {"answer_text": "The database is postgres."},
        )
        self.assertEqual(len(graded["consistent"]), 1)
        scorecard = mcp_tools.call_tool(self.store, self.user_id, "get_tool_scorecard", {})
        self.assertIn("hosts", scorecard)

    def test_call_tool_grading_requires_answer_text(self) -> None:
        with self.assertRaises(ValueError):
            mcp_tools.call_tool(self.store, self.user_id, "submit_answer_for_grading", {"answer_text": ""})


class Phase4GoldenSetEvalTests(unittest.TestCase):
    """Golden-set eval (roadmap Phase 4 test harness): a fixture corpus of user facts plus
    scripted answers with KNOWN planted contradictions and unsupported claims. Asserts grader
    precision/recall against hill-climbable targets (0.8 precision / 0.7 recall to start).
    Raise the targets as the grader improves — never lower them."""

    PRECISION_TARGET = 0.8
    RECALL_TARGET = 0.7

    # (field, true value, distractor value) — every field is one the deterministic claim
    # grammar understands, so this measures grading quality, not extraction vocabulary.
    FACTS = [
        ("database", "postgres", "sqlite"),
        ("storage backend", "sqlite", "postgres"),
        ("retrieval engine", "hybrid", "fts"),
        ("owner", "dana", "alex"),
        ("backup owner", "miguel", "dana"),
        ("incident owner", "priya", "miguel"),
        ("support owner", "alex", "priya"),
        ("handoff owner", "sam", "lee"),
        ("launch channel", "discord", "slack"),
        ("release channel", "beta", "stable"),
        ("primary connector", "gmail", "slack"),
        ("default connector", "github", "notion"),
        ("primary source", "obsidian", "notion"),
    ]

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "phase4-golden"
        self.store.update_settings(self.user_id, {"review_new_captures": False})
        for index, (field, value, _) in enumerate(self.FACTS):
            content = f"The {field} is {value}."
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source="note",
                source_url=None,
                title=None,
                extracted={
                    "summary": content,
                    "records": [
                        {"id": f"mem_gold_{index}", "kind": "decision", "layer": "decision",
                         "content": content, "confidence": "confirmed", "importance": 4,
                         "occurred_at": "2026-01-01T00:00:00Z", "topics": ["golden"], "entity_ids": []}
                    ],
                    "tasks": [],
                    "entities": [],
                },
            )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_grader_precision_and_recall_meet_targets(self) -> None:
        # Scripted answers: half state the truth, half plant the distractor (a contradiction).
        # Positive class = "contradicted". A perfect grader flags exactly the planted halves.
        true_positive = false_positive = false_negative = 0
        for index, (field, value, distractor) in enumerate(self.FACTS):
            planted = index % 2 == 0
            claim_value = distractor if planted else value
            answer = f"Based on your memory, the {field} is {claim_value}."
            result = self.store.grade_answer(self.user_id, answer)
            flagged = {claim["field"] for claim in result["contradicted"]}
            if planted:
                if field in flagged:
                    true_positive += 1
                else:
                    false_negative += 1
            elif field in flagged:
                false_positive += 1
        precision = true_positive / (true_positive + false_positive) if (true_positive + false_positive) else 0.0
        recall = true_positive / (true_positive + false_negative) if (true_positive + false_negative) else 0.0
        self.assertGreaterEqual(precision, self.PRECISION_TARGET, f"precision {precision:.2f} (tp={true_positive}, fp={false_positive})")
        self.assertGreaterEqual(recall, self.RECALL_TARGET, f"recall {recall:.2f} (tp={true_positive}, fn={false_negative})")

    def test_unsupported_claims_are_not_misfiled(self) -> None:
        # A claim about a field with no memory must land in unsupported — never contradicted
        # (that would gaslight the agent) and never consistent (that would invent support).
        result = self.store.grade_answer(self.user_id, "The deadline is march 3, and the budget is 40k.")
        fields = {claim["field"] for claim in result["unsupported"]}
        self.assertEqual(fields, {"deadline", "budget"})
        self.assertEqual(result["contradicted"], [])
        self.assertEqual(result["consistent"], [])

    def test_multi_claim_answer_grades_every_claim(self) -> None:
        answer = (
            "The database is postgres. The owner is alex. The launch channel is discord. "
            "The budget is 12k."
        )
        result = self.store.grade_answer(self.user_id, answer)
        self.assertEqual({claim["field"] for claim in result["consistent"]}, {"database", "launch channel"})
        self.assertEqual({claim["field"] for claim in result["contradicted"]}, {"owner"})
        self.assertEqual({claim["field"] for claim in result["unsupported"]}, {"budget"})
        self.assertEqual(result["claims_extracted"], 4)


if __name__ == "__main__":
    unittest.main()
