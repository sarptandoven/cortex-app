#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.twin_eval import (
    EstimateRange,
    PreflightAssumptions,
    PreflightBudget,
    PreflightPricing,
    RepeatedSwappedStrategy,
    build_cli_repository,
    estimate_pairwise_workload,
)
from backend.bench.pairwise_twin import (
    SYSTEMS,
    benchmark_profile,
    benchmark_prompts,
    run_offline_benchmark_with_report,
)


def _lower_bound(expected: float) -> float:
    return expected / 4


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic offline pairwise-twin benchmark.")
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--user-id", default="local")
    parser.add_argument("--replay-run-id")
    parser.add_argument(
        "--keyring-db-path",
        type=Path,
        help=(
            "hosted keyring database; requires CORTEX_KEK or "
            "CORTEX_KEK_FILE"
        ),
    )
    parser.add_argument(
        "--allow-plaintext-report",
        action="store_true",
        help=(
            "allow unencrypted persistence for this deterministic offline "
            "benchmark only; never use for Cortex/user data"
        ),
    )
    parser.add_argument(
        "--estimate-only",
        action="store_true",
        help="print a zero-call workload forecast instead of executing the benchmark",
    )
    parser.add_argument("--candidate-chars-expected", type=int, default=4_000)
    parser.add_argument("--candidate-chars-upper", type=int, default=16_000)
    parser.add_argument("--judge-output-tokens-expected", type=int, default=256)
    parser.add_argument("--judge-output-tokens-upper", type=int, default=1_024)
    parser.add_argument("--generator-latency-seconds", type=float, default=5.0)
    parser.add_argument("--generator-timeout-seconds", type=float, default=30.0)
    parser.add_argument("--judge-latency-seconds", type=float, default=5.0)
    parser.add_argument("--judge-timeout-seconds", type=float, default=60.0)
    parser.add_argument("--max-parallel-generations", type=int, default=1)
    parser.add_argument("--max-parallel-judgments", type=int, default=1)
    parser.add_argument(
        "--generator-input-price",
        type=float,
        help="generator input USD per million tokens",
    )
    parser.add_argument(
        "--generator-output-price",
        type=float,
        help="generator output USD per million tokens",
    )
    parser.add_argument(
        "--judge-input-price",
        type=float,
        help="judge input USD per million tokens",
    )
    parser.add_argument(
        "--judge-output-price",
        type=float,
        help="judge output USD per million tokens",
    )
    parser.add_argument("--max-provider-calls", type=int)
    parser.add_argument("--max-total-tokens", type=int)
    parser.add_argument("--max-cost-usd", type=float)
    parser.add_argument("--max-duration-seconds", type=float)
    args = parser.parse_args()
    if args.estimate_only:
        if (
            args.db_path is not None
            or args.replay_run_id is not None
            or args.allow_plaintext_report
            or args.keyring_db_path is not None
        ):
            parser.error("--estimate-only cannot be combined with persistence or replay")
        price_values = (
            args.generator_input_price,
            args.generator_output_price,
            args.judge_input_price,
            args.judge_output_price,
        )
        if any(value is not None for value in price_values) and not all(
            value is not None for value in price_values
        ):
            parser.error("all four pricing arguments are required when pricing is used")
        try:
            pricing = (
                PreflightPricing(*price_values)
                if all(value is not None for value in price_values)
                else None
            )
            assumptions = PreflightAssumptions(
                candidate_output_chars=EstimateRange(
                    _lower_bound(args.candidate_chars_expected),
                    args.candidate_chars_expected,
                    args.candidate_chars_upper,
                ),
                judge_output_tokens_per_call=EstimateRange(
                    _lower_bound(args.judge_output_tokens_expected),
                    args.judge_output_tokens_expected,
                    args.judge_output_tokens_upper,
                ),
                generator_latency_seconds=EstimateRange(
                    _lower_bound(args.generator_latency_seconds),
                    args.generator_latency_seconds,
                    args.generator_timeout_seconds,
                ),
                judge_latency_seconds=EstimateRange(
                    _lower_bound(args.judge_latency_seconds),
                    args.judge_latency_seconds,
                    args.judge_timeout_seconds,
                ),
                max_parallel_generations=args.max_parallel_generations,
                max_parallel_judgments=args.max_parallel_judgments,
                pricing=pricing,
            )
            budget = PreflightBudget(
                max_provider_calls=args.max_provider_calls,
                max_total_tokens=args.max_total_tokens,
                max_cost_usd=args.max_cost_usd,
                max_duration_seconds=args.max_duration_seconds,
            )
            estimate = estimate_pairwise_workload(
                benchmark_profile(),
                benchmark_prompts(),
                SYSTEMS,
                RepeatedSwappedStrategy(repetitions=args.repetitions),
                seed=args.seed,
                assumptions=assumptions,
                budget=budget,
            )
        except (TypeError, ValueError) as exc:
            parser.error(str(exc))
        print(json.dumps(estimate.to_dict(), indent=2, sort_keys=True))
        return 0 if estimate.within_budget else 1

    if args.replay_run_id:
        if args.db_path is None:
            parser.error("--replay-run-id requires --db-path")
        init_db(args.db_path)
        repository = build_cli_repository(
            args.db_path,
            keyring_db_path=args.keyring_db_path,
            allow_plaintext_reports=args.allow_plaintext_report,
        )
        repository.replay_bundle(args.user_id, args.replay_run_id)
        report = repository.load_report(args.user_id, args.replay_run_id)
        result = {
            "schema_version": "pairwise-twin-replay-summary/v1",
            "status": "replayed",
            "run_id": report.run_id,
            "artifact_digest": report.artifact_digest,
            "prompts": len(report.prompts),
            "systems": len(report.systems),
            "raw_judgments": len(report.comparisons),
            "logical_comparisons": len(report.resolved_comparisons),
            "ranking_connected": report.ranking.diagnostics.connected,
            "ranking_converged": report.ranking.diagnostics.converged,
            "content_included": False,
        }
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0

    result, report = run_offline_benchmark_with_report(
        seed=args.seed,
        repetitions=args.repetitions,
    )
    if args.db_path is not None:
        if (
            not args.allow_plaintext_report
            and args.keyring_db_path is None
        ):
            parser.error(
                "--db-path persistence requires --keyring-db-path or the "
                "local/test-only --allow-plaintext-report escape hatch"
            )
        init_db(args.db_path)
        build_cli_repository(
            args.db_path,
            keyring_db_path=args.keyring_db_path,
            allow_plaintext_reports=args.allow_plaintext_report,
        ).save_report(args.user_id, report)
        result["persisted"] = True
        result["db_path"] = str(args.db_path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
