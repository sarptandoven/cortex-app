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

# Read tools that are safe to dispatch against an empty store and whose result must be
# JSON-serializable (the /v1/tools REST layer transmits the value as JSON).
DISPATCH_READ_TOOLS: tuple[str, ...] = (
    "list_capabilities",
    "search_memory",
    "get_context",
    "get_memory_stats",
)

# Minimal args to reach each tool's happy path.
DISPATCH_ARGS: dict[str, dict[str, Any]] = {
    "list_capabilities": {},
    "search_memory": {"query": "cortex memory"},
    "get_context": {"task": "summarize what you know"},
    "get_memory_stats": {},
}


def _seed_store(store: CortexStore, user_id: str = USER_ID) -> None:
    """Enable read trust so read-tool dispatch is not blocked by Trust controls, and
    capture one memory so read tools exercise real (not just empty) code paths."""
    store.update_settings(
        user_id,
        {
            "allow_agent_reads": True,
            "allow_agent_writes": True,
            "review_new_captures": False,
            "allow_pending_in_context": True,
        },
    )
    store.save_capture(
        user_id=user_id,
        content="Cortex stores cited personal memory for AI agents in a local SQLite vault.",
        source="adapter-contract-eval",
        source_url=None,
        title="Adapter contract seed",
        extracted={
            "_timestamp": "2026-01-01T00:00:00Z",
            "summary": "Adapter contract eval seed memory.",
            "records": [
                {
                    "id": "ace_seed_semantic",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Cortex stores cited personal memory for AI agents in a local SQLite vault.",
                    "summary": "Cortex stores cited memory in a local SQLite vault.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "topics": ["cortex", "memory", "storage"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )


def _tool_by_name() -> dict[str, dict[str, Any]]:
    return {str(tool.get("name") or ""): tool for tool in mcp_tools.TOOLS}


def _check_openai_round_trip() -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ok_all = True
    exported = mcp_tools.export_openai_tools(None, surface="full")
    by_name = _tool_by_name()

    count_ok = len(exported) == len(mcp_tools.TOOLS)
    ok_all = ok_all and count_ok
    checks.append({"check": "openai_count", "exported": len(exported), "tools": len(mcp_tools.TOOLS), "ok": count_ok})

    for fn in exported:
        shape_ok = fn.get("type") == "function" and isinstance(fn.get("function"), dict)
        spec = fn.get("function") or {}
        name = str(spec.get("name") or "")
        name_ok = name in by_name
        params_ok = name_ok and spec.get("parameters") == mcp_tools._tool_input_schema(by_name[name])
        desc_ok = bool(str(spec.get("description") or ""))
        entry_ok = shape_ok and name_ok and params_ok and desc_ok
        ok_all = ok_all and entry_ok
        checks.append(
            {
                "check": "openai_entry",
                "tool": name,
                "shape_ok": shape_ok,
                "name_in_tools": name_ok,
                "parameters_match_input_schema": params_ok,
                "description_non_empty": desc_ok,
                "ok": entry_ok,
            }
        )
    return ok_all, checks


def _check_anthropic_round_trip() -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ok_all = True
    exported = mcp_tools.export_anthropic_tools(None, surface="full")
    by_name = _tool_by_name()

    count_ok = len(exported) == len(mcp_tools.TOOLS)
    ok_all = ok_all and count_ok
    checks.append({"check": "anthropic_count", "exported": len(exported), "tools": len(mcp_tools.TOOLS), "ok": count_ok})

    exported_names = {str(tool.get("name") or "") for tool in exported}
    parity_ok = exported_names == set(by_name)
    ok_all = ok_all and parity_ok
    checks.append({"check": "anthropic_name_parity", "ok": parity_ok})

    for tool in exported:
        name = str(tool.get("name") or "")
        keys_ok = "name" in tool and "description" in tool and "input_schema" in tool
        name_ok = name in by_name
        schema_ok = name_ok and tool.get("input_schema") == mcp_tools._tool_input_schema(by_name[name])
        desc_ok = bool(str(tool.get("description") or ""))
        entry_ok = keys_ok and name_ok and schema_ok and desc_ok
        ok_all = ok_all and entry_ok
        checks.append(
            {
                "check": "anthropic_entry",
                "tool": name,
                "keys_ok": keys_ok,
                "name_in_tools": name_ok,
                "input_schema_match": schema_ok,
                "description_non_empty": desc_ok,
                "ok": entry_ok,
            }
        )
    return ok_all, checks


def _check_openapi_validity() -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ok_all = True
    doc = mcp_tools.export_openapi("http://127.0.0.1:8766", None, surface="full")
    by_name = _tool_by_name()

    version_ok = doc.get("openapi") == "3.1.0"
    ok_all = ok_all and version_ok
    checks.append({"check": "openapi_version", "value": doc.get("openapi"), "ok": version_ok})

    paths = doc.get("paths") or {}
    count_ok = len(paths) == len(mcp_tools.TOOLS)
    ok_all = ok_all and count_ok
    checks.append({"check": "openapi_path_count", "paths": len(paths), "tools": len(mcp_tools.TOOLS), "ok": count_ok})

    for name, tool in by_name.items():
        key = f"/v1/tools/{name}"
        entry = paths.get(key) or {}
        post = entry.get("post") or {}
        path_ok = key in paths
        op_ok = post.get("operationId") == name
        request_schema = (((post.get("requestBody") or {}).get("content") or {}).get("application/json") or {}).get("schema")
        request_ok = request_schema == mcp_tools._tool_input_schema(tool)
        response_schema = ((((post.get("responses") or {}).get("200") or {}).get("content") or {}).get("application/json") or {}).get("schema")
        response_ok = isinstance(response_schema, dict) and bool(response_schema)
        entry_ok = path_ok and op_ok and request_ok and response_ok
        ok_all = ok_all and entry_ok
        checks.append(
            {
                "check": "openapi_path",
                "tool": name,
                "path_present": path_ok,
                "operation_id_matches": op_ok,
                "request_schema_matches": request_ok,
                "response_schema_present": response_ok,
                "ok": entry_ok,
            }
        )
    return ok_all, checks


def _check_scope_projection() -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ok_all = True
    read_only = {fn["function"]["name"] for fn in mcp_tools.export_openai_tools(["read"], surface="full")}
    full = {fn["function"]["name"] for fn in mcp_tools.export_openai_tools(None, surface="full")}

    subset_ok = read_only.issubset(full)
    ok_all = ok_all and subset_ok
    checks.append({"check": "scope_subset_of_full", "read_only_count": len(read_only), "full_count": len(full), "ok": subset_ok})

    excluded = ("remember_this", "delete_all_user_data", "forget_memory", "sync_github")
    for name in excluded:
        excluded_ok = name not in read_only
        ok_all = ok_all and excluded_ok
        checks.append({"check": "scope_excludes_write_destructive", "tool": name, "ok": excluded_ok})

    included = ("get_context", "search_memory", "ask_memory")
    for name in included:
        included_ok = name in read_only
        ok_all = ok_all and included_ok
        checks.append({"check": "scope_includes_read", "tool": name, "ok": included_ok})

    return ok_all, checks


def _check_dispatch_parity(store: CortexStore, user_id: str = USER_ID) -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ok_all = True
    for name in DISPATCH_READ_TOOLS:
        args = DISPATCH_ARGS.get(name, {})
        succeeded = False
        serializable = False
        error: str | None = None
        try:
            value = mcp_tools.call_tool(store, user_id, name, args, token_scopes=["read"])
            succeeded = True
            try:
                json.dumps(value)
                serializable = True
            except (TypeError, ValueError) as exc:
                serializable = False
                error = f"not JSON-serializable: {exc}"
        except Exception as exc:  # noqa: BLE001 - the eval reports any dispatch failure
            error = f"{type(exc).__name__}: {exc}"
        entry_ok = succeeded and serializable
        ok_all = ok_all and entry_ok
        checks.append(
            {
                "check": "dispatch_parity",
                "tool": name,
                "succeeded": succeeded,
                "json_serializable": serializable,
                "error": error,
                "ok": entry_ok,
            }
        )
    return ok_all, checks


def _check_export_tool_schema_dispatch() -> tuple[bool, list[dict[str, Any]]]:
    checks: list[dict[str, Any]] = []
    ok_all = True

    openai = mcp_tools.export_tool_schema("openai")
    openai_ok = isinstance(openai, list) and bool(openai) and openai[0].get("type") == "function"
    ok_all = ok_all and openai_ok
    checks.append({"check": "export_tool_schema_openai", "ok": openai_ok})

    anthropic = mcp_tools.export_tool_schema("anthropic")
    anthropic_ok = isinstance(anthropic, list) and bool(anthropic) and "input_schema" in anthropic[0]
    ok_all = ok_all and anthropic_ok
    checks.append({"check": "export_tool_schema_anthropic", "ok": anthropic_ok})

    openapi = mcp_tools.export_tool_schema("openapi")
    openapi_ok = isinstance(openapi, dict) and openapi.get("openapi") == "3.1.0"
    ok_all = ok_all and openapi_ok
    checks.append({"check": "export_tool_schema_openapi", "ok": openapi_ok})

    mcp = mcp_tools.export_tool_schema("mcp")
    mcp_ok = isinstance(mcp, list) and bool(mcp) and "inputSchema" in mcp[0]
    ok_all = ok_all and mcp_ok
    checks.append({"check": "export_tool_schema_mcp", "ok": mcp_ok})

    raised = False
    try:
        mcp_tools.export_tool_schema("graphql")
    except ValueError:
        raised = True
    except Exception:
        raised = False
    ok_all = ok_all and raised
    checks.append({"check": "export_tool_schema_unknown_raises_value_error", "ok": raised})

    return ok_all, checks


def run_adapter_contract_eval(db_path: Path, vault_path: Path | None = None, user_id: str = USER_ID) -> dict[str, Any]:
    init_db(db_path)
    store = CortexStore(db_path, vault_path)
    _seed_store(store, user_id)

    openai_ok, openai_checks = _check_openai_round_trip()
    anthropic_ok, anthropic_checks = _check_anthropic_round_trip()
    openapi_ok, openapi_checks = _check_openapi_validity()
    scope_ok, scope_checks = _check_scope_projection()
    dispatch_ok, dispatch_checks = _check_dispatch_parity(store, user_id)
    export_dispatch_ok, export_dispatch_checks = _check_export_tool_schema_dispatch()

    all_checks = [
        *openai_checks,
        *anthropic_checks,
        *openapi_checks,
        *scope_checks,
        *dispatch_checks,
        *export_dispatch_checks,
    ]
    failures = [c for c in all_checks if not c.get("ok", False)]

    return {
        "harness": "adapter_contract_eval",
        "openai_round_trip": openai_ok,
        "anthropic_round_trip": anthropic_ok,
        "openapi_valid": openapi_ok,
        "scope_projection": scope_ok,
        "dispatch_parity": dispatch_ok,
        "export_tool_schema_dispatch": export_dispatch_ok,
        "counts": {
            "total_checks": len(all_checks),
            "failures": len(failures),
        },
        "checks": all_checks,
        "failures": failures,
    }


def check_adapter_contract(result: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for key in (
        "openai_round_trip",
        "anthropic_round_trip",
        "openapi_valid",
        "scope_projection",
        "dispatch_parity",
        "export_tool_schema_dispatch",
    ):
        if not result.get(key):
            failures.append(f"{key} failed")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cortex universal-adapter contract gate.")
    parser.add_argument("--db-path", type=Path, help="Optional SQLite path. Defaults to a temporary database.")
    parser.add_argument("--vault-path", type=Path, help="Optional vault path. Defaults beside the SQLite database.")
    parser.add_argument("--user-id", default=USER_ID)
    parser.add_argument("--report-only", action="store_true", help="Print without failing on breaches.")
    args = parser.parse_args()

    if args.db_path:
        result = run_adapter_contract_eval(
            args.db_path.expanduser(),
            args.vault_path.expanduser() if args.vault_path else None,
            args.user_id,
        )
    else:
        tmp = Path(tempfile.mkdtemp(prefix="adapter-contract-eval-"))
        result = run_adapter_contract_eval(tmp / "adapter-contract-eval.sqlite", tmp / "Cortex.vault", args.user_id)

    print(json.dumps(result, indent=2, sort_keys=True))

    failures = check_adapter_contract(result)
    if failures and not args.report_only:
        print("\nADAPTER CONTRACT GATE FAILED:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
