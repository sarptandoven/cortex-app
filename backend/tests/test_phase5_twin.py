from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app import mcp_tools
from backend.app.storage import CortexStore, connect


# ---------------------------------------------------------------------------
# Fixture persona (roadmap Phase 5 test harness): 40 seeded preference/style/
# negative/decision memories, then 20 held-out questions with known answers.
# The hill-climbable objective: cited-prediction accuracy on the held-out set
# and ZERO uncited predictions (every non-insufficient verdict carries evidence).
# ---------------------------------------------------------------------------

_PERSONA: list[tuple[str, str, str]] = [
    # (layer, kind, content)
    ("preference", "preference", "I prefer tailwind for styling frontend projects"),
    ("preference", "preference", "I prefer postgres over mysql for relational storage"),
    ("preference", "preference", "I prefer typescript over plain javascript in web apps"),
    ("preference", "preference", "I prefer dark mode interfaces for daily tools"),
    ("preference", "preference", "I prefer async standups over live meetings"),
    ("preference", "preference", "I prefer pytest for python testing"),
    ("preference", "preference", "I prefer small pull requests under 400 lines"),
    ("preference", "preference", "I prefer espresso in the morning over drip coffee"),
    ("preference", "preference", "I prefer working from coffee shops on fridays"),
    ("preference", "preference", "I prefer direct blunt feedback over softened feedback"),
    ("preference", "preference", "I prefer rust for performance critical services"),
    ("preference", "preference", "I prefer sqlite for local-first desktop apps"),
    ("preference", "preference", "I prefer monorepos for tightly coupled services"),
    ("preference", "preference", "I prefer trunk-based development over long-lived branches"),
    ("preference", "preference", "I prefer keyboard shortcuts over mouse-driven workflows"),
    ("style", "style", "My writing style is concise with short declarative sentences"),
    ("style", "style", "I write emails without corporate filler phrases"),
    ("style", "style", "I use lowercase in casual slack messages"),
    ("style", "style", "My blog posts open with a concrete example, never a definition"),
    ("style", "style", "I sign work emails with just my first name"),
    ("negative", "negative", "Never schedule meetings before 10am"),
    ("negative", "negative", "Never use comic sans in any design"),
    ("negative", "negative", "Avoid mongodb for anything transactional"),
    ("negative", "negative", "Never deploy on fridays"),
    ("negative", "negative", "I dislike exclamation points in professional emails"),
    ("negative", "negative", "Avoid jquery in new frontend code"),
    ("negative", "negative", "Never store secrets in environment files committed to git"),
    ("negative", "negative", "I refuse to attend meetings without an agenda"),
    ("decision", "decision", "Decided to use tailwind for the dashboard project"),
    ("decision", "decision", "Decided to build the sync service in rust"),
    ("decision", "decision", "Chose postgres with pgvector for the hosted search backend"),
    ("decision", "decision", "Decided to adopt trunk-based development for the platform team"),
    ("decision", "decision", "Decided to keep the desktop app local-first on sqlite"),
    ("decision", "decision", "Chose pytest as the standard test runner for backend services"),
    ("decision", "decision", "Decided to move team standups to async slack threads"),
    ("decision", "decision", "Decided to migrate the web app to typescript"),
    ("decision", "decision", "Decided against mongodb for the billing ledger"),
    ("decision", "decision", "Decided to require agendas for all recurring meetings"),
    ("decision", "decision", "Decided to enforce a 400 line soft cap on pull requests"),
    ("decision", "decision", "Decided the marketing site stays javascript-free where possible"),
]

# Held-out questions -> expected verdict bucket. "yes" accepts likely_yes,
# "no" accepts likely_no, "abstain" requires insufficient_evidence.
_HELD_OUT: list[tuple[str, str]] = [
    ("Would I use tailwind for a new frontend project?", "yes"),
    ("Would I pick postgres for a relational database?", "yes"),
    ("Would I write the new web app in typescript?", "yes"),
    ("Would I choose pytest for testing a python backend?", "yes"),
    ("Would I use rust for a performance critical service?", "yes"),
    ("Would I keep a desktop app local-first on sqlite?", "yes"),
    ("Would I adopt trunk-based development?", "yes"),
    ("Would I run standups as async threads?", "yes"),
    ("Would I cap pull requests around 400 lines?", "yes"),
    ("Would I want direct blunt feedback?", "yes"),
    ("Would I use mongodb for a transactional billing ledger?", "no"),
    ("Would I schedule a meeting before 10am?", "no"),
    ("Would I deploy to production on a friday?", "no"),
    ("Would I use jquery in new frontend code?", "no"),
    ("Would I attend a recurring meeting without an agenda?", "no"),
    ("Would I use exclamation points in a professional email?", "no"),
    ("Would I buy a yacht this summer?", "abstain"),
    ("Would I move to portugal next year?", "abstain"),
    ("Would I take up oil painting?", "abstain"),
    ("Would I invest in commercial real estate?", "abstain"),
]

