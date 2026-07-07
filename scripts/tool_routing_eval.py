from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.app.database import init_db
from backend.app import mcp_tools
from backend.app.storage import CortexStore


USER_ID = "eval-user"

# Client tool-count caps we advertise Cortex under. The core surface must stay tiny
# (well under Cursor's ~40 ceiling) and the full surface must fit under the largest
# client ceiling so no assistant silently truncates the catalog.
CLIENT_TOOL_CAPS: dict[str, int] = {
    "cursor": 40,
    "chatgpt": 128,
    "claude": 1000,
}

# The scope-sets a real MCP token can hold. None == admin (the app's own path, which
# always sees everything). Ordered from least to most privileged.
SCOPE_SETS: tuple[tuple[str, list[str] | None], ...] = (
    ("read", ["read"]),
    ("read_write", ["read", "write"]),
    ("all", ["read", "write", "export", "maintenance", "destructive"]),
    ("admin", None),
)

SURFACES = ("core", "full")

# Snapshot of the curated core surface a read-scoped token is SHOWN by default. If this
# changes, the eval fails with a diff so the change is a deliberate, reviewed decision
# rather than an accidental surface drift.
EXPECTED_CORE_READ_TOOLS: tuple[str, ...] = (
    "ask_memory",
    "get_context",
    "get_entity_context",
    "get_person_map",
    "list_capabilities",
    "search_memory",
)


def _seed_store(store: CortexStore, user_id: str = USER_ID) -> None:
    """Enable every agent-access trust toggle so require_agent_access never raises.

    This isolates the scope-safety property test to the token-scope gate in
    _require_tool_access (which fires BEFORE store.require_agent_access and before any
    argument validation), so a PermissionError provably means "token not scoped".
    """
    store.update_settings(
        user_id,
        {
            "allow_agent_reads": True,
            "allow_agent_writes": True,
            "allow_agent_exports": True,
            "allow_agent_maintenance": True,
            "allow_agent_destructive_actions": True,
            "review_new_captures": False,
        },
    )


def _check_cap_compliance() -> tuple[float, list[dict[str, Any]]]:
    """Advertisement fits inside client tool-count ceilings.

    Spec: the CORE surface (what a scoped agent sees by default) must fit under EVERY
    client cap, including Cursor's ~40 ceiling. The FULL surface is the legacy expanded
    catalog and only needs to fit under the LARGEST client cap. Admin auth
    (token_scopes is None) is the app's own path and always sees every tool, so it is
    only held to the largest-cap ceiling.
    """
    checks: list[dict[str, Any]] = []
    passed = 0
    total = 0
    largest_cap = max(CLIENT_TOOL_CAPS.values())
    for scope_label, scopes in SCOPE_SETS:
        is_admin = scopes is None
        for surface in SURFACES:
            count = len(mcp_tools.tools_for_scopes(scopes, surface=surface))
            # Which caps this (scope, surface) must fit under.
            if is_admin or surface == "full":
                cap_targets = {"largest": largest_cap}
            else:
                cap_targets = dict(CLIENT_TOOL_CAPS)
            for client, cap in sorted(cap_targets.items()):
                total += 1
                ok = count <= cap
                if ok:
                    passed += 1
                checks.append(
                    {
                        "check": "cap_compliance",
                        "client": client,
                        "cap": cap,
                        "scopes": scope_label,
                        "surface": surface,
                        "advertised": count,
                        "ok": ok,
                    }
                )
        # Structural sanity: for a plain read / read+write token the core surface stays
        # tiny (one tool per job). Tokens minted with maintenance/destructive scopes
        # deliberately widen their core surface, and admin sees everything, so those are
        # exempt from the tiny-core floor.
        if not is_admin and not ({"maintenance", "destructive"} & set(scopes or [])):
            core_count = len(mcp_tools.tools_for_scopes(scopes, surface="core"))
            total += 1
            ok = core_count <= 12
            if ok:
                passed += 1
            checks.append(
                {"check": "core_is_tiny", "scopes": scope_label, "advertised": core_count, "ceiling": 12, "ok": ok}
            )
    metric = round(passed / total, 6) if total else 1.0
    return metric, checks


def _check_core_stability() -> tuple[bool, dict[str, Any]]:
    """The default core surface for a read token must equal the snapshot exactly."""
    actual = tuple(sorted(str(tool.get("name") or "") for tool in mcp_tools.tools_for_scopes(["read"], surface="core")))
    expected = EXPECTED_CORE_READ_TOOLS
    stable = actual == expected
    added = sorted(set(actual) - set(expected))
    removed = sorted(set(expected) - set(actual))
    return stable, {
        "check": "core_stability",
        "expected": list(expected),
        "actual": list(actual),
        "unexpected_additions": added,
        "unexpected_removals": removed,
        "ok": stable,
    }


def _insufficient_scope_variants(required: list[str], universe: list[str]) -> list[list[str]]:
    """Scope-sets that are each MISSING at least one capability the tool requires.

    Includes the empty set plus, for each required cap, the full universe minus that one
    cap (so exactly one required capability is absent).
    """
    required_set = set(required)
    variants: list[list[str]] = [[]]
    for missing in required:
        variant = sorted(set(universe) - {missing})
        variants.append(variant)
    # De-dupe while preserving determinism.
    seen: set[tuple[str, ...]] = set()
    unique: list[list[str]] = []
    for variant in variants:
        key = tuple(variant)
        if key in seen:
            continue
        # A variant is only a NEGATIVE case if it truly misses a required capability.
        if required_set.issubset(set(variant)):
            continue
        seen.add(key)
        unique.append(variant)
    return unique


