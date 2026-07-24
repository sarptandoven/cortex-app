from __future__ import annotations

import unittest

from backend.app.twin_eval.domain import (
    CitedProfileItem,
    ComparisonOutcome,
    EvaluationPrompt,
    HeldOutProfile,
    canonical_hash,
    derive_seed,
)
from backend.app.twin_eval.protocols import DeterministicGenerator, OracleJudge
from backend.app.twin_eval.ranking import BradleyTerryRanker, WinRateRanker
from backend.app.twin_eval.runner import PairwiseEvaluationRunner
from backend.app.twin_eval.strategies import AllPairsStrategy, AnchorStrategy, RepeatedSwappedStrategy


class PairwiseCoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = HeldOutProfile(
            "profile-1",
            (CitedProfileItem("mem-1", "Use short, direct sentences.", "cortex://mem-1", "style"),),
        )
        self.prompts = (
            EvaluationPrompt("prompt-1", "Write a project update."),
            EvaluationPrompt("prompt-2", "Decline a meeting."),
        )

    def test_hashes_and_child_seeds_are_canonical(self) -> None:
        self.assertEqual(canonical_hash({"b": 2, "a": 1}), canonical_hash({"a": 1, "b": 2}))
        self.assertEqual(derive_seed(7, "candidate", "a"), derive_seed(7, "candidate", "a"))
        self.assertNotEqual(derive_seed(7, "candidate", "a"), derive_seed(7, "candidate", "b"))

    def test_repeated_swapped_emits_both_presentations(self) -> None:
        plans = RepeatedSwappedStrategy(repetitions=2, shuffle=False).plan(
            self.prompts[:1], ("a", "b"), seed=3
        )
        self.assertEqual(len(plans), 4)
        self.assertEqual(
            {(plan.left_system_id, plan.right_system_id) for plan in plans},
            {("a", "b"), ("b", "a")},
        )

    def test_anchor_does_not_compare_non_anchor_systems(self) -> None:
        plans = AnchorStrategy("anchor", shuffle=False).plan(
            self.prompts[:1], ("anchor", "b", "c"), seed=1
        )
        self.assertEqual(len(plans), 2)
        self.assertTrue(
            all("anchor" in {plan.left_system_id, plan.right_system_id} for plan in plans)
        )

    def test_runner_is_reproducible_and_side_swap_invariant(self) -> None:
        generators = (
            DeterministicGenerator("preferred", lambda prompt, profile, seed: "Short update."),
            DeterministicGenerator("baseline", lambda prompt, profile, seed: "A long update."),
        )
        runner = PairwiseEvaluationRunner(
            generators,
            OracleJudge({prompt.prompt_id: "preferred" for prompt in self.prompts}),
            RepeatedSwappedStrategy(),
            BradleyTerryRanker(),
            blind_judge_inputs=False,
        )
        first = runner.run(self.profile, self.prompts, seed=42)
        second = runner.run(self.profile, self.prompts, seed=42)
        self.assertEqual(first, second)
        self.assertEqual(first.ranking.ratings[0].system_id, "preferred")
        self.assertEqual(len(first.comparisons), 4)

    def test_bradley_terry_reports_disconnected_graph_and_ignored_both_bad(self) -> None:
        result = BradleyTerryRanker().rank(
            ("a", "b", "c"),
            (
                ("a", "b", ComparisonOutcome.LEFT),
                ("b", "a", ComparisonOutcome.RIGHT),
                ("a", "c", ComparisonOutcome.BOTH_BAD),
            ),
        )
        self.assertFalse(result.diagnostics.connected)
        self.assertEqual(result.diagnostics.components, (("a", "b"), ("c",)))
        self.assertEqual(result.diagnostics.ignored_both_bad, 1)
        self.assertEqual(result.ratings[0].system_id, "a")

    def test_all_pairs_count(self) -> None:
        plans = AllPairsStrategy(repetitions=2, shuffle=False).plan(
            self.prompts, ("a", "b", "c"), seed=0
        )
        self.assertEqual(len(plans), 12)

    def test_win_rate_assigns_shared_dense_ranks_for_equal_scores(self) -> None:
        result = WinRateRanker().rank(
            ("a", "b", "c"),
            (
                ("a", "b", ComparisonOutcome.LEFT),
                ("b", "c", ComparisonOutcome.RIGHT),
                ("a", "c", ComparisonOutcome.TIE),
            ),
        )
        ratings = {rating.system_id: rating for rating in result.ratings}
        self.assertTrue(result.diagnostics.connected)
        self.assertEqual(ratings["a"].score, 0.75)
        self.assertEqual(ratings["c"].score, 0.75)
        self.assertEqual(ratings["a"].rank, 1)
        self.assertEqual(ratings["c"].rank, 1)
        self.assertEqual(ratings["b"].rank, 2)

    def test_win_rate_audits_ignored_outcomes_and_suppresses_global_ranks(self) -> None:
        result = WinRateRanker().rank(
            ("a", "b", "c"),
            (
                ("a", "b", ComparisonOutcome.LEFT),
                ("a", "c", ComparisonOutcome.BOTH_BAD),
                ("b", "c", ComparisonOutcome.ABSTAIN),
                ("a", "c", ComparisonOutcome.INVALID),
            ),
        )
        self.assertFalse(result.diagnostics.connected)
        self.assertTrue(all(rating.rank is None for rating in result.ratings))
        self.assertEqual(result.diagnostics.ignored_both_bad, 1)
        self.assertEqual(result.diagnostics.ignored_abstain, 1)
        self.assertEqual(result.diagnostics.ignored_invalid, 1)

    def test_win_rate_rejects_structurally_malformed_comparisons(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid ranking comparison"):
            WinRateRanker().rank(
                ("a", "b"),
                (("missing", "b", ComparisonOutcome.LEFT),),
            )


if __name__ == "__main__":
    unittest.main()