# Accuracy gate (roadmap: start at a defensible floor and hill-climb).
_ACCURACY_TARGET = 0.8


class _TwinFixture(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "twin-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, layer: str, kind: str, content: str) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="note",
            # Personal layers classify as user via the authorship gate; the capture URL
            # marks deliberate self-authorship for decision-layer facts too.
            source_url=f"cortex-capture://{memory_id}",
            title=None,
            extracted={
                "summary": content[:80],
                "records": [
                    {"id": memory_id, "kind": kind, "layer": layer, "content": content,
                     "confidence": "confirmed", "importance": 4,
                     "occurred_at": "2026-01-01T00:00:00Z", "topics": [], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _seed_persona(self) -> None:
        for index, (layer, kind, content) in enumerate(_PERSONA):
            self._seed(f"twin-mem-{index:03d}", layer, kind, content)


class TwinPredictionTests(_TwinFixture):
    """Phase 5.1: would_i contract — cited verdicts or honest abstention."""

    def test_persona_seeded_as_user_authored(self) -> None:
        self._seed_persona()
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM memories WHERE user_id = ? AND status = 'active' AND author_class = 'user'",
                (self.user_id,),
            ).fetchone()
        self.assertEqual(row["n"], len(_PERSONA))

    def test_held_out_accuracy_and_zero_uncited_predictions(self) -> None:
        """The headline objective: >=80% verdict accuracy on 20 held-out questions and
        zero uncited predictions (every non-abstain verdict must carry evidence)."""
        self._seed_persona()
        correct = 0
        for question, expected in _HELD_OUT:
            result = self.store.would_i(self.user_id, question)
            verdict = result["verdict"]
            if verdict != "insufficient_evidence":
                # The no-uncited-predictions invariant: a verdict without citations is a bug.
                self.assertGreater(
                    len(result["supporting"]) + len(result["opposing"]), 0,
                    f"uncited prediction for {question!r}",
                )
                for item in result["supporting"] + result["opposing"]:
                    self.assertTrue(item["memory_id"], f"citation missing memory_id for {question!r}")
            if expected == "yes" and verdict == "likely_yes":
                correct += 1
            elif expected == "no" and verdict == "likely_no":
                correct += 1
            elif expected == "abstain" and verdict == "insufficient_evidence":
                correct += 1
        accuracy = correct / len(_HELD_OUT)
        self.assertGreaterEqual(
            accuracy, _ACCURACY_TARGET,
            f"twin verdict accuracy {accuracy:.2f} below target {_ACCURACY_TARGET}",
        )

    def test_insufficient_evidence_on_empty_corpus(self) -> None:
        result = self.store.would_i(self.user_id, "Would I use tailwind?")
        self.assertEqual(result["verdict"], "insufficient_evidence")
        self.assertEqual(result["supporting"], [])
        self.assertEqual(result["opposing"], [])

    def test_prediction_persisted_as_event(self) -> None:
        self._seed_persona()
        result = self.store.would_i(self.user_id, "Would I use tailwind for a frontend project?")
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = ? AND event_type = 'twin_prediction'",
                (self.user_id, result["prediction_id"]),
            ).fetchone()
        self.assertIsNotNone(row)
        metadata = self.store._json_or_empty(row["metadata_json"])
        self.assertEqual(metadata["verdict"], result["verdict"])
        self.assertTrue(metadata["cited_memory_ids"])

    def test_empty_question_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.would_i(self.user_id, "   ")

    def test_hard_constraints_are_user_authored_only(self) -> None:
        self._seed_persona()
        # Agent-authored negative: must never appear as a hard constraint (roadmap 5.3).
        self.store.save_capture(
            user_id=self.user_id,
            content="Never deploy on fridays under any circumstances",
            source="claude",
            source_url="cortex-session://asess_test",
            title=None,
            extracted={
                "summary": "agent-reported friday veto",
                "records": [
                    {"id": "agent-neg-1", "kind": "negative", "layer": "negative",
                     "content": "Never deploy on fridays under any circumstances",
                     "confidence": "stated", "importance": 3,
                     "occurred_at": "2026-01-02T00:00:00Z", "topics": [], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )
        result = self.store.would_i(self.user_id, "Would I deploy to production on a friday?")
        for constraint in result["hard_constraints"]:
            self.assertEqual(constraint["author_class"], "user")


class TwinGradingLoopTests(_TwinFixture):
    """Phase 5.4: the self-labeling accuracy loop."""

    def test_grade_and_scorecard_roundtrip(self) -> None:
        self._seed_persona()
        first = self.store.would_i(self.user_id, "Would I use tailwind for a frontend project?")
        second = self.store.would_i(self.user_id, "Would I deploy to production on a friday?")
        third = self.store.would_i(self.user_id, "Would I pick postgres for a relational database?")
        self.store.grade_twin_prediction(self.user_id, first["prediction_id"], "correct")
        self.store.grade_twin_prediction(self.user_id, second["prediction_id"], "incorrect", actual="shipped a hotfix friday night")
        scorecard = self.store.get_twin_scorecard(self.user_id)
        self.assertEqual(scorecard["predictions"], 3)
        self.assertEqual(scorecard["graded"], 2)
        self.assertEqual(scorecard["accuracy"], 0.5)
        ungraded_ids = {p["prediction_id"] for p in scorecard["ungraded"]}
        self.assertEqual(ungraded_ids, {third["prediction_id"]})

    def test_grade_unknown_prediction_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.grade_twin_prediction(self.user_id, "twin_nonexistent", "correct")

    def test_grade_invalid_outcome_rejected(self) -> None:
        self._seed_persona()
        result = self.store.would_i(self.user_id, "Would I use tailwind for a frontend project?")
        with self.assertRaises(ValueError):
            self.store.grade_twin_prediction(self.user_id, result["prediction_id"], "maybe")

    def test_insufficient_evidence_excluded_from_grading_queue(self) -> None:
        self.store.would_i(self.user_id, "Would I buy a yacht?")
        scorecard = self.store.get_twin_scorecard(self.user_id)
        self.assertEqual(scorecard["predictions"], 1)
        self.assertEqual(scorecard["ungraded"], [])


class DraftAsMeTests(_TwinFixture):
    """Phase 5.2: the voice pack is compiled evidence, never a generated draft."""

    def test_voice_pack_shape_and_citations(self) -> None:
        self._seed_persona()
        pack = self.store.draft_as_me(self.user_id, "reply to a work email about the frontend migration", medium="email")
        self.assertEqual(pack["medium"], "email")
        self.assertTrue(pack["voice"], "expected style evidence in the voice pack")
        for section in ("voice", "preferences", "hard_constraints", "relevant_context"):
            for item in pack[section]:
                self.assertTrue(item["memory_id"], f"uncited item in {section}")
        # No generated text: the pack carries instructions for the CALLING agent only.
        self.assertNotIn("draft", pack)

    def test_hard_constraints_present_for_email_prompt(self) -> None:
        self._seed_persona()
        pack = self.store.draft_as_me(self.user_id, "professional email with exclamation points about the launch")
        contents = " ".join(c["content"] for c in pack["hard_constraints"]).lower()
        self.assertIn("exclamation", contents)

    def test_empty_prompt_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.draft_as_me(self.user_id, "")

    def test_no_duplicate_memory_ids_across_sections(self) -> None:
        self._seed_persona()
        pack = self.store.draft_as_me(self.user_id, "write a blog post about postgres and testing")
        seen: set[str] = set()
        for section in ("voice", "preferences", "relevant_context"):
            for item in pack[section]:
                self.assertNotIn(item["memory_id"], seen)
                seen.add(item["memory_id"])


class TwinToolSurfaceTests(_TwinFixture):
    """Cross-cutting rule 1 and 2: single-sourced tool specs, correct scopes, callable
    through the real call_tool dispatcher."""

    def test_tools_registered_with_scopes(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        for name in ("would_i", "draft_as_me", "grade_twin_prediction", "get_twin_scorecard"):
            self.assertIn(name, names)
        for name in ("would_i", "draft_as_me", "get_twin_scorecard"):
            self.assertEqual(mcp_tools.tool_required_capabilities(name), ["read"])
        self.assertEqual(mcp_tools.tool_required_capabilities("grade_twin_prediction"), ["write"])

    def test_call_tool_dispatch(self) -> None:
        self._seed_persona()
        result = mcp_tools.call_tool(
            self.store, self.user_id, "would_i",
            {"question": "Would I use tailwind for a frontend project?"},
        )
        self.assertIn(result["verdict"], {"likely_yes", "likely_no", "mixed", "insufficient_evidence"})
        graded = mcp_tools.call_tool(
            self.store, self.user_id, "grade_twin_prediction",
            {"prediction_id": result["prediction_id"], "outcome": "correct"},
        )
        self.assertEqual(graded["outcome"], "correct")
        pack = mcp_tools.call_tool(
            self.store, self.user_id, "draft_as_me",
            {"prompt": "email about the migration", "medium": "email"},
        )
        self.assertIn("hard_constraints", pack)
        scorecard = mcp_tools.call_tool(self.store, self.user_id, "get_twin_scorecard", {})
        self.assertEqual(scorecard["graded"], 1)

    def test_chat_surface_advertises_twin_tools(self) -> None:
        surface = mcp_tools.MCP_TOOL_SURFACES["chat"]
        self.assertIn("would_i", surface)
        self.assertIn("draft_as_me", surface)


if __name__ == "__main__":
    unittest.main()
