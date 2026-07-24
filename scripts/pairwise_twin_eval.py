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
from backend.app.twin_eval import TwinEvalRepository
from backend.bench.pairwise_twin import run_offline_benchmark_with_report


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the deterministic offline pairwise-twin benchmark.")
    parser.add_argument("--seed", type=int, default=20260724)
    parser.add_argument("--repetitions", type=int, default=3)
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--user-id", default="local")
    parser.add_argument("--replay-run-id")
    args = parser.parse_args()
    if args.replay_run_id:
        if args.db_path is None:
            parser.error("--replay-run-id requires --db-path")
        init_db(args.db_path)
        repository = TwinEvalRepository(args.db_path)
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
        init_db(args.db_path)
        TwinEvalRepository(args.db_path).save_report(args.user_id, report)
        result["persisted"] = True
        result["db_path"] = str(args.db_path)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
