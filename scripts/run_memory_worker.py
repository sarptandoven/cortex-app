#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.config import load_settings
from backend.app.sharding import StoreRegistry
from backend.app.worker import run_worker_tick


def _env_int(name: str, default: int) -> int:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.environ.get(name, "").strip()
    if not value:
        return default
    try:
        return float(value)
    except ValueError:
        return default


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run queued Cortex memory jobs.")
    parser.add_argument("--user-id", dest="user_ids", action="append", help="User id to process. Repeat for multiple users.")
    parser.add_argument("--limit", type=int, default=_env_int("CORTEX_WORKER_LIMIT", 10), help="Maximum jobs to run per user per tick.")
    parser.add_argument("--failed-job-limit", type=int, default=_env_int("CORTEX_WORKER_FAILED_JOB_LIMIT", 20), help="Recent failed jobs to include in the tick summary.")
    parser.add_argument("--worker-id", default=os.environ.get("CORTEX_WORKER_ID") or f"cortex-worker-{os.getpid()}", help="Worker id recorded on claimed jobs.")
    parser.add_argument("--interval-seconds", type=float, default=_env_float("CORTEX_WORKER_INTERVAL_SECONDS", 0.0), help="Sleep duration between ticks.")
    parser.add_argument("--iterations", type=int, default=_env_int("CORTEX_WORKER_ITERATIONS", 1), help="Number of ticks to run. Use 0 with --interval-seconds for a long-running worker.")
    parser.add_argument("--fail-on-failed", action="store_true", help="Exit with status 2 if failed jobs remain after a tick.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    settings = load_settings()
    store = StoreRegistry.from_settings(settings)
    user_ids = args.user_ids or [settings.default_user_id]
    iterations = max(0, int(args.iterations))
    interval = max(0.0, float(args.interval_seconds))
    exit_code = 0
    tick_count = 0

    while True:
        result: dict[str, Any] = run_worker_tick(
            store,
            user_ids,
            default_user_id=settings.default_user_id,
            limit_per_user=args.limit,
            worker_id=args.worker_id,
            failed_job_limit=args.failed_job_limit,
        )
        print(json.dumps(result, sort_keys=True), flush=True)
        tick_count += 1
        if args.fail_on_failed and int(result.get("failed") or 0) > 0:
            exit_code = 2
        if iterations and tick_count >= iterations:
            break
        if not interval:
            break
        time.sleep(interval)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
