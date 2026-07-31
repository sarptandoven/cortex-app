"""Forensics for the cross-tenant id-collision bug.

`memories.id` and `tasks.id` are single-column primary keys (see the comment on those table
definitions in database.py for why they must stay globally unique). Before the collision guards in
storage.py existed, an `INSERT OR REPLACE` from a second tenant that happened to derive the same id
silently DELETED the first tenant's row instead of raising. The guards stop that happening again,
but they cannot un-lose data already destroyed in a database that ran the old code.

This module answers the operational question the guards do not: *was this deployment ever hit?*

It works because a clobber only rewrote the `memories`/`tasks` row itself. Sibling rows carry their
own `user_id`, and the `memory_events` audit log is append-only and was never rewritten — so a row
whose `user_id` disagrees with the owning memory's is physical evidence that the memory changed
hands. Findings are strongest for `memory_events` (never rewritten by any write path) and weakest
where a later write may legitimately have re-pointed a row, which is why each check is reported
separately with its own confidence rather than summed into one number.

Read-only: opens the database in SQLite read-only URI mode and never writes.

    python -m backend.app.tenant_forensics /path/to/index.sqlite
    python -m backend.app.tenant_forensics /path/to/index.sqlite --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .sqlite_runtime import sqlite3


# (label, confidence, sql). Each query returns evidence rows where a sibling row's owner disagrees
# with the owner of the memory/task it belongs to.
_CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "memory_events",
        "high",
        # The audit log is append-only: no write path rewrites an existing row's user_id, so a
        # disagreement here is the most direct evidence that a memory changed hands.
        """
        SELECT e.user_id AS other_user_id, m.user_id AS current_owner, e.object_id AS object_id,
               COUNT(*) AS rows
        FROM memory_events e
        JOIN memories m ON m.id = e.object_id
        WHERE e.object_type = 'memory' AND e.user_id != m.user_id
        GROUP BY e.user_id, m.user_id, e.object_id
        """,
    ),
    (
        "memory_vec_map",
        "high",
        """
        SELECT v.user_id AS other_user_id, m.user_id AS current_owner, v.memory_id AS object_id,
               COUNT(*) AS rows
        FROM memory_vec_map v
        JOIN memories m ON m.id = v.memory_id
        WHERE v.user_id != m.user_id
        GROUP BY v.user_id, m.user_id, v.memory_id
        """,
    ),
    (
        "memory_relations",
        "high",
        """
        SELECT r.user_id AS other_user_id, m.user_id AS current_owner, r.source_memory_id AS object_id,
               COUNT(*) AS rows
        FROM memory_relations r
        JOIN memories m ON m.id = r.source_memory_id
        WHERE r.user_id != m.user_id
        GROUP BY r.user_id, m.user_id, r.source_memory_id
        """,
    ),
    (
        "memory_entities",
        "medium",
        # The live save path DELETEs these by memory_id before reinserting, so a same-capture
        # clobber erases the evidence; the vault-rebuild path does not, and leaves it behind.
        """
        SELECT me.user_id AS other_user_id, m.user_id AS current_owner, me.memory_id AS object_id,
               COUNT(*) AS rows
        FROM memory_entities me
        JOIN memories m ON m.id = me.memory_id
        WHERE me.user_id != m.user_id
        GROUP BY me.user_id, m.user_id, me.memory_id
        """,
    ),
    (
        "memory_topics",
        "medium",
        """
        SELECT mt.user_id AS other_user_id, m.user_id AS current_owner, mt.memory_id AS object_id,
               COUNT(*) AS rows
        FROM memory_topics mt
        JOIN memories m ON m.id = mt.memory_id
        WHERE mt.user_id != m.user_id
        GROUP BY mt.user_id, m.user_id, mt.memory_id
        """,
    ),
    (
        "memories_vs_capture",
        "medium",
        # A memory whose parent capture belongs to someone else: the row was replaced without its
        # capture_id being re-pointed, or the capture itself changed hands.
        """
        SELECT c.user_id AS other_user_id, m.user_id AS current_owner, m.id AS object_id,
               COUNT(*) AS rows
        FROM memories m
        JOIN captures c ON c.id = m.capture_id
        WHERE m.capture_id IS NOT NULL AND c.user_id != m.user_id
        GROUP BY c.user_id, m.user_id, m.id
        """,
    ),
    (
        "task_entities",
        "medium",
        """
        SELECT te.user_id AS other_user_id, t.user_id AS current_owner, te.task_id AS object_id,
               COUNT(*) AS rows
        FROM task_entities te
        JOIN tasks t ON t.id = te.task_id
        WHERE te.user_id != t.user_id
        GROUP BY te.user_id, t.user_id, te.task_id
        """,
    ),
    (
        "task_topics",
        "medium",
        """
        SELECT tt.user_id AS other_user_id, t.user_id AS current_owner, tt.task_id AS object_id,
               COUNT(*) AS rows
        FROM task_topics tt
        JOIN tasks t ON t.id = tt.task_id
        WHERE tt.user_id != t.user_id
        GROUP BY tt.user_id, t.user_id, tt.task_id
        """,
    ),
)


def _connect_readonly(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def cross_tenant_integrity_report(db_path: str | Path, *, sample_limit: int = 20) -> dict[str, Any]:
    """Scan a database for residue of a pre-guard cross-tenant clobber. Never writes."""
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"no such database: {db_path}")

    findings: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    conn = _connect_readonly(db_path)
    try:
        # Counted across the audit log and captures too, NOT just surviving memories: a clobber
        # that erased a tenant's only memory removes them from `memories` entirely, so counting
        # that table alone would report the worst case as a harmless single-tenant database.
        tenant_count = conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT user_id FROM memories
                UNION SELECT user_id FROM captures
                UNION SELECT user_id FROM memory_events
            )
            """
        ).fetchone()[0]
        for label, confidence, sql in _CHECKS:
            try:
                rows = conn.execute(sql).fetchall()
            except sqlite3.OperationalError as exc:
                # A table this deployment does not have (memory_vec_map needs sqlite-vec) or a
                # pre-migration schema. Report it rather than silently scoring the DB as clean.
                skipped.append({"check": label, "reason": str(exc)})
                continue
            if not rows:
                continue
            findings.append(
                {
                    "check": label,
                    "confidence": confidence,
                    "affected_objects": len(rows),
                    "evidence_rows": sum(int(r["rows"]) for r in rows),
                    "tenants_involved": sorted(
                        {r["other_user_id"] for r in rows} | {r["current_owner"] for r in rows}
                    ),
                    "samples": [
                        {
                            "object_id": r["object_id"],
                            "current_owner": r["current_owner"],
                            "other_user_id": r["other_user_id"],
                            "rows": int(r["rows"]),
                        }
                        for r in rows[:sample_limit]
                    ],
                }
            )
    finally:
        conn.close()

    high = [f for f in findings if f["confidence"] == "high"]
    return {
        "database": str(db_path),
        "distinct_tenants_seen": tenant_count,
        "multi_tenant": tenant_count > 1,
        # Every check compares two DIFFERENT user_ids, so a finding is cross-tenant evidence by
        # construction -- it is deliberately NOT gated on the surviving tenant count, because the
        # most severe case (a tenant wiped out entirely) leaves the fewest survivors behind.
        "clobber_evidence": bool(high),
        "suspicious": bool(findings),
        "findings": findings,
        "skipped_checks": skipped,
    }


