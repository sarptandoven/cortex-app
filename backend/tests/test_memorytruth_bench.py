"""Tests for the MemoryTruth-light benchmark harness (backend/bench/memorytruth.py).

Three properties make the bench trustworthy, and each gets a test class:

1. Determinism - the same seed must produce the identical scenario and, on the
   same code, the identical report. A benchmark with run-to-run variance at a
   fixed seed cannot be hill-climbed.
2. Honesty floors - on the current store, every category must hold its floor
   (1.0 on the reference seeds). A regression in retrieval, the citation gate,
   supersession, or the integrity chain shows up here as a floor break.
3. Non-tautology - the bench must actually be able to FAIL. We sabotage the
   store in targeted ways (confabulating gate, leaking supersession filter,
   tampered integrity event, overconfident unknowns, lying calibration report)
   and assert the matching category score drops.
   Without these, a "1.0" would prove nothing about the pipeline.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, connect
from backend.bench.memorytruth import (
    InProcessClient,
    _finite_confidence,
    _independent_metacognition_metrics,
    _metacognition_scorecard_mismatches,
    generate_scenario,
    run_bench,
)


def _fresh_store(root: Path) -> CortexStore:
    init_db(root / "bench.db")
    store = CortexStore(root / "bench.db", root / "vault")
    store._vector_ready = lambda conn: False  # deterministic lexical retrieval
    return store


class ScenarioDeterminismTests(unittest.TestCase):
    def test_same_seed_same_scenario(self) -> None:
        a = generate_scenario(7)
        b = generate_scenario(7)
        self.assertEqual(a.to_json(), b.to_json())

    def test_different_seed_different_scenario(self) -> None:
        a = generate_scenario(7)
        b = generate_scenario(8)
        self.assertNotEqual(a.to_json(), b.to_json())

    def test_slugs_are_unique_within_scenario(self) -> None:
        scenario = generate_scenario(7)
        slugs = [fact["slug"] for fact in scenario.facts]
        slugs += [rev["v1_slug"] for rev in scenario.revisions]
        slugs += [rev["v2_slug"] for rev in scenario.revisions]
        slugs += [node["slug"] for node in scenario.canvas_nodes]
        slugs += [case["slug"] for case in scenario.metacognition_cases]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_v4_metacognition_generation_does_not_change_v3_scenario_bytes(self) -> None:
        without_m6 = generate_scenario(7, metacognition_n=0)
        with_m6 = generate_scenario(7)
        self.assertEqual(without_m6.facts, with_m6.facts)
        self.assertEqual(without_m6.revisions, with_m6.revisions)
        self.assertEqual(without_m6.abstention_probes, with_m6.abstention_probes)
        self.assertEqual(without_m6.canvas_nodes, with_m6.canvas_nodes)

    def test_metacognition_gold_is_balanced_and_unknowns_are_never_seeded(self) -> None:
        scenario = generate_scenario(7)
        answerable = [case for case in scenario.metacognition_cases if case["answerability"] == "answerable"]
        unknown = [case for case in scenario.metacognition_cases if case["answerability"] == "unknown"]
        self.assertEqual(len(answerable), len(unknown))
        self.assertTrue(answerable)
        self.assertTrue(all(case["content"] for case in answerable))
        self.assertTrue(all(case["content"] is None for case in unknown))
        seeded = "\n".join(
            [fact["content"] for fact in scenario.facts]
            + [revision["v1_content"] for revision in scenario.revisions]
            + [revision["v2_content"] for revision in scenario.revisions]
            + [str(case["content"]) for case in answerable]
        )
        for case in unknown:
            self.assertNotIn(case["slug"], seeded)
            self.assertNotIn(case["project"], seeded)

    def test_abstention_projects_never_overlap_seeded_facts(self) -> None:
        scenario = generate_scenario(7)
        seeded_text = " ".join(fact["content"] for fact in scenario.facts)
        for probe in scenario.abstention_probes:
            # The probe names a project ("adjective-noun"); that compound must not
            # appear in any seeded fact, or the probe would not be a true unknown.
            project = probe.question.split(" for the ")[1].split(" project")[0]
            self.assertNotIn(project, seeded_text)

    def test_same_seed_same_report_on_same_code(self) -> None:
        reports = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                store = _fresh_store(Path(tmp))
                client = InProcessClient(store, "bench-user")
                report = run_bench(client, generate_scenario(11), mode="inprocess")
                report.pop("duration_seconds")
                report.pop("belief_proof_latency")  # wall-clock, informational only
                reports.append(report)
        self.assertEqual(reports[0], reports[1])


class IndependentMetacognitionMetricTests(unittest.TestCase):
    def test_mixed_fixture_matches_hand_calculated_metrics_and_bin_boundaries(self) -> None:
        metrics = _independent_metacognition_metrics(
            [
                {"answerability": "unknown", "confidence": 0.0, "known_unknown": True, "outcome": "correct"},
                {"answerability": "answerable", "confidence": 0.2, "known_unknown": True, "outcome": "incorrect"},
                {"answerability": "unknown", "confidence": 0.6, "known_unknown": False, "outcome": "incorrect"},
                {"answerability": "answerable", "confidence": 1.0, "known_unknown": False, "outcome": "correct"},
            ]
        )

        self.assertEqual(metrics["prediction_samples"], 4)
        self.assertEqual(metrics["graded_samples"], 4)
        self.assertEqual(metrics["expected_calibration_error"], 0.35)
        self.assertEqual(metrics["brier_score"], 0.25)
        self.assertEqual([item["count"] for item in metrics["bins"]], [1, 1, 0, 1, 1])
        self.assertEqual(metrics["bins"][1]["absolute_error"], 0.8)
        self.assertEqual(metrics["bins"][3]["absolute_error"], 0.6)
        self.assertEqual(metrics["abstention"]["true_positive"], 1)
        self.assertEqual(metrics["abstention"]["false_positive"], 1)
        self.assertEqual(metrics["abstention"]["false_negative"], 1)
        self.assertEqual(metrics["abstention"]["precision"], 0.5)
        self.assertEqual(metrics["abstention"]["recall"], 0.5)
        self.assertEqual(metrics["confident_wrong"]["count"], 1)
        self.assertEqual(metrics["confident_wrong"]["high_confidence_answered"], 2)
        self.assertEqual(metrics["confident_wrong"]["rate"], 0.5)
        self.assertEqual(metrics["coverage"], 0.5)

    def test_confidence_parser_rejects_non_numeric_and_out_of_range_values(self) -> None:
        for value in (None, "0.5", True, False, float("nan"), float("inf"), -0.01, 1.01):
            with self.subTest(value=value):
                self.assertIsNone(_finite_confidence(value))
        for value in (0, 0.2, 1):
            with self.subTest(value=value):
                self.assertEqual(_finite_confidence(value), float(value))

    def test_scorecard_parity_rejects_population_contamination(self) -> None:
        metrics = _independent_metacognition_metrics(
            [
                {"answerability": "unknown", "confidence": 0.0, "known_unknown": True, "outcome": "correct"},
                {"answerability": "answerable", "confidence": 1.0, "known_unknown": False, "outcome": "correct"},
            ]
        )
        reported = {
            **metrics,
            "window_days": 365,
            "cohort_prediction_ids": ["twin_a", "twin_b"],
            "ungraded_abstentions": [],
            "unlabeled_answerability": [],
        }
        self.assertEqual(
            _metacognition_scorecard_mismatches(
                reported,
                metrics,
                prediction_ids=["twin_a", "twin_b"],
                window_days=365,
            ),
            [],
        )

        reported["cohort_prediction_ids"] = ["twin_a", "twin_b", "twin_history"]
        reported["prediction_samples"] = 3
        reported["unlabeled_answerability"] = [{"prediction_id": "twin_history"}]
        mismatches = _metacognition_scorecard_mismatches(
            reported,
            metrics,
            prediction_ids=["twin_a", "twin_b"],
            window_days=365,
        )
        self.assertTrue(any(item.startswith("cohort_prediction_ids:") for item in mismatches))
        self.assertTrue(any(item.startswith("prediction_samples:") for item in mismatches))
        self.assertTrue(any(item.startswith("unlabeled_answerability:") for item in mismatches))


class HonestyFloorTests(unittest.TestCase):
    """The current store must hold a perfect score on the reference seeds.

    These floors are the hill-climb objective from docs/MEMORY_MOONSHOTS.md M1.
    If a change to retrieval/citation/supersession/integrity breaks one, this
    test names the category instead of letting the regression ship silently.
    """

    def _run(self, seed: int) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            return run_bench(client, generate_scenario(seed), mode="inprocess")

    def test_reference_seed_holds_all_floors(self) -> None:
        report = self._run(7)
        for category, payload in report["categories"].items():
            self.assertEqual(
                payload["score"],
                1.0,
                msg=f"{category} broke its floor: {payload['failures']}",
            )
        self.assertEqual(report["overall"], 1.0)

    def test_second_seed_holds_all_floors(self) -> None:
        report = self._run(42)
        for category, payload in report["categories"].items():
            self.assertEqual(
                payload["score"],
                1.0,
                msg=f"{category} broke its floor: {payload['failures']}",
            )


class NonTautologyTests(unittest.TestCase):
    """Sabotage the store; the matching category must drop. A bench that cannot
    fail is a tautology, which is exactly the LoCoMo failure mode M1 exists to
    avoid."""

    def test_confabulating_gate_tanks_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            # Sabotage: the citation gate accepts any candidate (always relevant).
            store._primary_citation_is_relevant = lambda *args, **kwargs: True
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["abstention"]["score"],
            1.0,
            msg="bench failed to detect a confabulating citation gate",
        )

    def test_broken_supersession_tanks_temporal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            # Sabotage: resolving a conflict no longer records supersession.
            store.resolve_conflict = lambda user_id, *, stale_id, current_id: True
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["temporal"]["score"],
            1.0,
            msg="bench failed to detect broken supersession (stale fact leaks)",
        )

    def test_tampered_event_log_tanks_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            scenario = generate_scenario(7)

            original_verify = store.verify_integrity

            def tampered_verify(user_id: str, expected_head: str) -> dict:
                # Sabotage BETWEEN pin and verify: the bench pinned the chain head
                # via integrity_digest; now silently rewrite one historical event
                # the way an attacker (or corrupting bug) would, then let the real
                # verification run. Tamper-evidence means exactly this ordering is
                # caught: matches must come back False.
                with connect(store.db_path) as conn:
                    conn.execute(
                        "UPDATE memory_events SET metadata_json = '{\"forged\": true}' "
                        "WHERE id = (SELECT id FROM memory_events WHERE user_id = ? LIMIT 1)",
                        (user_id,),
                    )
                return original_verify(user_id, expected_head)

            store.verify_integrity = tampered_verify
            report = run_bench(client, scenario, mode="inprocess")
        self.assertLess(
            report["categories"]["provenance"]["score"],
            1.0,
            msg="bench failed to detect a tampered integrity chain",
        )

    def test_dead_retrieval_tanks_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            original_answer = store.answer_query

            def broken_answer(user_id: str, query: str, *args, **kwargs) -> dict:
                result = original_answer(user_id, query, *args, **kwargs)
                # Sabotage: retrieval returns nothing citable.
                result["citations"] = []
                result["status"] = "no_cited_evidence"
                result["results"] = []
                return result

            store.answer_query = broken_answer
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertEqual(
            report["categories"]["recall"]["score"],
            0.0,
            msg="bench failed to detect dead retrieval",
        )

    def test_lossy_canvas_drilldown_tanks_canvas(self) -> None:
        # Sabotage: drill-down returns truncated raw evidence (silent evidence
        # loss - Tencent's structural failure mode). The bench compares BYTES
        # against the scenario, so a lossy recovery must tank the category even
        # though the store still reports verified=True internally.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            original = store.get_working_canvas_node

            def lossy(user_id, **kwargs):
                result = original(user_id, **kwargs)
                if result.get("raw_text"):
                    result["raw_text"] = result["raw_text"][: len(result["raw_text"]) // 2]
                return result

            store.get_working_canvas_node = lossy
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["canvas"]["score"],
            1.0,
            msg="bench failed to detect lossy canvas drill-down",
        )

    def test_leaky_canvas_board_tanks_canvas(self) -> None:
        # Sabotage: the compact canvas leaks the raw evidence into context
        # (defeating the whole offload). The no-leak + compactness probes must
        # catch it.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            original = store.get_working_canvas

            def leaky(user_id, **kwargs):
                result = original(user_id, **kwargs)
                blobs = []
                for node in result.get("nodes") or []:
                    raw = store.vault.read_working_canvas_evidence(node["raw_sha256"])
                    if raw:
                        blobs.append(raw.decode("utf-8"))
                result["canvas"] = result["canvas"] + "\n" + "\n".join(blobs)
                return result

            store.get_working_canvas = leaky
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["canvas"]["score"],
            1.0,
            msg="bench failed to detect raw evidence leaking into the compact canvas",
        )

    def test_retconning_store_tanks_belief_proof(self) -> None:
        # Sabotage: the store ignores known_at and always reports its CURRENT
        # belief - the canonical retcon ("we have always been at war with
        # Eastasia"). The retro probe must see v2 leak into the past, and the
        # end-of-run pin must fail because the "pinned" head silently tracked
        # the advancing current head.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            original = store.get_belief_proof

            def retcon(user_id, topic, **kwargs):
                kwargs.pop("known_at", None)
                kwargs.pop("valid_at", None)
                return original(user_id, topic, **kwargs)

            store.get_belief_proof = retcon
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["belief_proof"]["score"],
            1.0,
            msg="bench failed to detect a store that retcons known_at to current state",
        )

    def test_doctored_belief_content_tanks_belief_proof(self) -> None:
        # Sabotage: the store rewrites belief content in its RESPONSES while
        # keeping the sealed receipts intact (content-level retcon). Only the
        # bench's independent snapshot re-hash can catch this; the envelope's
        # chain is untouched and self-consistent.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            original = store.get_belief_proof

            def doctor(user_id, topic, **kwargs):
                result = original(user_id, topic, **kwargs)
                for belief in result.get("beliefs") or []:
                    belief["content"] = "the archives have always said so"
                return result

            store.get_belief_proof = doctor
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["belief_proof"]["score"],
            1.0,
            msg="bench failed to detect doctored belief content with intact receipts",
        )

    def test_lying_verifier_tanks_belief_proof(self) -> None:
        # Sabotage: the store's own verify_belief_proof always says verified.
        # The tamper probe cross-checks the store verdict against a proof the
        # bench doctored itself, so a yes-man verifier must fail that probe.
        # (The bench's own crypto grading never trusted this verdict anyway.)
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            store.verify_belief_proof = lambda proof, **kwargs: {
                "verified": True,
                "errors": [],
                "completeness_verified": False,
            }
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["belief_proof"]["score"],
            1.0,
            msg="bench failed to detect a verifier that rubber-stamps doctored proofs",
        )

    def test_coherent_history_rewrite_tanks_belief_proof(self) -> None:
        # Sabotage: AFTER the bench pins the chain head, rewrite a historical
        # event in the DB. The UPDATE trigger clears the cached fingerprint, so
        # the store coherently REBUILDS its chain from the forged bytes - its
        # own verification stays green, every returned proof is internally
        # consistent, and only the externally pinned head can expose that the
        # past changed. This is M2's core threat model.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            original_answer = store.answer_query
            state = {"tampered": False}

            def tamper_once_then_answer(user_id, query, *args, **kwargs):
                # answer_query first runs AFTER the belief-proof pin was taken,
                # so this rewrite lands between pin and the end-of-run replay.
                if not state["tampered"]:
                    with connect(store.db_path) as conn:
                        conn.execute(
                            "UPDATE memory_events SET metadata_json = '{\"forged\": true}' "
                            "WHERE rowid = (SELECT rowid FROM memory_events WHERE user_id = ? "
                            "ORDER BY created_at ASC, rowid ASC LIMIT 1)",
                            (user_id,),
                        )
                    state["tampered"] = True
                return original_answer(user_id, query, *args, **kwargs)

            store.answer_query = tamper_once_then_answer
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertTrue(state["tampered"], "sabotage never fired; test is vacuous")
        self.assertLess(
            report["categories"]["belief_proof"]["score"],
            1.0,
            msg="bench failed to detect a coherent history rewrite against the pinned head",
        )
        # The rewrite must be caught by the external pin, not by per-proof
        # crypto: a coherent rebuild is self-consistent by construction.
        pin_failures = [
            failure
            for failure in report["categories"]["belief_proof"]["failures"]
            if failure.startswith("belief_proof pin:")
        ]
        self.assertTrue(pin_failures, "expected the PIN probe to be the one that caught the rewrite")

    def test_overconfident_unknown_tanks_metacognition(self) -> None:
        # Sabotage: turn every honest twin abstention into a confident uncited
        # answer at the response boundary. Independent seeded-vs-absent gold must
        # catch it even though the underlying event was originally honest.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            original = store.would_i
            state = {"laundered": 0}

            def overconfident(user_id, question, **kwargs):
                result = original(user_id, question, **kwargs)
                if result.get("known_unknown") is True:
                    result["verdict"] = "likely_yes"
                    result["confidence"] = 0.95
                    result["known_unknown"] = False
                    result["knowledge_gap"] = None
                    state["laundered"] += 1
                return result

            store.would_i = overconfident
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertGreater(state["laundered"], 0, "sabotage never fired; test is vacuous")
        self.assertLess(
            report["categories"]["metacognition"]["score"],
            1.0,
            msg="bench failed to detect overconfident answers for never-seeded decisions",
        )

    def test_lying_calibration_scorecard_tanks_metacognition(self) -> None:
        # Sabotage: predictions remain perfect, but the first-class scorecard lies
        # about its ECE. The bench's independent calculation must reject it.
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            original = store.get_twin_calibration
            state = {"lied": False}

            def lying_scorecard(user_id, **kwargs):
                result = original(user_id, **kwargs)
                result["expected_calibration_error"] = 0.5
                state["lied"] = True
                return result

            store.get_twin_calibration = lying_scorecard
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertTrue(state["lied"], "sabotage never fired; test is vacuous")
        self.assertLess(
            report["categories"]["metacognition"]["score"],
            1.0,
            msg="bench failed to detect a calibration scorecard that disagrees with independent gold",
        )
        self.assertTrue(
            any(
                failure.startswith("metacognition scorecard parity:")
                for failure in report["categories"]["metacognition"]["failures"]
            ),
            "expected scorecard parity to name the lie",
        )


if __name__ == "__main__":
    unittest.main()
