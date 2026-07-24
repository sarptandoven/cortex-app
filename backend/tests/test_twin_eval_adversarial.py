"""Adversarial contracts for pairwise digital-twin evaluation."""

from __future__ import annotations

import math
import unittest

from backend.app.twin_eval import (
    AllPairsStrategy,
    BradleyTerryRanker,
    Candidate,
    CitedProfileItem,
    ComparisonOutcome,
    ComparisonPlan,
    DeterministicGenerator,
    EvaluationPrompt,
    HeldOutProfile,
    JudgeDecision,
    ObservableFeature,
    ObservableFeatureJudge,
    ObservableRubric,
    ObservableRule,
    PairwiseEvaluationRunner,
    PromptScopedCitationPolicy,
    RankingDiagnostics,
    RankingResult,
    RepeatedSwappedStrategy,
    SystemRating,
    clustered_bootstrap_mean,
    observable_utility,
    reliability_metrics,
)


def _profile() -> HeldOutProfile:
    return HeldOutProfile(
        "profile",
        (
            CitedProfileItem("active", "Use concise prose."),
            CitedProfileItem("agent", "Be enthusiastic.", author_class="agent"),
            CitedProfileItem("archived", "Use long prose.", status="archived"),
            CitedProfileItem("zero", "Use filler.", trust_score=0.0),
        ),
    )


def _prompt() -> EvaluationPrompt:
    return EvaluationPrompt("prompt", "Write an update.")


def _runner(judge, *, strategy=None, left="left", right="right", **limits):
    return PairwiseEvaluationRunner(
        (
            DeterministicGenerator("a", lambda prompt, profile, seed: left),
            DeterministicGenerator("b", lambda prompt, profile, seed: right),
        ),
        judge,
        strategy or AllPairsStrategy(shuffle=False),
        BradleyTerryRanker(),
        **limits,
    )


class _DecisionJudge:
    judge_id = "decision"

    def __init__(self, decision: JudgeDecision) -> None:
        self.decision = decision

    def judge(self, prompt, profile, left, right, *, seed):
        del prompt, profile, left, right, seed
        return self.decision


class _AlternatingJudge:
    judge_id = "same-config-nondeterministic"
    calls = 0

    def judge(self, prompt, profile, left, right, *, seed):
        del prompt, left, right, seed
        type(self).calls += 1
        return JudgeDecision(
            ComparisonOutcome.LEFT if type(self).calls % 2 else ComparisonOutcome.RIGHT,
            cited_memory_ids=(profile.items[0].memory_id,),
        )


class _IdentityEchoJudge:
    judge_id = "identity-echo"

    def judge(self, prompt, profile, left, right, *, seed):
        del prompt, profile, seed
        return JudgeDecision(
            ComparisonOutcome.TIE,
            metadata={
                "left_candidate_id": left.candidate_id,
                "left_system_id": left.system_id,
                "left_seed": left.seed,
                "left_metadata": dict(left.metadata),
                "right_candidate_id": right.candidate_id,
                "right_system_id": right.system_id,
                "right_seed": right.seed,
                "right_metadata": dict(right.metadata),
            },
        )


class _EmptyStrategy:
    strategy_id = "empty"

    def plan(self, prompts, system_ids, *, seed):
        del prompts, system_ids, seed
        return ()


class _DuplicateOrientationStrategy:
    strategy_id = "duplicate-orientation"

    def plan(self, prompts, system_ids, *, seed):
        del seed
        prompt_id = prompts[0].prompt_id
        left, right = sorted(system_ids)
        return (
            ComparisonPlan("c1", "logical", prompt_id, left, right, swapped=False),
            ComparisonPlan("c2", "logical", prompt_id, left, right, swapped=True),
        )


class _DuplicateTrialStrategy:
    strategy_id = "duplicate-trial"

    def plan(self, prompts, system_ids, *, seed):
        del seed
        prompt_id = prompts[0].prompt_id
        left, right = sorted(system_ids)
        return (
            ComparisonPlan("c1", "logical-1", prompt_id, left, right),
            ComparisonPlan("c2", "logical-2", prompt_id, right, left),
        )


class _AlternatingScheduleStrategy:
    strategy_id = "same-config-nondeterministic-schedule"
    calls = 0

    def plan(self, prompts, system_ids, *, seed):
        del seed
        type(self).calls += 1
        first, second = sorted(system_ids)
        swapped = type(self).calls % 2 == 0
        left, right = (second, first) if swapped else (first, second)
        return (
            ComparisonPlan(
                "same-comparison",
                "same-logical",
                prompts[0].prompt_id,
                left,
                right,
                swapped=swapped,
            ),
        )


