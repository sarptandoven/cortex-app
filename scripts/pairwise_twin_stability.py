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
    analyze_stability_reports,
    build_cli_repository,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Analyze repeated stochastic runs of one frozen twin-eval spec."
    )
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--run-id", action="append", required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-plaintext-report", action="store_true")
    parser.add_argument("--keyring-db-path", type=Path)
    args = parser.parse_args()
    if len(args.run_id) < 2:
        parser.error("--run-id must be provided at least twice")
    if not args.db_path.exists():
        parser.error(f"database does not exist: {args.db_path}")
    try:
        init_db(args.db_path)
        repository = build_cli_repository(
            args.db_path,
            keyring_db_path=args.keyring_db_path,
            allow_plaintext_reports=args.allow_plaintext_report,
        )
        reports = []
        for run_id in args.run_id:
            repository.replay_bundle(args.user_id, run_id)
            reports.append(repository.load_report(args.user_id, run_id))
        result = analyze_stability_reports(reports)
    except (KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))
    payload = json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload, encoding="utf-8")
    print(payload, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
