#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
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
ROOT = Path(__file__).resolve().parents[1]


class LiveSmokeFailure(AssertionError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def read_default_token() -> str:
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", DEFAULT_APP_DOMAIN, "-a", DEFAULT_TOKEN_KEY, "-w"],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0:
            token = result.stdout.strip()
            if token:
                return token
    except (OSError, subprocess.SubprocessError):
        pass
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


def default_mcp_stdio_path() -> Path:
    built_app_script = ROOT / "macos" / "build" / "Cortex.app" / "Contents" / "Resources" / "scripts" / "cortex_mcp_stdio.py"
    if built_app_script.exists():
        return built_app_script
    return Path(__file__).resolve().with_name("cortex_mcp_stdio.py")


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


def obsidian_fixture_note(vault: Path) -> Path:
    return vault / "Projects" / "Live Smoke.md"


def write_obsidian_fixture_note(vault: Path, marker: str, *, edited: bool = False) -> Path:
    notes = vault / "Projects"
    notes.mkdir(parents=True, exist_ok=True)
    decision = (
        f"Decision: Live Cortex edited marker {marker} proves changed Obsidian notes replace stale memory "
        "without creating a second source record."
        if edited
        else (
            f"Decision: Live Cortex smoke marker {marker} proves Obsidian sync, Review approval, "
            "cited Ask, and MCP retrieval in the packaged app."
        )
    )
    procedure = (
        "Procedure: After editing a connected note, re-run the connection-first live smoke and verify stale citations disappear."
        if edited
        else "Procedure: Before inviting beta users, run the connection-first live smoke against the packaged app."
    )
    preference = (
        "I prefer first-100 Cortex answers that cite the latest connected source revision before giving advice."
        if edited
        else "I prefer first-100 Cortex answers that cite the connected source before giving advice."
    )
    note = obsidian_fixture_note(vault)
    note.write_text(
        "\n".join(
            [
                "---",
                "title: Live First 100 Smoke",
                "tags: [cortex, live-smoke]",
                "---",
                "# Live First 100 Smoke",
                "",
                decision,
                procedure,
                preference,
                f"The live smoke redaction fixture includes password=supersecret123 and {DUMMY_OPENAI_KEY}.",
            ]
        ),
        encoding="utf-8",
    )
    return note


def write_obsidian_fixture(root: Path, marker: str) -> Path:
    vault = root / "Live First 100 Vault"
    write_obsidian_fixture_note(vault, marker)
    return vault


class LiveSmokeRunner:
    def __init__(self, *, base_url: str, token: str, user_id: str, tmp: Path, include_backup: bool, mcp_stdio_path: Path) -> None:
        self.base_url = base_url
        self.token = token
        self.mcp_token = f"cxm_first100_live_smoke_{uuid.uuid4().hex}"
        self.user_id = user_id
        self.tmp = tmp
        self.include_backup = include_backup
        self.mcp_stdio_path = mcp_stdio_path
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
            "get_product_loop",
            "list_source_connectors",
            "get_style_profile",
            "get_project_context",
            "get_procedure",
        }
        missing = sorted(expected - tools)
        ensure(not missing, "Packaged MCP tool surface is missing first-100 tools", {"missing": missing, "tools": sorted(tools)})
        blocked = {
            "approve_memory_capture",
            "connect_source_account",
            "sync_source_records",
            "sync_connected_sources",
            "get_memory_inbox",
            "get_daily_review",
            "delete_all_user_data",
        }
        exposed = sorted(blocked & tools)
        ensure(not exposed, "Read-only MCP token exposed review, write, or destructive tools", {"exposed": exposed, "tools": sorted(tools)})
        return {"detail": "Read-only MCP tool surface includes retrieval tools without write actions.", "payload": {"tool_count": len(tools)}}

    def obsidian_review_ask(self) -> dict[str, Any]:
        def step_status(loop: dict[str, Any], key: str) -> str:
            for item in loop.get("steps") or []:
                if item.get("key") == key:
                    return str(item.get("status") or "")
            return ""

        def assert_notes_review_ask_loop(loop: dict[str, Any], phase: str) -> None:
            steps = loop.get("steps") or []
            keys = [str(item.get("key") or "") for item in steps]
            expected = ["capture", "review", "reuse"]
            positions: list[int] = []
            for key in expected:
                ensure(key in keys, f"Product loop omitted {key} during {phase}", loop)
                positions.append(keys.index(key))
            ensure(
                positions == sorted(positions),
                f"Product loop was not notes -> Review -> Ask during {phase}",
                {"step_keys": keys, "loop": loop},
            )

            encoded = json.dumps(loop).lower()
            blocked_terms = ("manual import", "context-copy", "context copy", "copy context")
            leaked_terms = [term for term in blocked_terms if term in encoded]
            ensure(
                not leaked_terms,
                f"Product loop referenced a manual import/context-copy path during {phase}",
                {"terms": leaked_terms, "loop": loop},
            )

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
        loop_after_sync = self.request("/v1/loop")
        assert_notes_review_ask_loop(loop_after_sync, "notes sync")
        ensure(loop_after_sync["primary_action"]["action"] == "review", "Product loop did not move to Review after sync", loop_after_sync)
        ensure(loop_after_sync["counts"]["pending_captures"] >= len(self.capture_ids), "Product loop did not count pending synced captures", loop_after_sync)
        ensure(step_status(loop_after_sync, "capture") == "done", "Product loop capture step was not done after sync", loop_after_sync)
        ensure(step_status(loop_after_sync, "review") == "current", "Product loop review step was not current after sync", loop_after_sync)

        pending_search = self.request(f"/v1/search?query={quoted}&limit=5")
        ensure(pending_search["results"] == [], "Pending synced memory leaked into search", pending_search)

        pending_ask = self.request(f"/v1/ask?query={quoted}%20packaged%20app&limit=5")
        ensure(pending_ask["citations"] == [], "Pending synced memory leaked citations into Ask", pending_ask)
        ensure(pending_ask["results"] == [], "Pending synced memory leaked results into Ask", pending_ask)
        ensure(
            "Decision: Live Cortex smoke marker" not in pending_ask["answer"],
            "Pending synced memory leaked fixture content into Ask answer",
            pending_ask,
        )

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

        blocked_approval = self.scoped_mcp_request(
            "/mcp",
            method="POST",
            data={
                "jsonrpc": "2.0",
                "id": "blocked-approval",
                "method": "tools/call",
                "params": {
                    "name": "approve_memory_capture",
                    "arguments": {"capture_id": self.capture_ids[0]},
                },
            },
        )
        ensure("error" in blocked_approval, "Read-only MCP token was able to approve memory", blocked_approval)

        for capture_id in self.capture_ids:
            approved = self.request(f"/v1/captures/{capture_id}/approve", method="POST")
            ensure(approved["approved"] is True, "Capture approval failed", approved)

        review_after_approval = self.request("/v1/review/today")
        remaining_pending_ids = {item["id"] for item in review_after_approval["pending"]}
        ensure(
            not any(capture_id in remaining_pending_ids for capture_id in self.capture_ids),
            "Approved captures remained in Review",
            {"capture_ids": self.capture_ids, "pending": review_after_approval["pending"]},
        )
        loop_after_approval = self.request("/v1/loop")
        assert_notes_review_ask_loop(loop_after_approval, "Review approval")
        ensure(loop_after_approval["primary_action"]["action"] == "reuse", "Product loop did not move to Ask after approval", loop_after_approval)
        ensure(loop_after_approval["counts"]["pending_captures"] == 0, "Product loop still counted pending captures after approval", loop_after_approval)
        ensure(loop_after_approval["counts"]["approved_today"] >= len(self.capture_ids), "Product loop did not count approved captures", loop_after_approval)
        ensure(step_status(loop_after_approval, "review") == "done", "Product loop review step was not done after approval", loop_after_approval)

        readiness = self.request("/v1/sources/readiness")
        obsidian_source = next((item for item in readiness.get("sources") or [] if item.get("source") == "obsidian"), None)
        ensure(obsidian_source is not None, "Source readiness omitted Obsidian", readiness)
        ensure(int(obsidian_source.get("active_memories") or 0) > 0, "Source readiness did not count approved Obsidian memory", obsidian_source)
        ensure(readiness.get("summary", {}).get("connected", 0) >= 1, "Source readiness did not report a connected notes source", readiness)

        search = self.request(f"/v1/search?query={quoted}&limit=5")
        ensure(search["results"], "Approved Obsidian memory was not searchable", search)
        ensure(all(result["source"] == "obsidian" for result in search["results"]), "Search returned unexpected source", search)

        asked = self.request(f"/v1/ask?query={quoted}%20packaged%20app&limit=5")
        ensure(asked["citations"], "Ask returned no citations for approved memory", asked)
        ensure(any(self.marker in citation.get("excerpt", "") for citation in asked["citations"]), "Ask citation omitted smoke marker", asked)
        ensure(
            any(str(citation.get("source_url") or "").startswith("local-file://Live%20Smoke.md") for citation in asked["citations"]),
            "Ask citations did not include a safe local-file source",
            asked,
        )
        ensure(str(vault) not in json.dumps(asked), "Ask leaked the local Obsidian vault path", asked)
        loop_after_ask = self.request("/v1/loop")
        assert_notes_review_ask_loop(loop_after_ask, "cited Ask")
        ensure(loop_after_ask["primary_action"]["action"] == "done", "Product loop did not complete after cited Ask", loop_after_ask)
        ensure(loop_after_ask["counts"]["used_today"] >= 1, "Product loop did not count cited Ask use", loop_after_ask)
        ensure(step_status(loop_after_ask, "reuse") == "done", "Product loop reuse step was not done after cited Ask", loop_after_ask)

        unchanged = self.request(
            "/v1/connectors/obsidian/sync",
            method="POST",
            data={"vault_path": str(vault), "processing": "sync", "max_records": 10},
        )
        ensure(unchanged["status"] == "complete", "Unchanged Obsidian resync did not complete", unchanged)
        ensure(unchanged["saved"] == 0, "Unchanged Obsidian resync saved duplicate captures", unchanged)
        ensure(unchanged["skipped"] >= len(self.capture_ids), "Unchanged Obsidian resync did not skip existing note records", unchanged)
        unchanged_capture_ids = {record.get("capture_id") for record in unchanged.get("records") or [] if record.get("capture_id")}
        ensure(
            set(self.capture_ids).issubset(unchanged_capture_ids),
            "Unchanged Obsidian resync did not report the existing capture id",
            {"capture_ids": self.capture_ids, "unchanged": unchanged},
        )

        previous_marker = self.marker
        updated_marker = f"first100-live-smoke-edited-{uuid.uuid4().hex[:10]}"
        write_obsidian_fixture_note(vault, updated_marker, edited=True)
        changed = self.request(
            "/v1/connectors/obsidian/sync",
            method="POST",
            data={"vault_path": str(vault), "processing": "sync", "max_records": 10},
        )
        ensure(changed["status"] == "complete", "Changed Obsidian resync did not complete", changed)
        ensure(changed["saved"] >= 1, "Changed Obsidian resync did not update a capture", changed)
        ensure(changed["failed"] == 0, "Changed Obsidian resync reported failures", changed)
        updated_records = [record for record in changed.get("records") or [] if record.get("capture_id") in set(self.capture_ids)]
        ensure(updated_records, "Changed Obsidian resync did not reuse the existing capture id", changed)
        ensure(any(record.get("status") == "updated" for record in updated_records), "Changed Obsidian resync was not marked updated", changed)
        ensure(str(vault) not in json.dumps(changed), "Changed Obsidian resync leaked the local vault path", changed)

        previous_quoted = urllib.parse.quote(previous_marker)
        updated_quoted = urllib.parse.quote(updated_marker)
        stale_search = self.request(f"/v1/search?query={previous_quoted}&limit=5")
        ensure(stale_search["results"] == [], "Edited Obsidian note left stale approved memory searchable", stale_search)
        pending_updated_ask = self.request(f"/v1/ask?query={updated_quoted}%20edited%20source&limit=5")
        ensure(pending_updated_ask["citations"] == [], "Edited pending Obsidian memory leaked citations into Ask", pending_updated_ask)
        ensure(pending_updated_ask["results"] == [], "Edited pending Obsidian memory leaked results into Ask", pending_updated_ask)

        review_after_edit = self.request("/v1/review/today")
        pending_after_edit = {item["id"] for item in review_after_edit["pending"]}
        ensure(
            any(capture_id in pending_after_edit for capture_id in self.capture_ids),
            "Edited Obsidian capture was not returned to Review",
            {"capture_ids": self.capture_ids, "pending": review_after_edit["pending"]},
        )
        for capture_id in self.capture_ids:
            approved = self.request(f"/v1/captures/{capture_id}/approve", method="POST")
            ensure(approved["approved"] is True, "Edited capture approval failed", approved)

        self.marker = updated_marker
        quoted = updated_quoted
        refreshed_search = self.request(f"/v1/search?query={quoted}&limit=5")
        ensure(refreshed_search["results"], "Edited approved Obsidian memory was not searchable", refreshed_search)
        ensure(
            all(previous_marker not in json.dumps(result) for result in refreshed_search["results"]),
            "Edited approved Obsidian search returned stale marker content",
            refreshed_search,
        )
        asked = self.request(f"/v1/ask?query={quoted}%20edited%20source&limit=5")
        ensure(asked["citations"], "Ask returned no citations for edited approved memory", asked)
        ensure(any(self.marker in citation.get("excerpt", "") for citation in asked["citations"]), "Ask citation omitted edited marker", asked)
        ensure(
            any(str(citation.get("source_url") or "").startswith("local-file://Live%20Smoke.md") for citation in asked["citations"]),
            "Edited Ask citations did not include a safe local-file source",
            asked,
        )
        ensure(str(vault) not in json.dumps(asked), "Edited Ask leaked the local Obsidian vault path", asked)

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

        mcp_procedure = self.scoped_mcp_request(
            "/mcp",
            method="POST",
            data={
                "jsonrpc": "2.0",
                "id": "procedure",
                "method": "tools/call",
                "params": {
                    "name": "get_procedure",
                    "arguments": {"query": "edited connected note stale citations disappear", "limit": 3},
                },
            },
        )
        ensure("stale citations disappear" in mcp_procedure["result"]["content"][0]["text"], "MCP get_procedure missed edited smoke procedure", mcp_procedure)

        mcp_style = self.scoped_mcp_request(
            "/mcp",
            method="POST",
            data={
                "jsonrpc": "2.0",
                "id": "style",
                "method": "tools/call",
                "params": {
                    "name": "get_style_profile",
                    "arguments": {"query": "first-100 Cortex answers cite latest connected source revision", "limit": 4},
                },
            },
        )
        ensure("latest connected source revision" in mcp_style["result"]["content"][0]["text"], "MCP get_style_profile missed edited smoke preference", mcp_style)

        return {
            "detail": "Packaged app synced Obsidian, skipped unchanged notes, replaced edited memory, gated Review, answered with safe citations, and served scoped MCP retrieval.",
            "payload": {
                "capture_ids": self.capture_ids,
                "citations": len(asked["citations"]),
                "active_obsidian_memories": obsidian_source.get("active_memories"),
                "unchanged_skipped": unchanged["skipped"],
                "edited_records": len(updated_records),
            },
        }

    def mcp_stdio_bridge(self) -> dict[str, Any]:
        script = self.mcp_stdio_path.expanduser()
        ensure(script.exists(), "MCP stdio proxy script is missing", {"path": str(script)})
        ensure(script.is_file(), "MCP stdio proxy path is not a file", {"path": str(script)})
        messages = [
            {"jsonrpc": "2.0", "id": "initialize", "method": "initialize", "params": {}},
            {"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}},
            {
                "jsonrpc": "2.0",
                "id": "search",
                "method": "tools/call",
                "params": {"name": "search_memory", "arguments": {"query": self.marker, "top_k": 5}},
            },
        ]
        stdin = "\n".join(json.dumps(message) for message in messages) + "\n"
        env = os.environ.copy()
        env["CORTEX_BASE_URL"] = self.base_url.rstrip("/")
        env["CORTEX_API_KEY"] = self.mcp_token
        python = shutil.which("python3") or sys.executable
        try:
            result = subprocess.run(
                [python, str(script)],
                input=stdin,
                capture_output=True,
                text=True,
                timeout=30,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise LiveSmokeFailure("MCP stdio proxy could not be executed", {"path": str(script), "error": str(exc)}) from exc
        payload = {
            "path": str(script),
            "returncode": result.returncode,
            "stdout": result.stdout[-2000:],
            "stderr": result.stderr[-2000:],
        }
        ensure(result.returncode == 0, "MCP stdio proxy exited non-zero", payload)
        lines = [line for line in result.stdout.splitlines() if line.strip()]
        ensure(len(lines) == len(messages), "MCP stdio proxy did not emit one response per request", payload)
        responses: dict[str, dict[str, Any]] = {}
        for line in lines:
            try:
                response = json.loads(line)
            except json.JSONDecodeError as exc:
                raise LiveSmokeFailure("MCP stdio proxy emitted invalid JSON", {**payload, "line": line}) from exc
            responses[str(response.get("id"))] = response
        missing_ids = [message["id"] for message in messages if str(message["id"]) not in responses]
        ensure(not missing_ids, "MCP stdio proxy omitted expected response ids", {**payload, "missing_ids": missing_ids})
        ensure("error" not in responses.get("initialize", {}), "MCP stdio initialize failed", responses.get("initialize"))
        ensure("error" not in responses.get("tools", {}), "MCP stdio tools/list failed", responses.get("tools"))
        ensure("error" not in responses.get("search", {}), "MCP stdio search_memory failed", responses.get("search"))
        tools = {tool["name"] for tool in responses["tools"]["result"]["tools"]}
        ensure("search_memory" in tools, "MCP stdio tools/list omitted search_memory", {"tools": sorted(tools)})
        search_text = responses["search"]["result"]["content"][0]["text"]
        ensure(self.marker in search_text, "MCP stdio search did not return approved smoke memory", {"text": search_text[:2000]})
        return {
            "detail": "MCP stdio proxy initialized, listed tools, and retrieved approved Obsidian memory.",
            "payload": {"path": str(script), "tool_count": len(tools)},
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
        self.run_step("mcp_stdio_bridge", self.mcp_stdio_bridge)
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
    parser.add_argument(
        "--mcp-stdio-path",
        type=Path,
        default=None,
        help="Path to the MCP stdio proxy script to verify. Defaults to the built app script, with repo fallback when no app build exists.",
    )
    args = parser.parse_args(argv)

    token = args.token.strip() or read_default_token()
    if not token:
        print(json.dumps({"status": "failed", "error": "Pass --token or launch Cortex once so the local app token exists."}, indent=2))
        return 2

    tmp = Path(tempfile.mkdtemp(prefix="cortex-first100-live-smoke-"))
    mcp_stdio_path = args.mcp_stdio_path or default_mcp_stdio_path()
    runner = LiveSmokeRunner(
        base_url=args.base_url,
        token=token,
        user_id=args.user_id,
        tmp=tmp,
        include_backup=args.include_backup,
        mcp_stdio_path=mcp_stdio_path,
    )
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