def format_report(report: dict[str, Any]) -> str:
    lines = [f"database: {report['database']}", f"tenants seen: {report['distinct_tenants_seen']}"]
    if report["clobber_evidence"]:
        lines.append("")
        lines.append("!! CROSS-TENANT CLOBBER EVIDENCE FOUND -- data was likely destroyed.")
    elif report["suspicious"]:
        lines.append("")
        lines.append("Lower-confidence ownership mismatches found; review below.")
    else:
        lines.append("")
        lines.append("No ownership mismatches found.")

    for finding in report["findings"]:
        lines.append("")
        lines.append(
            f"[{finding['confidence'].upper()}] {finding['check']}: "
            f"{finding['affected_objects']} object(s), {finding['evidence_rows']} row(s)"
        )
        lines.append(f"  tenants: {', '.join(finding['tenants_involved'])}")
        for sample in finding["samples"]:
            lines.append(
                f"    {sample['object_id']}  now owned by {sample['current_owner']}"
                f"  but {sample['rows']} row(s) belong to {sample['other_user_id']}"
            )
    for skip in report["skipped_checks"]:
        lines.append(f"  (skipped {skip['check']}: {skip['reason']})")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="tenant_forensics",
        description="Scan a Cortex SQLite database for evidence of a cross-tenant id clobber.",
    )
    parser.add_argument("database", help="path to index.sqlite (opened read-only)")
    parser.add_argument("--json", action="store_true", help="emit the raw report as JSON")
    parser.add_argument("--sample-limit", type=int, default=20, help="samples per finding (default 20)")
    args = parser.parse_args(argv)

    try:
        report = cross_tenant_integrity_report(args.database, sample_limit=args.sample_limit)
    except FileNotFoundError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2) if args.json else format_report(report))
    # 1 signals high-confidence evidence so this can gate a deploy check.
    return 1 if report["clobber_evidence"] else 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