def _check_scope_safety(store: CortexStore, user_id: str = USER_ID) -> tuple[float, list[dict[str, Any]]]:
    """Property test: for every tool, any scope-set missing a required capability must
    make call_tool raise PermissionError before it does any work."""
    universe = ["read", "write", "export", "maintenance", "destructive"]
    checks: list[dict[str, Any]] = []
    passed = 0
    total = 0
    for tool in mcp_tools.TOOLS:
        name = str(tool.get("name") or "")
        required = mcp_tools.tool_required_capabilities(name, scoped=True)
        if not required:
            # No scope requirement → no negative case to construct.
            continue
        for scopes in _insufficient_scope_variants(required, universe):
            total += 1
            raised = False
            try:
                mcp_tools.call_tool(store, user_id, name, {}, token_scopes=scopes)
            except PermissionError:
                raised = True
            except Exception:
                # Any other exception means the scope gate did NOT fire first: fail.
                raised = False
            if raised:
                passed += 1
            checks.append(
                {
                    "check": "scope_safety",
                    "tool": name,
                    "required": required,
                    "scopes": scopes,
                    "raised_permission_error": raised,
                    "ok": raised,
                }
            )
    metric = round(passed / total, 6) if total else 1.0
    return metric, checks


def _check_annotations() -> tuple[bool, list[dict[str, Any]]]:
    """readOnlyHint / destructiveHint / openWorldHint must match the scope-set classification."""
    checks: list[dict[str, Any]] = []
    ok_all = True
    for tool in mcp_tools.TOOLS:
        name = str(tool.get("name") or "")
        ann = tool.get("annotations") or {}
        is_read = name in mcp_tools.READ_TOOLS
        is_export = name in mcp_tools.EXPORT_TOOLS
        is_write = name in mcp_tools.WRITE_TOOLS
        is_maintenance = name in mcp_tools.MAINTENANCE_TOOLS
        is_destructive = name in mcp_tools.DESTRUCTIVE_TOOLS
        is_review = name in mcp_tools.REVIEW_TOOLS

        expected_read_only = (is_read or is_export) and not (is_write or is_maintenance or is_destructive or is_review)
        expected_destructive = is_destructive
        expected_open_world = name in mcp_tools._OPEN_WORLD_TOOLS

        read_ok = bool(ann.get("readOnlyHint")) == expected_read_only
        destructive_ok = bool(ann.get("destructiveHint")) == expected_destructive
        open_world_ok = bool(ann.get("openWorldHint")) == expected_open_world

        # Spec crosschecks: WRITE/DESTRUCTIVE tools must never claim readOnlyHint True.
        write_destructive_not_read = (bool(ann.get("readOnlyHint")) is False) if (is_write or is_destructive) else True

        tool_ok = read_ok and destructive_ok and open_world_ok and write_destructive_not_read
        ok_all = ok_all and tool_ok
        checks.append(
            {
                "check": "annotations",
                "tool": name,
                "readOnlyHint": bool(ann.get("readOnlyHint")),
                "destructiveHint": bool(ann.get("destructiveHint")),
                "openWorldHint": bool(ann.get("openWorldHint")),
                "expected_read_only": expected_read_only,
                "expected_destructive": expected_destructive,
                "expected_open_world": expected_open_world,
                "ok": tool_ok,
            }
        )
    return ok_all, checks


def run_tool_routing_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    _seed_store(store, user_id)

    cap_metric, cap_checks = _check_cap_compliance()
    core_stable, core_check = _check_core_stability()
    scope_metric, scope_checks = _check_scope_safety(store, user_id)
    annotation_ok, annotation_checks = _check_annotations()

    all_checks = [*cap_checks, core_check, *scope_checks, *annotation_checks]
    failures = [c for c in all_checks if not c.get("ok", False)]

    return {
        "harness": "tool_routing_eval",
        "cap_compliance": cap_metric,
        "scope_safety": scope_metric,
        "core_stable": core_stable,
        "annotation_ok": annotation_ok,
        "counts": {
            "cap_checks": len(cap_checks),
            "scope_checks": len(scope_checks),
            "annotation_checks": len(annotation_checks),
            "total_checks": len(all_checks),
            "failures": len(failures),
        },
        "checks": all_checks,
        "failures": failures,
    }


def check_tool_routing_thresholds(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    if float(result.get("cap_compliance") or 0.0) != 1.0:
        failures.append(f"cap_compliance={result.get('cap_compliance')} != 1.0")
    if float(result.get("scope_safety") or 0.0) != 1.0:
        failures.append(f"scope_safety={result.get('scope_safety')} != 1.0")
    if not result.get("core_stable"):
        failures.append("core surface drifted from EXPECTED_CORE_READ_TOOLS snapshot")
    if not result.get("annotation_ok"):
        failures.append("tool annotations do not match scope-set classification")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cortex MCP tool-routing / authorization gate.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--report-only", action="store_true", help="Print without failing on breaches.")
    args = parser.parse_args()

    if args.db_path:
        result = run_tool_routing_eval(
            args.db_path.expanduser(),
            args.vault_path.expanduser() if args.vault_path else None,
            args.user_id,
        )
    else:
        tmp = Path(tempfile.mkdtemp(prefix="tool-routing-eval-"))
        result = run_tool_routing_eval(tmp / "tool-routing-eval.sqlite", tmp / "Cortex.vault", args.user_id)

    print(json.dumps(result, indent=2, sort_keys=True))

    failures = check_tool_routing_thresholds(result)
    if failures and not args.report_only:
        print("\nTOOL ROUTING GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
