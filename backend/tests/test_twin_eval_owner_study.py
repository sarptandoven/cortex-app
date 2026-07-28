from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from backend.app.database import init_db
from backend.app.keyring import LocalKekProvider, UserKeyring
from backend.app.twin_eval import (
    ComparisonOutcome,
    OwnerLabel,
    OwnerLabelOutcome,
    TwinEvalRepository,
    analyze_owner_study,
    build_owner_study,
    canonical_json,
    cohort_from_dict,
    key_from_dict,
    labels_from_dict,
    labels_template,
)
from backend.bench.pairwise_twin import build_offline_benchmark_report


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "pairwise_twin_owner_study.py"
KEK_B64 = base64.b64encode(bytes(range(32))).decode("ascii")


def _perfect_labels(report, key) -> tuple[OwnerLabel, ...]:
    resolved = {
        item.logical_comparison_id: item
        for item in report.resolved_comparisons
    }
    labels = []
    for item in key.items:
        outcome = resolved[item.logical_comparison_id].outcome
        if outcome is ComparisonOutcome.LEFT:
            owner = (
                OwnerLabelOutcome.A
                if item.displayed_a_system_id == item.canonical_system_a_id
                else OwnerLabelOutcome.B
            )
        elif outcome is ComparisonOutcome.RIGHT:
            owner = (
                OwnerLabelOutcome.A
                if item.displayed_a_system_id == item.canonical_system_b_id
                else OwnerLabelOutcome.B
            )
        elif outcome is ComparisonOutcome.TIE:
            owner = OwnerLabelOutcome.TIE
        elif outcome is ComparisonOutcome.BOTH_BAD:
            owner = OwnerLabelOutcome.BOTH_BAD
        else:
            owner = OwnerLabelOutcome.ABSTAIN
        labels.append(OwnerLabel(item.item_id, owner))
    return tuple(labels)


class OwnerStudyContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.report = build_offline_benchmark_report(seed=7, repetitions=3)

    def test_export_is_deterministic_blinded_and_contains_reversed_repeats(self) -> None:
        first = build_owner_study(
            self.report,
            seed=19,
            reversed_repeat_fraction=0.2,
        )
        second = build_owner_study(
            self.report,
            seed=19,
            reversed_repeat_fraction=0.2,
        )
        self.assertEqual(first, second)
        cohort, key = first

        self.assertEqual(len({item.pair_group_id for item in key.items}), 72)
        self.assertEqual(len(cohort.items), 86)
        self.assertEqual(sum(item.is_reversed_repeat for item in key.items), 14)
        public_json = canonical_json(cohort)
        self.assertNotIn("logical_comparison_id", public_json)
        self.assertNotIn("displayed_a_system_id", public_json)
        self.assertNotIn("candidate_id", public_json)

        public_by_id = {item.item_id: item for item in cohort.items}
        groups = {}
        for item in key.items:
            groups.setdefault(item.pair_group_id, []).append(item)
        for group in groups.values():
            if len(group) == 2:
                first_public = public_by_id[group[0].item_id]
                second_public = public_by_id[group[1].item_id]
                self.assertEqual(first_public.response_a, second_public.response_b)
                self.assertEqual(first_public.response_b, second_public.response_a)

    def test_perfect_owner_labels_report_agreement_and_repeat_stability(self) -> None:
        cohort, key = build_owner_study(
            self.report,
            seed=19,
            reversed_repeat_fraction=0.2,
        )
        labels = _perfect_labels(self.report, key)
        baseline = {
            item.pair_group_id: ComparisonOutcome.INVALID
            for item in key.items
        }
        result = analyze_owner_study(
            self.report,
            cohort,
            key,
            labels,
            bootstrap_seed=3,
            bootstrap_resamples=500,
            baseline_outcomes=baseline,
        )

        self.assertEqual(result["pairwise_owner_agreement"], 1.0)
        self.assertEqual(result["owner_repeat_agreement"], 1.0)
        self.assertEqual(result["baseline_owner_agreement"], 0.0)
        self.assertEqual(result["paired_pairwise_minus_baseline"], 1.0)
        self.assertEqual(
            result["pairwise_owner_agreement_ci95"]["clusters"],
            24,
        )

    def test_reversed_repeat_labels_measure_reliability_without_double_weighting(self) -> None:
        cohort, key = build_owner_study(
            self.report,
            seed=19,
            reversed_repeat_fraction=1.0,
        )
        perfect_by_id = {
            label.item_id: label for label in _perfect_labels(self.report, key)
        }
        labels = []
        for item in key.items:
            perfect = perfect_by_id[item.item_id]
            outcome = perfect.outcome
            if item.is_reversed_repeat:
                if outcome is OwnerLabelOutcome.A:
                    outcome = OwnerLabelOutcome.B
                elif outcome is OwnerLabelOutcome.B:
                    outcome = OwnerLabelOutcome.A
                else:
                    outcome = OwnerLabelOutcome.A
            labels.append(OwnerLabel(item.item_id, outcome))

        result = analyze_owner_study(
            self.report,
            cohort,
            key,
            labels,
            bootstrap_seed=3,
            bootstrap_resamples=100,
        )

        self.assertEqual(result["agreement_items"], 72)
        self.assertEqual(result["reversed_repeat_items"], 72)
        self.assertEqual(result["pairwise_owner_agreement"], 1.0)
        self.assertEqual(result["owner_repeat_agreement"], 0.0)

    def test_baseline_outcomes_must_exactly_cover_pair_groups(self) -> None:
        cohort, key = build_owner_study(
            self.report,
            seed=19,
            reversed_repeat_fraction=0.0,
        )
        baseline = {
            item.pair_group_id: ComparisonOutcome.TIE
            for item in key.items
        }
        baseline["unrelated-pair"] = ComparisonOutcome.TIE
        with self.assertRaisesRegex(ValueError, "exactly cover"):
            analyze_owner_study(
                self.report,
                cohort,
                key,
                _perfect_labels(self.report, key),
                baseline_outcomes=baseline,
            )

    def test_analysis_reconstructs_and_verifies_public_and_private_artifacts(self) -> None:
        cohort, key = build_owner_study(
            self.report,
            seed=19,
            reversed_repeat_fraction=0.2,
        )
        labels = _perfect_labels(self.report, key)
        tampered_public = replace(
            cohort,
            items=(
                replace(
                    cohort.items[0],
                    response_a=cohort.items[0].response_a + " tampered",
                ),
                *cohort.items[1:],
            ),
        )
        with self.assertRaisesRegex(ValueError, "public cohort"):
            analyze_owner_study(
                self.report,
                tampered_public,
                key,
                labels,
            )

        tampered_key = replace(key, items=tuple(reversed(key.items)))
        with self.assertRaisesRegex(ValueError, "private key"):
            analyze_owner_study(
                self.report,
                cohort,
                tampered_key,
                labels,
            )

    def test_json_roundtrip_and_incomplete_template_rejection(self) -> None:
        cohort, key = build_owner_study(self.report, seed=4)
        decoded_cohort = cohort_from_dict(json.loads(canonical_json(cohort)))
        decoded_key = key_from_dict(json.loads(canonical_json(key)))
        self.assertEqual(decoded_cohort, cohort)
        self.assertEqual(decoded_key, key)
        with self.assertRaisesRegex(ValueError, "incomplete"):
            labels_from_dict(labels_template(cohort))


