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
    TwinEvalRepository,
    analyze_owner_study,
    baseline_outcomes_from_dict,
    build_owner_study,
    build_scalar_study,
    canonical_json,
    cohort_from_dict,
    key_from_dict,
    labels_from_dict,
    labels_template,
    scalar_baseline_from_scores,
    scalar_key_from_dict,
    scalar_scores_from_dict,
    scalar_scores_template,
)


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _write_json(path: Path, value: object, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        json.loads(canonical_json(value)),
        indent=2,
        sort_keys=True,
        ensure_ascii=False,
    )
    path.write_text(payload + "\n", encoding="utf-8")
    if private:
        path.chmod(0o600)


def _repository(db_path: Path) -> TwinEvalRepository:
    if not db_path.exists():
        raise ValueError(f"database does not exist: {db_path}")
    init_db(db_path)
    return TwinEvalRepository(db_path)


def _export(args: argparse.Namespace) -> dict:
    repository = _repository(args.db_path)
    repository.replay_bundle(args.user_id, args.run_id)
    report = repository.load_report(args.user_id, args.run_id)
    cohort, key = build_owner_study(
        report,
        seed=args.seed,
        reversed_repeat_fraction=args.reversed_repeat_fraction,
    )
    _write_json(args.public_out, cohort)
    _write_json(args.key_out, key, private=True)
    _write_json(args.labels_out, labels_template(cohort), private=True)
    return {
        "status": "exported",
        "cohort_id": cohort.cohort_id,
        "items": len(cohort.items),
        "independent_pair_groups": len({item.pair_group_id for item in key.items}),
        "reversed_repeats": sum(item.is_reversed_repeat for item in key.items),
        "public_path": str(args.public_out),
        "private_key_path": str(args.key_out),
        "labels_path": str(args.labels_out),
        "private_key_mode": oct(args.key_out.stat().st_mode & 0o777),
    }


def _analyze(args: argparse.Namespace) -> dict:
    repository = _repository(args.db_path)
    cohort = cohort_from_dict(_read_object(args.public))
    key = key_from_dict(_read_object(args.key))
    labels = labels_from_dict(_read_object(args.labels))
    repository.replay_bundle(args.user_id, key.source_run_id)
    report = repository.load_report(args.user_id, key.source_run_id)
    baseline = None
    if args.baseline:
        baseline = baseline_outcomes_from_dict(
            _read_object(args.baseline),
            key,
        )
    result = analyze_owner_study(
        report,
        cohort,
        key,
        labels,
        bootstrap_seed=args.bootstrap_seed,
        bootstrap_resamples=args.bootstrap_resamples,
        baseline_outcomes=baseline,
    )
    if args.output:
        _write_json(args.output, result)
    return result


def _scalar_export(args: argparse.Namespace) -> dict:
    repository = _repository(args.db_path)
    owner_key = key_from_dict(_read_object(args.owner_key))
    repository.replay_bundle(args.user_id, owner_key.source_run_id)
    report = repository.load_report(args.user_id, owner_key.source_run_id)
    cohort, key = build_scalar_study(report, owner_key)
    _write_json(args.public_out, cohort)
    _write_json(args.key_out, key, private=True)
    _write_json(args.scores_out, scalar_scores_template(cohort), private=True)
    return {
        "status": "scalar_exported",
        "cohort_id": cohort.cohort_id,
        "items": len(cohort.items),
        "public_path": str(args.public_out),
        "private_key_path": str(args.key_out),
        "scores_path": str(args.scores_out),
    }


def _scalar_baseline(args: argparse.Namespace) -> dict:
    owner_key = key_from_dict(_read_object(args.owner_key))
    scalar_key = scalar_key_from_dict(_read_object(args.scalar_key))
    scores = scalar_scores_from_dict(_read_object(args.scores), scalar_key)
    result = scalar_baseline_from_scores(
        owner_key,
        scalar_key,
        scores,
        tie_margin=args.tie_margin,
        both_bad_at_or_below=args.both_bad_at_or_below,
    )
    _write_json(args.output, result, private=True)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Export and analyze blinded owner labels for pairwise twin evaluation."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export = subparsers.add_parser("export", help="Export a blinded cohort and private key.")
    export.add_argument("--db-path", type=Path, required=True)
    export.add_argument("--user-id", required=True)
    export.add_argument("--run-id", required=True)
    export.add_argument("--public-out", type=Path, required=True)
    export.add_argument("--key-out", type=Path, required=True)
    export.add_argument("--labels-out", type=Path, required=True)
    export.add_argument("--seed", type=int, default=0)
    export.add_argument("--reversed-repeat-fraction", type=float, default=0.2)
    export.set_defaults(handler=_export)

    analyze = subparsers.add_parser("analyze", help="Analyze completed blinded labels.")
    analyze.add_argument("--db-path", type=Path, required=True)
    analyze.add_argument("--user-id", required=True)
    analyze.add_argument("--public", type=Path, required=True)
    analyze.add_argument("--key", type=Path, required=True)
    analyze.add_argument("--labels", type=Path, required=True)
    analyze.add_argument("--baseline", type=Path)
    analyze.add_argument("--output", type=Path)
    analyze.add_argument("--bootstrap-seed", type=int, default=0)
    analyze.add_argument("--bootstrap-resamples", type=int, default=2_000)
    analyze.set_defaults(handler=_analyze)

    scalar_export = subparsers.add_parser(
        "scalar-export",
        help="Export the same frozen candidates for independent pointwise scoring.",
    )
    scalar_export.add_argument("--db-path", type=Path, required=True)
    scalar_export.add_argument("--user-id", required=True)
    scalar_export.add_argument("--owner-key", type=Path, required=True)
    scalar_export.add_argument("--public-out", type=Path, required=True)
    scalar_export.add_argument("--key-out", type=Path, required=True)
    scalar_export.add_argument("--scores-out", type=Path, required=True)
    scalar_export.set_defaults(handler=_scalar_export)

    scalar_baseline = subparsers.add_parser(
        "scalar-baseline",
        help="Convert completed pointwise scores to frozen pair outcomes.",
    )
    scalar_baseline.add_argument("--owner-key", type=Path, required=True)
    scalar_baseline.add_argument("--scalar-key", type=Path, required=True)
    scalar_baseline.add_argument("--scores", type=Path, required=True)
    scalar_baseline.add_argument("--output", type=Path, required=True)
    scalar_baseline.add_argument("--tie-margin", type=float, default=0.0)
    scalar_baseline.add_argument("--both-bad-at-or-below", type=float)
    scalar_baseline.set_defaults(handler=_scalar_baseline)

    args = parser.parse_args()
    try:
        result = args.handler(args)
    except (KeyError, OSError, ValueError) as exc:
        parser.error(str(exc))
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
