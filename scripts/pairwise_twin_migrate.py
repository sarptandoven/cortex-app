#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app.storage import CortexStore
from backend.app.twin_eval import build_cli_repository


def _repository(args: argparse.Namespace):
    if not args.db_path.exists():
        raise ValueError(f"database does not exist: {args.db_path}")
    init_db(args.db_path)
    return build_cli_repository(
        args.db_path,
        keyring_db_path=getattr(args, "keyring_db_path", None),
    )


def _preview(args: argparse.Namespace) -> dict:
    return asdict(
        _repository(args).preview_legacy_report_migration(
            args.user_id,
            limit=args.limit,
        )
    )


def _apply(args: argparse.Namespace) -> dict:
    return asdict(
        _repository(args).migrate_legacy_reports(
            args.user_id,
            expected_run_ids=tuple(args.expected_run_id),
            expected_selection_digest=args.expected_selection_digest,
        )
    )


def _audit(args: argparse.Namespace) -> dict:
    return _repository(args).audit_report_storage(
        args.user_id,
        require_clean=args.require_clean,
    )


def _finalize(args: argparse.Namespace) -> dict:
    if not args.exclusive_maintenance:
        raise ValueError(
            "finalize requires --exclusive-maintenance after stopping all "
            "Cortex processes and remediating old backups"
        )
    backup_audit = CortexStore(
        args.db_path,
        args.vault_root,
        ensure_vault=False,
    ).audit_pairwise_backup_storage(require_clean=True)
    finalization = _repository(
        args
    ).finalize_legacy_report_migration(
        exclusive_maintenance=True,
    )
    return {
        **finalization,
        "managed_backup_audit": backup_audit,
    }


def _backup_audit(args: argparse.Namespace) -> dict:
    return CortexStore(
        args.db_path,
        args.vault_root,
        ensure_vault=False,
    ).audit_pairwise_backup_storage(
        require_clean=args.require_clean,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Explicit maintenance workflow for encrypted pairwise reports"
        )
    )
    commands = parser.add_subparsers(dest="command", required=True)

    preview = commands.add_parser("preview")
    preview.add_argument("--db-path", type=Path, required=True)
    preview.add_argument("--user-id", required=True)
    preview.add_argument("--limit", type=int, default=100)
    preview.set_defaults(handler=_preview)

    apply = commands.add_parser("apply")
    apply.add_argument("--db-path", type=Path, required=True)
    apply.add_argument("--user-id", required=True)
    apply.add_argument("--keyring-db-path", type=Path, required=True)
    apply.add_argument(
        "--expected-run-id",
        action="append",
        required=True,
    )
    apply.add_argument("--expected-selection-digest", required=True)
    apply.set_defaults(handler=_apply)

    audit = commands.add_parser("audit")
    audit.add_argument("--db-path", type=Path, required=True)
    audit.add_argument("--user-id", required=True)
    audit.add_argument("--keyring-db-path", type=Path, required=True)
    audit.add_argument("--require-clean", action="store_true")
    audit.set_defaults(handler=_audit)

    backup_audit = commands.add_parser("backup-audit")
    backup_audit.add_argument("--db-path", type=Path, required=True)
    backup_audit.add_argument("--vault-root", type=Path, required=True)
    backup_audit.add_argument("--require-clean", action="store_true")
    backup_audit.set_defaults(handler=_backup_audit)

    finalize = commands.add_parser("finalize")
    finalize.add_argument("--db-path", type=Path, required=True)
    finalize.add_argument("--keyring-db-path", type=Path, required=True)
    finalize.add_argument("--vault-root", type=Path, required=True)
    finalize.add_argument("--exclusive-maintenance", action="store_true")
    finalize.set_defaults(handler=_finalize)

    args = parser.parse_args()
    try:
        result = args.handler(args)
    except (KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