class OwnerStudyCliTests(unittest.TestCase):
    def test_export_and_analyze_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            db_path = root / "cortex.sqlite"
            public_path = root / "public.json"
            key_path = root / "private-key.json"
            labels_path = root / "labels.json"
            output_path = root / "analysis.json"
            scalar_public_path = root / "scalar-public.json"
            scalar_key_path = root / "scalar-key.json"
            scalar_scores_path = root / "scalar-scores.json"
            scalar_baseline_path = root / "scalar-baseline.json"
            keyring_path = root / "keyring.sqlite"
            env = {**os.environ, "CORTEX_KEK": KEK_B64}
            init_db(db_path)
            report = build_offline_benchmark_report(seed=7, repetitions=1)
            TwinEvalRepository(
                db_path,
                artifact_cipher=UserKeyring(
                    keyring_path,
                    LocalKekProvider(
                        env={"CORTEX_KEK": KEK_B64}
                    ),
                ),
            ).save_report("owner", report)

            exported = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "export",
                    "--db-path",
                    str(db_path),
                    "--user-id",
                    "owner",
                    "--run-id",
                    report.run_id,
                    "--public-out",
                    str(public_path),
                    "--key-out",
                    str(key_path),
                    "--labels-out",
                    str(labels_path),
                    "--seed",
                    "5",
                    "--keyring-db-path",
                    str(keyring_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            export_result = json.loads(exported.stdout)
            self.assertEqual(export_result["status"], "exported")
            self.assertEqual(export_result["private_key_mode"], "0o600")
            self.assertNotIn("tests pass. ship monday.", exported.stdout)

            scalar_exported = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "scalar-export",
                    "--db-path",
                    str(db_path),
                    "--user-id",
                    "owner",
                    "--owner-key",
                    str(key_path),
                    "--public-out",
                    str(scalar_public_path),
                    "--key-out",
                    str(scalar_key_path),
                    "--scores-out",
                    str(scalar_scores_path),
                    "--keyring-db-path",
                    str(keyring_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            self.assertEqual(
                json.loads(scalar_exported.stdout)["status"],
                "scalar_exported",
            )
            scalar_key_payload = json.loads(
                scalar_key_path.read_text(encoding="utf-8")
            )
            system_by_item = {
                item["item_id"]: item["system_id"]
                for item in scalar_key_payload["items"]
            }
            score_by_system = {"strong": 90, "partial": 60, "mismatch": 10}
            scalar_scores_payload = json.loads(
                scalar_scores_path.read_text(encoding="utf-8")
            )
            for item in scalar_scores_payload["scores"]:
                item["score"] = score_by_system[system_by_item[item["item_id"]]]
            scalar_scores_path.write_text(
                json.dumps(scalar_scores_payload),
                encoding="utf-8",
            )
            scalar_baseline = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "scalar-baseline",
                    "--owner-key",
                    str(key_path),
                    "--scalar-key",
                    str(scalar_key_path),
                    "--scores",
                    str(scalar_scores_path),
                    "--output",
                    str(scalar_baseline_path),
                    "--tie-margin",
                    "2",
                    "--both-bad-at-or-below",
                    "5",
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
            )
            baseline_result = json.loads(scalar_baseline.stdout)
            self.assertEqual(
                baseline_result["method"],
                "independent_pointwise_0_100",
            )

            labels_payload = json.loads(labels_path.read_text(encoding="utf-8"))
            for item in labels_payload["labels"]:
                item["outcome"] = "tie"
            labels_path.write_text(
                json.dumps(labels_payload),
                encoding="utf-8",
            )
            analyzed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT),
                    "analyze",
                    "--db-path",
                    str(db_path),
                    "--user-id",
                    "owner",
                    "--public",
                    str(public_path),
                    "--key",
                    str(key_path),
                    "--labels",
                    str(labels_path),
                    "--output",
                    str(output_path),
                    "--baseline",
                    str(scalar_baseline_path),
                    "--bootstrap-resamples",
                    "100",
                    "--keyring-db-path",
                    str(keyring_path),
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            result = json.loads(analyzed.stdout)
            self.assertEqual(result["cohort_id"], export_result["cohort_id"])
            self.assertIn("baseline_owner_agreement", result)
            self.assertTrue(output_path.exists())


if __name__ == "__main__":
    unittest.main()