class _AlternatingOrderStrategy:
    strategy_id = "same-config-nondeterministic-order"
    calls = 0

    def plan(self, prompts, system_ids, *, seed):
        del seed
        type(self).calls += 1
        left, right = sorted(system_ids)
        plans = (
            ComparisonPlan("c0", "logical-0", prompts[0].prompt_id, left, right, 0),
            ComparisonPlan("c1", "logical-1", prompts[0].prompt_id, left, right, 1),
        )
        return tuple(reversed(plans)) if type(self).calls % 2 == 0 else plans


class _AlternatingRanker:
    ranking_id = "same-config-nondeterministic-ranker"
    calls = 0

    def rank(self, system_ids, comparisons):
        del comparisons
        type(self).calls += 1
        systems = tuple(sorted(system_ids))
        winner, loser = (
            systems if type(self).calls % 2 else tuple(reversed(systems))
        )
        return RankingResult(
            (
                SystemRating(winner, 1.0, 1, 1, 1, 1.0),
                SystemRating(loser, -1.0, 2, 2, 1, 0.0),
            ),
            RankingDiagnostics(
                True,
                (systems,),
                True,
                1,
                0.0,
                -1.0,
            ),
        )


class _DuplicateIdGenerator:
    def __init__(self, system_id: str) -> None:
        self.system_id = system_id

    def generate(self, prompt, profile, *, seed):
        del profile
        return Candidate("duplicate", self.system_id, self.system_id, prompt.prompt_id, seed)


