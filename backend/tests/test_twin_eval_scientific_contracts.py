"""Scientific contract tests for the offline pairwise twin-evaluation core.

These tests intentionally exercise public ``backend.app.twin_eval`` APIs.  They
use deterministic in-process generators and judges only: no provider, network,
database, clock, or filesystem state is involved.
"""

from __future__ import annotations

import itertools
import unittest

from backend.app.twin_eval import (
    AllPairsStrategy,
    BradleyTerryRanker,
    CitedProfileItem,
    ComparisonOutcome,
    DeterministicGenerator,
    EvaluationPrompt,
    HeldOutProfile,
    OracleJudge,
    PairwiseEvaluationRunner,
    RepeatedSwappedStrategy,
    canonical_json,
)


def _profile(content: str = "Use short, direct sentences.") -> HeldOutProfile:
    return HeldOutProfile(
        "profile-1",
        (CitedProfileItem("mem-1", content, "cortex://mem-1", "style"),),
    )


def _prompt(prompt_id: str = "prompt-1", text: str = "Write a project update.") -> EvaluationPrompt:
    return EvaluationPrompt(prompt_id, text)


def _generator(system_id: str) -> DeterministicGenerator:
    return DeterministicGenerator(
        system_id,
        lambda prompt, profile, seed: f"{system_id}: {prompt.text} [{seed}]",
    )


def _rating_map(result) -> dict[str, object]:
    return {rating.system_id: rating for rating in result.ratings}


class ComparisonOutcomeContractTests(unittest.TestCase):
    def test_abstain_and_invalid_are_distinct_from_tie_and_each_other(self) -> None:
        abstain = ComparisonOutcome.normalize("abstain")
        invalid = ComparisonOutcome.normalize("invalid")

        self.assertIsNot(abstain, invalid)
        self.assertIsNot(abstain, ComparisonOutcome.TIE)
        self.assertIsNot(invalid, ComparisonOutcome.TIE)
        self.assertEqual(abstain.swapped(), abstain)
        self.assertEqual(invalid.swapped(), invalid)

    def test_abstain_and_invalid_are_separately_audited_and_never_ranked(self) -> None:
        result = BradleyTerryRanker().rank(
            ("alpha", "bravo"),
            (
                ("alpha", "bravo", ComparisonOutcome.ABSTAIN),
                ("alpha", "bravo", ComparisonOutcome.INVALID),
            ),
        )
        ratings = _rating_map(result)
        self.assertEqual(result.diagnostics.ignored_abstain, 1)
        self.assertEqual(result.diagnostics.ignored_invalid, 1)
        self.assertEqual(ratings["alpha"].comparisons, 0)
        self.assertEqual(ratings["bravo"].comparisons, 0)
        self.assertEqual(ratings["alpha"].score, ratings["bravo"].score)


class DeterministicScheduleContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.prompts = (_prompt("p-2"), _prompt("p-1"))
        self.systems = ("charlie", "alpha", "bravo")

    def test_same_seed_produces_identical_schedule(self) -> None:
        strategy = RepeatedSwappedStrategy(repetitions=2)
        first = strategy.plan(self.prompts, self.systems, seed=91)
        second = strategy.plan(self.prompts, self.systems, seed=91)
        self.assertEqual(canonical_json(first), canonical_json(second))

    def test_schedule_is_invariant_to_input_permutation(self) -> None:
        strategy = RepeatedSwappedStrategy(repetitions=2)
        expected = canonical_json(strategy.plan(self.prompts, self.systems, seed=91))

        for prompts in (self.prompts, tuple(reversed(self.prompts))):
            for systems in itertools.permutations(self.systems):
                with self.subTest(prompts=[item.prompt_id for item in prompts], systems=systems):
                    self.assertEqual(
                        canonical_json(strategy.plan(prompts, systems, seed=91)),
                        expected,
                    )

    def test_each_logical_trial_has_exactly_two_opposite_presentations(self) -> None:
        plans = RepeatedSwappedStrategy(repetitions=3, shuffle=False).plan(
            (_prompt(),), ("alpha", "bravo", "charlie"), seed=7
        )
        grouped: dict[tuple[str, frozenset[str], int], list[object]] = {}
        for plan in plans:
            key = (
                plan.prompt_id,
                frozenset((plan.left_system_id, plan.right_system_id)),
                plan.repetition,
            )
            grouped.setdefault(key, []).append(plan)

        self.assertEqual(len(grouped), 9)
        for pair in grouped.values():
            self.assertEqual(len(pair), 2)
            self.assertEqual(pair[0].left_system_id, pair[1].right_system_id)
            self.assertEqual(pair[0].right_system_id, pair[1].left_system_id)
            self.assertNotEqual(pair[0].comparison_id, pair[1].comparison_id)


