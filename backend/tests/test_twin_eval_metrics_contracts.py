"""Deterministic scientific-metrics contracts for pairwise twin evaluation."""

from __future__ import annotations

import unittest

from backend.app.twin_eval import (
    BradleyTerryRanker,
    CitedProfileItem,
    ComparisonOutcome,
    DeterministicGenerator,
    EvaluationPrompt,
    HeldOutProfile,
    JudgeDecision,
    OracleJudge,
    PairwiseEvaluationRunner,
    RepeatedSwappedStrategy,
    ObservableFeature,
    ObservableFeatureJudge,
    ObservableRubric,
    ObservableRule,
    clustered_bootstrap_mean,
    paired_clustered_bootstrap_delta,
    reliability_metrics,
)


def _profile() -> HeldOutProfile:
    return HeldOutProfile(
        "profile-1",
        (CitedProfileItem("mem-1", "Use short, direct sentences."),),
    )


def _prompt() -> EvaluationPrompt:
    return EvaluationPrompt("prompt-1", "Write a project update.")


def _generator(system_id: str) -> DeterministicGenerator:
    return DeterministicGenerator(
        system_id,
        lambda prompt, profile, seed: f"{system_id}: {prompt.text} [{seed}]",
    )


def _runner(judge, *, repetitions: int = 1) -> PairwiseEvaluationRunner:
    return PairwiseEvaluationRunner(
        (_generator("baseline"), _generator("preferred")),
        judge,
        RepeatedSwappedStrategy(repetitions=repetitions, shuffle=False),
        BradleyTerryRanker(),
        blind_judge_inputs=not getattr(judge, "requires_candidate_identity", False),
    )


class _AlwaysLeftJudge:
    """Synthetic position-biased judge: useful for proving swap checks can fail."""

    judge_id = "always-left"

    def judge(self, prompt, profile, left, right, *, seed):
        del prompt, left, right, seed
        return JudgeDecision(
            ComparisonOutcome.LEFT,
            rationale="synthetic first-position preference",
            cited_memory_ids=tuple(item.memory_id for item in profile.items),
        )


class ReliabilityMetricsContractTests(unittest.TestCase):
    def test_oracle_has_perfect_swap_agreement_and_zero_position_bias(self) -> None:
        report = _runner(OracleJudge({"prompt-1": "preferred"})).run(
            _profile(), (_prompt(),), seed=13
        )
        metrics = reliability_metrics(report)

        self.assertEqual(metrics.raw_judgments, 2)
        self.assertEqual(metrics.logical_comparisons, 1)
        self.assertEqual(metrics.swapped_pairs, 1)
        self.assertEqual(metrics.swap_consistent_pairs, 1)
        self.assertEqual(metrics.swap_agreement, 1.0)
        self.assertEqual(metrics.displayed_left_wins, 1)
        self.assertEqual(metrics.displayed_right_wins, 1)
        self.assertEqual(metrics.position_bias, 0.0)

    def test_repeat_agreement_is_one_for_stable_oracle(self) -> None:
        report = _runner(
            OracleJudge({"prompt-1": "preferred"}),
            repetitions=3,
        ).run(_profile(), (_prompt(),), seed=13)
        metrics = reliability_metrics(report)

        self.assertEqual(metrics.logical_comparisons, 3)
        self.assertEqual(metrics.repeat_pairs, 3)
        self.assertEqual(metrics.repeat_agreement, 1.0)
        self.assertEqual(metrics.swap_agreement, 1.0)

    def test_swap_inconsistency_resolves_to_invalid(self) -> None:
        report = _runner(_AlwaysLeftJudge()).run(_profile(), (_prompt(),), seed=13)
        metrics = reliability_metrics(report)

        self.assertEqual(len(report.resolved_comparisons), 1)
        self.assertEqual(
            report.resolved_comparisons[0].outcome,
            ComparisonOutcome.INVALID,
        )
        self.assertFalse(report.resolved_comparisons[0].swap_consistent)
        self.assertEqual(metrics.invalid, 1)
        self.assertEqual(metrics.swap_consistent_pairs, 0)
        self.assertEqual(metrics.swap_agreement, 0.0)
        self.assertEqual(report.ranking.diagnostics.ignored_invalid, 1)
        self.assertTrue(
            all(rating.comparisons == 0 for rating in report.ranking.ratings)
        )

    def test_repeated_invalid_resolutions_do_not_report_perfect_repeat_agreement(self) -> None:
        report = _runner(_AlwaysLeftJudge(), repetitions=3).run(
            _profile(), (_prompt(),), seed=13
        )
        metrics = reliability_metrics(report)
        self.assertEqual(metrics.repeat_pairs, 0)
        self.assertEqual(metrics.invalid_repeat_pairs, 3)
        self.assertIsNone(metrics.repeat_agreement)


