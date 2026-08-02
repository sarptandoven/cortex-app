from __future__ import annotations

import math
import unittest

from backend.app.twin_eval import (
    AllPairsStrategy,
    CitedProfileItem,
    EstimateRange,
    EvaluationPrompt,
    HeldOutProfile,
    PreflightAssumptions,
    PreflightBudget,
    PreflightPricing,
    RepeatedSwappedStrategy,
    canonical_hash,
    estimate_pairwise_workload,
)
from backend.app.twin_eval.scheduling import build_evaluation_schedule
from backend.bench.pairwise_twin import (
    SYSTEMS,
    benchmark_profile,
    benchmark_prompts,
    build_offline_benchmark_report,
)


class DuplicatePlanStrategy:
    strategy_id = "duplicate"

    def reproducibility_config(self) -> dict[str, object]:
        return {}

    def plan(self, prompts, system_ids, *, seed):
        plans = AllPairsStrategy(shuffle=False).plan(
            prompts,
            system_ids,
            seed=seed,
        )
        return plans + (plans[0],)


class PairwisePreflightTests(unittest.TestCase):
    def setUp(self) -> None:
        self.profile = HeldOutProfile(
            "preflight",
            (CitedProfileItem("m1", "Use concise answers."),),
        )
        self.prompts = (
            EvaluationPrompt("p1", "Answer one."),
            EvaluationPrompt("p2", "Answer two."),
        )
        self.systems = ("a", "b", "c")

    def test_exact_schedule_counts_for_repeated_swapped_strategy(self) -> None:
        estimate = estimate_pairwise_workload(
            self.profile,
            self.prompts,
            self.systems,
            RepeatedSwappedStrategy(repetitions=3, shuffle=False),
            seed=7,
        ).to_dict()

        self.assertEqual(estimate["provider_calls_made"], 0)
        self.assertEqual(estimate["schedule"]["candidate_generations"], 6)
        self.assertEqual(estimate["schedule"]["raw_judgments"], 36)
        self.assertEqual(estimate["schedule"]["logical_comparisons"], 18)
        self.assertEqual(estimate["schedule"]["swapped_presentations"], 18)
        self.assertEqual(estimate["schedule"]["total_provider_calls"], 42)
        schedule = build_evaluation_schedule(
            self.profile,
            self.prompts,
            self.systems,
            RepeatedSwappedStrategy(repetitions=3, shuffle=False),
            seed=7,
        )
        self.assertEqual(
            estimate["schedule"]["schedule_digest"],
            canonical_hash(schedule.plans),
        )

    def test_all_pairs_schedule_is_not_assumed_to_be_swapped(self) -> None:
        estimate = estimate_pairwise_workload(
            self.profile,
            self.prompts,
            self.systems,
            AllPairsStrategy(repetitions=2, swap_sides=False, shuffle=False),
        ).to_dict()
        self.assertEqual(estimate["schedule"]["raw_judgments"], 12)
        self.assertEqual(estimate["schedule"]["logical_comparisons"], 12)

    def test_token_cost_and_parallel_runtime_ranges_are_auditable(self) -> None:
        assumptions = PreflightAssumptions(
            candidate_output_chars=EstimateRange(100, 200, 400),
            judge_output_tokens_per_call=EstimateRange(10, 20, 40),
            generator_latency_seconds=EstimateRange(1, 2, 4),
            judge_latency_seconds=EstimateRange(2, 3, 8),
            chars_per_token=4,
            max_parallel_generations=4,
            max_parallel_judgments=5,
            pricing=PreflightPricing(1, 2, 3, 4),
        )
        estimate = estimate_pairwise_workload(
            self.profile,
            self.prompts,
            self.systems,
            RepeatedSwappedStrategy(repetitions=1, shuffle=False),
            assumptions=assumptions,
        ).to_dict()

        self.assertEqual(estimate["concurrency"]["generation_batches"], 2)
        self.assertEqual(estimate["concurrency"]["judgment_batches"], 3)
        self.assertEqual(
            estimate["duration_seconds"],
            {"lower": 8, "expected": 13, "upper": 32},
        )
        self.assertIsNotNone(estimate["cost_usd"])
        self.assertLessEqual(
            estimate["tokens"]["total"]["lower"],
            estimate["tokens"]["total"]["expected"],
        )
        self.assertLessEqual(
            estimate["tokens"]["total"]["expected"],
            estimate["tokens"]["total"]["upper"],
        )

    def test_budgets_use_conservative_upper_estimates(self) -> None:
        estimate = estimate_pairwise_workload(
            self.profile,
            self.prompts,
            self.systems,
            RepeatedSwappedStrategy(repetitions=1, shuffle=False),
            budget=PreflightBudget(
                max_provider_calls=10,
                max_total_tokens=1,
                max_duration_seconds=1,
            ),
        )

        self.assertFalse(estimate.within_budget)
        metrics = {
            violation["metric"]
            for violation in estimate.to_dict()["budget"]["violations"]
        }
        self.assertEqual(
            metrics,
            {"provider_calls", "total_tokens", "duration_seconds"},
        )

    def test_cost_budget_requires_pricing(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires pricing"):
            estimate_pairwise_workload(
                self.profile,
                self.prompts,
                self.systems,
                AllPairsStrategy(),
                budget=PreflightBudget(max_cost_usd=1),
            )

    def test_invalid_or_non_finite_assumptions_are_rejected(self) -> None:
        for invalid in (0, -1, math.inf, math.nan, True):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    PreflightAssumptions(chars_per_token=invalid)

    def test_same_inputs_produce_identical_forecast(self) -> None:
        kwargs = {
            "profile": self.profile,
            "prompts": self.prompts,
            "system_ids": self.systems,
            "strategy": RepeatedSwappedStrategy(repetitions=2),
            "seed": 93,
        }
        first = estimate_pairwise_workload(**kwargs).to_dict()
        second = estimate_pairwise_workload(**kwargs).to_dict()
        self.assertEqual(first, second)

    def test_estimate_payload_is_immutable_from_the_callers_view(self) -> None:
        estimate = estimate_pairwise_workload(
            self.profile,
            self.prompts,
            self.systems,
            AllPairsStrategy(),
        )
        changed = estimate.payload
        changed["budget"]["within_budget"] = False
        self.assertTrue(estimate.within_budget)

    def test_malformed_schedule_is_rejected_before_any_provider_call(self) -> None:
        with self.assertRaisesRegex(ValueError, "duplicate comparison_id"):
            estimate_pairwise_workload(
                self.profile,
                self.prompts,
                self.systems,
                DuplicatePlanStrategy(),
            )

    def test_estimate_digest_matches_executed_benchmark_schedule(self) -> None:
        report = build_offline_benchmark_report(seed=7, repetitions=1)
        estimate = estimate_pairwise_workload(
            benchmark_profile(),
            benchmark_prompts(),
            SYSTEMS,
            RepeatedSwappedStrategy(repetitions=1),
            seed=7,
        ).to_dict()
        self.assertEqual(
            estimate["schedule"]["schedule_digest"],
            report.metadata["reproducibility_manifest"]["plan_digest"],
        )


if __name__ == "__main__":
    unittest.main()