class SwappedResolutionContractTests(unittest.TestCase):
    def test_balanced_presentations_are_one_logical_observation(self) -> None:
        runner = PairwiseEvaluationRunner(
            (_generator("baseline"), _generator("preferred")),
            OracleJudge({"prompt-1": "preferred"}),
            RepeatedSwappedStrategy(repetitions=1, shuffle=False),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        report = runner.run(_profile(), (_prompt(),), seed=11)
        ratings = _rating_map(report.ranking)

        # Both raw presentations remain auditable.
        self.assertEqual(len(report.comparisons), 2)
        # But a balanced A/B + B/A trial is one independent preference sample.
        self.assertEqual(ratings["baseline"].comparisons, 1)
        self.assertEqual(ratings["preferred"].comparisons, 1)
        self.assertEqual(ratings["preferred"].wins, 1.0)
        self.assertEqual(ratings["baseline"].wins, 0.0)


class PairwiseOracleContractTests(unittest.TestCase):
    def test_three_system_oracle_uses_pairwise_utility(self) -> None:
        """A prompt-level oracle must answer every pair, even without one global winner.

        Numeric utilities are deliberately prompt-local.  They permit the same
        oracle fixture to label alpha>bravo, bravo>charlie, and alpha>charlie
        without raising merely because the globally best system is absent from
        a particular comparison.
        """

        runner = PairwiseEvaluationRunner(
            tuple(_generator(item) for item in ("alpha", "bravo", "charlie")),
            OracleJudge({"prompt-1": {"alpha": 3.0, "bravo": 2.0, "charlie": 1.0}}),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        report = runner.run(_profile(), (_prompt(),), seed=5)

        winners = []
        for record in report.comparisons:
            if record.decision.outcome is ComparisonOutcome.LEFT:
                winners.append(record.left.system_id)
            elif record.decision.outcome is ComparisonOutcome.RIGHT:
                winners.append(record.right.system_id)
        self.assertEqual(set(winners), {"alpha", "bravo"})
        self.assertEqual(
            [rating.system_id for rating in report.ranking.ratings],
            ["alpha", "bravo", "charlie"],
        )


class BradleyTerryScientificContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.comparisons = (
            ("alpha", "bravo", ComparisonOutcome.LEFT),
            ("alpha", "charlie", ComparisonOutcome.LEFT),
            ("bravo", "charlie", ComparisonOutcome.LEFT),
            ("charlie", "alpha", ComparisonOutcome.RIGHT),
        )

    def test_connected_graph_reports_one_component_and_expected_order(self) -> None:
        result = BradleyTerryRanker().rank(("alpha", "bravo", "charlie"), self.comparisons)
        self.assertTrue(result.diagnostics.connected)
        self.assertEqual(result.diagnostics.components, (("alpha", "bravo", "charlie"),))
        self.assertTrue(result.diagnostics.converged)
        self.assertEqual(
            [rating.system_id for rating in result.ratings],
            ["alpha", "bravo", "charlie"],
        )

    def test_disconnected_graph_is_explicit_and_deterministic(self) -> None:
        result = BradleyTerryRanker().rank(
            ("delta", "charlie", "bravo", "alpha"),
            (
                ("alpha", "bravo", ComparisonOutcome.LEFT),
                ("charlie", "delta", ComparisonOutcome.LEFT),
            ),
        )
        self.assertFalse(result.diagnostics.connected)
        self.assertEqual(
            result.diagnostics.components,
            (("alpha", "bravo"), ("charlie", "delta")),
        )

    def test_ranking_is_invariant_to_system_and_comparison_permutation(self) -> None:
        ranker = BradleyTerryRanker()
        expected = _rating_map(
            ranker.rank(("alpha", "bravo", "charlie"), self.comparisons)
        )

        for systems in itertools.permutations(("alpha", "bravo", "charlie")):
            for records in (self.comparisons, tuple(reversed(self.comparisons))):
                with self.subTest(systems=systems, reversed=records is not self.comparisons):
                    actual = _rating_map(ranker.rank(systems, records))
                    self.assertEqual(actual, expected)


class RunIdentityAndReplayContractTests(unittest.TestCase):
    def _run(
        self,
        *,
        seed: int = 17,
        profile: HeldOutProfile | None = None,
        prompt: EvaluationPrompt | None = None,
    ):
        runner = PairwiseEvaluationRunner(
            (_generator("alpha"), _generator("bravo")),
            OracleJudge({"prompt-1": "alpha"}),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        return runner.run(profile or _profile(), (prompt or _prompt(),), seed=seed)

    def test_exact_replay_produces_equal_report_and_run_id(self) -> None:
        first = self._run()
        replay = self._run()
        self.assertEqual(replay, first)
        self.assertEqual(replay.run_id, first.run_id)

    def test_trial_id_distinguishes_replicates_without_changing_spec(self) -> None:
        runner = PairwiseEvaluationRunner(
            (_generator("alpha"), _generator("bravo")),
            OracleJudge({"prompt-1": "alpha"}),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        first = runner.run(_profile(), (_prompt(),), seed=17, trial_id="replicate-1")
        second = runner.run(_profile(), (_prompt(),), seed=17, trial_id="replicate-2")

        self.assertEqual(first.metadata["spec_id"], second.metadata["spec_id"])
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotEqual(first.artifact_digest, second.artifact_digest)
        self.assertEqual(first.metadata["trial_id"], "replicate-1")
        self.assertEqual(first.comparisons, second.comparisons)

    def test_trial_id_and_generated_metadata_keys_are_validated(self) -> None:
        runner = PairwiseEvaluationRunner(
            (_generator("alpha"), _generator("bravo")),
            OracleJudge({"prompt-1": "alpha"}),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        for invalid in ("", " ", "x" * 201):
            with self.subTest(trial_id=invalid):
                with self.assertRaisesRegex(ValueError, "trial_id"):
                    runner.run(_profile(), (_prompt(),), trial_id=invalid)

        reserved = PairwiseEvaluationRunner(
            (_generator("alpha"), _generator("bravo")),
            OracleJudge({"prompt-1": "alpha"}),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
            metadata={"spec_id": "caller-value"},
            blind_judge_inputs=False,
        )
        with self.assertRaisesRegex(ValueError, "reserved keys"):
            reserved.run(_profile(), (_prompt(),))

    def test_run_identity_changes_with_seed_profile_or_prompt_content(self) -> None:
        baseline = self._run()
        variants = (
            self._run(seed=18),
            self._run(profile=_profile("Prefer warm, conversational prose.")),
            self._run(prompt=_prompt(text="Decline a meeting.")),
        )
        for variant in variants:
            with self.subTest(run_id=variant.run_id):
                self.assertNotEqual(variant.run_id, baseline.run_id)
                self.assertNotEqual(variant, baseline)

    def test_artifact_identity_changes_when_oracle_configuration_changes(self) -> None:
        def run_with_winner(winner: str):
            runner = PairwiseEvaluationRunner(
                (_generator("alpha"), _generator("bravo")),
                OracleJudge({"prompt-1": winner}),
                AllPairsStrategy(shuffle=False),
                BradleyTerryRanker(),
                blind_judge_inputs=False,
            )
            return runner.run(_profile(), (_prompt(),), seed=17)

        alpha_wins = run_with_winner("alpha")
        bravo_wins = run_with_winner("bravo")
        self.assertNotEqual(alpha_wins.comparisons, bravo_wins.comparisons)
        # The run ID identifies the reproducible evaluation specification; the
        # artifact digest additionally identifies the exact resulting artifact.
        self.assertNotEqual(alpha_wins.run_id, bravo_wins.run_id)
        self.assertNotEqual(alpha_wins.artifact_digest, bravo_wins.artifact_digest)


if __name__ == "__main__":
    unittest.main()
