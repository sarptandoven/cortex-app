#!/usr/bin/env python3
"""Live clean-vault product smoke for Cortex.

Starts the standalone backend against a temporary vault and exercises the core
first-user loop over HTTP: settings, Obsidian/local-note sync, review approve,
Ask with citations, export, backup, deletion, and disconnected-source recovery.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

TOKEN = "live-product-smoke-token"


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def request(base: str, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        base + path,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {TOKEN}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/markdown, */*",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            raw = resp.read()
            ctype = resp.headers.get("content-type", "")
            if "application/json" in ctype:
                return json.loads(raw.decode("utf-8"))
            return raw.decode("utf-8")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise AssertionError(f"{method} {path} failed: {exc.code} {detail}") from exc


def wait_ready(base: str, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    last: Exception | None = None
    while time.time() < deadline:
        try:
            request(base, "GET", "/v1/settings")
            return
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.25)
    raise AssertionError(f"server did not become ready: {last}")


def assert_true(value: Any, message: str) -> None:
    if not value:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--python", default=sys.executable)
    args = parser.parse_args()
    repo = args.repo.resolve()
    backend = repo / "backend"

    with tempfile.TemporaryDirectory(prefix="cortex-live-product-") as tmpdir:
        root = Path(tmpdir)
        vault = root / "Cortex.vault"
        source_vault = root / "Source Notes"
        source_vault.mkdir(parents=True)
        note = source_vault / "Launch Loop.md"
        note.write_text(
            """---\ntitle: Launch Loop\ntags: [cortex, launch]\n---\n# Launch Loop\n\nDecision: Cortex users can sync local notes, review candidate memories, and ask cited questions from approved memory.\nProcedure: Before launch, run the live product smoke over a clean vault.\nPreference: Cortex answers should cite source notes instead of guessing.\n""",
            encoding="utf-8",
        )
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": str(backend),
                "CORTEX_API_KEY": TOKEN,
                "CORTEX_DB_PATH": str(vault / "index.sqlite"),
                "CORTEX_VAULT_PATH": str(vault),
                "CORTEX_STANDALONE_WORKER_ENABLED": "0",
                "PYTHONNOUSERSITE": "1",
            }
        )
        env.pop("ANTHROPIC_API_KEY", None)
        proc = subprocess.Popen(
            [args.python, "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(backend),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            wait_ready(base)

            settings = request(base, "PUT", "/v1/settings", {"review_new_captures": True, "allow_pending_in_context": False})
            assert_true(settings["review_new_captures"] is True, "review setting did not persist")

            sync = request(base, "POST", "/v1/connectors/obsidian/sync", {"vault_path": str(source_vault), "processing": "sync"})
            assert_true(sync["status"] == "complete", f"sync not complete: {sync}")
            assert_true(sync["saved"] == 1, f"expected one saved note, got {sync}")
            capture_id = sync["records"][0]["capture_id"]
            account_id = sync["source_account"]["id"]

            inbox = request(base, "GET", "/v1/inbox?limit=10")
            assert_true(any(item["id"] == capture_id for item in inbox["results"]), "synced capture missing from inbox")

            pre_ask = request(base, "GET", "/v1/ask?" + urllib.parse.urlencode({"q": "sync local notes review candidate memories", "limit": 5}))
            assert_true(pre_ask["status"] == "no_cited_evidence", f"pending memory leaked into ask: {pre_ask}")

            approved = request(base, "POST", f"/v1/captures/{capture_id}/approve", {})
            assert_true(approved == {"approved": True}, f"approval failed: {approved}")

            ask = request(base, "GET", "/v1/ask?" + urllib.parse.urlencode({"q": "How should Cortex answer launch questions?", "limit": 5}))
            assert_true(ask["citations"], f"approved memory produced no citations: {ask}")
            assert_true(any("source notes" in (c.get("excerpt") or "") for c in ask["citations"]), f"expected source-note citation: {ask}")

            export_json = request(base, "GET", "/v1/export.json")
            assert_true(export_json.get("memories"), "export.json has no memories after approval")
            export_md = request(base, "GET", "/v1/export.md")
            assert_true("Launch Loop" in export_md or "source notes" in export_md, "export.md missing approved content")

            backup = request(base, "POST", "/v1/backups", {})
            assert_true(backup.get("backup_path") and backup.get("size_bytes", 0) > 0, f"backup did not report success: {backup}")

            disconnected = request(base, "POST", f"/v1/source-accounts/{account_id}/disconnect", {})
            assert_true(disconnected["status"] == "disconnected", f"disconnect failed: {disconnected}")
            preserved = request(base, "GET", "/v1/ask?" + urllib.parse.urlencode({"q": "clean vault product smoke", "limit": 5}))
            assert_true(preserved["citations"], "disconnect removed approved memory from Ask")
            resumed = request(base, "POST", f"/v1/source-accounts/{account_id}/resume", {})
            assert_true(resumed["status"] == "available", f"resume failed: {resumed}")

            deleted = request(base, "DELETE", f"/v1/captures/{capture_id}")
            assert_true(deleted == {"deleted": True}, f"capture delete failed: {deleted}")
            post_delete = request(base, "GET", "/v1/ask?" + urllib.parse.urlencode({"q": "source notes instead of guessing", "limit": 5}))
            assert_true(not post_delete["citations"], f"deleted capture still cited: {post_delete}")

            print(json.dumps({"ok": True, "vault": str(vault), "source_vault": str(source_vault), "capture_id": capture_id, "account_id": account_id}, indent=2))
            return 0
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
            if proc.returncode not in {0, -15, None}:
                output = proc.stdout.read() if proc.stdout else ""
                print(output[-2000:], file=sys.stderr)


if __name__ == "__main__":
    raise SystemExit(main())
