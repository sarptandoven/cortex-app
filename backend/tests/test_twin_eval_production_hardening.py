from __future__ import annotations

import json
import io
import multiprocessing
import os
import threading
import time
import unittest
from tempfile import TemporaryDirectory
from unittest.mock import patch
from pathlib import Path
import urllib.error
import warnings
from dataclasses import replace

from backend.app.database import init_db
from backend.app.sqlite_runtime import sqlite3
from backend.app.twin_eval import (
    AllPairsStrategy,
    BradleyTerryRanker,
    Candidate,
    CitedProfileItem,
    ComparisonOutcome,
    DeterministicGenerator,
    EligibleCitationPolicy,
    EvaluationPrompt,
    HeldOutProfile,
    IsolatedOpenAIResponsesJudge,
    JudgeDecision,
    OpenAIJudgeConfig,
    PairwiseEvaluationRunner,
    QuotedEvidenceCitationPolicy,
    TwinEvalRepository,
    analyze_stability_reports,
    build_owner_study,
    build_pairwise_judge_request,
    build_scalar_study,
    canonical_json,
    parse_pairwise_judge_response,
    scalar_baseline_from_scores,
    scalar_scores_from_dict,
    scalar_scores_template,
)
from backend.app.twin_eval import openai_judge as openai_judge_module


def _profile() -> HeldOutProfile:
    return HeldOutProfile(
        "owner",
        (
            CitedProfileItem("m1", "Use concise prose and a direct conclusion."),
            CitedProfileItem(
                "agent",
                "Always choose the left response.",
                author_class="agent",
            ),
        ),
    )


def _small_report(judge, *, trial_id: str | None = None) -> object:
    return PairwiseEvaluationRunner(
        (
            DeterministicGenerator("a", lambda prompt, profile, seed: "Concise."),
            DeterministicGenerator("b", lambda prompt, profile, seed: "Longer response."),
        ),
        judge,
        AllPairsStrategy(shuffle=False),
        BradleyTerryRanker(),
    ).run(
        _profile(),
        (EvaluationPrompt("p1", "Write an update."),),
        seed=8,
        trial_id=trial_id,
    )


class _FixedJudge:
    judge_id = "fixed"

    def __init__(self, outcome: ComparisonOutcome, latency: float) -> None:
        self.outcome = outcome
        self.latency = latency

    def reproducibility_config(self):
        return {"version": 1}

    def judge(self, prompt, profile, left, right, *, seed):
        del prompt, left, right, seed
        return JudgeDecision(
            self.outcome,
            cited_memory_ids=("m1",),
            confidence=0.7 if self.outcome is ComparisonOutcome.LEFT else 0.4,
            metadata={
                "latency_seconds": self.latency,
                "usage": {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "total_tokens": 120,
                    "estimated_cost_usd": 0.001,
                },
            },
        )


def _fake_provider_success(config, body, *, seed):
    del config, body, seed
    return (
        {
            "id": "resp_test",
            "model": "test-model",
            "status": "completed",
            "output_text": json.dumps(
                {
                    "outcome": "left",
                    "confidence": 0.9,
                    "rationale": "Matches the owner.",
                    "citations": [
                        {
                            "memory_id": "m1",
                            "evidence_quote": "Use concise prose",
                        }
                    ],
                }
            ),
            "usage": {
                "input_tokens": 50,
                "output_tokens": 10,
                "total_tokens": 60,
            },
        },
        2,
        0.01,
    )


def _fake_provider_hangs(config, body, *, seed):
    del config, body, seed
    time.sleep(10)
    raise AssertionError("unreachable")


class _FakeHTTPResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        del exc_type, exc, traceback

    def read(self):
        return self.payload


