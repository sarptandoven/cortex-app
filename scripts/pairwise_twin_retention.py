#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.twin_eval import TwinEvalRepository, canonical_hash


def _retention_days_default() -> int:
    raw = os.environ.get("CORTEX_TWIN_EVAL_RETENTION_DAYS", "90")
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            "CORTEX_TWIN_EVAL_RETENTION_DAYS must be an integer"
        ) from exc
    if not 1 <= value <= 3_650:
        raise ValueError("retention days must be between 1 and 3650")
    return value


def _as_of(value: str | None) -> dt.datetime:
    if value is None:
        return dt.datetime.now(dt.timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = dt.datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        raise ValueError("--as-of must include a timezone")
    return parsed.astimezone(dt.timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Preview or apply bounded per-user twin-evaluation retention. "
            "Apply requires the digest from a matching preview."
        )
    )
    parser.add_argument("command", choices=("preview", "apply"))
    parser.add_argument("--db-path", type=Path, required=True)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--retention-days", type=int)
    parser.add_argument("--as-of")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--expected-preview-digest")
    args = parser.parse_args()
    try:
        days = (
            args.retention_days
            if args.retention_days is not None
            else _retention_days_default()
        )
        if not 1 <= days <= 3_650:
            raise ValueError("retention days must be between 1 and 3650")
        if not args.db_path.exists():
            raise ValueError(f"database does not exist: {args.db_path}")
        as_of = _as_of(args.as_of)
        before = as_of - dt.timedelta(days=days)
        before_text = before.isoformat(timespec="seconds")
        init_db(args.db_path)
        repository = TwinEvalRepository(args.db_path)
        run_ids = repository.list_reports_before(
            args.user_id,
            before_text,
            limit=args.limit,
        )
        preview = {
            "user_id": args.user_id,
            "before": before_text,
            "retention_days": days,
            "limit": args.limit,
            "run_ids": run_ids,
        }
        preview_digest = canonical_hash(preview, prefix="retention_preview_")
        if args.command == "apply":
            if not args.expected_preview_digest:
                raise ValueError("apply requires --expected-preview-digest")
            if args.expected_preview_digest != preview_digest:
                raise ValueError(
                    "retention preview changed; run preview again before applying"
                )
            deleted = repository.purge_reports_before(
                args.user_id,
                before_text,
                limit=args.limit,
                expected_run_ids=run_ids,
            )
        else:
            deleted = ()
    except (OSError, RuntimeError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "schema_version": "pairwise-twin-retention/v1",
                "status": "applied" if args.command == "apply" else "preview",
                "preview_digest": preview_digest,
                "eligible_count": len(run_ids),
                "deleted_count": len(deleted),
                "before": before_text,
                "retention_days": days,
                "limit": args.limit,
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