class PairwiseAdversarialTests(unittest.TestCase):
    def test_judge_inputs_are_identity_blind_by_default(self) -> None:
        report = _runner(_IdentityEchoJudge()).run(
            _profile(), (_prompt(),), seed=1
        )
        metadata = report.comparisons[0].decision.metadata

        self.assertEqual(metadata["left_candidate_id"], "candidate_a")
        self.assertEqual(metadata["left_system_id"], "candidate_a")
        self.assertEqual(metadata["left_seed"], 0)
        self.assertEqual(metadata["left_metadata"], {})
        self.assertEqual(metadata["right_candidate_id"], "candidate_b")
        self.assertEqual(metadata["right_system_id"], "candidate_b")
        self.assertEqual(metadata["right_seed"], 0)
        self.assertEqual(metadata["right_metadata"], {})

    def test_identity_aware_fixture_judge_requires_explicit_opt_out(self) -> None:
        from backend.app.twin_eval import OracleJudge

        runner = PairwiseEvaluationRunner(
            (
                DeterministicGenerator("a", lambda prompt, profile, seed: "left"),
                DeterministicGenerator("b", lambda prompt, profile, seed: "right"),
            ),
            OracleJudge({"prompt": "a"}),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
        )
        with self.assertRaisesRegex(ValueError, "requires candidate identity"):
            runner.run(_profile(), (_prompt(),), seed=1)

    def test_prompt_scoped_policy_rejects_eligible_but_irrelevant_citation(self) -> None:
        profile = HeldOutProfile(
            "scoped",
            (
                CitedProfileItem("relevant", "Use direct language."),
                CitedProfileItem("unrelated", "I prefer tea."),
            ),
        )
        policy = PromptScopedCitationPolicy({"prompt": ("relevant",)})
        irrelevant = _runner(
            _DecisionJudge(
                JudgeDecision(
                    ComparisonOutcome.LEFT,
                    cited_memory_ids=("unrelated",),
                )
            )
        )
        irrelevant = PairwiseEvaluationRunner(
            irrelevant.generators,
            irrelevant.judge,
            irrelevant.strategy,
            irrelevant.ranker,
            citation_policy=policy,
        ).run(profile, (_prompt(),), seed=1)
        self.assertEqual(
            irrelevant.resolved_comparisons[0].outcome,
            ComparisonOutcome.INVALID,
        )

        relevant = _runner(
            _DecisionJudge(
                JudgeDecision(
                    ComparisonOutcome.LEFT,
                    cited_memory_ids=("relevant",),
                )
            )
        )
        relevant = PairwiseEvaluationRunner(
            relevant.generators,
            relevant.judge,
            relevant.strategy,
            relevant.ranker,
            citation_policy=policy,
        ).run(profile, (_prompt(),), seed=1)
        self.assertEqual(
            relevant.resolved_comparisons[0].outcome,
            ComparisonOutcome.LEFT,
        )

    def test_ineligible_hallucinated_missing_and_duplicate_citations_are_invalid(self) -> None:
        citations = (
            ("missing",),
            ("agent",),
            ("archived",),
            ("zero",),
            ("active", "active"),
            (),
        )
        for cited in citations:
            with self.subTest(citations=cited):
                report = _runner(
                    _DecisionJudge(
                        JudgeDecision(
                            ComparisonOutcome.LEFT,
                            cited_memory_ids=cited,
                        )
                    )
                ).run(_profile(), (_prompt(),), seed=1)
                self.assertEqual(
                    report.resolved_comparisons[0].outcome,
                    ComparisonOutcome.INVALID,
                )

    def test_identical_and_near_identical_text_preserve_judge_semantics(self) -> None:
        tie_judge = _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE))
        identical = _runner(tie_judge, left="same", right="same").run(
            _profile(), (_prompt(),), seed=1
        )
        self.assertEqual(
            identical.resolved_comparisons[0].outcome,
            ComparisonOutcome.TIE,
        )
        leaked = _runner(
            _DecisionJudge(
                JudgeDecision(
                    ComparisonOutcome.LEFT,
                    cited_memory_ids=("active",),
                )
            ),
            left="same",
            right="same",
        ).run(_profile(), (_prompt(),), seed=1)
        self.assertEqual(
            leaked.resolved_comparisons[0].outcome,
            ComparisonOutcome.INVALID,
        )
        both_bad = _runner(
            _DecisionJudge(JudgeDecision(ComparisonOutcome.BOTH_BAD)),
            left="same",
            right="same",
        ).run(_profile(), (_prompt(),), seed=1)
        self.assertEqual(
            both_bad.resolved_comparisons[0].outcome,
            ComparisonOutcome.BOTH_BAD,
        )

        nearly = _runner(
            _DecisionJudge(
                JudgeDecision(
                    ComparisonOutcome.LEFT,
                    cited_memory_ids=("active",),
                )
            ),
            left="same",
            right="same.",
        ).run(_profile(), (_prompt(),), seed=1)
        self.assertEqual(
            nearly.resolved_comparisons[0].outcome,
            ComparisonOutcome.LEFT,
        )

    def test_empty_and_fake_swapped_schedules_are_rejected(self) -> None:
        judge = _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE))
        with self.assertRaisesRegex(ValueError, "no plans"):
            _runner(judge, strategy=_EmptyStrategy()).run(
                _profile(), (_prompt(),), seed=1
            )
        with self.assertRaisesRegex(ValueError, "opposite orientations"):
            _runner(judge, strategy=_DuplicateOrientationStrategy()).run(
                _profile(), (_prompt(),), seed=1
            )
        with self.assertRaisesRegex(ValueError, "multiple logical_comparison_id"):
            _runner(judge, strategy=_DuplicateTrialStrategy()).run(
                _profile(), (_prompt(),), seed=1
            )

    def test_duplicate_candidate_ids_and_oversize_answers_are_rejected(self) -> None:
        runner = PairwiseEvaluationRunner(
            (_DuplicateIdGenerator("a"), _DuplicateIdGenerator("b")),
            _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE)),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
        )
        with self.assertRaisesRegex(ValueError, "duplicate candidate_id"):
            runner.run(_profile(), (_prompt(),), seed=1)

        with self.assertRaisesRegex(ValueError, "max_candidate_chars"):
            _runner(
                _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE)),
                left="x" * 11,
                max_candidate_chars=10,
            ).run(_profile(), (_prompt(),), seed=1)

    def test_nondeterministic_trials_share_spec_id_but_not_run_id(self) -> None:
        _AlternatingJudge.calls = 0
        runner = _runner(_AlternatingJudge())
        first = runner.run(_profile(), (_prompt(),), seed=1)
        second = runner.run(_profile(), (_prompt(),), seed=1)

        self.assertEqual(first.metadata["spec_id"], second.metadata["spec_id"])
        self.assertNotEqual(first.run_id, second.run_id)
        self.assertNotEqual(first.artifact_digest, second.artifact_digest)

    def test_nondeterministic_rankings_do_not_alias_run_id(self) -> None:
        _AlternatingRanker.calls = 0
        runner = PairwiseEvaluationRunner(
            (
                DeterministicGenerator("a", lambda prompt, profile, seed: "left"),
                DeterministicGenerator("b", lambda prompt, profile, seed: "right"),
            ),
            _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE)),
            AllPairsStrategy(shuffle=False),
            _AlternatingRanker(),
        )
        first = runner.run(_profile(), (_prompt(),), seed=1)
        second = runner.run(_profile(), (_prompt(),), seed=1)

        self.assertEqual(first.metadata["spec_id"], second.metadata["spec_id"])
        self.assertNotEqual(first.run_id, second.run_id)

    def test_nondeterministic_schedules_do_not_alias_spec_or_run_id(self) -> None:
        _AlternatingScheduleStrategy.calls = 0
        runner = _runner(
            _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE)),
            strategy=_AlternatingScheduleStrategy(),
        )
        first = runner.run(_profile(), (_prompt(),), seed=1)
        second = runner.run(_profile(), (_prompt(),), seed=1)

        self.assertNotEqual(first.metadata["spec_id"], second.metadata["spec_id"])
        self.assertNotEqual(first.run_id, second.run_id)

    def test_execution_order_is_part_of_the_frozen_specification(self) -> None:
        _AlternatingOrderStrategy.calls = 0
        runner = _runner(
            _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE)),
            strategy=_AlternatingOrderStrategy(),
        )
        first = runner.run(_profile(), (_prompt(),), seed=1)
        second = runner.run(_profile(), (_prompt(),), seed=1)

        self.assertNotEqual(first.metadata["spec_id"], second.metadata["spec_id"])
        self.assertNotEqual(first.run_id, second.run_id)

    def test_integer_and_string_seeds_do_not_alias(self) -> None:
        judge = _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE))
        integer = _runner(judge).run(_profile(), (_prompt(),), seed=1)
        string = _runner(judge).run(_profile(), (_prompt(),), seed="1")
        self.assertNotEqual(integer.run_id, string.run_id)

    def test_domain_metadata_is_deeply_immutable(self) -> None:
        source = {"nested": {"values": [1, 2]}}
        prompt = EvaluationPrompt("immutable", "text", source)
        source["nested"]["values"].append(3)

        self.assertEqual(tuple(prompt.metadata["nested"]["values"]), (1, 2))
        with self.assertRaises(TypeError):
            prompt.metadata["new"] = True
        with self.assertRaises(TypeError):
            EvaluationPrompt("bad", "text", {1: "ambiguous"})

    def test_invalid_source_judgments_do_not_create_perfect_swap_agreement(self) -> None:
        report = _runner(
            _DecisionJudge(JudgeDecision(ComparisonOutcome.LEFT)),
            strategy=RepeatedSwappedStrategy(shuffle=False),
        ).run(_profile(), (_prompt(),), seed=1)
        metrics = reliability_metrics(report)

        self.assertEqual(metrics.invalid_swap_pairs, 1)
        self.assertIsNone(metrics.swap_agreement)

    def test_unicode_normalization_blocks_phrase_and_word_count_evasions(self) -> None:
        profile = HeldOutProfile(
            "unicode",
            (CitedProfileItem("rule", "Never deploy Friday; keep it short."),),
        )
        prompt = EvaluationPrompt("unicode", "Choose a day.")
        rubric = ObservableRubric(
            "unicode",
            ("rule",),
            (
                ObservableRule(
                    ObservableFeature.PROHIBITED_PHRASE,
                    "friday",
                    hard_constraint=True,
                ),
                ObservableRule(ObservableFeature.MAX_WORDS, 3),
            ),
        )
        judge = ObservableFeatureJudge({"unicode": rubric})
        left = Candidate("left", "a", "fri\u200bday", "unicode", 1)
        right = Candidate("right", "b", "部署星期一", "unicode", 2)
        decision = judge.judge(prompt, profile, left, right, seed=1)

        self.assertEqual(decision.outcome, ComparisonOutcome.RIGHT)

    def test_long_unsegmented_scripts_cannot_bypass_word_limits(self) -> None:
        rubric = ObservableRubric(
            "unicode",
            ("rule",),
            (ObservableRule(ObservableFeature.MAX_WORDS, 3),),
        )
        for text in (
            "あ" * 100,
            "ก" * 100,
            "한" * 100,
        ):
            with self.subTest(script=text[0]):
                score, _ = observable_utility(text, rubric)
                self.assertLess(score, 0)

    def test_malformed_numeric_configuration_fails_at_construction(self) -> None:
        for scale in (-1.0, 0.0, math.nan, math.inf):
            with self.subTest(scale=scale), self.assertRaises(ValueError):
                BradleyTerryRanker(scale=scale)
        with self.assertRaises(ValueError):
            ObservableRubric("p", ("m",), (), tie_epsilon=math.nan)
        with self.assertRaises(ValueError):
            ObservableRule(ObservableFeature.REQUIRED_PHRASE, "")
        with self.assertRaisesRegex(ValueError, "non-finite"):
            clustered_bootstrap_mean({"case": (math.nan,)}, seed=1)
        with self.assertRaisesRegex(ValueError, "resamples"):
            clustered_bootstrap_mean({"case": (1.0,)}, seed=1, resamples=True)

    def test_report_ceiling_includes_runner_metadata(self) -> None:
        runner = PairwiseEvaluationRunner(
            (
                DeterministicGenerator("a", lambda prompt, profile, seed: "left"),
                DeterministicGenerator("b", lambda prompt, profile, seed: "right"),
            ),
            _DecisionJudge(JudgeDecision(ComparisonOutcome.TIE)),
            AllPairsStrategy(shuffle=False),
            BradleyTerryRanker(),
            metadata={"blob": "x" * 20_000},
            max_report_chars=5_000,
        )
        with self.assertRaisesRegex(ValueError, "exceeds max_report_chars"):
            runner.run(_profile(), (_prompt(),), seed=1)


if __name__ == "__main__":
    unittest.main()
