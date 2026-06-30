#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path
from typing import Any


DUMMY_OPENAI_KEY = "sk-" + ("0" * 24)
DEFAULT_BASE_URL = "http://127.0.0.1:8766"
DEFAULT_APP_DOMAIN = "com.cortex.doppl"
DEFAULT_TOKEN_KEY = "localBetaAPIKey.v1"
DEFAULT_CREDENTIALS_PATH = Path.home() / "Library" / "Application Support" / "Cortex" / "credentials.json"


class LiveSmokeFailure(AssertionError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def read_default_token() -> str:
    try:
        if DEFAULT_CREDENTIALS_PATH.exists():
            payload = json.loads(DEFAULT_CREDENTIALS_PATH.read_text(encoding="utf-8"))
            token = str(payload.get(DEFAULT_TOKEN_KEY) or "").strip()
            if token:
                return token
    except (OSError, json.JSONDecodeError):
        pass
    try:
        result = subprocess.run(
            ["defaults", "read", DEFAULT_APP_DOMAIN, DEFAULT_TOKEN_KEY],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def request(
    base_url: str,
    token: str,
    path: str,
    *,
    user_id: str,
    method: str = "GET",
    data: dict[str, Any] | None = None,
    text: bool = False,
    expected_status: int = 200,
) -> Any:
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "X-Cortex-User": user_id,
    }
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base_url.rstrip("/") + path, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            status = resp.status
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        status = exc.code
    if status != expected_status:
        raise LiveSmokeFailure(
            f"{method} {path} returned HTTP {status}, expected {expected_status}",
            {"body": raw.decode("utf-8", errors="replace")[:1600]},
        )
    decoded = raw.decode("utf-8")
    if text:
        return decoded
    if not decoded:
        return {}
    return json.loads(decoded)


def ensure(condition: bool, message: str, payload: dict[str, Any] | None = None) -> None:
    if not condition:
        raise LiveSmokeFailure(message, payload)


def write_obsidian_fixture(root: Path, marker: str) -> Path:
    vault = root / "Live First 100 Vault"
    notes = vault / "Projects"
    notes.mkdir(parents=True)
    (notes / "Live Smoke.md").write_text(
        "\n".join(
            [
                "---",
                "title: Live First 100 Smoke",
                "tags: [cortex, live-smoke]",
                "---",
                "# Live First 100 Smoke",
                "",
                (
                    f"Decision: Live Cortex smoke marker {marker} proves Obsidian sync, Review approval, "
                    "cited Ask, and MCP retrieval in the packaged app."
                ),
                "Procedure: Before inviting beta users, run the connection-first live smoke against the packaged app.",
                "I prefer first-100 Cortex answers that cite the connected source before giving advice.",
                f"The live smoke redaction fixture includes password=supersecret123 and {DUMMY_OPENAI_KEY}.",
            ]
        ),
        encoding="utf-8",
    )
    return vault


class LiveSmokeRunner:
    def __init__(self, *, base_url: str, token: str, user_id: str, tmp: Path, include_backup: bool) -> None:
        self.base_url = base_url
        self.token = token
        self.mcp_token = f"cxm_first100_live_smoke_{uuid.uuid4().hex}"
        self.user_id = user_id
        self.tmp = tmp
        self.include_backup = include_backup
        self.marker = f"first100-live-smoke-{uuid.uuid4().hex[:10]}"
        self.checks: list[dict[str, Any]] = []
        self.capture_ids: list[str] = []

    def run_step(self, name: str, fn) -> None:
        try:
            payload = fn()
        except Exception as exc:
            failed = {"name": name, "status": "failed", "detail": str(exc)}
            if isinstance(exc, LiveSmokeFailure):
                failed["payload"] = exc.payload
            self.checks.append(failed)
            raise
        self.checks.append({"name": name, "status": "ok", **payload})

    def request(self, path: str, *, method: str = "GET", data: dict[str, Any] | None = None, text: bool = False, expected_status: int = 200) -> Any:
        return request(
            self.base_url,
            self.token,
            path,
            user_id=self.user_id,
            method=method,
            data=data,
            text=text,
            expected_status=expected_status,
        )

    def scoped_mcp_request(
        self,
        path: str,
        *,
        method: str = "GET",
        data: dict[str, Any] | None = None,
        text: bool = False,
        expected_status: int = 200,
    ) -> Any:
        return request(
            self.base_url,
            self.mcp_token,
            path,
            user_id=self.user_id,
            method=method,
            data=data,
            text=text,
            expected_status=expected_status,
        )

    def cleanup(self) -> dict[str, Any]:
        return self.request("/v1/user-data?include_backups=false", method="DELETE")

    def health(self) -> dict[str, Any]:
        health = self.request("/health")
        ready = self.request("/ready")
        ensure(health.get("status") == "ok", "Health endpoint was not ok", health)
        ensure(ready.get("status") == "ok", "Ready endpoint was not ok", ready)
        ensure(health.get("mode") == "standalone", "Live smoke should run against the packaged standalone backend", health)
        return {
            "detail": "Running packaged backend is healthy.",
            "payload": {
                "backend_version": health.get("backend_version"),
                "health_contract": health.get("health_contract"),
                "vault_path": health.get("vault_path"),
            },
        }

    def baseline_settings(self) -> dict[str, Any]:
        settings = self.request(
            "/v1/settings",
            method="PUT",
            data={
                "review_new_captures": True,
                "allow_pending_in_context": False,
                "allow_agent_reads": True,
                "allow_agent_writes": True,
                "allow_agent_exports": True,
                "allow_agent_maintenance": False,
                "allow_agent_destructive_actions": False,
                "redact_sensitive_context": True,
            },
        )
        ensure(settings["review_new_captures"] is True, "Review gate was not enabled", settings)
        ensure(settings["allow_pending_in_context"] is False, "Pending memory gate was not enabled", settings)
        ensure(settings["allow_agent_reads"] is True, "Read access was not enabled for smoke MCP checks", settings)
        return {"detail": "Smoke user settings are guarded and review-first.", "payload": settings}

    def scoped_mcp_token(self) -> dict[str, Any]:
        registered = self.request(
            "/v1/integrations/mcp-token",
            method="POST",
            data={"token": self.mcp_token, "label": "First 100 live smoke MCP", "scopes": ["read"]},
        )
        ensure(registered["audience"] == "mcp", "Registered token was not an MCP token", registered)
        ensure(registered["scopes"] == ["read"], "Registered MCP token did not preserve read-only scope", registered)
        self.scoped_mcp_request("/v1/search?query=should-not-work", expected_status=401)
        return {
            "detail": "Scoped read-only MCP token is registered and cannot call REST endpoints.",
            "payload": {"token_id": registered["token_id"], "scopes": registered["scopes"]},
        }

    def mcp_tools(self) -> dict[str, Any]:
        result = self.scoped_mcp_request(
            "/mcp",
            method="POST",
            data={"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}},
        )
        tools = {tool["name"] for tool in result["result"]["tools"]}
        expected = {
            "search_memory",
            "get_memory_inbox",
            "approve_memory_capture",
            "get_product_loop",
            "list_source_connectors",
            "connect_source_account",
            "sync_source_records",
            "get_style_profile",
            "get_project_context",
            "get_procedure",
        }
        missing = sorted(expected - tools)
        ensure(not missing, "Packaged MCP tool surface is missing first-100 tools", {"missing": missing, "tools": sorted(tools)})
        return {"detail": "MCP tool surface includes first-100 memory and connector tools.", "payload": {"tool_count": len(tools)}}

    def obsidian_review_ask(self) -> dict[str, Any]:
        vault = write_obsidian_fixture(self.tmp, self.marker)
        synced = self.request(
            "/v1/connectors/obsidian/sync",
            method="POST",
            data={"vault_path": str(vault), "processing": "sync", "max_records": 10},
        )
        ensure(synced["status"] == "complete", "Obsidian sync did not complete", synced)
        ensure(synced["saved"] >= 1, "Obsidian sync did not save a capture", synced)
        ensure(synced["failed"] == 0, "Obsidian sync reported failures", synced)
        self.capture_ids = [record["capture_id"] for record in synced["records"] if record.get("capture_id")]
        ensure(bool(self.capture_ids), "Obsidian sync did not return capture ids", synced)

        quoted = urllib.parse.quote(self.marker)
        pending_search = self.request(f"/v1/search?query={quoted}&limit=5")
        ensure(pending_search["results"] == [], "Pending synced memory leaked into search", pending_search)

        review = self.request("/v1/review/today")
        pending_ids = {item["id"] for item in review["pending"]}
        ensure(any(capture_id in pending_ids for capture_id in self.capture_ids), "Synced capture was not pending review", review)
        pending_captures = [item for item in review["pending"] if item["id"] in set(self.capture_ids)]
        encoded_pending = json.dumps(pending_captures)
        ensure(str(vault) not in encoded_pending, "Review queue leaked the local Obsidian vault path", {"pending": pending_captures})
        ensure(
            any(str(item.get("source_url") or "").startswith("local-file://Live%20Smoke.md") for item in pending_captures),
            "Review queue did not expose a safe local-file citation",
            {"pending": pending_captures},
        )

        for capture_id in self.capture_ids:
            approved = self.request(f"/v1/captures/{capture_id}/approve", method="POST")
            ensure(approved["approved"] is True, "Capture approval failed", approved)

        search = self.request(f"/v1/search?query={quoted}&limit=5")
        ensure(search["results"], "Approved Obsidian memory was not searchable", search)
        ensure(all(result["source"] == "obsidian" for result in search["results"]), "Search returned unexpected source", search)

        asked = self.request(f"/v1/ask?query={quoted}%20packaged%20app&limit=5")
        ensure(asked["citations"], "Ask returned no citations for approved memory", asked)
        ensure(any(self.marker in citation.get("excerpt", "") for citation in asked["citations"]), "Ask citation omitted smoke marker", asked)

        mcp_search = self.scoped_mcp_request(
            "/mcp",
            method="POST",
            data={
                "jsonrpc": "2.0",
                "id": "search",
                "method": "tools/call",
                "params": {"name": "search_memory", "arguments": {"query": self.marker, "top_k": 5}},
            },
        )
        ensure(self.marker in mcp_search["result"]["content"][0]["text"], "MCP search did not return smoke memory", mcp_search)
        return {
            "detail": "Packaged app synced Obsidian, gated pending memory, approved review, answered with citations, and served MCP retrieval.",
            "payload": {"capture_ids": self.capture_ids, "citations": len(asked["citations"])},
        }

    def privacy_and_queue(self) -> dict[str, Any]:
        markdown = self.request("/v1/export.md", text=True)
        ensure(self.marker in markdown, "Markdown export omitted smoke memory")
        ensure("[REDACTED_SECRET]" in markdown, "Markdown export did not redact password fixture")
        ensure("[REDACTED_OPENAI_KEY]" in markdown, "Markdown export did not redact API key fixture")
        ensure("supersecret123" not in markdown and DUMMY_OPENAI_KEY not in markdown, "Markdown export leaked secret fixture")

        support = self.request("/v1/support/bundle")
        encoded = json.dumps(support)
        ensure(self.marker not in encoded, "Support bundle included raw smoke memory content", support)
        ensure("supersecret123" not in encoded and DUMMY_OPENAI_KEY not in encoded, "Support bundle leaked secret fixture", support)

        jobs = self.request("/v1/jobs/run?limit=25", method="POST")
        diagnostics = self.request("/v1/diagnostics")
        ensure(jobs["failed"] == 0, "Worker run reported failed jobs", jobs)
        ensure(diagnostics["counts"]["failed_jobs"] == 0, "Diagnostics reported failed jobs", diagnostics)

        backup_payload: dict[str, Any] | None = None
        if self.include_backup:
            backup = self.request("/v1/backups", method="POST")
            ensure(int(backup.get("size_bytes") or 0) > 0, "Backup did not write data", backup)
            backup_payload = {"backup_path": backup["backup_path"], "size_bytes": backup["size_bytes"]}

        return {
            "detail": "Export redaction, support bundle privacy, and queue health passed.",
            "payload": {
                "worker": {"processed": jobs["processed"], "pending": jobs["pending"], "failed": jobs["failed"]},
                "backup": backup_payload,
            },
        }

    def run(self) -> dict[str, Any]:
        self.run_step("health", self.health)
        self.run_step("baseline_settings", self.baseline_settings)
        self.run_step("scoped_mcp_token", self.scoped_mcp_token)
        self.run_step("mcp_tools", self.mcp_tools)
        self.run_step("obsidian_review_ask", self.obsidian_review_ask)
        self.run_step("privacy_and_queue", self.privacy_and_queue)
        return {
            "status": "ok",
            "base_url": self.base_url,
            "user_id": self.user_id,
            "marker": self.marker,
            "checks": self.checks,
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a connection-first first-100 smoke against the running packaged Cortex app.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--token", default="", help="Local app REST token. Defaults to macOS app defaults when omitted.")
    parser.add_argument("--user-id", default=f"first100-live-smoke-{uuid.uuid4().hex[:10]}")
    parser.add_argument("--keep-data", action="store_true", help="Do not delete smoke user rows after the run.")
    parser.add_argument("--include-backup", action="store_true", help="Also create a real local vault backup. Off by default to avoid backup churn.")
    args = parser.parse_args(argv)

    token = args.token.strip() or read_default_token()
    if not token:
        print(json.dumps({"status": "failed", "error": "Pass --token or launch Cortex once so the local app token exists."}, indent=2))
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="cortex-first100-live-smoke-"))
    runner = LiveSmokeRunner(base_url=args.base_url, token=token, user_id=args.user_id, tmp=tmp, include_backup=args.include_backup)
    cleanup: dict[str, Any] | None = None
    try:
        result = runner.run()
        if not args.keep_data:
            cleanup = runner.cleanup()
        result["cleanup"] = cleanup or {"kept": True}
        result["temp_root"] = str(tmp) if args.keep_data else "removed"
        print(json.dumps(result, indent=2))
        return 0
    except Exception as exc:
        payload: dict[str, Any] = {
            "status": "failed",
            "error": str(exc),
            "base_url": args.base_url,
            "user_id": args.user_id,
            "checks": runner.checks,
            "temp_root": str(tmp),
        }
        if isinstance(exc, LiveSmokeFailure):
            payload["payload"] = exc.payload
        if not args.keep_data:
            try:
                payload["cleanup"] = runner.cleanup()
            except Exception as cleanup_exc:
                payload["cleanup_error"] = str(cleanup_exc)
        print(json.dumps(payload, indent=2))
        return 1
    finally:
        if not args.keep_data:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
