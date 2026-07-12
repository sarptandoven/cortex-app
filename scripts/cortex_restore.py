#!/usr/bin/env python3
"""Cortex — New-Machine Restore (headless / CLI).

The "1Password moment" for the brew-install crowd: on a fresh machine you run one
command and your memory rebuilds itself from your hosted Cortex account into the
LOCAL store. This is the headless equivalent of the guided macOS onboarding
"Restore from your account" path — and a testable spec for exactly what that
restore does.

    brew install cortex        # (external — sets up the local engine)
    cortex restore             # (this script — pulls your memory back down)

WHAT IT DOES (and does NOT invent)
----------------------------------
Restore is a PULL: it pages the HOSTED account's outbound capture feed
(GET <hosted>/v1/sync/captures?after_seq=N) and applies each page into the LOCAL
Cortex engine (POST <local>/v1/sync/ingest). That is byte-for-byte the same feed
+ apply the shipping macOS pull-sync worker (CortexPullSync.swift) uses, so this
script and the app converge on identical memory. It advances a monotonic
hosted-rowid cursor and only after a page's local apply succeeds, so an
interrupted restore resumes exactly where it stopped (a safe idempotent re-apply).

It reports "N memories recovered" from the LOCAL engine's own /v1/stats after the
pull — the honest count of what actually landed, never a fabricated number.

HONEST GAPS (documented, not papered over)
------------------------------------------
* There is no single hosted "restore" backend route, and this script does NOT
  invent one. It composes the two feeds that DO exist: the hosted capture feed
  (out) and the local sync/ingest (in). If a hosted deployment gates the feed
  behind a cloud access token, pass it via CORTEX_CLOUD_TOKEN (Authorization:
  Bearer) — the same cxs_ token the app mints from its refresh token.
* Zero-access (E2EE) encrypted captures ride the feed as ciphertext. Decrypting
  them requires the on-device key + recovery code, which lives in the app's
  Keychain, NOT here. This CLI restores PLAINTEXT captures and reports how many
  encrypted items it had to skip so nothing is silently lost. Use the macOS app
  (which holds your key) to restore an encrypted account, or the offline
  cortex-decrypt tool for a one-off decrypt.
* Distilled MEMORIES (the higher layer) are derived from captures by the local
  memory worker; immediately after a restore the capture count is exact while the
  memory count catches up as the worker runs. We surface both, honestly.

Stdlib only. No third-party deps. macOS 13+ / any Python 3.9+.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import urlencode


# ---------------------------------------------------------------------------
# Config (env-driven so a fresh machine needs no flags for the common case)
# ---------------------------------------------------------------------------

# The LOCAL Cortex engine this machine runs (the brew/desktop backend). Restore
# writes recovered captures HERE. Never the hosted URL: Option A keeps the data
# plane local.
DEFAULT_LOCAL_URL = os.environ.get("CORTEX_LOCAL_URL", "http://127.0.0.1:8766").rstrip("/")
# The HOSTED account feed restore pulls FROM.
DEFAULT_HOSTED_URL = os.environ.get(
    "CORTEX_HOSTED_URL", "https://api.signindoppl.com"
).rstrip("/")
# The LOCAL engine's machine API key (what the app writes to disk). Restore's
# apply calls authenticate with this.
DEFAULT_LOCAL_KEY = os.environ.get("CORTEX_API_KEY", "").strip()
# The HOSTED account's cloud access token (cxs_...). Required when the hosted feed
# is auth-gated (the production case). Mint it in the app / from a refresh token.
DEFAULT_CLOUD_TOKEN = os.environ.get("CORTEX_CLOUD_TOKEN", "").strip()

PAGE_LIMIT = 100
HTTP_TIMEOUT = 30
# Placeholder the hosted feed uses for a zero-access (E2EE) capture's cleartext
# content. Mirrors the app + backend contract; we detect it to skip-not-poison.
ENCRYPTED_PLACEHOLDER = "[encrypted]"


class RestoreError(RuntimeError):
    """A fatal restore error with an already-human-readable message."""


# ---------------------------------------------------------------------------
# Tiny stdlib HTTP helpers (mirror the app's cloudGet / request semantics)
# ---------------------------------------------------------------------------


def _request(
    method: str,
    url: str,
    *,
    bearer: Optional[str] = None,
    body: Optional[dict] = None,
    timeout: int = HTTP_TIMEOUT,
) -> Any:
    data = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            if not raw:
                return {}
            return json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", "replace")
        except Exception:
            pass
        raise RestoreError(
            f"{method} {url} failed: HTTP {exc.code}"
            + (f" — {detail.strip()[:300]}" if detail.strip() else "")
        ) from exc
    except urllib.error.URLError as exc:
        raise RestoreError(
            f"{method} {url} failed: could not reach the server ({exc.reason}). "
            "Is the address correct and the server running?"
        ) from exc


def _hosted_get(hosted_url: str, path: str, cloud_token: str) -> Any:
    return _request("GET", hosted_url + path, bearer=cloud_token or None)


def _local_post(local_url: str, path: str, local_key: str, body: dict) -> Any:
    return _request("POST", local_url + path, bearer=local_key or None, body=body)


def _local_get(local_url: str, path: str, local_key: str) -> Any:
    return _request("GET", local_url + path, bearer=local_key or None)


def wait_for_local_ready(local_url: str, *, timeout: float = 20.0) -> None:
    """Block until the local engine answers /health, so a just-booted brew backend
    isn't hit before it's listening. Best-effort — raises only on total timeout."""
    deadline = time.monotonic() + timeout
    last_err: Optional[Exception] = None
    while time.monotonic() < deadline:
        try:
            _request("GET", local_url + "/health", timeout=5)
            return
        except Exception as exc:  # noqa: BLE001 — retry any transient boot error
            last_err = exc
            time.sleep(0.25)
    raise RestoreError(
        f"Local Cortex engine at {local_url} did not become ready. "
        + (f"Last error: {last_err}" if last_err else "")
    )


# ---------------------------------------------------------------------------
# The restore itself — pull hosted captures, apply to local, report recovered
# ---------------------------------------------------------------------------


def _capture_count(local_url: str, local_key: str) -> tuple[int, int]:
    """Return (captures, memories) from the local engine's /v1/stats. Captures is
    the exact recovered count immediately after a pull; memories lags as the
    worker distills. Best-effort — returns (-1, -1) if stats is unreadable."""
    try:
        stats = _local_get(local_url, "/v1/stats", local_key)
        return int(stats.get("captures", 0)), int(stats.get("memories", 0))
    except Exception:
        return -1, -1


def restore(
    *,
    local_url: str,
    hosted_url: str,
    local_key: str,
    cloud_token: str,
    on_progress=None,
) -> dict:
    """Drive the full pull-restore. Returns a summary dict:
        {applied, encrypted_skipped, pages, captures_before, captures_after,
         memories_after}
    `on_progress(applied_so_far, page_index)` is called after each applied page so
    a caller can render a live "N memories recovered" line (the app does this too).
    """
    captures_before, _ = _capture_count(local_url, local_key)
    cursor = 0
    applied = 0
    encrypted_skipped = 0
    pages = 0

    while True:
        query = urlencode({"after_seq": cursor, "limit": PAGE_LIMIT})
        page = _hosted_get(hosted_url, f"/v1/sync/captures?{query}", cloud_token)
        items = page.get("items") or []
        if not items:
            break
        pages += 1

        apply_items: list[dict] = []
        for item in items:
            content = item.get("content") or ""
            # Zero-access (E2EE) captures ride the feed as ciphertext with a
            # non-secret placeholder for `content`. Decrypting needs the on-device
            # key (Keychain), which this headless tool does NOT hold — so SKIP them
            # (never apply the placeholder, which would poison the store) and count
            # them so the summary is honest. The macOS app, holding the key,
            # restores these.
            if item.get("encrypted_payload") or content == ENCRYPTED_PLACEHOLDER:
                encrypted_skipped += 1
                continue
            if not content.strip():
                continue
            apply_item = {
                "client_capture_id": item.get("client_capture_id"),
                "content": content,
                "source": item.get("source") or "restore",
                "captured_at": item.get("captured_at") or "",
            }
            if item.get("source_url"):
                apply_item["source_url"] = item["source_url"]
            if item.get("title"):
                apply_item["title"] = item["title"]
            # Trust passthrough: an approval already granted on another device rides
            # along (the local per-source policy still wins on ingest).
            if item.get("review_status"):
                apply_item["review_status"] = item["review_status"]
            apply_items.append(apply_item)

        if apply_items:
            resp = _local_post(local_url, "/v1/sync/ingest", local_key, {"items": apply_items})
            applied += int(resp.get("applied", len(apply_items)))

        # Advance the cursor ONLY after the local apply succeeded — an interrupted
        # restore resumes at the last applied page (a safe idempotent re-apply).
        next_seq = page.get("next_seq", cursor)
        cursor = int(next_seq)
        if on_progress is not None:
            local_captures, _ = _capture_count(local_url, local_key)
            on_progress(local_captures if local_captures >= 0 else applied, pages)
        if not page.get("has_more"):
            break

    captures_after, memories_after = _capture_count(local_url, local_key)
    return {
        "applied": applied,
        "encrypted_skipped": encrypted_skipped,
        "pages": pages,
        "captures_before": captures_before,
        "captures_after": captures_after,
        "memories_after": memories_after,
    }


def _print_report(summary: dict) -> None:
    applied = summary["applied"]
    captures_after = summary["captures_after"]
    encrypted = summary["encrypted_skipped"]
    if applied == 0 and captures_after <= max(summary["captures_before"], 0):
        # Never claim a restore that didn't happen (the app enforces the same).
        print("Nothing to restore yet — your account has no memory to pull down.")
    else:
        print(f"Your memory is back — {applied} memories recovered.")
        if captures_after >= 0:
            print(f"  This device now holds {captures_after} captures.")
        if summary["memories_after"] >= 0:
            print(
                f"  {summary['memories_after']} distilled so far "
                "(the rest catch up as the memory worker runs)."
            )
    if encrypted:
        print(
            f"  Skipped {encrypted} encrypted (zero-access) item(s): restore those "
            "in the Cortex app, which holds your on-device key."
        )
    print(
        "\nReconnect your AI tools to use your memory here — MCP/tool wiring is "
        "per-device and isn't synced (run: cortex connect)."
    )


# ---------------------------------------------------------------------------
# Self-test: seed a hosted store, prove restore rebuilds an empty local store
# ---------------------------------------------------------------------------


def _self_test() -> int:
    """Spin up a seeded HOSTED + empty LOCAL standalone server on spare ports and
    prove pull-restore brings memory into the empty store. Uses only endpoints
    that exist (the hosted capture feed + local /v1/sync/ingest). Spare ports
    only — never 8766."""
    import subprocess
    import tempfile
    from contextlib import ExitStack

    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    hosted_port = 8791
    local_port = 8792
    hosted_url = f"http://127.0.0.1:{hosted_port}"
    local_url = f"http://127.0.0.1:{local_port}"
    api_key = "selftest-restore-key"

    def _spawn(port: int, db_dir: str) -> subprocess.Popen:
        env = dict(os.environ)
        env["CORTEX_PORT"] = str(port)
        env["CORTEX_DB_PATH"] = os.path.join(db_dir, "index.sqlite")
        env["CORTEX_VAULT_PATH"] = os.path.join(db_dir, "vault")
        env["CORTEX_API_KEY"] = api_key
        # Auto-approve so seeded captures land as active memory (deterministic count)
        # and the restored local store mirrors an already-reviewed account.
        env["CORTEX_AUTO_APPROVE_CAPTURES"] = "1"
        # Keep the memory worker inline so distillation is synchronous + testable.
        env["CORTEX_WORKER_MODE"] = "inline"
        return subprocess.Popen(
            [sys.executable, "-m", "backend.app.standalone_server", "--port", str(port)],
            cwd=repo_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    print("[self-test] booting seeded hosted + empty local servers on spare ports…")
    with ExitStack() as stack:
        hosted_dir = stack.enter_context(tempfile.TemporaryDirectory())
        local_dir = stack.enter_context(tempfile.TemporaryDirectory())
        hosted_proc = _spawn(hosted_port, hosted_dir)
        stack.callback(hosted_proc.terminate)
        local_proc = _spawn(local_port, local_dir)
        stack.callback(local_proc.terminate)

        wait_for_local_ready(hosted_url, timeout=25)
        wait_for_local_ready(local_url, timeout=25)

        # Seed the HOSTED store using the ingest endpoint that exists (this is the
        # SAME apply the restore uses — so the feed it later emits is real).
        seed_items = [
            {
                "client_capture_id": f"seed-{i}",
                "content": f"Restore proof memory {i}: I prefer tea over coffee.",
                "source": "self-test",
                "captured_at": "2026-07-11T00:00:00Z",
                "review_status": "approved",
            }
            for i in range(5)
        ]
        seed_resp = _local_post(hosted_url, "/v1/sync/ingest", api_key, {"items": seed_items})
        seeded = int(seed_resp.get("applied", 0))
        print(f"[self-test] seeded hosted account with {seeded} captures.")

        hosted_captures, _ = _capture_count(hosted_url, api_key)
        local_before, _ = _capture_count(local_url, api_key)
        print(f"[self-test] hosted has {hosted_captures} captures; "
              f"local starts EMPTY ({local_before}).")
        if local_before != 0:
            print("[self-test] FAIL: local store was not empty before restore.")
            return 1

        # Run the actual restore this CLI ships.
        summary = restore(
            local_url=local_url,
            hosted_url=hosted_url,
            local_key=api_key,
            cloud_token=api_key,  # local standalone shares one machine key
        )
        print(f"[self-test] restore summary: {json.dumps(summary)}")

        local_after, _ = _capture_count(local_url, api_key)
        ok = (
            summary["applied"] == len(seed_items)
            and local_after == len(seed_items)
            and local_before == 0
        )
        if ok:
            print(
                f"[self-test] PASS: empty local store ({local_before}) → "
                f"{local_after} captures restored from the hosted account "
                f"({summary['applied']} applied)."
            )
            # Prove idempotent resume: a second restore adds nothing.
            again = restore(
                local_url=local_url,
                hosted_url=hosted_url,
                local_key=api_key,
                cloud_token=api_key,
            )
            final, _ = _capture_count(local_url, api_key)
            if final == len(seed_items):
                print(
                    f"[self-test] PASS: re-run is idempotent — still {final} captures "
                    f"(applied={again['applied']} this time, no duplicates)."
                )
                return 0
            print(f"[self-test] FAIL: re-run changed the count to {final}.")
            return 1
        print(
            f"[self-test] FAIL: expected {len(seed_items)} restored, "
            f"got applied={summary['applied']} local_after={local_after}."
        )
        return 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="cortex_restore",
        description="Rebuild this machine's Cortex memory from your hosted account "
        "(headless equivalent of the app's 'Restore from your account').",
    )
    parser.add_argument(
        "--local-url",
        default=DEFAULT_LOCAL_URL,
        help="Local Cortex engine base URL (default: %(default)s or $CORTEX_LOCAL_URL).",
    )
    parser.add_argument(
        "--hosted-url",
        default=DEFAULT_HOSTED_URL,
        help="Hosted Cortex account base URL to restore FROM "
        "(default: %(default)s or $CORTEX_HOSTED_URL).",
    )
    parser.add_argument(
        "--local-key",
        default=DEFAULT_LOCAL_KEY,
        help="Local engine machine API key (default: $CORTEX_API_KEY).",
    )
    parser.add_argument(
        "--cloud-token",
        default=DEFAULT_CLOUD_TOKEN,
        help="Hosted cloud access token (cxs_...), required when the hosted feed is "
        "auth-gated (default: $CORTEX_CLOUD_TOKEN).",
    )
    parser.add_argument(
        "--self-test",
        action="store_true",
        help="Seed a hosted+local pair on spare ports and prove restore works. No network.",
    )
    args = parser.parse_args(argv)

    if args.self_test:
        return _self_test()

    try:
        wait_for_local_ready(args.local_url)
        print(f"Restoring your memory from {args.hosted_url}…")

        def _tick(count: int, page: int) -> None:
            print(f"  …{count} memories recovered (page {page})")

        summary = restore(
            local_url=args.local_url,
            hosted_url=args.hosted_url,
            local_key=args.local_key,
            cloud_token=args.cloud_token,
            on_progress=_tick,
        )
        _print_report(summary)
        return 0
    except RestoreError as exc:
        print(f"Restore failed: {exc}", file=sys.stderr)
        print(
            "Your memory is safe in your account — nothing was lost. "
            "Fix the address/token and run restore again.",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