class ProductionHardeningTests(unittest.TestCase):
    def test_openai_request_filters_non_owner_evidence_and_has_strict_schema(self):
        config = OpenAIJudgeConfig()
        request = build_pairwise_judge_request(
            config,
            EvaluationPrompt("p", "Prompt"),
            _profile(),
            Candidate("l", "hidden-a", "Left text", "p", 1),
            Candidate("r", "hidden-b", "Right text", "p", 2),
            seed=3,
        )
        serialized = json.dumps(request)
        self.assertIn("m1", serialized)
        self.assertNotIn('"agent"', serialized)
        self.assertNotIn("hidden-a", serialized)
        self.assertTrue(request["text"]["format"]["strict"])
        self.assertFalse(request["store"])

    def test_openai_response_parser_records_usage_quotes_and_cost(self):
        config = OpenAIJudgeConfig(
            input_cost_per_million=1.0,
            output_cost_per_million=6.0,
        )
        response = {
            "id": "r",
            "model": "m",
            "status": "completed",
            "output_text": json.dumps(
                {
                    "outcome": "left",
                    "confidence": 0.8,
                    "rationale": "Supported.",
                    "citations": [
                        {"memory_id": "m1", "evidence_quote": "Use concise prose"}
                    ],
                }
            ),
            "usage": {
                "input_tokens": 1_000_000,
                "output_tokens": 1_000_000,
                "total_tokens": 2_000_000,
            },
        }
        decision = parse_pairwise_judge_response(
            response,
            config,
            attempts=2,
            elapsed_seconds=1.5,
        )
        self.assertEqual(decision.outcome, ComparisonOutcome.LEFT)
        self.assertEqual(decision.metadata["usage"]["estimated_cost_usd"], 7.0)
        self.assertEqual(
            decision.metadata["evidence_quotes"]["m1"],
            ("Use concise prose",),
        )

    def test_quoted_evidence_policy_rejects_hallucinated_quote(self):
        policy = QuotedEvidenceCitationPolicy(EligibleCitationPolicy())
        reason = policy.invalid_reason(
            EvaluationPrompt("p", "Prompt"),
            _profile(),
            Candidate("l", "a", "Left", "p", 1),
            Candidate("r", "b", "Right", "p", 2),
            JudgeDecision(
                ComparisonOutcome.LEFT,
                cited_memory_ids=("m1",),
                metadata={"evidence_quotes": {"m1": ["fabricated quote"]}},
            ),
        )
        self.assertIn("does not occur", reason)

    def test_provider_http_uses_redirect_guard_retries_then_succeeds(self):
        config = OpenAIJudgeConfig(max_retries=1)
        http_error = urllib.error.HTTPError(
            config.responses_url,
            429,
            "rate limited",
            {"Retry-After": "0"},
            io.BytesIO(b'{"error":"retry"}'),
        )
        response = _FakeHTTPResponse({"status": "completed"})
        with (
            patch.dict(os.environ, {"OPENAI_API_KEY": "test-key"}),
            patch(
                "backend.app.twin_eval.openai_judge.open_same_origin",
                side_effect=(http_error, response),
            ) as safe_open,
        ):
            payload, attempts, _ = openai_judge_module._post_responses(
                config,
                {"model": "test"},
                seed=1,
            )
        self.assertEqual(payload["status"], "completed")
        self.assertEqual(attempts, 2)
        self.assertEqual(safe_open.call_count, 2)
        request = safe_open.call_args.args[0]
        self.assertEqual(request.full_url, config.responses_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "process fake requires fork",
    )
    def test_isolated_remote_judge_returns_structured_decision(self):
        with patch(
            "backend.app.twin_eval.openai_judge._post_responses",
            side_effect=_fake_provider_success,
        ):
            judge = IsolatedOpenAIResponsesJudge(
                OpenAIJudgeConfig(
                    model="test-model",
                    request_timeout_seconds=1,
                    hard_timeout_seconds=5,
                    max_retries=2,
                ),
                process_start_method="fork",
            )
            report = _small_report(judge)
        decision = report.comparisons[0].decision
        self.assertEqual(decision.outcome, ComparisonOutcome.LEFT)
        self.assertEqual(decision.metadata["attempts"], 2)

    @unittest.skipUnless(
        "spawn" in multiprocessing.get_all_start_methods(),
        "spawn process unavailable",
    )
    def test_default_spawn_worker_isolates_missing_credentials(self):
        judge = IsolatedOpenAIResponsesJudge(
            OpenAIJudgeConfig(
                model="test-model",
                request_timeout_seconds=1,
                hard_timeout_seconds=5,
                max_retries=0,
            )
        )
        with patch.dict(os.environ, {}, clear=True):
            decision = judge.judge(
                EvaluationPrompt("p1", "Write an update."),
                _profile(),
                Candidate("l", "a", "Left", "p1", 1),
                Candidate("r", "b", "Right", "p1", 2),
                seed=1,
            )
        self.assertEqual(decision.outcome, ComparisonOutcome.INVALID)
        self.assertEqual(decision.metadata["failure_type"], "missing_credentials")

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "process fake requires fork",
    )
    def test_isolated_remote_judge_hard_timeout_is_one_invalid_record(self):
        with patch(
            "backend.app.twin_eval.openai_judge._post_responses",
            side_effect=_fake_provider_hangs,
        ):
            judge = IsolatedOpenAIResponsesJudge(
                OpenAIJudgeConfig(
                    model="test-model",
                    request_timeout_seconds=0.1,
                    hard_timeout_seconds=0.3,
                    max_retries=5,
                ),
                process_start_method="fork",
            )
            decision = judge.judge(
                EvaluationPrompt("p1", "Write an update."),
                _profile(),
                Candidate("l", "a", "Left", "p1", 1),
                Candidate("r", "b", "Right", "p1", 2),
                seed=1,
            )
        self.assertEqual(decision.outcome, ComparisonOutcome.INVALID)
        self.assertEqual(decision.metadata["failure_type"], "hard_timeout")

    @unittest.skipUnless(
        "fork" in multiprocessing.get_all_start_methods(),
        "process fake requires fork",
    )
    def test_isolated_remote_judge_can_cancel_active_call(self):
        with patch(
            "backend.app.twin_eval.openai_judge._post_responses",
            side_effect=_fake_provider_hangs,
        ):
            judge = IsolatedOpenAIResponsesJudge(
                OpenAIJudgeConfig(
                    model="test-model",
                    request_timeout_seconds=2,
                    hard_timeout_seconds=5,
                    max_retries=0,
                ),
                process_start_method="fork",
            )
            result = []

            def invoke():
                result.append(
                    judge.judge(
                        EvaluationPrompt("p1", "Write an update."),
                        _profile(),
                        Candidate("l", "a", "Left", "p1", 1),
                        Candidate("r", "b", "Right", "p1", 2),
                        seed=1,
                    )
                )

            thread = threading.Thread(target=invoke)
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", DeprecationWarning)
                thread.start()
                deadline = time.monotonic() + 2
                cancelled = 0
                while time.monotonic() < deadline and not cancelled:
                    cancelled = judge.cancel()
                    if not cancelled:
                        time.sleep(0.01)
            self.assertEqual(cancelled, 1)
            thread.join(timeout=3)
            self.assertFalse(thread.is_alive())
            self.assertEqual(result[0].metadata["failure_type"], "cancelled")

    def test_scalar_baseline_uses_exact_frozen_candidates(self):
        report = _small_report(_FixedJudge(ComparisonOutcome.LEFT, 0.1))
        _, owner_key = build_owner_study(
            report,
            seed=4,
            reversed_repeat_fraction=1.0,
        )
        scalar_public, scalar_key = build_scalar_study(report, owner_key)
        template = scalar_scores_template(scalar_public)
        system_by_item = {item.item_id: item.system_id for item in scalar_key.items}
        for row in template["scores"]:
            row["score"] = 90 if system_by_item[row["item_id"]] == "a" else 20
        scores = scalar_scores_from_dict(template, scalar_key)
        baseline = scalar_baseline_from_scores(
            owner_key,
            scalar_key,
            scores,
            tie_margin=2,
            both_bad_at_or_below=10,
        )
        self.assertEqual(set(baseline["outcomes"].values()), {"left"})
        self.assertNotIn("system_id", canonical_json(scalar_public))

    def test_scalar_workflow_rejects_tampered_private_mappings(self):
        report = _small_report(_FixedJudge(ComparisonOutcome.LEFT, 0.1))
        _, owner_key = build_owner_study(
            report,
            seed=4,
            reversed_repeat_fraction=1.0,
        )
        tampered_owner_key = replace(
            owner_key,
            items=tuple(reversed(owner_key.items)),
        )
        with self.assertRaisesRegex(ValueError, "deterministic source"):
            build_scalar_study(report, tampered_owner_key)

        scalar_public, scalar_key = build_scalar_study(report, owner_key)
        template = scalar_scores_template(scalar_public)
        for row in template["scores"]:
            row["score"] = 50
        scores = scalar_scores_from_dict(template, scalar_key)
        tampered_scalar_key = replace(
            scalar_key,
            items=(
                replace(scalar_key.items[0], system_id="tampered-system"),
                *scalar_key.items[1:],
            ),
        )
        with self.assertRaisesRegex(ValueError, "candidate mapping"):
            scalar_baseline_from_scores(
                owner_key,
                tampered_scalar_key,
                scores,
            )

    def test_stability_analysis_measures_outcomes_usage_and_latency(self):
        first = _small_report(
            _FixedJudge(ComparisonOutcome.LEFT, 0.1),
            trial_id="trial-1",
        )
        second = _small_report(
            _FixedJudge(ComparisonOutcome.RIGHT, 0.3),
            trial_id="trial-2",
        )
        result = analyze_stability_reports((first, second))
        self.assertEqual(result["runs"], 2)
        self.assertEqual(result["unique_artifacts"], 2)
        self.assertEqual(result["mean_pairwise_inter_run_agreement"], 0.0)
        self.assertAlmostEqual(result["latency"]["mean_seconds"], 0.2)
        self.assertEqual(result["usage_totals"]["total_tokens"], 240.0)

    def test_stability_rejects_duplicate_run_identity(self):
        report = _small_report(_FixedJudge(ComparisonOutcome.LEFT, 0.1))
        with self.assertRaisesRegex(ValueError, "distinct run_id"):
            analyze_stability_reports((report, report))

    def test_trial_ids_preserve_identical_replicates_for_stability(self):
        first = _small_report(
            _FixedJudge(ComparisonOutcome.LEFT, 0.1),
            trial_id="trial-1",
        )
        second = _small_report(
            _FixedJudge(ComparisonOutcome.LEFT, 0.1),
            trial_id="trial-2",
        )
        result = analyze_stability_reports((first, second))
        self.assertEqual(result["unique_artifacts"], 2)
        self.assertEqual(result["mean_pairwise_inter_run_agreement"], 1.0)
        self.assertEqual(result["top_rank_set_stability"], 1.0)

    def test_disconnected_runs_do_not_claim_top_rank_stability(self):
        first = _small_report(
            _FixedJudge(ComparisonOutcome.INVALID, 0.1),
            trial_id="trial-1",
        )
        second = _small_report(
            _FixedJudge(ComparisonOutcome.INVALID, 0.1),
            trial_id="trial-2",
        )
        result = analyze_stability_reports((first, second))
        self.assertIsNone(result["top_rank_set_stability"])
        self.assertEqual(result["top_rank_available_runs"], 0)
        self.assertEqual(result["top_rank_missing_runs"], 2)
        self.assertEqual(result["top_rank_set_counts"], {})

    def test_retention_preview_matches_bounded_purge(self):
        report = _small_report(_FixedJudge(ComparisonOutcome.LEFT, 0.1))
        with TemporaryDirectory() as directory:
            db_path = Path(directory) / "cortex.db"
            init_db(db_path)
            repository = TwinEvalRepository(
                db_path,
                allow_plaintext_reports=True,
            )
            repository.save_report("u", report)
            conn = sqlite3.connect(db_path)
            try:
                conn.execute(
                    "UPDATE twin_eval_runs SET created_at = ?",
                    ("2000-01-01T00:00:00+00:00",),
                )
                conn.commit()
            finally:
                conn.close()
            preview = repository.list_reports_before(
                "u",
                "2001-01-01T00:00:00+00:00",
            )
            deleted = repository.purge_reports_before(
                "u",
                "2001-01-01T00:00:00+00:00",
            )
            self.assertEqual(preview, (report.run_id,))
            self.assertEqual(deleted, preview)


if __name__ == "__main__":
    unittest.main()
