from __future__ import annotations

import unittest

from backend.bench.pairwise_twin import benchmark_cases, run_offline_benchmark


class PairwiseTwinBenchmarkTests(unittest.TestCase):
    def test_manifest_has_six_balanced_adversarial_strata(self) -> None:
        cases = benchmark_cases()
        counts: dict[str, int] = {}
        for case in cases:
            counts[case.stratum] = counts.get(case.stratum, 0) + 1
        self.assertEqual(len(cases), 24)
        self.assertEqual(
            counts,
            {
                "clear_style": 4,
                "negative_constraint": 4,
                "near_tie": 4,
                "conflicting": 4,
                "sparse_noisy": 4,
                "deceptive": 4,
            },
        )

    def test_offline_benchmark_meets_preregistered_mechanical_gates(self) -> None:
        result = run_offline_benchmark(seed=7)
        self.assertTrue(result["passed"])
        self.assertEqual(result["synthetic_pair_accuracy"], 1.0)
        self.assertEqual(result["swap_agreement"], 1.0)
        self.assertEqual(result["repeat_agreement"], 1.0)
        self.assertEqual(result["position_bias"], 0.0)
        self.assertEqual(result["invalid_rate"], 0.0)
        self.assertEqual(result["hard_constraint_violation_win_rate"], 0.0)
        self.assertAlmostEqual(result["reported_confidence_coverage"], 5 / 6)
        self.assertEqual(result["mean_reported_confidence"], 1.0)
        self.assertAlmostEqual(
            result["synthetic_five_bin_oracle_baseline_accuracy"],
            71 / 72,
        )
        self.assertAlmostEqual(result["paired_recovery_delta"], 1 / 72)
        self.assertEqual(result["paired_recovery_delta_ci95"]["clusters"], 24)
        self.assertEqual(result["paired_recovery_delta_ci95"]["resamples"], 2_000)
        self.assertLessEqual(
            result["paired_recovery_delta_ci95"]["low"],
            result["paired_recovery_delta"],
        )
        self.assertGreaterEqual(
            result["paired_recovery_delta_ci95"]["high"],
            result["paired_recovery_delta"],
        )
        self.assertTrue(result["ranking_connected"])
        self.assertTrue(result["ranking_converged"])
        self.assertTrue(result["top_recovered"])

    def test_same_seed_replays_exactly_and_seed_changes_preserve_results(self) -> None:
        first = run_offline_benchmark(seed=7)
        replay = run_offline_benchmark(seed=7)
        alternate = run_offline_benchmark(seed=41)
        self.assertEqual(first, replay)
        self.assertEqual(first["artifact_digest"], replay["artifact_digest"])
        self.assertNotEqual(first["run_id"], alternate["run_id"])
        self.assertEqual(first["dataset_digest"], alternate["dataset_digest"])
        for key in (
            "synthetic_pair_accuracy",
            "swap_agreement",
            "repeat_agreement",
            "position_bias",
            "actual_top",
            "ranking",
        ):
            self.assertEqual(first[key], alternate[key])


if __name__ == "__main__":
    unittest.main()
