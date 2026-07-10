from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app import mcp_tools
from backend.app.database import init_db
from backend.app.storage import CortexStore, connect


class CalibratedMetacognitionTests(unittest.TestCase):
    """M6: Cortex must know when its memory can and cannot answer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "m6-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_memory(
        self,
        memory_id: str,
        content: str,
        *,
        layer: str = "semantic",
        kind: str = "observation",
        occurred_at: str = "2026-07-01T00:00:00Z",
    ) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="note",
            source_url=f"cortex-capture://{memory_id}",
            title=None,
            extracted={
                "summary": content[:120],
                "records": [
                    {
                        "id": memory_id,
                        "kind": kind,
                        "layer": layer,
                        "content": content,
                        "confidence": "confirmed",
                        "importance": 4,
                        "occurred_at": occurred_at,
                        "topics": [],
                        "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _record_prediction(
        self,
        prediction_id: str,
        *,
        confidence: float,
        known_unknown: bool,
        outcome: str,
        answerability: str | None = None,
    ) -> None:
        verdict = "insufficient_evidence" if known_unknown else "likely_yes"
        with connect(self.db_path) as conn:
            self.store._event(
                conn,
                self.user_id,
                prediction_id,
                "twin",
                "twin_prediction",
                {
                    "question": prediction_id,
                    "verdict": verdict,
                    "confidence": confidence,
                    "raw_confidence": confidence,
                    "known_unknown": known_unknown,
                    "confidence_threshold": 0.6,
                    "evidence_count": 0 if known_unknown else 1,
                    "cited_memory_ids": [] if known_unknown else ["mem_fixture"],
                },
            )
            self.store._event(
                conn,
                self.user_id,
                prediction_id,
                "twin",
                "twin_prediction_graded",
                {
                    "outcome": outcome,
                    "answerability": answerability,
                    "actual": None,
                    "predicted_verdict": verdict,
                    "question": prediction_id,
                },
            )

    def test_ask_returns_high_confidence_for_cited_answer(self) -> None:
        self._seed_memory("mem_atlas_code", "Project Atlas launch code is amber quartz")

        result = self.store.answer_query(self.user_id, "What is the Project Atlas launch code?")

        self.assertTrue(result["citations"])
        self.assertFalse(result["known_unknown"])
        self.assertGreaterEqual(result["confidence"], result["confidence_detail"]["threshold"])
        self.assertEqual(result["confidence_detail"]["method"], "heuristic_v1")
        self.assertIsNone(result["knowledge_gap"])

    def test_ask_returns_explicit_known_unknown_for_missing_fact(self) -> None:
        self._seed_memory("mem_atlas_code", "Project Atlas launch code is amber quartz")

        result = self.store.answer_query(self.user_id, "What is the Zephyr Monolith tax domicile?")

        self.assertEqual(result["status"], "no_cited_evidence")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(result["known_unknown"])
        self.assertEqual(result["knowledge_gap"]["reason"], "no_cited_evidence")
        self.assertIn("capture", result["knowledge_gap"]["suggested_action"].lower())

    def test_would_i_returns_calibrated_confidence_and_persists_it(self) -> None:
        self._seed_memory(
            "mem_tailwind",
            "I prefer tailwind for styling frontend projects",
            layer="preference",
            kind="preference",
        )

        result = self.store.would_i(self.user_id, "Would I use tailwind for a frontend project?")

        self.assertEqual(result["verdict"], "likely_yes")
        self.assertFalse(result["known_unknown"])
        self.assertGreaterEqual(result["confidence"], result["confidence_detail"]["threshold"])
        self.assertEqual(
            set(result["confidence_detail"]["components"]),
            {"coverage", "agreement", "trust", "recency"},
        )
        with connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = ? AND event_type = 'twin_prediction'",
                (self.user_id, result["prediction_id"]),
            ).fetchone()
        metadata = self.store._json_or_empty(row["metadata_json"])
        self.assertEqual(metadata["confidence"], result["confidence"])
        self.assertEqual(metadata["known_unknown"], result["known_unknown"])
        self.assertEqual(metadata["confidence_components"], result["confidence_detail"]["components"])

    def test_would_i_abstention_is_an_explicit_known_unknown(self) -> None:
        result = self.store.would_i(self.user_id, "Would I buy a yacht this summer?")

        self.assertEqual(result["verdict"], "insufficient_evidence")
        self.assertEqual(result["confidence"], 0.0)
        self.assertTrue(result["known_unknown"])
        self.assertEqual(result["knowledge_gap"]["reason"], "insufficient_evidence")

    def test_calibration_scorecard_reports_ece_abstention_and_confident_wrong(self) -> None:
        # Answered + correct, answered + incorrect, abstained + correct, abstained + incorrect.
        self._record_prediction(
            "twin_answer_correct", confidence=0.9, known_unknown=False,
            outcome="correct", answerability="answerable",
        )
        self._record_prediction(
            "twin_answer_wrong", confidence=0.8, known_unknown=False,
            outcome="incorrect", answerability="unknown",
        )
        self._record_prediction(
            "twin_abstain_correct", confidence=0.1, known_unknown=True,
            outcome="correct", answerability="unknown",
        )
        self._record_prediction(
            "twin_abstain_wrong", confidence=0.2, known_unknown=True,
            outcome="incorrect", answerability="answerable",
        )

        calibration = self.store.get_twin_calibration(self.user_id)

        self.assertEqual(calibration["graded_samples"], 4)
        self.assertAlmostEqual(calibration["expected_calibration_error"], 0.4, places=4)
        self.assertAlmostEqual(calibration["brier_score"], 0.325, places=4)
        self.assertEqual(calibration["abstention"]["true_positive"], 1)
        self.assertEqual(calibration["abstention"]["false_positive"], 1)
        self.assertEqual(calibration["abstention"]["false_negative"], 1)
        self.assertEqual(calibration["abstention"]["precision"], 0.5)
        self.assertEqual(calibration["abstention"]["recall"], 0.5)
        self.assertEqual(calibration["confident_wrong"]["count"], 1)
        self.assertEqual(calibration["confident_wrong"]["rate"], 0.5)
        self.assertEqual(calibration["coverage"], 0.5)
        self.assertEqual(calibration["outcome_graded_samples"], 4)
        self.assertEqual(calibration["legacy_unlabeled_samples"], 0)
        self.assertEqual(sum(item["count"] for item in calibration["bins"]), 4)

    def test_correctness_and_answerability_are_orthogonal_labels(self) -> None:
        # Ample evidence can still produce a wrong verdict; that is a confident-wrong answer,
        # not proof that memory was unanswerable. A lucky correct guess can still be unsupported.
        self._record_prediction(
            "twin_supported_but_wrong", confidence=0.9, known_unknown=False,
            outcome="incorrect", answerability="answerable",
        )
        self._record_prediction(
            "twin_lucky_guess", confidence=0.8, known_unknown=False,
            outcome="correct", answerability="unknown",
        )

        calibration = self.store.get_twin_calibration(self.user_id)

        self.assertEqual(calibration["graded_samples"], 2)
        self.assertEqual(calibration["confident_wrong"]["count"], 1)
        self.assertEqual(calibration["abstention"]["false_negative"], 1)
        high_bin = calibration["bins"][4]
        self.assertEqual(high_bin["empirical_answerability"], 0.5)

    def test_calibration_is_honest_when_no_outcomes_exist(self) -> None:
        result = self.store.would_i(self.user_id, "Would I buy a yacht this summer?")
        self.assertTrue(result["known_unknown"])

        calibration = self.store.get_twin_calibration(self.user_id)

        self.assertEqual(calibration["graded_samples"], 0)
        self.assertIsNone(calibration["expected_calibration_error"])
        self.assertIsNone(calibration["brier_score"])
        self.assertIsNone(calibration["abstention"]["precision"])
        self.assertIsNone(calibration["abstention"]["recall"])
        self.assertIsNone(calibration["confident_wrong"]["rate"])
        self.assertEqual(
            {item["prediction_id"] for item in calibration["ungraded_abstentions"]},
            {result["prediction_id"]},
        )

    def test_empirical_calibration_cannot_turn_zero_evidence_into_an_answer(self) -> None:
        # Twenty low-bin abstentions graded "incorrect" teach that this user often expected an
        # answer in that bin. Even then, zero citations remains a hard abstention invariant.
        for index in range(20):
            self._record_prediction(
                f"twin_low_bin_{index}",
                confidence=0.1,
                known_unknown=True,
                outcome="incorrect",
                answerability="answerable",
            )

        ask = self.store.answer_query(self.user_id, "What is the Zephyr Monolith tax domicile?")
        twin = self.store.would_i(self.user_id, "Would I buy a yacht this summer?")

        self.assertEqual(ask["confidence"], 0.0)
        self.assertTrue(ask["known_unknown"])
        self.assertEqual(twin["confidence"], 0.0)
        self.assertTrue(twin["known_unknown"])

    def test_empirical_calibration_activates_after_five_same_bin_outcomes(self) -> None:
        for index in range(5):
            self._record_prediction(
                f"twin_high_bin_{index}",
                confidence=0.9,
                known_unknown=False,
                outcome="correct",
                answerability="answerable",
            )

        calibrated, method, samples = self.store._calibrate_metacognition_confidence(self.user_id, 0.85)

        self.assertEqual(method, "empirical_bin_v1")
        self.assertEqual(samples, 5)
        self.assertGreater(calibrated, 0.85)

    def test_twin_history_never_empirically_calibrates_general_ask(self) -> None:
        self._seed_memory("mem_atlas_code", "Project Atlas launch code is amber quartz")
        before = self.store.answer_query(self.user_id, "What is the Project Atlas launch code?")
        for index in range(10):
            self._record_prediction(
                f"twin_unrelated_{index}", confidence=0.9, known_unknown=False,
                outcome="correct", answerability="answerable",
            )

        after = self.store.answer_query(self.user_id, "What is the Project Atlas launch code?")

        self.assertEqual(after["confidence"], before["confidence"])
        self.assertEqual(after["confidence_detail"]["method"], "heuristic_v1")
        self.assertEqual(after["confidence_detail"]["calibration_samples"], 0)

    def test_coverage_uses_all_predictions_not_only_selectively_graded_ones(self) -> None:
        self._record_prediction(
            "twin_answer_labeled", confidence=0.9, known_unknown=False,
            outcome="correct", answerability="answerable",
        )
        self._record_prediction(
            "twin_answer_unlabeled", confidence=0.8, known_unknown=False,
            outcome="unclear", answerability=None,
        )
        self._record_prediction(
            "twin_abstain_labeled", confidence=0.1, known_unknown=True,
            outcome="correct", answerability="unknown",
        )
        self._record_prediction(
            "twin_abstain_unlabeled", confidence=0.2, known_unknown=True,
            outcome="unclear", answerability=None,
        )

        calibration = self.store.get_twin_calibration(self.user_id)

        self.assertEqual(calibration["coverage"], 0.5)
        self.assertEqual(calibration["graded_samples"], 2)

    def test_legacy_correctness_grades_are_reported_but_not_mislabeled(self) -> None:
        with connect(self.db_path) as conn:
            self.store._event(
                conn,
                self.user_id,
                "twin_legacy",
                "twin",
                "twin_prediction",
                {"question": "legacy", "verdict": "insufficient_evidence", "evidence_count": 0},
            )
            self.store._event(
                conn,
                self.user_id,
                "twin_legacy",
                "twin",
                "twin_prediction_graded",
                {"outcome": "correct", "predicted_verdict": "insufficient_evidence"},
            )

        calibration = self.store.get_twin_calibration(self.user_id)

        self.assertEqual(calibration["outcome_graded_samples"], 1)
        self.assertEqual(calibration["legacy_unlabeled_samples"], 1)
        self.assertEqual(calibration["graded_samples"], 0)
        self.assertIsNone(calibration["expected_calibration_error"])
        self.assertEqual(
            {item["prediction_id"] for item in calibration["unlabeled_answerability"]},
            {"twin_legacy"},
        )

    def test_malformed_legacy_confidence_metadata_does_not_break_scorecard(self) -> None:
        with connect(self.db_path) as conn:
            self.store._event(
                conn,
                self.user_id,
                "twin_malformed",
                "twin",
                "twin_prediction",
                {
                    "question": "legacy",
                    "verdict": "likely_yes",
                    "confidence": "not-a-number",
                    "confidence_threshold": "also-bad",
                },
            )

        scorecard = self.store.get_twin_scorecard(self.user_id)

        self.assertEqual(scorecard["predictions"], 1)
        self.assertEqual(scorecard["calibration"]["graded_samples"], 0)

    def test_twin_scorecard_embeds_the_same_calibration_read_model(self) -> None:
        self._record_prediction(
            "twin_known", confidence=0.9, known_unknown=False,
            outcome="correct", answerability="answerable",
        )

        scorecard = self.store.get_twin_scorecard(self.user_id)
        calibration = self.store.get_twin_calibration(self.user_id)

        self.assertEqual(scorecard["calibration"], calibration)

    def test_mcp_calibration_tool_is_read_scoped_and_callable(self) -> None:
        names = {tool["name"] for tool in mcp_tools.TOOLS}
        self.assertIn("get_twin_calibration", names)
        self.assertEqual(mcp_tools.tool_required_capabilities("get_twin_calibration"), ["read"])

        result = mcp_tools.call_tool(self.store, self.user_id, "get_twin_calibration", {"days": 30})

        self.assertEqual(result["window_days"], 30)
        self.assertEqual(result["graded_samples"], 0)

        prediction = self.store.would_i(self.user_id, "Would I buy a yacht this summer?")
        graded = mcp_tools.call_tool(
            self.store,
            self.user_id,
            "grade_twin_prediction",
            {
                "prediction_id": prediction["prediction_id"],
                "outcome": "correct",
                "answerability": "unknown",
            },
        )
        self.assertEqual(graded["answerability"], "unknown")
        self.assertEqual(self.store.get_twin_calibration(self.user_id)["graded_samples"], 1)


if __name__ == "__main__":
    unittest.main()
