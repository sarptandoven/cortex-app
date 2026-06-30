#!/usr/bin/env python3
from __future__ import annotations

import argparse
import contextlib
import json
import os
import socket
import sys
import tempfile
import traceback
import uuid
from pathlib import Path
from typing import Any, Callable


API_TOKEN = "beta-smoke-local-token-1234567890"
DUMMY_OPENAI_KEY = "sk-" + ("0" * 24)


class SmokeFailure(AssertionError):
    def __init__(self, message: str, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = payload or {}


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def configure_offline_environment(tmp: Path) -> Path:
    vault_path = tmp / "Cortex.vault"
    os.environ.update(
        {
            "CORTEX_VAULT_PATH": str(vault_path),
            "CORTEX_DB_PATH": str(vault_path / "index.sqlite"),
            "CORTEX_API_KEY": API_TOKEN,
            "CORTEX_PUBLIC_BASE_URL": "http://127.0.0.1:8766",
            "CORTEX_EMBEDDING_PROVIDER": "hash",
            "CORTEX_EMBEDDING_STRICT": "0",
            "CORTEX_EXTRACTION_MODE": "local",
            "CORTEX_REQUIRE_SCOPED_API_TOKENS": "0",
            "CORTEX_SHARD_MODE": "local",
        }
    )
    for name in (
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "CORTEX_OPENAI_EMBEDDINGS_URL",
        "CORTEX_MCP_API_KEY",
        "CORTEX_MCP_API_KEY_SCOPES",
        "CORTEX_SHARD_ROOT",
        "CORTEX_ALLOW_INSECURE_DEV_TOKEN",
    ):
        os.environ.pop(name, None)
    return vault_path


@contextlib.contextmanager
def block_network() -> Any:
    attempts: list[str] = []
    original_connect = socket.socket.connect
    original_create_connection = socket.create_connection

    def blocked_connect(sock: socket.socket, address: Any) -> None:
        attempts.append(repr(address))
        raise RuntimeError(f"Network access is blocked during backend beta smoke: {address!r}")

    def blocked_create_connection(address: Any, *args: Any, **kwargs: Any) -> socket.socket:
        attempts.append(repr(address))
        raise RuntimeError(f"Network access is blocked during backend beta smoke: {address!r}")

    socket.socket.connect = blocked_connect  # type: ignore[method-assign]
    socket.create_connection = blocked_create_connection  # type: ignore[assignment]
    try:
        yield attempts
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
        socket.create_connection = original_create_connection  # type: ignore[assignment]


def write_obsidian_fixture(tmp: Path, marker: str) -> Path:
    vault_dir = tmp / "Obsidian Beta Vault"
    notes_dir = vault_dir / "Projects"
    notes_dir.mkdir(parents=True)
    (notes_dir / "Beta Smoke Notes.md").write_text(
        "\n".join(
            [
                "---",
                "title: Beta Smoke Notes",
                "tags: [cortex, beta]",
                "---",
                "# Beta Smoke Notes",
                "",
                (
                    f"Decision: Project Taipei backend smoke marker {marker} verifies Obsidian sync, "
                    "review approval, cited Ask answers, markdown export, and Trust controls before beta invites."
                ),
                "I prefer concise technical answers when debugging Cortex beta issues.",
                f"Action: follow up with Mira about the beta invite checklist for smoke marker {marker}.",
                f"The beta smoke redaction fixture includes password=supersecret123 and {DUMMY_OPENAI_KEY}.",
            ]
        ),
        encoding="utf-8",
    )
    return vault_dir


class SmokeRunner:
    def __init__(self, client: Any, tmp: Path, vault_path: Path, marker: str) -> None:
        self.client = client
        self.tmp = tmp
        self.vault_path = vault_path
        self.marker = marker
        self.headers = {"Authorization": f"Bearer {API_TOKEN}"}
        self.checks: list[dict[str, Any]] = []
        self.capture_ids: list[str] = []
        self.source_account_id = ""
        self.export_token = "cxa_beta_smoke_export_1234567890"
        self.write_token = "cxa_beta_smoke_write_1234567890"

    def run_step(self, name: str, func: Callable[[], dict[str, Any]]) -> None:
        try:
            payload = func()
        except Exception as exc:
            failed = {"name": name, "status": "failed", "detail": str(exc)}
            if isinstance(exc, SmokeFailure):
                failed["payload"] = exc.payload
            self.checks.append(failed)
            raise
        self.checks.append({"name": name, "status": "ok", **payload})

    def request(
        self,
        method: str,
        path: str,
        *,
        expected_status: int = 200,
        headers: dict[str, str] | None = None,
        **kwargs: Any,
    ) -> Any:
        response = self.client.request(method, path, headers=headers or self.headers, **kwargs)
        if response.status_code != expected_status:
            raise SmokeFailure(
                f"{method} {path} returned HTTP {response.status_code}, expected {expected_status}",
                {"body": response.text[:1200]},
            )
        return response

    def ensure(self, condition: bool, message: str, payload: dict[str, Any] | None = None) -> None:
        if not condition:
            raise SmokeFailure(message, payload)

    def startup_health(self) -> dict[str, Any]:
        health = self.request("GET", "/health", headers={}).json()
        ready = self.request("GET", "/ready", headers={}).json()
        diagnostics = self.request("GET", "/v1/diagnostics").json()
        self.ensure(health.get("status") == "ok", "Health did not return ok", health)
        self.ensure(ready.get("status") == "ok", "Ready did not return ok", ready)
        self.ensure(diagnostics.get("status") == "ok", "Diagnostics did not return ok", diagnostics)
        embedding = diagnostics.get("embedding") or {}
        self.ensure(embedding.get("provider") == "hash", "Smoke must use hash embeddings", embedding)
        self.ensure(embedding.get("network_required") is False, "Smoke embeddings must not require network", embedding)
        self.ensure(str(self.vault_path) in str(diagnostics.get("db_path")), "Diagnostics did not use temp vault", diagnostics)
        return {
            "detail": "FastAPI app imported, temp store initialized, health/ready/diagnostics passed.",
            "payload": {
                "mode": health.get("mode"),
                "health_contract": health.get("health_contract"),
                "db_path": diagnostics.get("db_path"),
                "embedding": embedding,
            },
        }

    def baseline_trust_settings(self) -> dict[str, Any]:
        settings = self.request(
            "PUT",
            "/v1/settings",
            json={
                "review_new_captures": True,
                "allow_pending_in_context": False,
                "allow_agent_reads": True,
                "allow_agent_writes": False,
                "allow_agent_exports": False,
                "allow_agent_maintenance": False,
                "allow_agent_destructive_actions": False,
                "redact_sensitive_context": True,
            },
        ).json()
        self.ensure(settings["review_new_captures"] is True, "Review gate is not enabled", settings)
        self.ensure(settings["allow_pending_in_context"] is False, "Pending context gate is not enabled", settings)
        self.ensure(settings["allow_agent_writes"] is False, "Baseline Trust write gate did not apply", settings)
        self.ensure(settings["allow_agent_exports"] is False, "Agent exports should start disabled", settings)
        return {
            "detail": "Guarded beta Trust settings applied.",
            "payload": {
                "review_new_captures": settings["review_new_captures"],
                "allow_pending_in_context": settings["allow_pending_in_context"],
                "allow_agent_writes": settings["allow_agent_writes"],
                "allow_agent_exports": settings["allow_agent_exports"],
            },
        }

    def mcp_tool_surface(self) -> dict[str, Any]:
        listed = self.request(
            "POST",
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
        ).json()
        tools = {tool["name"] for tool in listed["result"]["tools"]}
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
        self.ensure(not missing, "MCP tool list is missing first-100 tools", {"missing": missing, "tools": sorted(tools)})
        return {
            "detail": "MCP exposes the high-value first-100 memory and connector tools.",
            "payload": {"tool_count": len(tools), "checked": sorted(expected)},
        }

    def sync_review_and_approve(self) -> dict[str, Any]:
        vault_dir = write_obsidian_fixture(self.tmp, self.marker)
        synced = self.request(
            "POST",
            "/v1/connectors/obsidian/sync",
            json={"vault_path": str(vault_dir), "processing": "sync", "max_records": 10},
        ).json()
        self.source_account_id = synced["source_account_id"]
        self.capture_ids = [record["capture_id"] for record in synced["records"] if record.get("capture_id")]
        self.ensure(synced["status"] == "complete", "Obsidian sync did not complete", synced)
        self.ensure(synced["source"] == "obsidian", "Obsidian sync returned the wrong source", synced)
        self.ensure(synced["source_account"]["source"] == "obsidian", "Obsidian source account had wrong source", synced)
        self.ensure(synced["source_account"]["connection_type"] == "local-folder", "Obsidian source account was not local-folder", synced)
        self.ensure(synced["saved"] >= 1, "Obsidian sync did not save any captures", synced)
        self.ensure(synced["failed"] == 0, "Obsidian sync had failures", synced)
        self.ensure(synced["scan"]["records_found"] >= 1, "Obsidian scan found no records", synced)
        self.ensure(synced["source_account"]["connection_type"] == "local-folder", "Obsidian account was not local-folder", synced)
        self.ensure(bool(self.capture_ids), "Obsidian sync returned no capture IDs", synced)

        search_pending = self.request("GET", "/v1/search", params={"query": self.marker, "limit": 5}).json()
        self.ensure(search_pending["results"] == [], "Pending Obsidian sync leaked into search", search_pending)

        review = self.request("GET", "/v1/review/today").json()
        pending_ids = {item["id"] for item in review["pending"]}
        self.ensure(any(capture_id in pending_ids for capture_id in self.capture_ids), "Synced capture was not pending review", review)
        self.ensure(review["recommended_actions"], "Daily review did not include recommended actions", review)
        readiness_before = self.request("GET", "/v1/sources/readiness").json()
        obsidian_before = next((item for item in readiness_before.get("sources") or [] if item.get("source") == "obsidian"), None)
        self.ensure(obsidian_before is not None, "Readiness omitted Obsidian before approval", readiness_before)
        self.ensure(obsidian_before["status"] == "needs_review", "Obsidian readiness was not needs_review before approval", obsidian_before)
        self.ensure(obsidian_before["pending"] >= len(self.capture_ids), "Obsidian readiness did not count pending captures", obsidian_before)

        for capture_id in self.capture_ids:
            approved = self.request("POST", f"/v1/captures/{capture_id}/approve").json()
            self.ensure(approved["approved"] is True, f"Capture {capture_id} was not approved", approved)

        readiness_after = self.request("GET", "/v1/sources/readiness").json()
        obsidian_after = next((item for item in readiness_after.get("sources") or [] if item.get("source") == "obsidian"), None)
        self.ensure(obsidian_after is not None, "Readiness omitted Obsidian after approval", readiness_after)
        self.ensure(obsidian_after["status"] == "synced", "Obsidian readiness was not synced after approval", obsidian_after)
        self.ensure(obsidian_after["pending"] == 0, "Obsidian readiness still had pending captures after approval", obsidian_after)
        self.ensure(obsidian_after["approved"] >= len(self.capture_ids), "Obsidian readiness did not count approved captures", obsidian_after)
        self.ensure(obsidian_after["active_memories"] >= 1, "Obsidian readiness did not count active memories", obsidian_after)
        self.ensure(obsidian_after["citation_coverage"] == 1.0, "Obsidian readiness did not report full citation coverage", obsidian_after)

        search_approved = self.request("GET", "/v1/search", params={"query": self.marker, "limit": 5}).json()
        self.ensure(search_approved["results"], "Approved Obsidian memory was not searchable", search_approved)
        self.ensure(
            all(result["source"] == "obsidian" for result in search_approved["results"]),
            "Approved search returned non-Obsidian source records for the marker",
            search_approved,
        )
        encoded_search = json.dumps(search_approved)
        self.ensure(str(vault_dir) not in encoded_search, "Approved search leaked the temp Obsidian vault path", search_approved)
        self.ensure("file:///Users/" not in encoded_search, "Approved search leaked a raw local file URL", search_approved)
        self.ensure(
            any(
                str(result.get("source_url") or "").startswith("local-file://")
                and "line=" in str(result.get("source_url") or "")
                and "excerpt=" in str(result.get("source_url") or "")
                for result in search_approved["results"]
            ),
            "Approved search did not include safe cited local-file source URLs",
            search_approved,
        )
        return {
            "detail": "Generated Obsidian vault synced, held for review, approved, and became searchable.",
            "payload": {
                "source_account_id": self.source_account_id,
                "records_found": synced["scan"]["records_found"],
                "capture_ids": self.capture_ids,
                "approved_results": len(search_approved["results"]),
                "active_obsidian_memories": obsidian_after["active_memories"],
            },
        }

    def ask_and_export(self) -> dict[str, Any]:
        asked = self.request("GET", "/v1/ask", params={"query": f"{self.marker} beta invite checklist", "limit": 5}).json()
        self.ensure(asked["citations"], "Ask returned no citations", asked)
        self.ensure(
            any(self.marker in citation.get("excerpt", "") for citation in asked["citations"]),
            "Ask citations did not include the smoke marker",
            asked,
        )
        matching_citation = next((citation for citation in asked["citations"] if self.marker in citation.get("excerpt", "")), None)
        self.ensure(matching_citation is not None, "Ask did not return a citation matching the smoke marker", asked)
        self.ensure(matching_citation["source"] == "obsidian", "Ask marker citation was not from Obsidian", matching_citation)
        citation_url = str(matching_citation.get("source_url") or "")
        self.ensure(citation_url.startswith("local-file://"), "Ask marker citation was not a safe local-file locator", matching_citation)
        self.ensure("line=" in citation_url and "excerpt=" in citation_url, "Ask marker citation missed line/excerpt source parameters", matching_citation)
        self.ensure(matching_citation.get("source_account_id"), "Ask marker citation missed source_account_id", matching_citation)
        encoded_ask = json.dumps(asked)
        self.ensure(str(self.tmp) not in encoded_ask, "Ask leaked the temp vault path", asked)
        self.ensure("file:///Users/" not in encoded_ask, "Ask leaked a raw local file URL", asked)

        exported = self.request("GET", "/v1/export.json").json()
        self.ensure(exported["stats"]["memories"] >= 1, "JSON export did not include memory stats", exported.get("stats"))

        markdown = self.request("GET", "/v1/export.md").text
        self.ensure("# Cortex Export" in markdown, "Markdown export did not render Cortex export heading")
        self.ensure(self.marker in markdown, "Markdown export omitted approved smoke memory")
        self.ensure("[REDACTED_SECRET]" in markdown, "Markdown export did not redact password fixture")
        self.ensure("[REDACTED_OPENAI_KEY]" in markdown, "Markdown export did not redact API key fixture")
        self.ensure("supersecret123" not in markdown and DUMMY_OPENAI_KEY not in markdown, "Markdown export leaked secret fixture")
        return {
            "detail": "Ask returned cited memory and exports included approved, redacted temp data.",
            "payload": {
                "citations": len(asked["citations"]),
                "export_memories": exported["stats"]["memories"],
            },
        }

    def backup_support_and_queue_health(self) -> dict[str, Any]:
        backup = self.request("POST", "/v1/backups").json()
        self.ensure(int(backup.get("size_bytes") or 0) > 0, "Backup did not write data", backup)
        self.ensure(Path(backup["backup_path"]).exists(), "Backup path does not exist", backup)

        support = self.request("GET", "/v1/support/bundle").json()
        encoded = json.dumps(support)
        self.ensure(self.marker not in encoded, "Support bundle included raw smoke memory content", support)
        self.ensure("supersecret123" not in encoded and DUMMY_OPENAI_KEY not in encoded, "Support bundle leaked secret fixture", support)

        jobs = self.request("POST", "/v1/jobs/run", params={"limit": 25}).json()
        queue_health = self.request("GET", "/v1/jobs/health").json()
        diagnostics = self.request("GET", "/v1/diagnostics").json()
        self.ensure(jobs["failed"] == 0, "Worker run reported failed jobs", jobs)
        self.ensure(queue_health["status"] != "blocked", "Queue health is blocked", queue_health)
        self.ensure(queue_health["counts"]["failed"] == 0, "Queue health reported failed jobs", queue_health)
        self.ensure(not queue_health["stale_running"], "Queue health reported stale running jobs", queue_health)
        self.ensure(diagnostics["counts"]["failed_jobs"] == 0, "Diagnostics reported failed jobs", diagnostics)
        return {
            "detail": "Backup, content-free support bundle, and queue health checks passed.",
            "payload": {
                "backup_path": backup["backup_path"],
                "worker": {"processed": jobs["processed"], "pending": jobs["pending"], "failed": jobs["failed"]},
                "queue": {
                    "status": queue_health["status"],
                    "queued": queue_health["counts"]["queued"],
                    "running": queue_health["counts"]["running"],
                    "failed": queue_health["counts"]["failed"],
                },
            },
        }

    def scoped_trust_controls(self) -> dict[str, Any]:
        for token, label, scopes in (
            (self.write_token, "Beta smoke write token", ["read", "write"]),
            (self.export_token, "Beta smoke export token", ["read", "export"]),
        ):
            registered = self.request(
                "POST",
                "/v1/integrations/api-token",
                json={"token": token, "label": label, "scopes": scopes},
            ).json()
            self.ensure(set(registered["scopes"]) == set(scopes), "Scoped token registration returned unexpected scopes", registered)

        write_headers = {"Authorization": f"Bearer {self.write_token}", "X-Cortex-User": "local"}
        export_headers = {"Authorization": f"Bearer {self.export_token}", "X-Cortex-User": "local"}
        blocked_write = self.request(
            "POST",
            "/v1/captures",
            expected_status=403,
            headers=write_headers,
            json={"content": "Blocked scoped write should not save.", "source": "beta-smoke"},
        ).json()
        self.ensure("writes are disabled" in blocked_write["detail"], "Scoped write was not blocked by Trust", blocked_write)

        blocked_export = self.request("GET", "/v1/export.md", expected_status=403, headers=export_headers).json()
        self.ensure("exports are disabled" in blocked_export["detail"], "Scoped export was not blocked by Trust", blocked_export)

        enabled = self.request("PUT", "/v1/settings", json={"allow_agent_writes": True, "allow_agent_exports": True}).json()
        self.ensure(enabled["allow_agent_writes"] is True, "Agent writes did not enable", enabled)
        self.ensure(enabled["allow_agent_exports"] is True, "Agent exports did not enable", enabled)

        allowed_write = self.request(
            "POST",
            "/v1/captures",
            headers=write_headers,
            json={
                "content": f"Scoped write allowed for backend beta smoke marker {self.marker}.",
                "source": "beta-smoke",
            },
        ).json()
        self.ensure(bool(allowed_write["capture_id"]), "Scoped write did not create capture", allowed_write)
        scoped_markdown = self.request("GET", "/v1/export.md", headers=export_headers).text
        self.ensure("# Cortex Export" in scoped_markdown, "Scoped export did not return markdown export")

        trust = self.request("GET", "/v1/trust/summary").json()
        risk_text = "\n".join(trust["risk_flags"])
        self.ensure("write" in risk_text.lower(), "Trust summary did not flag enabled writes", trust)
        self.ensure("export" in risk_text.lower(), "Trust summary did not flag enabled exports", trust)
        return {
            "detail": "Scoped REST tokens were blocked by Trust defaults, then allowed after explicit Trust updates.",
            "payload": {
                "allowed_capture_id": allowed_write["capture_id"],
                "trust_score": trust["trust_score"],
                "risk_flags": trust["risk_flags"],
            },
        }

    def run(self) -> dict[str, Any]:
        self.run_step("startup_health", self.startup_health)
        self.run_step("baseline_trust_settings", self.baseline_trust_settings)
        self.run_step("mcp_tool_surface", self.mcp_tool_surface)
        self.run_step("obsidian_sync_review_approve", self.sync_review_and_approve)
        self.run_step("ask_export", self.ask_and_export)
        self.run_step("backup_support_queue_health", self.backup_support_and_queue_health)
        self.run_step("scoped_trust_controls", self.scoped_trust_controls)
        return {
            "status": "ok",
            "marker": self.marker,
            "checks": self.checks,
            "temp_vault": str(self.vault_path),
            "network": "socket connect/create_connection blocked during smoke checks",
        }


def run_smoke(tmp: Path) -> dict[str, Any]:
    root = repo_root()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    vault_path = configure_offline_environment(tmp)
    marker = f"backend-beta-smoke-{uuid.uuid4().hex[:10]}"

    from fastapi.testclient import TestClient

    with block_network() as network_attempts:
        from backend.app import main as main_module

        runner = SmokeRunner(TestClient(main_module.app), tmp, vault_path, marker)
        try:
            result = runner.run()
        except Exception as exc:
            if isinstance(exc, SmokeFailure):
                exc.payload = {**exc.payload, "checks": runner.checks}
            else:
                setattr(exc, "smoke_checks", runner.checks)
            raise
        runner.ensure(not network_attempts, "Smoke attempted network access", {"attempts": network_attempts})
        return result


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run the Cortex backend beta smoke lane against temp data with network sockets blocked."
    )
    parser.add_argument("--keep-temp", action="store_true", help="Keep the temp vault and Obsidian fixture for debugging.")
    args = parser.parse_args()

    tmp_root = Path(tempfile.mkdtemp(prefix="cortex-backend-beta-smoke-"))
    try:
        result = run_smoke(tmp_root)
    except Exception as exc:
        payload = {
            "status": "failed",
            "error": str(exc),
            "temp_root": str(tmp_root),
            "traceback": traceback.format_exc(limit=8),
        }
        if isinstance(exc, SmokeFailure):
            payload["payload"] = exc.payload
            payload["checks"] = exc.payload.get("checks", [])
        elif hasattr(exc, "smoke_checks"):
            payload["checks"] = getattr(exc, "smoke_checks")
        print(json.dumps(payload, indent=2))
        raise SystemExit(1) from exc
    else:
        result["temp_root"] = str(tmp_root) if args.keep_temp else "removed"
        print(json.dumps(result, indent=2))
    finally:
        if not args.keep_temp:
            import shutil

            shutil.rmtree(tmp_root, ignore_errors=True)


if __name__ == "__main__":
    main()