class ObservableOracleContractTests(unittest.TestCase):
    def test_one_hard_constraint_violator_loses_before_any_soft_tiebreak(self) -> None:
        profile = HeldOutProfile(
            "hard-profile",
            (CitedProfileItem("no-exclaim", "Never use exclamation points."),),
        )
        prompt = EvaluationPrompt("hard-prompt", "Write an update.")
        rubric = ObservableRubric(
            "hard-prompt",
            ("no-exclaim",),
            (
                ObservableRule(
                    ObservableFeature.MAX_EXCLAMATIONS,
                    0,
                    weight=0.01,
                    hard_constraint=True,
                ),
                *(
                    ObservableRule(ObservableFeature.REQUIRED_PHRASE, "preferred")
                    for _ in range(20)
                ),
            ),
        )
        judge = ObservableFeatureJudge({"hard-prompt": rubric})
        from backend.app.twin_eval import Candidate

        violating = Candidate(
            "violating",
            "left",
            "preferred!",
            prompt.prompt_id,
            1,
        )
        compliant = Candidate(
            "compliant",
            "right",
            "plain update",
            prompt.prompt_id,
            2,
        )

        decision = judge.judge(prompt, profile, violating, compliant, seed=3)

        self.assertEqual(decision.outcome, ComparisonOutcome.RIGHT)

    def test_two_hard_constraint_violators_resolve_both_bad_before_soft_tiebreak(self) -> None:
        profile = HeldOutProfile(
            "hard-profile",
            (CitedProfileItem("no-exclaim", "Never use exclamation points."),),
        )
        prompt = EvaluationPrompt("hard-prompt", "Write an update.")
        rubric = ObservableRubric(
            "hard-prompt",
            ("no-exclaim",),
            (
                ObservableRule(
                    ObservableFeature.MAX_EXCLAMATIONS,
                    0,
                    hard_constraint=True,
                ),
                ObservableRule(ObservableFeature.MAX_WORDS, 4),
            ),
        )
        judge = ObservableFeatureJudge({"hard-prompt": rubric})
        # Construct explicit candidates to exercise unequal soft scores.
        from backend.app.twin_eval import Candidate

        left = Candidate("left-1", "left", "short!", prompt.prompt_id, 1)
        right = Candidate(
            "right-1",
            "right",
            "a much longer response that also violates the rule!",
            prompt.prompt_id,
            2,
        )
        decision = judge.judge(prompt, profile, left, right, seed=3)
        self.assertEqual(decision.outcome, ComparisonOutcome.BOTH_BAD)


class ClusteredBootstrapContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.values = {
            "profile-a": (1.0, 1.0),
            "profile-b": (0.0, 0.0),
            "profile-c": (0.5, 0.5),
            "profile-d": (0.75, 0.75),
        }

    def test_clustered_bootstrap_is_deterministic_at_fixed_seed(self) -> None:
        first = clustered_bootstrap_mean(self.values, seed=29, resamples=500)
        second = clustered_bootstrap_mean(self.values, seed=29, resamples=500)
        self.assertEqual(first, second)
        self.assertEqual(first.clusters, 4)
        self.assertEqual(first.resamples, 500)
        self.assertIsNotNone(first.low)
        self.assertIsNotNone(first.high)
        self.assertLessEqual(first.low, first.estimate)
        self.assertLessEqual(first.estimate, first.high)

    def test_duplicating_observations_within_clusters_does_not_change_interval(self) -> None:
        duplicated = {
            cluster_id: tuple(value for value in observations for _ in range(10))
            for cluster_id, observations in self.values.items()
        }
        original = clustered_bootstrap_mean(self.values, seed=29, resamples=500)
        repeated = clustered_bootstrap_mean(duplicated, seed=29, resamples=500)

        self.assertEqual(repeated, original)
        self.assertEqual(repeated.clusters, len(self.values))

    def test_one_cluster_reports_estimate_but_insufficient_interval_bounds(self) -> None:
        interval = clustered_bootstrap_mean(
            {"only-profile": (0.0, 1.0, 1.0)},
            seed=29,
            resamples=500,
        )
        self.assertAlmostEqual(interval.estimate, 2.0 / 3.0)
        self.assertIsNone(interval.low)
        self.assertIsNone(interval.high)
        self.assertEqual(interval.clusters, 1)
        self.assertEqual(interval.resamples, 500)


class PairedDeltaContractTests(unittest.TestCase):
    def test_paired_delta_requires_identical_cohorts(self) -> None:
        with self.assertRaisesRegex(ValueError, "identical cluster IDs"):
            paired_clustered_bootstrap_delta(
                {"profile-a": (0.0,), "profile-b": (1.0,)},
                {"profile-a": (1.0,), "profile-c": (1.0,)},
                seed=7,
                resamples=100,
            )

    def test_delta_sign_is_challenger_minus_baseline(self) -> None:
        baseline = {
            "profile-a": (0.0, 0.0),
            "profile-b": (0.5, 0.5),
            "profile-c": (0.0, 0.0),
        }
        challenger = {
            "profile-a": (1.0, 1.0),
            "profile-b": (0.5, 0.5),
            "profile-c": (0.5, 0.5),
        }
        improvement = paired_clustered_bootstrap_delta(
            baseline, challenger, seed=7, resamples=500
        )
        regression = paired_clustered_bootstrap_delta(
            challenger, baseline, seed=7, resamples=500
        )

        self.assertEqual(improvement.estimate, 0.5)
        self.assertEqual(regression.estimate, -0.5)
        self.assertEqual(improvement.low, -regression.high)
        self.assertEqual(improvement.high, -regression.low)


class BradleyTerryRankSemanticsContractTests(unittest.TestCase):
    def test_disconnected_graph_has_no_global_ranks(self) -> None:
        result = BradleyTerryRanker().rank(
            ("alpha", "bravo", "charlie", "delta"),
            (
                ("alpha", "bravo", ComparisonOutcome.LEFT),
                ("charlie", "delta", ComparisonOutcome.LEFT),
            ),
        )
        self.assertFalse(result.diagnostics.connected)
        self.assertTrue(all(rating.rank is None for rating in result.ratings))
        self.assertEqual(
            {rating.system_id: rating.component_rank for rating in result.ratings},
            {"alpha": 1, "bravo": 2, "charlie": 1, "delta": 2},
        )

    def test_equal_scores_share_dense_rank(self) -> None:
        result = BradleyTerryRanker().rank(
            ("alpha", "bravo", "charlie"),
            (
                ("alpha", "bravo", ComparisonOutcome.TIE),
                ("alpha", "charlie", ComparisonOutcome.TIE),
                ("bravo", "charlie", ComparisonOutcome.TIE),
            ),
        )
        self.assertTrue(result.diagnostics.connected)
        self.assertEqual({rating.score for rating in result.ratings}, {0.0})
        self.assertEqual({rating.rank for rating in result.ratings}, {1})
        self.assertEqual({rating.component_rank for rating in result.ratings}, {1})


if __name__ == "__main__":
    unittest.main()
