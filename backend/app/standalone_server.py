from __future__ import annotations

import argparse
import base64
import binascii
import html
import hmac
import secrets
import json
import os
import threading
import time
from datetime import timedelta
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import APP_BRAND, load_settings
from .context_file import (
    DEFAULT_STYLE as CONTEXT_FILE_DEFAULT_STYLE,
    SUPPORTED_STYLES as CONTEXT_FILE_SUPPORTED_STYLES,
    render_context_block,
    sync_context_file,
    validate_context_file_path,
)
from .extractor import extract_context
from .hosted_readiness import hosted_readiness_contract
from .mcp_tools import (
    CORE_TOOL_NAMES,
    TOOLS,
    _assemble_context_ext_kwargs,
    call_tool,
    export_tool_schema,
    get_prompt,
    list_prompts,
    list_resource_templates,
    list_resources,
    read_resource,
    tool_call_result,
    tools_for_scopes,
)
from .sharding import StoreRegistry
from .storage import BACKEND_VERSION


settings = load_settings()
store = StoreRegistry.from_settings(settings)
# Startup migrations/backfills are best-effort: a non-fatal error on a damaged vault (a DB hiccup
# in _backfill_memory_markdown, a stray file) must NOT prevent the server from binding its port and
# serving — otherwise the app is stuck forever on "Cortex is starting" with no way to self-heal.
# The readiness gate (/ready) still catches genuine corruption; these just must not crash boot.
try:
    store.ensure_vault_backfilled(settings.default_user_id)
except Exception as exc:  # pragma: no cover - defensive startup guard
    print(f"cortex: vault backfill skipped ({type(exc).__name__}: {exc})", flush=True)
if settings.mcp_api_key:
    try:
        store.ensure_mcp_token(
            settings.default_user_id,
            settings.mcp_api_key,
            label="Local MCP integrations",
            scopes=settings.mcp_api_key_scopes or None,
            token_id="tok_local_mcp",
        )
    except Exception as exc:  # pragma: no cover - defensive startup guard
        print(f"cortex: local MCP token setup skipped ({type(exc).__name__}: {exc})", flush=True)


def _cors_origins() -> set[str]:
    configured = [item.strip().rstrip("/") for item in os.environ.get("CORTEX_CORS_ORIGINS", "").split(",") if item.strip()]
    if configured:
        return set(configured)
    public_base = settings.public_base_url.rstrip("/")
    return {
        origin for origin in {
            public_base,
            "http://127.0.0.1:8766",
            "http://localhost:8766",
        } if origin
    }


ALLOWED_CORS_ORIGINS = _cors_origins()

# Browser-extension origins are dynamic per install (chrome-extension://<id>, moz-extension://<id>,
# safari-web-extension://<id>). The Cortex extension the user installed presents a paired Bearer
# token, so the token — not CORS — is the auth boundary; echoing the extension's origin is safe.
_EXTENSION_ORIGIN_SCHEMES = ("chrome-extension://", "moz-extension://", "safari-web-extension://")


def _cors_origin_allowed(origin: str) -> bool:
    if not origin:
        return False
    if origin in ALLOWED_CORS_ORIGINS:
        return True
    if os.environ.get("CORTEX_ALLOW_EXTENSION_CORS", "1").strip().lower() in {"1", "true", "on", "yes"}:
        return origin.startswith(_EXTENSION_ORIGIN_SCHEMES)
    return False


def _env_int(name: str, default: int, low: int, high: int) -> int:
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(high, max(low, value))


def _env_float(name: str, default: float, low: float, high: float) -> float:
    try:
        value = float(os.environ.get(name, str(default)))
    except ValueError:
        value = default
    return min(high, max(low, value))


def _standalone_worker_enabled() -> bool:
    configured = os.environ.get("CORTEX_STANDALONE_WORKER_ENABLED")
    if configured is not None:
        return _bool_value(configured, default=True)
    return settings.shard_mode == "local" and settings.worker_mode != "external"


def _run_standalone_worker_tick(limit: int | None = None, worker_id: str = "standalone-local-worker") -> dict[str, Any]:
    resolved_limit = limit if limit is not None else _env_int("CORTEX_STANDALONE_WORKER_LIMIT", 25, 1, 100)
    return store.run_due_jobs(
        settings.default_user_id,
        limit=resolved_limit,
        worker_id=worker_id,
        schedule_source_syncs=False,
    )


def _start_standalone_worker() -> threading.Thread | None:
    if not _standalone_worker_enabled():
        return None

    interval_seconds = _env_int("CORTEX_STANDALONE_WORKER_INTERVAL_SECONDS", 15, 2, 3600)
    limit = _env_int("CORTEX_STANDALONE_WORKER_LIMIT", 25, 1, 100)

    # Idle backoff cap: after a run of empty ticks the sleep grows geometrically from
    # interval_seconds toward this ceiling so an idle app settles to a rare poll instead
    # of a fixed write cycle. The instant any tick sees work (processed or pending) we snap
    # back to interval_seconds, so new jobs are never left waiting longer than one base tick.
    idle_cap_seconds = float(_env_int("CORTEX_STANDALONE_WORKER_IDLE_CAP_SECONDS", 300, interval_seconds, 3600))

    def worker_loop() -> None:
        idle_count = 0
        while True:
            try:
                result = _run_standalone_worker_tick(limit=limit)
                if result.get("processed") or result.get("pending"):
                    # Work happened or is queued — stay responsive at the base cadence.
                    idle_count = 0
                elif interval_seconds * (2 ** idle_count) < idle_cap_seconds:
                    # Only keep growing the exponent while it still matters; once the sleep
                    # has reached the cap, stop counting so 2**idle_count can't balloon.
                    idle_count += 1
            except Exception as exc:  # pragma: no cover - defensive server loop
                print(f"{APP_BRAND} standalone worker error: {exc}", flush=True)
                # A failing tick is not "idle" — keep probing at the base cadence.
                idle_count = 0
            sleep_seconds = min(interval_seconds * (2 ** idle_count), idle_cap_seconds)
            time.sleep(sleep_seconds)

    thread = threading.Thread(target=worker_loop, name="cortex-standalone-worker", daemon=True)
    thread.start()
    return thread


def _required_api_scope(method: str, path: str) -> str:
    normalized_method = method.upper()
    normalized_path = path.rstrip("/") or "/"
    # Raw bulk dumps of the corpus stay export-gated. The DISTILLED profile / person map /
    # adaptation / context pack are reads — the holistic picture Cortex exists to hand an agent.
    if normalized_path in {"/v1/export.json", "/v1/export.md", "/v1/support/bundle"}:
        return "export"
    # Phase D: the portable bundle carries the full corpus out of Cortex custody — export-scoped.
    # The integrity digest / manifest / verify endpoints expose only hashes + counts (no content),
    # so they are reads (verify-* are POSTs only because they carry input in the body).
    if normalized_path == "/v1/export/bundle":
        return "export"
    if normalized_path in {
        "/v1/beliefs/proof",
        "/v1/beliefs/proof/verify",
        "/v1/integrity/digest",
        "/v1/integrity/verify",
        "/v1/export/manifest",
        "/v1/export/verify",
    }:
        return "read"
    if normalized_method == "POST" and (
        normalized_path == "/v1/shared-memory/principals"
        or (
            normalized_path.startswith("/v1/shared-memory/principals/")
            and normalized_path.endswith("/revoke")
        )
    ):
        return "maintenance"
    # Obsidian write-back persists distilled memory into user-owned vault files (egress out of
    # Cortex custody) — export-scoped like the bulk exports, parity with the MCP tool.
    if normalized_path == "/v1/connectors/obsidian/write-back":
        return "export"
    # CLAUDE.md compiler: previewing the rendered block is a read (nothing leaves Cortex custody
    # until it's written); syncing WRITES distilled memory into a user-owned file outside the vault
    # — export-scoped, same reasoning as Obsidian write-back / delivery send.
    if normalized_path == "/v1/context-file/preview":
        return "read"
    if normalized_path == "/v1/context-file/sync":
        return "export"
    # Delivery: previewing the cited brief is a read; SENDING it out of Cortex is egress of
    # personal memory, gated like a bulk export (export scope + allow_agent_exports trust toggle).
    if normalized_path == "/v1/delivery/preview":
        return "read"
    if normalized_path == "/v1/delivery/send":
        return "export"
    if normalized_path in {"/v1/context", "/v1/context-pack", "/v1/personal-profile", "/v1/profile", "/v1/person-map", "/v1/agent-adaptation", "/v1/working-canvas"}:
        return "read"
    if normalized_path.startswith("/v1/working-canvas/") and normalized_method == "GET":
        return "read"
    if normalized_path == "/v1/settings" and normalized_method in {"PUT", "PATCH"}:
        return "maintenance"
    if normalized_path == "/v1/memory/consolidation" and normalized_method == "GET":
        return "read"
    if normalized_path == "/v1/memory/consolidate" and normalized_method == "POST":
        return "maintenance"
    if normalized_path in {"/v1/diagnostics", "/v1/reliability/report", "/v1/jobs/health"}:
        return "maintenance"
    # Recompute-verify is a maintenance diagnostic (same scope as the MCP tool): it re-runs
    # the context engine and writes an audit event, beyond what plain read tokens are for.
    if normalized_path.startswith("/v1/context/packs/") and normalized_path.endswith("/verify") and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/maintenance/") or normalized_path in {"/v1/jobs/run", "/v1/maintenance/jobs/run", "/v1/sources/sync-due"}:
        return "maintenance"
    if normalized_path in {"/v1/integrations/api-token", "/v1/integrations/mcp-token", "/v1/integrations/tokens", "/v1/pair"}:
        return "maintenance"
    if normalized_path.startswith("/v1/integrations/tokens/"):
        return "maintenance"
    if normalized_path == "/v1/source-accounts" and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/connectors/google/oauth/") and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/connectors/oauth/") and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/source-accounts/") and normalized_method == "DELETE":
        return "maintenance"
    if normalized_path.startswith("/v1/source-accounts/") and normalized_path.endswith("/disconnect") and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/source-accounts/") and normalized_path.endswith("/resume") and normalized_method == "POST":
        return "maintenance"
    if normalized_path == "/v1/sync-cursors" and normalized_method == "POST":
        return "maintenance"
    if normalized_path == "/v1/sync/devices" and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/sync/devices/") and normalized_path.endswith("/receipts") and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/sync/devices/") and normalized_method == "DELETE":
        return "maintenance"
    if normalized_path == "/v1/backups/restore-latest":
        return "destructive"
    if normalized_method == "DELETE":
        return "destructive"
    if normalized_path == "/v1/backups" and normalized_method == "POST":
        return "maintenance"
    if normalized_method in {"POST", "PUT", "PATCH"}:
        return "write"
    return "read"


def _api_token_has_scope(scoped: dict, required_scope: str) -> bool:
    return required_scope in set(scoped.get("scopes") or [])


def _require_api_token_trust(user_id: str, required_scope: str) -> None:
    store.require_agent_access(user_id, required_scope)


def _hosted_readiness_contract() -> dict:
    runtime: dict[str, Any] = {}
    runtime_storage_status = getattr(store, "runtime_storage_status", None)
    if callable(runtime_storage_status):
        runtime["storage"] = runtime_storage_status()
    control_plane_status = getattr(store, "control_plane_status", None)
    if settings.shard_mode != "local" and callable(control_plane_status):
        runtime["control_plane"] = control_plane_status()
        hosted_job_health = getattr(store, "hosted_job_health", None)
        if callable(hosted_job_health):
            runtime["worker_queue"] = hosted_job_health()
    return hosted_readiness_contract(settings, runtime=runtime)


ROOT_HTML = f"""
<!doctype html>
<html>
  <head>
    <title>{APP_BRAND} Local API</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 40px; line-height: 1.45; max-width: 760px; }}
      code {{ background: #f3f3f3; padding: 2px 5px; border-radius: 4px; }}
      .status {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #e8f7ed; color: #116329; font-weight: 600; }}
    </style>
  </head>
  <body>
    <p class="status">{APP_BRAND} backend is running</p>
    <h1>{APP_BRAND} Local API</h1>
    <p>This local service stores and retrieves shared AI memory for the macOS app and MCP-compatible tools.</p>
    <p>Useful checks: <code>/health</code>, <code>/ready</code>, <code>/.well-known/cortex.json</code>.</p>
    <p>Authenticated API endpoints require the {APP_BRAND} token configured in the app.</p>
  </body>
</html>
"""


GOOGLE_OAUTH_PENDING_TTL = timedelta(minutes=10)


def _remember_google_oauth_pending(user_id: str, body: dict[str, Any], started: dict[str, Any]) -> None:
    state = str(started.get("state") or "").strip()
    if not state:
        return
    store.remember_oauth_pending(
        state=state,
        user_id=user_id,
        flow="google",
        ttl_seconds=int(GOOGLE_OAUTH_PENDING_TTL.total_seconds()),
        payload={
            "source": started.get("source") or body.get("source"),
            "redirect_uri": started.get("redirect_uri") or body.get("redirect_uri"),
            "client_id": body.get("client_id"),
            "client_secret": body.get("client_secret"),
            "token_endpoint": body.get("token_endpoint"),
            "code_verifier": body.get("code_verifier"),
            "source_account_id": body.get("source_account_id"),
            "account_label": body.get("account_label"),
            "account_identifier": body.get("account_identifier"),
            "query": body.get("query"),
            "label_ids": body.get("label_ids") if isinstance(body.get("label_ids"), list) else [],
            "mime_types": body.get("mime_types") if isinstance(body.get("mime_types"), list) else [],
            "include_body": _bool_value(body.get("include_body"), default=True),
            "include_content": _bool_value(body.get("include_content"), default=True),
        },
    )


def _pop_google_oauth_pending(state: str) -> dict[str, Any] | None:
    return store.pop_oauth_pending(state or "", flow="google")


def _remember_managed_oauth_pending(user_id: str, body: dict[str, Any], started: dict[str, Any]) -> None:
    state = str(started.get("state") or "").strip()
    if not state:
        return
    store.remember_oauth_pending(
        state=state,
        user_id=user_id,
        flow="managed",
        ttl_seconds=int(GOOGLE_OAUTH_PENDING_TTL.total_seconds()),
        payload={
            "source": started.get("source") or body.get("source"),
            "redirect_uri": started.get("redirect_uri") or body.get("redirect_uri"),
            "client_id": body.get("client_id"),
            "client_secret": body.get("client_secret"),
            "token_endpoint": body.get("token_endpoint"),
            # PKCE verifier for the public-client (Microsoft/Outlook) flow. Prefer the value the
            # store minted in start (server-generated) and fall back to one the app supplied; it
            # must survive start→complete keyed by state so the code exchange needs no secret.
            "code_verifier": started.get("code_verifier") or body.get("code_verifier"),
            "source_account_id": body.get("source_account_id"),
            "account_label": body.get("account_label"),
            "account_identifier": body.get("account_identifier"),
            "include_content": _bool_value(body.get("include_content"), default=True),
            "api_base_url": body.get("api_base_url"),
            "notion_version": body.get("notion_version"),
        },
    )


def _pop_managed_oauth_pending(state: str) -> dict[str, Any] | None:
    return store.pop_oauth_pending(state or "", flow="managed")


def _google_oauth_callback_page(title: str, detail: str, *, success: bool) -> str:
    icon = "Connected" if success else "Needs attention"
    color = "#136f45" if success else "#9a3412"
    safe_title = html.escape(title)
    safe_detail = html.escape(detail)
    safe_icon = html.escape(icon)
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light; }}
    body {{
      margin: 0;
      min-height: 100vh;
      display: grid;
      place-items: center;
      background: #faf9f6;
      color: #1f2933;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    }}
    main {{
      width: min(560px, calc(100vw - 48px));
      padding: 32px;
      border: 1px solid rgba(30, 41, 59, 0.12);
      border-radius: 16px;
      background: #ffffff;
      box-shadow: 0 20px 70px rgba(15, 23, 42, 0.08);
    }}
    .status {{ color: {color}; font-size: 13px; font-weight: 700; margin-bottom: 12px; text-transform: uppercase; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    p {{ margin: 0; color: #53606f; font-size: 15px; line-height: 1.5; }}
  </style>
</head>
<body>
  <main>
    <div class="status">{safe_icon}</div>
    <h1>{safe_title}</h1>
    <p>{safe_detail}</p>
  </main>
</body>
</html>"""


def _int_param(params: dict[str, list[str]], name: str, default: int, low: int, high: int) -> int:
    try:
        value = int((params.get(name) or [default])[0])
    except (TypeError, ValueError):
        value = default
    return min(high, max(low, value))


def _bool_value(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _body_bool(body: dict[str, Any], name: str, default: bool) -> bool:
    value = body.get(name, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
    raise ValueError(f"{name} must be a boolean")


def _body_int(body: dict[str, Any], name: str, default: int, low: int, high: int) -> int:
    value = body.get(name, default)
    if isinstance(value, bool):
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer between {low} and {high}") from exc
    if parsed < low or parsed > high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")
    return parsed


# Cap request bodies so a single oversized/hostile upload can't buffer unbounded memory (the
# shipping server hand-rolls body reads on a thread-per-connection server, with no framework in
# front of it). 16 MiB comfortably exceeds any legitimate capture/import page.
MAX_REQUEST_BODY_BYTES = 16 * 1024 * 1024

# MCP protocol revisions /mcp can serve, newest first. The JSON-RPC shapes Cortex uses
# (initialize, tools/list, tools/call, ping) are identical across these revisions, so
# initialize echoes whichever revision the client requested and offers the newest otherwise.
MCP_PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class _RequestTooLarge(Exception):
    """Raised when a request body exceeds MAX_REQUEST_BODY_BYTES (-> 413)."""


class _BadRequestBody(Exception):
    """Raised when a request body is present but not valid JSON (-> 422), matching the framework
    server which rejects malformed bodies with 422 rather than surfacing a 500."""


class _RequestGuards:
    """Overload protection for the request-serving surface (/mcp and /v1/*). A runaway MCP
    client — an agent stuck in a loop — could otherwise wedge the backend: ThreadingHTTPServer
    spawns a thread per connection with no cap and no throttle. Two lightweight layers:

    1. A bounded concurrency gate (CORTEX_MAX_CONCURRENT_REQUESTS, default 8) acquired
       non-blockingly; when saturated the request is rejected with 503 + Retry-After.
    2. A per-bearer-token token bucket (CORTEX_RATE_LIMIT_RPS / CORTEX_RATE_LIMIT_BURST,
       defaults 20 req/s sustained with a burst of 60; set either to 0 to disable). This is
       wedge protection, not quota policy — the admin app token gets the same generous limit.

    Health endpoints (/health, /ready) never pass through either layer, so the app can always
    supervise a saturated backend.
    """

    def __init__(self) -> None:
        self.max_concurrent = _env_int("CORTEX_MAX_CONCURRENT_REQUESTS", 8, 1, 256)
        self.rate_limit_rps = _env_float("CORTEX_RATE_LIMIT_RPS", 20.0, 0.0, 10_000.0)
        self.rate_limit_burst = _env_float("CORTEX_RATE_LIMIT_BURST", 60.0, 0.0, 100_000.0)
        self._gate = threading.BoundedSemaphore(self.max_concurrent)
        self._lock = threading.Lock()
        # identity -> [tokens, last_refill] (time.monotonic seconds).
        self._buckets: dict[str, list[float]] = {}

    def acquire_slot(self) -> bool:
        return self._gate.acquire(blocking=False)

    def release_slot(self) -> None:
        self._gate.release()

    def allow_request(self, identity: str) -> bool:
        if self.rate_limit_rps <= 0 or self.rate_limit_burst <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            bucket = self._buckets.get(identity)
            if bucket is None:
                # Callers cycling random tokens must not grow this dict unbounded; resetting
                # all buckets is harmless at these rates.
                if len(self._buckets) >= 4096:
                    self._buckets.clear()
                self._buckets[identity] = [self.rate_limit_burst - 1.0, now]
                return True
            tokens = min(self.rate_limit_burst, bucket[0] + (now - bucket[1]) * self.rate_limit_rps)
            bucket[1] = now
            if tokens < 1.0:
                bucket[0] = tokens
                return False
            bucket[0] = tokens - 1.0
            return True


REQUEST_GUARDS = _RequestGuards()


def _source_import_request(body: dict, *, analyze: bool = False) -> dict:
    raw_paths = body.get("paths")
    if not isinstance(raw_paths, list):
        raise ValueError("paths must be a list")
    paths = [str(path).strip() for path in raw_paths if str(path).strip()]
    if not paths:
        raise ValueError("paths must include at least one local file or folder")
    if len(paths) > 200:
        raise ValueError("paths cannot include more than 200 entries")
    source_hint = str(body.get("source_hint") or "")[:80]
    try:
        requested_max = int(body.get("max_records") or (500 if analyze else 1000))
    except (TypeError, ValueError):
        raise ValueError("max_records must be an integer")
    high = 500 if analyze else 5000
    max_records = min(max(requested_max, 1), high)
    processing = str(body.get("processing") or "async")
    if processing not in {"sync", "async"}:
        raise ValueError("processing must be sync or async")
    try:
        offset = max(0, int(body.get("offset") or 0))
    except (TypeError, ValueError):
        raise ValueError("offset must be a non-negative integer")
    # A user-initiated import is trusted (its content is usable immediately, not held per-record
    # in Review) unless the caller explicitly opts back into review.
    auto_approve = bool(body.get("auto_approve", True))
    return {"paths": paths, "source_hint": source_hint, "max_records": max_records, "processing": processing, "offset": offset, "auto_approve": auto_approve}


def _capture_page(message: str = "", status: str = "ready", token: str = "", title: str = "", url: str = "", content: str = "") -> str:
    escaped_message = html.escape(message)
    escaped_status = html.escape(status)
    escaped_title = html.escape(title)
    escaped_url = html.escape(url)
    escaped_content = html.escape(content)
    status_class = "ok" if status == "saved" else "err" if status == "error" else "review" if status == "review" else ""
    return f"""
<!doctype html>
<html>
  <head>
    <title>{APP_BRAND} Capture</title>
    <style>
      body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; line-height: 1.45; max-width: 760px; color: #1f2328; }}
      label {{ display: block; font-weight: 600; margin-top: 14px; }}
      input, textarea {{ width: 100%; box-sizing: border-box; font: inherit; padding: 9px; border: 1px solid #d0d7de; border-radius: 8px; }}
      textarea {{ min-height: 220px; }}
      button {{ margin-top: 16px; padding: 9px 14px; border-radius: 8px; border: 0; background: #0969da; color: white; font-weight: 700; }}
      .status {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #ddf4ff; color: #0969da; font-weight: 700; }}
      .ok {{ background: #dafbe1; color: #116329; }}
      .review {{ background: #fff4d6; color: #9a6700; }}
      .err {{ background: #ffebe9; color: #cf222e; }}
      .hint {{ color: #57606a; }}
    </style>
  </head>
  <body>
    <p class="status {status_class}">{escaped_status}</p>
    <h1>Save to {APP_BRAND}</h1>
    <p class="hint">Capture selected text, page context, links, or notes into your local {APP_BRAND} memory.</p>
    {f"<p><strong>{escaped_message}</strong></p>" if escaped_message else ""}
    <form method="post" action="/capture">
      <label>Token</label>
      <input name="token" value="" autocomplete="off" placeholder="Paste {APP_BRAND} token" />
      <label>Title</label>
      <input name="title" value="{escaped_title}" />
      <label>Source URL</label>
      <input name="url" value="{escaped_url}" />
      <label>Content</label>
      <textarea name="content">{escaped_content}</textarea>
      <input type="hidden" name="source" value="browser-capture" />
      <button type="submit">Save to {APP_BRAND}</button>
    </form>
  </body>
</html>
"""


def _capture_confirmation(saved: dict) -> tuple[str, str]:
    """Build a truthful (message, status) pair for a saved capture.

    A pending capture is NOT retrievable yet (search/Ask exclude it until it's approved in
    Review), so we must not render the green "saved" pill over content the app won't surface.
    Distinguish "saved and searchable now" from "saved, waiting in Review"."""
    count = len(saved.get("memories", []))
    if saved.get("review_status") == "pending":
        return (
            f"Saved {count} memories. Approve them in Review before they can answer questions in Ask.",
            "review",
        )
    return (f"Saved {count} memories.", "saved")


class CortexRequestHandler(BaseHTTPRequestHandler):
    server_version = f"{APP_BRAND}Standalone/0.1"
    # Socket read timeout (seconds). Without it a client that declares a Content-Length but sends
    # fewer bytes would block the handler thread forever in rfile.read() while holding one of the
    # (default 8) concurrency-gate slots — 8 such stalls wedge the whole server. 30s sits safely
    # above the /v1/activity long-poll ceiling (8s) so real long-polls are unaffected.
    timeout = 30

    def do_OPTIONS(self) -> None:
        self._send_bytes(b"", status=HTTPStatus.NO_CONTENT)

    def do_GET(self) -> None:
        self._handle("GET")

    def do_POST(self) -> None:
        self._handle("POST")

    def do_PUT(self) -> None:
        self._handle("PUT")

    def do_DELETE(self) -> None:
        self._handle("DELETE")

    def log_message(self, format: str, *args) -> None:
        # Never log the raw query string: capture tokens (GET /capture?token=...)
        # and OAuth callback codes (GET /v1/connectors/.../oauth/callback?code=...&state=...)
        # would otherwise be written to stdout/log files. Log only method + path + status.
        message = format % args
        try:
            requestline = getattr(self, "requestline", "") or ""
            if requestline and requestline in message:
                parts = requestline.split(" ")
                if len(parts) >= 2:
                    parts[1] = urlparse(parts[1]).path
                message = message.replace(requestline, " ".join(parts))
        except Exception:
            pass
        print(f"{self.address_string()} - {message}", flush=True)

    @staticmethod
    def _safe_error_message(exc: Exception) -> str:
        # PermissionError/ValueError messages are intentionally user-facing and safe.
        # For any other exception (e.g. sqlite3.OperationalError, OSError/FileNotFoundError)
        # the message may carry an absolute vault/db path or secret; run it through the
        # store's agent-facing redaction before exposing it to callers/agents.
        if isinstance(exc, (PermissionError, ValueError)):
            return str(exc)
        message = str(exc)
        try:
            redactor = getattr(store, "default_store", store)
            return redactor._redact_text(message)
        except Exception:
            return "Internal error"

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
        if path == "/v1/activity":
            # The live-activity long-poll parks a worker thread for up to a few seconds waiting
            # for the next event, so it must NOT hold one of the (only 8) concurrency slots — a
            # burst import could otherwise starve real requests. It is a cheap condition-variable
            # wait, safe to run outside the overload gate, but still per-token rate-limited.
            if not REQUEST_GUARDS.allow_request(self._rate_limit_identity()):
                self.close_connection = True
                self._send_json(
                    {"detail": "Too many requests; slow down and retry."},
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                    retry_after=1,
                )
                return
            self._dispatch(method, path, params)
            return
        if path == "/mcp" or path.startswith("/v1/"):
            # A rejected request's body is never read, so close the connection to avoid a
            # keep-alive protocol desync from leftover unread bytes (as for _RequestTooLarge).
            # For /mcp these are plain HTTP JSON errors, not JSON-RPC — clients treat any
            # non-200 as retryable.
            if not REQUEST_GUARDS.allow_request(self._rate_limit_identity()):
                self.close_connection = True
                self._send_json(
                    {"detail": "Too many requests; slow down and retry."},
                    status=HTTPStatus.TOO_MANY_REQUESTS,
                    retry_after=1,
                )
                return
            if not REQUEST_GUARDS.acquire_slot():
                self.close_connection = True
                self._send_json(
                    {"detail": f"{APP_BRAND} is busy handling other requests; retry shortly."},
                    status=HTTPStatus.SERVICE_UNAVAILABLE,
                    retry_after=1,
                )
                return
            try:
                self._dispatch(method, path, params)
            finally:
                REQUEST_GUARDS.release_slot()
            return
        self._dispatch(method, path, params)

    def _rate_limit_identity(self) -> str:
        # Rate limiting keys on the caller's bearer token and runs before auth, so floods of
        # invalid credentials are throttled too; anonymous callers share one bucket.
        authorization = self.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return authorization.split(" ", 1)[1].strip()
        return ""

    def _dispatch(self, method: str, path: str, params: dict[str, list[str]]) -> None:
        try:
            if method == "GET" and path == "/":
                self._send_text(ROOT_HTML, media_type="text/html")
                return
            if method == "GET" and path == "/health":
                payload = store.health_payload(mode="standalone", auth=bool(settings.api_key))
                payload["hosted_readiness"] = _hosted_readiness_contract()
                self._send_json(payload)
                return
            if method == "GET" and path == "/ready":
                hosted_readiness = _hosted_readiness_contract()
                if hosted_readiness["status"] != "ok":
                    self._send_json(
                        {
                            "detail": {
                                "status": "needs_configuration",
                                "hosted_readiness": hosted_readiness,
                            }
                        },
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                diagnostics = store.diagnostics(settings.default_user_id)
                if diagnostics["status"] != "ok":
                    self._send_json({"detail": diagnostics}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({"status": "ok", "diagnostics": diagnostics, "hosted_readiness": hosted_readiness})
                return
            if method == "GET" and path == "/.well-known/cortex.json":
                self._send_json({
                    "name": APP_BRAND,
                    "description": "Shared memory for AI assistants.",
                    "api": {"base_url": settings.public_base_url, "version": BACKEND_VERSION},
                    "health": store.health_payload(mode="standalone", auth=bool(settings.api_key)),
                    "mcp": {
                        "endpoint": "/mcp",
                        "tools": [tool["name"] for tool in TOOLS],
                        "core_tools": sorted(CORE_TOOL_NAMES),
                    },
                    "context_engine": {"endpoint": "/v1/context"},
                })
                return
            if method == "GET" and path == "/capture":
                payload = (params.get("content") or params.get("text") or [""])[0]
                token = (params.get("token") or [""])[0]
                title = (params.get("title") or [""])[0]
                source_url = (params.get("url") or [""])[0]
                source = (params.get("source") or ["browser-capture"])[0]
                if payload.strip():
                    try:
                        user_id = self._auth_token(token)
                    except PermissionError as exc:
                        self._send_text(_capture_page(str(exc), "error", token, title, source_url, payload), status=HTTPStatus.FORBIDDEN, media_type="text/html")
                        return
                    if not user_id:
                        self._send_text(_capture_page(f"Missing or invalid {APP_BRAND} capture token", "error", token, title, source_url, payload), status=HTTPStatus.UNAUTHORIZED, media_type="text/html")
                        return
                    try:
                        saved = self._save_capture(user_id, payload, source, title, source_url)
                    except ValueError as exc:
                        self._send_text(_capture_page(str(exc), "error", token, title, source_url, payload), status=HTTPStatus.BAD_REQUEST, media_type="text/html")
                        return
                    confirmation, confirmation_status = _capture_confirmation(saved)
                    self._send_text(_capture_page(confirmation, confirmation_status, token, title, source_url), media_type="text/html")
                    return
                self._send_text(_capture_page(token=token, title=title, url=source_url, content=payload), media_type="text/html")
                return
            if method == "POST" and path == "/capture":
                form = self._form_body()
                token = (form.get("token") or [""])[0]
                content = (form.get("content") or form.get("text") or [""])[0]
                title = (form.get("title") or [""])[0]
                source_url = (form.get("url") or [""])[0]
                source = (form.get("source") or ["browser-capture"])[0]
                try:
                    user_id = self._auth_token(token)
                except PermissionError as exc:
                    self._send_text(_capture_page(str(exc), "error", token, title, source_url, content), status=HTTPStatus.FORBIDDEN, media_type="text/html")
                    return
                if not user_id:
                    self._send_text(_capture_page(f"Missing or invalid {APP_BRAND} capture token", "error", token, title, source_url, content), status=HTTPStatus.UNAUTHORIZED, media_type="text/html")
                    return
                try:
                    saved = self._save_capture(user_id, content, source, title, source_url)
                except ValueError as exc:
                    self._send_text(_capture_page(str(exc), "error", token, title, source_url, content), status=HTTPStatus.BAD_REQUEST, media_type="text/html")
                    return
                confirmation, confirmation_status = _capture_confirmation(saved)
                self._send_text(_capture_page(confirmation, confirmation_status, token, title, source_url), media_type="text/html")
                return
            if method == "GET" and path == "/v1/connectors/google/oauth/callback":
                error_value = (params.get("error") or [""])[0]
                if error_value:
                    detail = (params.get("error_description") or [f"Google did not authorize {APP_BRAND}."])[0]
                    self._send_text(
                        _google_oauth_callback_page("Google sign-in was not completed", detail, success=False),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                    return
                state_value = (params.get("state") or [""])[0]
                code_value = (params.get("code") or [""])[0]
                pending = _pop_google_oauth_pending(state_value)
                if not pending:
                    self._send_text(
                        _google_oauth_callback_page(
                            "Google sign-in expired",
                            f"Return to {APP_BRAND} and start the connection again. No account was connected.",
                            success=False,
                        ),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                    return
                if not code_value:
                    self._send_text(
                        _google_oauth_callback_page(
                            "Google sign-in did not return a code",
                            f"Return to {APP_BRAND} and start the connection again. No account was connected.",
                            success=False,
                        ),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                    return
                try:
                    callback_user = str(pending.get("user_id") or settings.default_user_id)
                    result = store.complete_google_oauth(
                        callback_user,
                        str(pending.get("source") or ""),
                        code=code_value,
                        redirect_uri=pending.get("redirect_uri"),
                        state=state_value,
                        expected_state=state_value,
                        client_id=pending.get("client_id"),
                        client_secret=pending.get("client_secret"),
                        token_endpoint=pending.get("token_endpoint"),
                        code_verifier=pending.get("code_verifier"),
                        source_account_id=pending.get("source_account_id"),
                        account_label=pending.get("account_label"),
                        account_identifier=pending.get("account_identifier"),
                        query=pending.get("query"),
                        label_ids=pending.get("label_ids") or [],
                        mime_types=pending.get("mime_types") or [],
                        include_body=_bool_value(pending.get("include_body"), default=True),
                        include_content=_bool_value(pending.get("include_content"), default=True),
                    )
                    account = result.get("source_account") or {}
                    if account.get("id"):
                        try:
                            store.enqueue_source_account_sync(callback_user, account["id"], processing="async", max_records=200)
                        except ValueError:
                            pass
                    label = str(account.get("account_label") or "Google")
                    self._send_text(
                        _google_oauth_callback_page(
                            "Google is connected",
                            f"{label} is connected. Return to {APP_BRAND}; the first sync will start automatically.",
                            success=True,
                        ),
                        media_type="text/html",
                    )
                except ValueError as exc:
                    self._send_text(
                        _google_oauth_callback_page("Google sign-in could not finish", str(exc), success=False),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                return
            if method == "GET" and path == "/v1/connectors/oauth/callback":
                error_value = (params.get("error") or [""])[0]
                if error_value:
                    detail = (params.get("error_description") or [f"The service did not authorize {APP_BRAND}."])[0]
                    self._send_text(
                        _google_oauth_callback_page("Sign-in was not completed", detail, success=False),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                    return
                state_value = (params.get("state") or [""])[0]
                code_value = (params.get("code") or [""])[0]
                pending = _pop_managed_oauth_pending(state_value)
                if not pending:
                    self._send_text(
                        _google_oauth_callback_page(
                            "Sign-in expired",
                            f"Return to {APP_BRAND} and start the connection again. No account was connected.",
                            success=False,
                        ),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                    return
                if not code_value:
                    self._send_text(
                        _google_oauth_callback_page(
                            "Sign-in did not return a code",
                            f"Return to {APP_BRAND} and start the connection again. No account was connected.",
                            success=False,
                        ),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                    return
                try:
                    callback_user = str(pending.get("user_id") or settings.default_user_id)
                    result = store.complete_managed_oauth(
                        callback_user,
                        str(pending.get("source") or ""),
                        code=code_value,
                        redirect_uri=pending.get("redirect_uri"),
                        state=state_value,
                        expected_state=state_value,
                        client_id=pending.get("client_id"),
                        client_secret=pending.get("client_secret"),
                        token_endpoint=pending.get("token_endpoint"),
                        code_verifier=pending.get("code_verifier"),
                        source_account_id=pending.get("source_account_id"),
                        account_label=pending.get("account_label"),
                        account_identifier=pending.get("account_identifier"),
                        include_content=_bool_value(pending.get("include_content"), default=True),
                        api_base_url=pending.get("api_base_url"),
                        notion_version=pending.get("notion_version"),
                    )
                    account = result.get("source_account") or {}
                    if account.get("id"):
                        try:
                            store.enqueue_source_account_sync(callback_user, account["id"], processing="async", max_records=200)
                        except ValueError:
                            pass
                    label = str(account.get("account_label") or "Source")
                    self._send_text(
                        _google_oauth_callback_page(
                            "Source is connected",
                            f"{label} is connected. Return to {APP_BRAND}; the first sync will start automatically.",
                            success=True,
                        ),
                        media_type="text/html",
                    )
                except ValueError as exc:
                    self._send_text(
                        _google_oauth_callback_page("Sign-in could not finish", str(exc), success=False),
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                        media_type="text/html",
                    )
                return
            if method == "POST" and path == "/mcp":
                context = self._auth_mcp()
                if not context:
                    return
                self._handle_mcp(context)
                return

            # Universal adapter surface: the same tool catalog, reachable by any function-calling
            # app over plain HTTP. Authed like /mcp (Bearer → scoped context) and enforced per-tool
            # by call_tool, so a read-only token gets a read-only surface. GET /v1/tools/schema
            # projects the catalog to openai/anthropic/openapi/mcp; POST /v1/tools/call (generic)
            # and POST /v1/tools/{name} (per-tool, matches the OpenAPI operationIds) dispatch tools.
            if path == "/v1/tools/schema" and method == "GET":
                context = self._auth_mcp()
                if not context:
                    return
                token_scopes = None if context.get("admin") else list(context.get("scopes") or [])
                fmt = (params.get("format") or ["openai"])[0]
                host = self.headers.get("Host") or "127.0.0.1:8766"
                base_url = f"http://{host}"
                try:
                    self._send_json({"schema": export_tool_schema(fmt, token_scopes, surface="full", base_url=base_url)})
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if path.startswith("/v1/tools/") and method == "POST":
                context = self._auth_mcp()
                if not context:
                    return
                tool_user = context["user_id"]
                token_scopes = None if context.get("admin") else list(context.get("scopes") or [])
                body = self._json_body()
                if path == "/v1/tools/call":
                    tool_name = str(body.get("name") or "")
                    arguments = body.get("arguments") if isinstance(body.get("arguments"), dict) else {}
                    wrap = True
                else:
                    # /v1/tools/{name}: the URL names the tool; the whole body is the arguments.
                    tool_name = path[len("/v1/tools/"):]
                    arguments = body if isinstance(body, dict) else {}
                    wrap = False
                if not tool_name:
                    self._send_json({"detail": "tool name is required"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                try:
                    value = call_tool(store, tool_user, tool_name, arguments, token_scopes=token_scopes)
                    store.record_agent_event(tool_user, tool_name, arguments, success=True, token=context, result=value)
                except PermissionError as exc:
                    store.record_agent_event(tool_user, tool_name, arguments, success=False, error=str(exc), token=context)
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                    return
                except (ValueError, KeyError) as exc:
                    store.record_agent_event(tool_user, tool_name, arguments, success=False, error=str(exc), token=context)
                    self._send_json({"detail": self._safe_error_message(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                except Exception as exc:
                    store.record_agent_event(tool_user, tool_name, arguments, success=False, error=str(exc), token=context)
                    self._send_json({"detail": self._safe_error_message(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)
                    return
                self._send_json({"tool": tool_name, "result": value} if wrap else value)
                return

            user_id = self._auth_user(method, path)
            if not user_id:
                return

            if method == "POST" and path == "/v1/delivery/preview":
                # Show exactly what would be delivered (cited, sector-scoped, identity-omitted) —
                # a read of the user's own memory; nothing leaves Cortex.
                from .delivery import build_delivery_payload

                body = self._json_body()
                try:
                    payload = build_delivery_payload(
                        store,
                        user_id,
                        task=str(body.get("task") or ""),
                        sector=str(body.get("sector") or "") or None,
                        token_budget=int(body.get("token_budget") or 1500),
                        kind=str(body.get("kind") or "brief"),
                    )
                    self._send_json({"preview": payload})
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/delivery/send":
                # Egress: deliver the cited brief to a user-provided webhook. Auth already required
                # export scope + the allow_agent_exports trust toggle (_auth_user). SSRF-guarded,
                # audited. Nothing here bypasses the cited-only/sector-isolated pack guarantees.
                from .delivery import build_delivery_payload, deliver_webhook, is_safe_webhook_url

                body = self._json_body()
                url = str(body.get("url") or "").strip()
                safe, reason = is_safe_webhook_url(url)
                if not safe:
                    self._send_json({"detail": f"Refusing to deliver: {reason}"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                try:
                    payload = build_delivery_payload(
                        store,
                        user_id,
                        task=str(body.get("task") or ""),
                        sector=str(body.get("sector") or "") or None,
                        token_budget=int(body.get("token_budget") or 1500),
                        kind=str(body.get("kind") or "brief"),
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                result = deliver_webhook(url, payload)
                item_count = sum(len(layer.get("items") or []) for layer in ((payload.get("pack", {}) or {}).get("layers") or []) if isinstance(layer, dict))
                audit_meta = {"target_host": urlparse(url).hostname or "", "sector": payload.get("sector"), "items": item_count}
                store.record_agent_event(user_id, "delivery:webhook", audit_meta, success=bool(result.get("ok")), error=None if result.get("ok") else str(result.get("reason")), token=None)
                self._send_json({"delivered": bool(result.get("ok")), "status": result.get("status"), "reason": result.get("reason")}, status=HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_GATEWAY)
                return

            if method == "POST" and path == "/v1/eval/grade-answer":
                body = self._json_body()
                try:
                    self._send_json(
                        store.grade_answer(
                            user_id,
                            str(body.get("answer_text") or "")[:20000],
                            session_id=str(body.get("session_id") or "")[:120] or None,
                            pack_sha=str(body.get("pack_sha") or "")[:80] or None,
                        )
                    )
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/twin/would-i":
                body = self._json_body()
                try:
                    self._send_json(
                        store.would_i(
                            user_id,
                            str(body.get("question") or "")[:500],
                            limit=max(1, min(int(body.get("limit") or 8), 20)),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/twin/draft-as-me":
                body = self._json_body()
                try:
                    self._send_json(
                        store.draft_as_me(
                            user_id,
                            str(body.get("prompt") or "")[:2000],
                            medium=str(body.get("medium") or "")[:60],
                            limit=max(1, min(int(body.get("limit") or 8), 20)),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/twin/grade":
                body = self._json_body()
                try:
                    self._send_json(
                        store.grade_twin_prediction(
                            user_id,
                            str(body.get("prediction_id") or "")[:120],
                            str(body.get("outcome") or "")[:20],
                            actual=str(body.get("actual") or "")[:500],
                            answerability=str(body.get("answerability") or "")[:20] or None,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/shared-memory/principals":
                body = self._json_body()
                try:
                    trust_raw = body.get("trust_score")
                    self._send_json(
                        store.create_shared_principal(
                            user_id,
                            label=str(body.get("label") or "")[:120],
                            kind=str(body.get("kind") or "agent")[:20],
                            trust_score=None if trust_raw is None else float(trust_raw),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path.startswith("/v1/shared-memory/principals/") and path.endswith("/revoke"):
                principal_id = unquote(
                    path.removeprefix("/v1/shared-memory/principals/").removesuffix("/revoke").strip("/")
                )
                try:
                    self._send_json(store.revoke_shared_principal(user_id, principal_id[:120]))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/shared-memory/writes":
                body = self._json_body()
                try:
                    self._send_json(
                        store.record_shared_memory(
                            user_id,
                            principal_id=str(body.get("principal_id") or "")[:120],
                            nonce=str(body.get("nonce") or "")[:120],
                            content=str(body.get("content") or "")[:200000],
                            signature=str(body.get("signature") or "")[:256],
                            source_url=str(body.get("source_url") or "")[:500],
                            title=str(body.get("title") or "")[:200],
                            supersedes_memory_id=str(body.get("supersedes_memory_id") or "")[:120],
                        )
                    )
                except PermissionError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path.startswith("/v1/alerts/") and path.endswith("/resolve"):
                alert_id = unquote(path.removeprefix("/v1/alerts/").removesuffix("/resolve").strip("/"))
                body = self._json_body()
                try:
                    self._send_json(
                        store.resolve_proactive_alert(
                            user_id,
                            alert_id[:120],
                            str(body.get("resolution") or (params.get("resolution") or [""])[0])[:20],
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/captures/queue":
                body = self._json_body()
                try:
                    self._send_json(
                        store.enqueue_capture(
                            user_id=user_id,
                            content=str(body.get("content") or body.get("text") or ""),
                            source=str(body.get("source") or "macos")[:80],
                            source_url=str(body.get("source_url") or "")[:500] or None,
                            title=str(body.get("title") or "")[:200] or None,
                            cite_capture_provenance=True,
                            auto_approve=settings.auto_approve_captures,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                except ValueError as exc:
                    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "large" in str(exc) else HTTPStatus.UNPROCESSABLE_ENTITY
                    self._send_json({"detail": str(exc)}, status=status)
                return
            if method == "POST" and path == "/v1/captures":
                body = self._json_body()
                # Accept "text" as an alias for "content" — agents and scripts commonly send it,
                # and rejecting a payload that clearly contains the note text is hostile.
                content = str(body.get("content") or body.get("text") or "")
                source = str(body.get("source") or "macos")[:80]
                title = str(body.get("title") or "")[:200] or None
                source_url = str(body.get("source_url") or "")[:500] or None
                try:
                    processing = (params.get("processing") or ["sync"])[0].strip().lower()
                    if processing == "async":
                        self._send_json(store.enqueue_capture(
                            user_id=user_id,
                            content=content,
                            source=source,
                            source_url=source_url,
                            title=title,
                            cite_capture_provenance=True,
                            auto_approve=settings.auto_approve_captures,
                        ))
                    else:
                        self._send_json(self._save_capture(user_id, content, source, title, source_url))
                except ValueError as exc:
                    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "large" in str(exc) else HTTPStatus.UNPROCESSABLE_ENTITY
                    self._send_json({"detail": str(exc)}, status=status)
                return
            if method == "GET" and path.startswith("/v1/captures/") and path.endswith("/status"):
                capture_id = unquote(path.removeprefix("/v1/captures/").removesuffix("/status").strip("/"))
                try:
                    self._send_json(store.capture_status(user_id, capture_id))
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if method == "GET" and path == "/v1/imports/sources":
                self._send_json({"results": store.supported_import_sources()})
                return
            if method == "GET" and path == "/v1/source-accounts/catalog":
                self._send_json({"results": store.source_connector_catalog()})
                return
            if method == "POST" and path == "/v1/connectors/google/oauth/start":
                body = self._json_body()
                try:
                    target_store = store.store_for_user(user_id) if hasattr(store, "store_for_user") else store
                    scopes = body.get("scopes") if isinstance(body.get("scopes"), list) else []
                    started = target_store.start_google_oauth(
                        str(body.get("source") or ""),
                        redirect_uri=str(body.get("redirect_uri") or "") or None,
                        state=str(body.get("state") or "") or None,
                        client_id=str(body.get("client_id") or "") or None,
                        code_challenge=str(body.get("code_challenge") or "") or None,
                        code_challenge_method=str(body.get("code_challenge_method") or "") or None,
                        scopes=[str(item) for item in scopes],
                    )
                    _remember_google_oauth_pending(user_id, body, started)
                    self._send_json(started)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/google/oauth/complete":
                body = self._json_body()
                try:
                    label_ids = body.get("label_ids") if isinstance(body.get("label_ids"), list) else []
                    mime_types = body.get("mime_types") if isinstance(body.get("mime_types"), list) else []
                    result = store.complete_google_oauth(
                        user_id,
                        str(body.get("source") or ""),
                        code=str(body.get("code") or ""),
                        redirect_uri=str(body.get("redirect_uri") or "") or None,
                        state=str(body.get("state") or "") or None,
                        expected_state=str(body.get("expected_state") or "") or None,
                        client_id=str(body.get("client_id") or "") or None,
                        client_secret=str(body.get("client_secret") or "") or None,
                        token_endpoint=str(body.get("token_endpoint") or "") or None,
                        code_verifier=str(body.get("code_verifier") or "") or None,
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        query=str(body.get("query") or "") or None,
                        label_ids=[str(item) for item in label_ids],
                        mime_types=[str(item) for item in mime_types],
                        include_body=_bool_value(body.get("include_body"), default=True),
                        include_content=_bool_value(body.get("include_content"), default=True),
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/oauth/start":
                body = self._json_body()
                try:
                    target_store = store.store_for_user(user_id) if hasattr(store, "store_for_user") else store
                    scopes = body.get("scopes") if isinstance(body.get("scopes"), list) else []
                    started = target_store.start_managed_oauth(
                        str(body.get("source") or ""),
                        redirect_uri=str(body.get("redirect_uri") or "") or None,
                        state=str(body.get("state") or "") or None,
                        client_id=str(body.get("client_id") or "") or None,
                        scopes=[str(item) for item in scopes],
                        code_challenge=str(body.get("code_challenge") or "") or None,
                        code_challenge_method=str(body.get("code_challenge_method") or "") or None,
                        code_verifier=str(body.get("code_verifier") or "") or None,
                    )
                    _remember_managed_oauth_pending(user_id, body, started)
                    self._send_json(started)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/oauth/complete":
                body = self._json_body()
                try:
                    result = store.complete_managed_oauth(
                        user_id,
                        str(body.get("source") or ""),
                        code=str(body.get("code") or ""),
                        redirect_uri=str(body.get("redirect_uri") or "") or None,
                        state=str(body.get("state") or "") or None,
                        expected_state=str(body.get("expected_state") or "") or None,
                        client_id=str(body.get("client_id") or "") or None,
                        client_secret=str(body.get("client_secret") or "") or None,
                        token_endpoint=str(body.get("token_endpoint") or "") or None,
                        code_verifier=str(body.get("code_verifier") or "") or None,
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        include_content=_bool_value(body.get("include_content"), default=True),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                        notion_version=str(body.get("notion_version") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path == "/v1/sources/readiness":
                self._send_json(store.source_readiness_report(user_id))
                return
            if method == "GET" and path == "/v1/source-accounts":
                include_disconnected = (params.get("include_disconnected") or ["false"])[0].strip().lower() in {"1", "true", "yes"}
                accounts = store.list_source_accounts(user_id, include_disconnected=include_disconnected)
                accounts = store.public_payload(user_id, accounts) if hasattr(store, "public_payload") else accounts
                self._send_json({"results": accounts})
                return
            if method == "POST" and path == "/v1/source-accounts":
                body = self._json_body()
                try:
                    account = store.upsert_source_account(
                        user_id,
                        source=str(body.get("source") or ""),
                        account_label=str(body.get("account_label") or ""),
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        connection_type=str(body.get("connection_type") or "manual"),
                        status=str(body.get("status") or "available"),
                        auth_state=str(body.get("auth_state") or "not_configured"),
                        policy=body.get("policy") if isinstance(body.get("policy"), dict) else None,
                        metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else None,
                        last_error=str(body.get("last_error") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, account) if hasattr(store, "public_payload") else account)
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "DELETE" and path.startswith("/v1/source-accounts/"):
                account_id = unquote(path.removeprefix("/v1/source-accounts/").strip("/"))
                disconnected = store.disconnect_source_account(user_id, account_id)
                if not disconnected:
                    self._send_json({"detail": "Source account not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(store.public_payload(user_id, disconnected) if hasattr(store, "public_payload") else disconnected)
                return
            if method == "POST" and path.startswith("/v1/source-accounts/") and path.endswith("/disconnect"):
                account_id = unquote(path.removeprefix("/v1/source-accounts/").removesuffix("/disconnect").strip("/"))
                disconnected = store.disconnect_source_account(user_id, account_id)
                if not disconnected:
                    self._send_json({"detail": "Source account not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(store.public_payload(user_id, disconnected) if hasattr(store, "public_payload") else disconnected)
                return
            if method == "POST" and path.startswith("/v1/source-accounts/") and path.endswith("/resume"):
                account_id = unquote(path.removeprefix("/v1/source-accounts/").removesuffix("/resume").strip("/"))
                resumed = store.resume_source_account(user_id, account_id)
                if not resumed:
                    self._send_json({"detail": "Source account not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(store.public_payload(user_id, resumed) if hasattr(store, "public_payload") else resumed)
                return
            if method == "POST" and path.startswith("/v1/source-accounts/") and path.endswith("/sync"):
                account_id = unquote(path.removeprefix("/v1/source-accounts/").removesuffix("/sync").strip("/"))
                body = self._json_body()
                records = body.get("records") if isinstance(body.get("records"), list) else []
                try:
                    result = store.sync_source_account_records(
                        user_id,
                        account_id,
                        records=records,
                        cursor_name=str(body.get("cursor_name") or "default"),
                        cursor_value=str(body.get("cursor_value") or "") or None,
                        high_water_mark=str(body.get("high_water_mark") or "") or None,
                        state=body.get("state") if isinstance(body.get("state"), dict) else None,
                        processing=str(body.get("processing") or "async"),
                        archive_missing=_bool_value(body.get("archive_missing"), default=False),
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/obsidian/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 1000)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 5000:
                        raise ValueError("max_records must be between 1 and 5000")
                    result = store.sync_obsidian_vault(
                        user_id,
                        vault_path=str(body.get("vault_path") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "local-folder"),
                        # Default direct notes sync to Review so first-time users can approve what
                        # becomes memory. Explicit trusted flows may pass review_required=false.
                        review_required=bool(body.get("review_required", True)),
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/agent-sessions/sync":
                # Harvest the user's own messages from local coding-agent session logs. No
                # directory-override fields: the caller cannot point the scanner at arbitrary
                # paths (agent list only), same guard as the MCP tool.
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 200)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    try:
                        per_session_limit = int(body.get("per_session_limit") or 25)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("per_session_limit must be an integer") from exc
                    if per_session_limit < 1 or per_session_limit > 200:
                        raise ValueError("per_session_limit must be between 1 and 200")
                    raw_agents = body.get("agents")
                    agents = [str(item) for item in raw_agents] if isinstance(raw_agents, list) else None
                    result = store.sync_agent_sessions(
                        user_id,
                        agents=agents,
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        per_session_limit=per_session_limit,
                        cursor_name=str(body.get("cursor_name") or "agent-sessions"),
                        review_required=bool(body.get("review_required", True)),
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/obsidian/write-back":
                # Obsidian write-back: refresh the distilled, cited Cortex/ pages in the user's
                # vault. Export-scoped (memory egress into user-owned files), MCP-tool parity.
                body = self._json_body()
                try:
                    try:
                        people_limit = int(body.get("people_limit") or 10)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("people_limit must be an integer") from exc
                    result = store.write_obsidian_pages(
                        user_id,
                        vault_path=str(body.get("vault_path") or "") or None,
                        people_limit=people_limit,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except PermissionError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/context-file/preview":
                # CLAUDE.md compiler: render the managed block WITHOUT writing anything, so a
                # caller can see exactly what would be synced first. Read-scoped.
                body = self._json_body()
                try:
                    style = str(body.get("style") or CONTEXT_FILE_DEFAULT_STYLE).strip().lower() or CONTEXT_FILE_DEFAULT_STYLE
                    if style not in CONTEXT_FILE_SUPPORTED_STYLES:
                        raise ValueError(f"style must be one of {sorted(CONTEXT_FILE_SUPPORTED_STYLES)}")
                    block = render_context_block(store, user_id, style=style)
                    self._send_json({"block": block, "style": style})
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/context-file/sync":
                # CLAUDE.md compiler: render + write the managed block into a file the user
                # already owns (CLAUDE.md / AGENTS.md / .cursorrules / GEMINI.md / any .md). The
                # data plane never leaves the local filesystem — export-scoped like Obsidian
                # write-back because it persists distilled memory outside Cortex custody.
                body = self._json_body()
                try:
                    style = str(body.get("style") or CONTEXT_FILE_DEFAULT_STYLE).strip().lower() or CONTEXT_FILE_DEFAULT_STYLE
                    if style not in CONTEXT_FILE_SUPPORTED_STYLES:
                        raise ValueError(f"style must be one of {sorted(CONTEXT_FILE_SUPPORTED_STYLES)}")
                    raw_path = str(body.get("path") or "").strip()
                    if not raw_path:
                        raise ValueError("path is required")
                    vault_root = store.vault_root_path(user_id)
                    resolved = validate_context_file_path(raw_path, vault_root=vault_root)
                    result = sync_context_file(store, user_id, str(resolved), style=style)
                    self._send_json(result)
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                except PermissionError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/integrity/verify":
                # Phase D: recompute the chain and compare to a caller-pinned head (read scope).
                body = self._json_body()
                try:
                    expected = str(body.get("expected_head") or "").strip()
                    if not expected:
                        raise ValueError("expected_head is required")
                    self._send_json(store.verify_integrity(user_id, expected))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/beliefs/proof/verify":
                body = self._json_body()
                try:
                    proof = body.get("proof")
                    if not isinstance(proof, dict):
                        raise ValueError("proof must be a JSON object")
                    expected_head = str(body.get("expected_head") or "").strip() or None
                    self._send_json(store.verify_belief_proof(proof, expected_head=expected_head))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/export/verify":
                # Phase D: verify a portable bundle WITHOUT trusting its source. Pure function of
                # the bundle bytes; auth-gated for parity but never touches the caller's own store.
                body = self._json_body()
                try:
                    bundle = body.get("bundle")
                    if not isinstance(bundle, dict):
                        raise ValueError("bundle must be a JSON object")
                    expected_value = body.get("expected_signing_key_id")
                    if expected_value is not None and not isinstance(expected_value, str):
                        raise ValueError("expected_signing_key_id must be a string")
                    expected_signing_key_id = str(expected_value or "").strip() or None
                    if expected_signing_key_id and len(expected_signing_key_id) > 128:
                        raise ValueError("expected_signing_key_id exceeds 128 characters")
                    self._send_json(
                        store.verify_portable_bundle(
                            bundle,
                            expected_signing_key_id=expected_signing_key_id,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/import/bundle":
                body = self._json_body()
                try:
                    bundle = body.get("bundle")
                    if not isinstance(bundle, dict):
                        raise ValueError("bundle must be a JSON object")
                    expected_value = body.get("expected_signing_key_id")
                    if expected_value is not None and not isinstance(expected_value, str):
                        raise ValueError("expected_signing_key_id must be a string")
                    expected_signing_key_id = str(expected_value or "").strip() or None
                    if expected_signing_key_id and len(expected_signing_key_id) > 128:
                        raise ValueError("expected_signing_key_id exceeds 128 characters")
                    self._send_json(
                        store.import_portable_bundle(
                            user_id,
                            bundle,
                            expected_signing_key_id=expected_signing_key_id,
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/github/discover":
                body = self._json_body()
                try:
                    from .connectors.github import discover_github_repositories

                    result = discover_github_repositories(
                        token=str(body.get("token") or ""),
                        limit=int(body.get("limit") or 100),
                        page=int(body.get("page") or 1),
                        api_base_url=str(body.get("api_base_url") or "") or "https://api.github.com",
                    ).to_summary()
                    self._send_json(result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/slack/discover":
                body = self._json_body()
                try:
                    from .connectors.slack import discover_slack_channels

                    result = discover_slack_channels(
                        token=str(body.get("token") or ""),
                        limit=int(body.get("limit") or 100),
                        include_private=_bool_value(body.get("include_private"), default=True),
                        cursor=str(body.get("cursor") or "") or None,
                        api_base_url=str(body.get("api_base_url") or "") or "https://slack.com/api",
                    ).to_summary()
                    self._send_json(result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/github/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    try:
                        max_comments_per_item = int(body.get("max_comments_per_item") if body.get("max_comments_per_item") is not None else 10)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_comments_per_item must be an integer") from exc
                    if max_comments_per_item < 0 or max_comments_per_item > 50:
                        raise ValueError("max_comments_per_item must be between 0 and 50")
                    repositories = body.get("repositories") if isinstance(body.get("repositories"), list) else []
                    result = store.sync_github_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or ""),
                        repositories=[str(item) for item in repositories],
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        include_comments=_bool_value(body.get("include_comments"), default=True),
                        max_comments_per_item=max_comments_per_item,
                        cursor_name=str(body.get("cursor_name") or "issues"),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/github/device/start":
                # Begin the GitHub OAuth Device Flow (RFC 8628). Secretless: the app only needs the
                # public client ID, provisioned via CORTEX_GITHUB_OAUTH_CLIENT_ID. The app shows the
                # returned user_code, opens verification_uri, then polls /device/poll.
                body = self._json_body()
                client_id = (str(body.get("client_id") or "").strip()
                             or os.environ.get("CORTEX_GITHUB_OAUTH_CLIENT_ID", "").strip())
                if not client_id:
                    self._send_json(
                        {"detail": "GitHub sign-in is not configured on this device."},
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                try:
                    from .connectors.github import DEFAULT_DEVICE_SCOPE, github_device_start

                    scope = (str(body.get("scope") or "").strip()
                             or os.environ.get("CORTEX_GITHUB_OAUTH_SCOPE", "").strip()
                             or DEFAULT_DEVICE_SCOPE)
                    self._send_json(github_device_start(client_id, scope))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                except OSError as exc:
                    self._send_json({"detail": f"Could not reach GitHub: {exc}"}, status=HTTPStatus.BAD_GATEWAY)
                return
            if method == "POST" and path == "/v1/connectors/github/device/poll":
                # Poll once for the device-flow token. Stateless: the app holds device_code and drives
                # the loop, honoring the interval/slow_down/expired statuses we surface verbatim.
                body = self._json_body()
                client_id = (str(body.get("client_id") or "").strip()
                             or os.environ.get("CORTEX_GITHUB_OAUTH_CLIENT_ID", "").strip())
                if not client_id:
                    self._send_json(
                        {"detail": "GitHub sign-in is not configured on this device."},
                        status=HTTPStatus.SERVICE_UNAVAILABLE,
                    )
                    return
                try:
                    from .connectors.github import github_device_poll

                    self._send_json(github_device_poll(client_id, str(body.get("device_code") or "")))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                except OSError as exc:
                    self._send_json({"detail": f"Could not reach GitHub: {exc}"}, status=HTTPStatus.BAD_GATEWAY)
                return
            if method == "POST" and path == "/v1/connectors/gmail/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 50)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 200:
                        raise ValueError("max_records must be between 1 and 200")
                    label_ids = body.get("label_ids") if isinstance(body.get("label_ids"), list) else []
                    result = store.sync_gmail_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        access_token=str(body.get("access_token") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        query=str(body.get("query") or "") or None,
                        label_ids=[str(item) for item in label_ids],
                        since=str(body.get("since") or "") or None,
                        page_token=str(body.get("page_token") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "messages"),
                        include_body=_bool_value(body.get("include_body"), default=True),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/google-drive/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 50)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 200:
                        raise ValueError("max_records must be between 1 and 200")
                    mime_types = body.get("mime_types") if isinstance(body.get("mime_types"), list) else []
                    result = store.sync_google_drive_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        access_token=str(body.get("access_token") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        query=str(body.get("query") or "") or None,
                        mime_types=[str(item) for item in mime_types],
                        since=str(body.get("since") or "") or None,
                        page_token=str(body.get("page_token") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "files"),
                        include_content=_bool_value(body.get("include_content"), default=True),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/outlook/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 50)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 200:
                        raise ValueError("max_records must be between 1 and 200")
                    result = store.sync_outlook_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        access_token=str(body.get("access_token") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        query=str(body.get("query") or "") or None,
                        since=str(body.get("since") or "") or None,
                        page_token=str(body.get("page_token") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "messages"),
                        include_body=_bool_value(body.get("include_body"), default=True),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/slack/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 200:
                        raise ValueError("max_records must be between 1 and 200")
                    channels = body.get("channels") if isinstance(body.get("channels"), list) else []
                    result = store.sync_slack_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or ""),
                        channels=[str(item) for item in channels],
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "messages"),
                        workspace_url=str(body.get("workspace_url") or "") or None,
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/readwise/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    result = store.sync_readwise_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        page_cursor=str(body.get("page_cursor") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "highlights"),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/calendar/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    result = store.sync_calendar_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        ics_path=str(body.get("ics_path") or "") or None,
                        feed_url=str(body.get("feed_url") or "") or None,
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "events"),
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/raindrop/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    result = store.sync_raindrop_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or ""),
                        collection_id=str(body.get("collection_id") or "0"),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        page=str(body.get("page") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "raindrops"),
                        include_highlights=_bool_value(body.get("include_highlights"), default=True),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/zotero/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    result = store.sync_zotero_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or "") or None,
                        library_type=str(body.get("library_type") or "user"),
                        library_id=str(body.get("library_id") or "0"),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        cursor=str(body.get("cursor") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "items"),
                        include_attachments=_bool_value(body.get("include_attachments"), default=False),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/linear/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    result = store.sync_linear_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        cursor=str(body.get("cursor") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "issues"),
                        api_url=str(body.get("api_url") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/jira/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 100)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 500:
                        raise ValueError("max_records must be between 1 and 500")
                    result = store.sync_jira_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        email=str(body.get("email") or ""),
                        api_token=str(body.get("api_token") or ""),
                        site_url=str(body.get("site_url") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        jql=str(body.get("jql") or "") or None,
                        since=str(body.get("since") or "") or None,
                        page_token=str(body.get("page_token") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "issues"),
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/connectors/notion/sync":
                body = self._json_body()
                try:
                    try:
                        max_records = int(body.get("max_records") or 50)
                    except (TypeError, ValueError) as exc:
                        raise ValueError("max_records must be an integer") from exc
                    if max_records < 1 or max_records > 200:
                        raise ValueError("max_records must be between 1 and 200")
                    result = store.sync_notion_account(
                        user_id,
                        complete_snapshot=_bool_value(body.get("complete_snapshot"), default=False),
                        token=str(body.get("token") or ""),
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        cursor=str(body.get("cursor") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "pages"),
                        include_content=_bool_value(body.get("include_content"), default=True),
                        api_base_url=str(body.get("api_base_url") or "") or None,
                        notion_version=str(body.get("notion_version") or "") or None,
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path == "/v1/sync-cursors":
                source_account_id = (params.get("source_account_id") or [None])[0]
                self._send_json({"results": store.list_sync_cursors(user_id, source_account_id=source_account_id)})
                return
            if method == "POST" and path == "/v1/sync-cursors":
                body = self._json_body()
                try:
                    self._send_json(store.upsert_sync_cursor(
                        user_id,
                        source=str(body.get("source") or ""),
                        cursor_name=str(body.get("cursor_name") or ""),
                        cursor_value=str(body.get("cursor_value") or "") or None,
                        high_water_mark=str(body.get("high_water_mark") or "") or None,
                        state=body.get("state") if isinstance(body.get("state"), dict) else None,
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        last_error=str(body.get("last_error") or "") or None,
                        completed=_bool_value(body.get("completed"), default=True),
                    ))
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path == "/v1/sync/devices":
                include_revoked = (params.get("include_revoked") or ["false"])[0].lower() in {"1", "true", "yes"}
                self._send_json({"results": store.list_sync_devices(user_id, include_revoked=include_revoked)})
                return
            if method == "POST" and path == "/v1/sync/devices":
                body = self._json_body()
                capabilities = body.get("capabilities") if isinstance(body.get("capabilities"), list) else []
                try:
                    self._send_json(store.register_sync_device(
                        user_id,
                        device_name=str(body.get("device_name") or ""),
                        platform=str(body.get("platform") or "unknown"),
                        device_key=str(body.get("device_key") or "") or None,
                        public_key=str(body.get("public_key") or "") or None,
                        capabilities=[str(item) for item in capabilities],
                    ))
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if path.startswith("/v1/sync/devices/") and path.endswith("/receipts"):
                device_id = unquote(path.removeprefix("/v1/sync/devices/").removesuffix("/receipts").strip("/"))
                if method == "GET":
                    self._send_json({"results": store.list_sync_receipts(user_id, device_id, limit=_int_param(params, "limit", 50, 1, 200))})
                    return
                if method == "POST":
                    body = self._json_body()
                    try:
                        self._send_json(store.record_sync_receipt(
                            user_id,
                            device_id,
                            cursor=str(body.get("cursor") or ""),
                            status=str(body.get("status") or "accepted"),
                            manifest_hash=str(body.get("manifest_hash") or "") or None,
                            remote_ref=str(body.get("remote_ref") or "") or None,
                            error=str(body.get("error") or "") or None,
                            stats=body.get("stats") if isinstance(body.get("stats"), dict) else None,
                        ))
                    except ValueError as exc:
                        self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
            if method == "DELETE" and path.startswith("/v1/sync/devices/"):
                device_id = unquote(path.removeprefix("/v1/sync/devices/").strip("/"))
                revoked = store.revoke_sync_device(user_id, device_id)
                if not revoked:
                    self._send_json({"detail": "Sync device not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(revoked)
                return
            if method == "GET" and path == "/v1/imports":
                include_deleted = (params.get("include_deleted") or ["true"])[0].lower() not in {"0", "false", "no"}
                self._send_json({"results": store.list_imports(
                    user_id,
                    limit=_int_param(params, "limit", 50, 1, 100),
                    include_deleted=include_deleted,
                )})
                return
            if method == "GET" and path == "/v1/imports/detect":
                # Local-first convenience: scan the user's Downloads/Desktop (and a dedicated
                # ~/CortexImports drop folder we create) for AI-chat / app exports so the app can
                # offer "found your ChatGPT export (N conversations) — import" without a file picker.
                from .source_ingest import scan_export_candidates, default_export_scan_dirs
                import os as _os
                extra = params.get("dir") or []
                dirs = [d for d in extra if d] or default_export_scan_dirs()
                drop = _os.path.join(_os.path.expanduser("~"), "CortexImports")
                try:
                    _os.makedirs(drop, exist_ok=True)  # initialize the drop folder so it always exists
                except OSError:
                    pass
                self._send_json({"candidates": scan_export_candidates(dirs), "scanned_dirs": dirs, "drop_folder": drop})
                return
            if method == "POST" and path == "/v1/imports/analyze":
                body = self._json_body()
                try:
                    parsed = _source_import_request(body, analyze=True)
                    self._send_json(store.analyze_import_sources(
                        parsed["paths"],
                        source_hint=parsed["source_hint"],
                        max_records=parsed["max_records"],
                    ))
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "POST" and path == "/v1/imports":
                body = self._json_body()
                try:
                    parsed = _source_import_request(body)
                    self._send_json(store.import_sources(
                        user_id=user_id,
                        paths=parsed["paths"],
                        source_hint=parsed["source_hint"],
                        processing=parsed["processing"],
                        max_records=parsed["max_records"],
                        offset=parsed["offset"],
                        auto_approve=parsed["auto_approve"],
                    ))
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path.startswith("/v1/imports/"):
                import_id = unquote(path.removeprefix("/v1/imports/").strip("/"))
                result = store.get_import(user_id, import_id)
                if not result:
                    self._send_json({"detail": "Import not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(result)
                return
            if method == "DELETE" and path.startswith("/v1/imports/"):
                import_id = unquote(path.removeprefix("/v1/imports/").strip("/"))
                try:
                    self._send_json(store.delete_import(user_id, import_id))
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if method == "GET" and path == "/v1/jobs":
                self._send_json({"results": store.list_jobs(
                    user_id,
                    status=(params.get("status") or [None])[0],
                    job_type=(params.get("job_type") or [None])[0],
                    limit=_int_param(params, "limit", 50, 1, 100),
                )})
                return
            if method == "GET" and path == "/v1/jobs/health":
                self._send_json(store.job_health(
                    user_id,
                    failed_limit=_int_param(params, "failed_limit", 10, 0, 50),
                    stale_after_seconds=_int_param(params, "stale_after_seconds", 900, 60, 86400),
                ))
                return
            if method == "POST" and path == "/v1/jobs/run":
                self._send_json(store.run_due_jobs(
                    user_id,
                    limit=_int_param(params, "limit", 10, 1, 100),
                    schedule_source_syncs=_bool_value((params.get("schedule_source_syncs") or [True])[0], default=True),
                ))
                return
            if method == "POST" and path == "/v1/maintenance/jobs/run":
                self._send_json(store.run_due_jobs(
                    user_id,
                    limit=_int_param(params, "limit", 10, 1, 100),
                    schedule_source_syncs=_bool_value((params.get("schedule_source_syncs") or [True])[0], default=True),
                ))
                return
            if method == "POST" and path == "/v1/sources/sync-due":
                self._send_json(store.run_due_source_sync_jobs(user_id, limit=_int_param(params, "limit", 10, 1, 100), worker_id="api-source-sync"))
                return
            if method == "GET" and path.startswith("/v1/jobs/"):
                job_id = unquote(path.removeprefix("/v1/jobs/"))
                job = store.get_job(user_id, job_id)
                if not job:
                    self._send_json({"detail": "Job not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(job)
                return
            if method == "GET" and path == "/v1/activity":
                # Live ticker: return events newer than `since`, blocking up to `wait` seconds for
                # the next one so each memory surfaces the instant it lands (bounded long-poll — no
                # SSE, no held slot). Empty result + the current cursor is a valid heartbeat.
                from .activity import activity_hub

                since = _int_param(params, "since", 0, 0, 1 << 62)
                wait_seconds = _int_param(params, "wait", 0, 0, 8)
                events = activity_hub.wait_since(since, user_id=user_id, timeout=float(wait_seconds))
                # Advance the cursor ONLY past events actually returned. Jumping to latest_seq() on
                # the empty branch would skip any event published in the window between wait_since()
                # timing out and that read — the client would never see it (its next `since` is past it).
                cursor = events[-1]["seq"] if events else since
                self._send_json({"events": events, "cursor": cursor})
                return
            if method == "GET" and path == "/v1/sources/stats":
                self._send_json({"results": store.source_memory_stats(user_id)})
                return
            if method == "POST" and path == "/v1/captures/approve-all":
                # Clear the whole review backlog (optionally one source's) in one decision — the
                # UI's 10-at-a-time batch made a 99+ queue unmanageable.
                body = self._json_body()
                self._send_json(store.approve_all_captures(
                    user_id,
                    source=str(body.get("source") or "") or None,
                ))
                return
            if method == "DELETE" and path.startswith("/v1/sources/") and path.endswith("/memories"):
                source = unquote(path.removeprefix("/v1/sources/").removesuffix("/memories").strip("/"))
                try:
                    self._send_json(store.purge_source_memories(user_id, source))
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return

            if method == "GET" and path == "/v1/recent":
                limit = _int_param(params, "limit", 20, 1, 100)
                results = store.public_recent(user_id, limit) if hasattr(store, "public_recent") else store.recent(user_id, limit)
                self._send_json({"results": results})
                return
            if method == "GET" and path == "/v1/inbox":
                self._send_json({"results": store.inbox(user_id, _int_param(params, "limit", 30, 1, 100))})
                return
            if method == "GET" and path == "/v1/search":
                query = (params.get("query") or [""])[0]
                kind = (params.get("kind") or [None])[0]
                layer = (params.get("layer") or [None])[0]
                sector = (params.get("sector") or [None])[0]
                source = (params.get("source") or [None])[0]
                source_account_id = (params.get("source_account_id") or [None])[0]
                as_of = (params.get("as_of") or [None])[0]
                associative = str((params.get("associative") or [""])[0]).strip().lower() in {"1", "true", "yes", "on"}
                association_mode = str((params.get("association_mode") or [""])[0]).strip().lower() or None
                if association_mode not in {None, "one_hop", "bounded", "ppr"}:
                    self._send_json({"detail": "association_mode must be one_hop, bounded, or ppr"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                metadata_filters = {
                    "repository": (params.get("repository") or [None])[0],
                    "channel": (params.get("channel") or [None])[0],
                    "record_scope": (params.get("record_scope") or [None])[0],
                    "state": (params.get("state") or [None])[0],
                    "project": (params.get("project") or [None])[0],
                }
                limit = _int_param(params, "limit", 10, 1, 50)
                if hasattr(store, "public_search_payload"):
                    payload = store.public_search_payload(
                        user_id,
                        query,
                        limit,
                        kind,
                        layer,
                        sector=sector,
                        source=source,
                        source_account_id=source_account_id,
                        as_of=as_of,
                        associative=associative,
                        association_mode=association_mode,
                        metadata_filters=metadata_filters,
                    )
                elif hasattr(store, "public_search"):
                    results = store.public_search(
                        user_id,
                        query,
                        limit,
                        kind,
                        layer,
                        sector=sector,
                        source=source,
                        source_account_id=source_account_id,
                        as_of=as_of,
                        include_related=associative,
                        association_mode=association_mode,
                        metadata_filters=metadata_filters,
                    )
                    payload = {"query": query, "sector": sector, "filters": {}, "results": results, "retrieval": {"diagnostics_unavailable": True}}
                else:
                    results = store.search(
                        user_id,
                        query,
                        limit,
                        kind,
                        layer,
                        sector=sector,
                        source=source,
                        source_account_id=source_account_id,
                        as_of=as_of,
                        include_related=associative,
                        association_mode=association_mode,
                        metadata_filters=metadata_filters,
                    )
                    payload = {"query": query, "sector": sector, "filters": {}, "results": results, "retrieval": {"diagnostics_unavailable": True}}
                self._send_json(payload)
                return
            if method == "GET" and path == "/v1/ask":
                query = (params.get("query") or [""])[0]
                sector = (params.get("sector") or [None])[0]
                self._send_json(
                    store.answer_query(
                        user_id,
                        query,
                        _int_param(params, "limit", 8, 1, 20),
                        sector=sector,
                        source=(params.get("source") or [None])[0],
                        source_account_id=(params.get("source_account_id") or [None])[0],
                        as_of=(params.get("as_of") or [None])[0],
                        metadata_filters={
                            "repository": (params.get("repository") or [None])[0],
                            "channel": (params.get("channel") or [None])[0],
                            "record_scope": (params.get("record_scope") or [None])[0],
                            "state": (params.get("state") or [None])[0],
                            "project": (params.get("project") or [None])[0],
                        },
                    )
                )
                return
            if method == "GET" and path == "/v1/action-brief":
                brief = store.action_brief(
                    user_id,
                    (params.get("task") or [""])[0],
                    limit=_int_param(params, "limit", 8, 1, 20),
                    sector=(params.get("sector") or [None])[0],
                    as_of=(params.get("as_of") or [None])[0],
                )
                if (params.get("format") or ["json"])[0] == "markdown":
                    self._send_text(brief["markdown"], media_type="text/markdown")
                else:
                    self._send_json(brief)
                return
            if method == "GET" and path == "/v1/decisions/history":
                include_superseded = (params.get("include_superseded") or ["true"])[0].strip().lower() not in {"0", "false", "no"}
                self._send_json(
                    store.decision_history(
                        user_id,
                        (params.get("query") or [""])[0],
                        limit=_int_param(params, "limit", 12, 1, 30),
                        sector=(params.get("sector") or [None])[0],
                        include_superseded=include_superseded,
                        as_of=(params.get("as_of") or [None])[0],
                    )
                )
                return
            if method == "GET" and path == "/v1/beliefs/timeline":
                try:
                    self._send_json(
                        store.get_belief_timeline(
                            user_id,
                            (params.get("topic") or [""])[0],
                            limit=_int_param(params, "limit", 20, 1, 50),
                            as_of=(params.get("as_of") or [None])[0],
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path == "/v1/beliefs/proof":
                try:
                    self._send_json(
                        store.get_belief_proof(
                            user_id,
                            (params.get("topic") or [""])[0],
                            valid_at=(params.get("valid_at") or [None])[0],
                            known_at=(params.get("known_at") or [None])[0],
                            expected_head=(params.get("expected_head") or [None])[0],
                            limit=_int_param(params, "limit", 20, 1, 50),
                        )
                    )
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path == "/v1/eval/scorecard":
                self._send_json(
                    store.get_tool_scorecard(
                        user_id,
                        days=_int_param(params, "days", 7, 1, 90),
                        token_id=(params.get("token_id") or [None])[0],
                    )
                )
                return
            if method == "GET" and path == "/v1/usage/headline":
                # North-star headline: memories actively used across N distinct AIs this window
                # (weekly cross-AI recall). Shared/default/untokened traffic is exposed as
                # unattributed_calls and never counted in distinct_ais.
                self._send_json(
                    store.cross_ai_recall_headline(
                        user_id,
                        days=_int_param(params, "days", 7, 1, 90),
                    )
                )
                return
            if method == "GET" and path == "/v1/twin/scorecard":
                self._send_json(
                    store.get_twin_scorecard(
                        user_id,
                        days=_int_param(params, "days", 90, 1, 365),
                    )
                )
                return
            if method == "GET" and path == "/v1/twin/calibration":
                self._send_json(
                    store.get_twin_calibration(
                        user_id,
                        days=_int_param(params, "days", 90, 1, 365),
                        prediction_ids=(params.get("prediction_ids") or [])[:100]
                        if "prediction_ids" in params
                        else None,
                    )
                )
                return
            if method == "GET" and path == "/v1/shared-memory/principals":
                include_revoked = (params.get("include_revoked") or ["false"])[0].strip().lower() in {
                    "1",
                    "true",
                    "yes",
                }
                self._send_json(
                    {"principals": store.list_shared_principals(user_id, include_revoked=include_revoked)}
                )
                return
            if method == "GET" and path == "/v1/shared-memory/verify":
                self._send_json(
                    store.verify_shared_memory(
                        user_id,
                        principal_id=(params.get("principal_id") or [None])[0],
                    )
                )
                return
            if method == "GET" and path == "/v1/shared-memory/poisoning-attempts":
                self._send_json(
                    store.get_poisoning_attempts(
                        user_id,
                        principal_id=(params.get("principal_id") or [None])[0],
                        limit=_int_param(params, "limit", 100, 1, 500),
                    )
                )
                return
            if method == "GET" and path == "/v1/sources/reputation":
                # Phase C: per-source approve/reject reputation from the review ledger (read-only).
                self._send_json(
                    store.source_reputation(
                        user_id,
                        days=_int_param(params, "days", 90, 1, 365),
                    )
                )
                return
            if method == "GET" and path == "/v1/alerts":
                status = (params.get("status") or ["pending"])[0]
                self._send_json(
                    {
                        "alerts": store.list_proactive_alerts(
                            user_id,
                            status=None if status == "all" else status,
                            limit=_int_param(params, "limit", 20, 1, 100),
                        )
                    }
                )
                return
            if method == "GET" and path == "/v1/alerts/precision":
                self._send_json(store.get_alert_precision(user_id, days=_int_param(params, "days", 30, 1, 365)))
                return
            if method == "GET" and path == "/v1/prefetch/hit-rate":
                self._send_json(store.get_prefetch_hit_rate(user_id, days=_int_param(params, "days", 30, 1, 365)))
                return
            if method == "GET" and path == "/v1/tasks/open":
                self._send_json({"results": store.open_tasks(user_id, _int_param(params, "limit", 20, 1, 100))})
                return
            if method == "GET" and path == "/v1/topics":
                sector = (params.get("sector") or [None])[0]
                self._send_json({"sector": sector, "results": store.list_topics(user_id, _int_param(params, "limit", 30, 1, 100), sector=sector, exclude_taste_excluded=True)})
                return
            if method == "GET" and path == "/v1/entities":
                sector = (params.get("sector") or [None])[0]
                self._send_json({"sector": sector, "results": store.list_entities(user_id, _int_param(params, "limit", 30, 1, 100), sector=sector, exclude_taste_excluded=True)})
                return
            if method == "GET" and path.startswith("/v1/people/") and path.endswith("/context"):
                name = unquote(path.removeprefix("/v1/people/").removesuffix("/context").strip("/"))
                self._send_json(store.person_context(user_id, name, limit=_int_param(params, "limit", 8, 1, 20)))
                return
            if method == "GET" and path.startswith("/v1/people/"):
                name = unquote(path.removeprefix("/v1/people/"))
                self._send_json({"results": store.about_person(user_id, name, _int_param(params, "limit", 12, 1, 50))})
                return
            if method == "GET" and path.startswith("/v1/entities/"):
                name = unquote(path.removeprefix("/v1/entities/"))
                self._send_json({"results": store.about_entity(user_id, name, _int_param(params, "limit", 12, 1, 50))})
                return
            if method == "GET" and path == "/v1/review/today":
                self._send_json(store.daily_review(user_id))
                return
            if method == "GET" and path == "/v1/review/sections":
                # Section-grouped review: a large backlog becomes <= 15 one-shot decisions.
                self._send_json(store.review_sections(user_id))
                return
            if method == "POST" and path.startswith("/v1/review/sections/") and path.endswith("/approve"):
                section_id = unquote(path.removeprefix("/v1/review/sections/").removesuffix("/approve").strip("/"))
                result = store.approve_review_section(user_id, section_id)
                if result["requested"] == 0:
                    self._send_json({"detail": "Section not found or already reviewed"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(result)
                return
            if method == "POST" and path.startswith("/v1/review/sections/") and path.endswith("/archive"):
                section_id = unquote(path.removeprefix("/v1/review/sections/").removesuffix("/archive").strip("/"))
                result = store.archive_review_section(user_id, section_id)
                if result["requested"] == 0:
                    self._send_json({"detail": "Section not found or already reviewed"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(result)
                return
            if method == "GET" and path == "/v1/loop":
                self._send_json(store.product_loop(user_id))
                return
            if method == "POST" and path == "/v1/loop/reuse":
                body = self._json_body()
                self._send_json(store.record_context_reuse(
                    user_id,
                    surface=str(body.get("surface") or "macos")[:80],
                    query=str(body.get("query") or "")[:500],
                    target=str(body.get("target") or "")[:80],
                ))
                return
            if method == "GET" and path == "/v1/context-pack":
                query = (params.get("query") or [""])[0]
                sector = (params.get("sector") or [None])[0]
                self._send_text(store.context_pack(user_id, query=query, limit=_int_param(params, "limit", 12, 1, 50), sector=sector), media_type="text/markdown")
                return
            if path == "/v1/context" and method in {"GET", "POST"}:
                if method == "POST":
                    body = self._json_body()
                    task = str(body.get("task") or "")
                    surface = str(body.get("surface") or "agent")
                    intent = str(body.get("intent") or "") or None
                    sector = str(body.get("sector") or "") or None
                    project = str(body.get("project") or "") or None
                    as_of = str(body.get("as_of") or "") or None
                    output_format = str(body.get("format") or "json").strip().lower()
                    model = str(body.get("model") or "") or None
                    pin = bool(body.get("pin"))
                    pin_session_id = str(body.get("session_id") or "") or None
                    try:
                        token_budget = int(body.get("token_budget") or 2000)
                    except (TypeError, ValueError):
                        self._send_json({"detail": "token_budget must be an integer"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                        return
                else:
                    task = (params.get("task") or [""])[0]
                    surface = (params.get("surface") or ["agent"])[0]
                    intent = (params.get("intent") or [None])[0]
                    sector = (params.get("sector") or [None])[0]
                    project = (params.get("project") or [None])[0]
                    as_of = (params.get("as_of") or [None])[0]
                    output_format = ((params.get("format") or ["json"])[0] or "json").strip().lower()
                    model = (params.get("model") or [None])[0] or None
                    token_budget = _int_param(params, "token_budget", 2000, 1, 100000)
                    pin = ((params.get("pin") or [""])[0] or "").strip().lower() in {"1", "true", "yes"}
                    pin_session_id = (params.get("session_id") or [None])[0] or None
                if output_format not in {"json", "markdown", "smp"}:
                    self._send_json({"detail": "format must be json, markdown, or smp"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                # 'smp' selects the self-describing SMP envelope (response_format), not a text
                # renderer; internal render format falls back to json. model=None + text is
                # byte-identical to the prior behavior.
                response_format = "smp" if output_format == "smp" else "text"
                internal_format = "json" if output_format == "smp" else output_format
                pack = store.assemble_context(
                    user_id,
                    task,
                    surface=surface,
                    token_budget=token_budget,
                    sector=sector,
                    project=project,
                    as_of=as_of,
                    intent=intent,
                    # Reaching /v1/context already required read scope; the identity layer is a read
                    # of distilled context, so it is always included.
                    include_identity=True,
                    format=internal_format,
                    pin=pin,
                    session_id=pin_session_id,
                    **_assemble_context_ext_kwargs(response_format, model),
                )
                if internal_format == "markdown":
                    self._send_text(pack, media_type="text/markdown")
                else:
                    self._send_json(pack)
                return
            if method == "GET" and path == "/v1/context/packs":
                self._send_json(store.list_context_packs(
                    user_id,
                    session_id=(params.get("session_id") or [None])[0] or None,
                    limit=_int_param(params, "limit", 20, 1, 100),
                ))
                return
            if method == "GET" and path.startswith("/v1/context/packs/"):
                try:
                    self._send_json(store.get_context_pack(user_id, unquote(path.rsplit("/", 1)[1])))
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if method == "POST" and path.startswith("/v1/context/packs/") and path.endswith("/verify"):
                # Phase 2b diagnostic: recompute with stored inputs and diff. POST because it
                # does work (a full assembly) and emits an audit event, unlike the pure reads.
                sha = unquote(path[len("/v1/context/packs/"):-len("/verify")])
                try:
                    self._send_json(store.verify_context_pack(user_id, sha))
                except PermissionError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if method == "POST" and path == "/v1/working-canvas/nodes":
                # M3: offload raw tool evidence into a receipted symbolic working-memory canvas
                # node (write scope, parity with the record_working_canvas_node MCP tool).
                # raw_text is never trimmed or truncated - the contract is byte-exact recovery,
                # so an oversized payload is rejected (422, parity with the FastAPI model).
                body = self._json_body()
                raw_text = str(body.get("raw_text") or "")
                if len(raw_text) > 200_000:
                    self._send_json({"detail": "raw_text exceeds 200000 characters"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                try:
                    self._send_json(store.record_working_canvas_node(
                        user_id,
                        session_id=str(body.get("session_id") or "")[:120],
                        node_id=str(body.get("node_id") or "")[:120],
                        label=str(body.get("label") or "")[:120],
                        summary=str(body.get("summary") or "")[:500],
                        raw_text=raw_text,
                        predecessor_node_id=(str(body.get("predecessor_node_id") or "")[:120] or None),
                    ))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path == "/v1/working-canvas":
                # M3: compact Mermaid canvas for an agent session, with verifiable node receipts.
                # max_chars caps the rendering (oldest nodes elide first, still drill-downable).
                try:
                    self._send_json(store.get_working_canvas(
                        user_id,
                        session_id=(params.get("session_id") or [""])[0],
                        limit=_int_param(params, "limit", 80, 1, 200),
                        max_chars=_int_param(params, "max_chars", 0, 0, 200000),
                    ))
                except (TypeError, ValueError) as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                return
            if method == "GET" and path.startswith("/v1/working-canvas/") and "/nodes/" in path:
                # M3 drill-down: unknown node -> 404; missing/tampered evidence -> 409 (the record
                # exists but its proof is broken, which callers must treat differently).
                remainder = path.removeprefix("/v1/working-canvas/")
                session_part, _, node_part = remainder.partition("/nodes/")
                include_raw = ((params.get("include_raw") or ["true"])[0] or "true").strip().lower() in {"1", "true", "yes"}
                try:
                    self._send_json(store.get_working_canvas_node(
                        user_id,
                        session_id=unquote(session_part),
                        node_id=unquote(node_part),
                        include_raw=include_raw,
                    ))
                except (TypeError, ValueError) as exc:
                    message = str(exc)
                    status = HTTPStatus.NOT_FOUND if "not found" in message else HTTPStatus.CONFLICT
                    self._send_json({"detail": message}, status=status)
                return
            if method == "GET" and path == "/v1/personal-profile":
                query = (params.get("query") or [""])[0]
                sector = (params.get("sector") or [None])[0]
                include_pending = (params.get("include_pending") or ["false"])[0].strip().lower() in {"1", "true", "yes"}
                profile = store.personal_profile(
                    user_id,
                    query=query,
                    limit=_int_param(params, "limit", 6, 1, 20),
                    include_pending=include_pending,
                    sector=sector,
                )
                if (params.get("format") or ["json"])[0] == "markdown":
                    self._send_text(profile["markdown"], media_type="text/markdown")
                else:
                    self._send_json(profile)
                return
            if method == "GET" and path == "/v1/agent-adaptation":
                query = (params.get("query") or [""])[0]
                sector = (params.get("sector") or [None])[0]
                target = (params.get("target") or ["assistant"])[0][:80]
                include_pending = (params.get("include_pending") or ["false"])[0].strip().lower() in {"1", "true", "yes"}
                adaptation = store.agent_adaptation(
                    user_id,
                    query=query,
                    target=target,
                    limit=_int_param(params, "limit", 8, 1, 20),
                    include_pending=include_pending,
                    sector=sector,
                )
                if (params.get("format") or ["json"])[0] == "markdown":
                    self._send_text(adaptation["markdown"], media_type="text/markdown")
                else:
                    self._send_json(adaptation)
                return
            if method == "DELETE" and path.startswith("/v1/memories/"):
                memory_id = unquote(path.removeprefix("/v1/memories/"))
                if not store.delete_memory(user_id, memory_id):
                    self._send_json({"detail": "Memory not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"deleted": True})
                return
            if method == "POST" and path.startswith("/v1/captures/") and path.endswith("/approve"):
                capture_id = unquote(path.removeprefix("/v1/captures/").removesuffix("/approve").strip("/"))
                if not store.approve_capture(user_id, capture_id):
                    self._send_json({"detail": "Capture not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"approved": True})
                return
            if method == "POST" and path.startswith("/v1/captures/") and path.endswith("/archive"):
                capture_id = unquote(path.removeprefix("/v1/captures/").removesuffix("/archive").strip("/"))
                if not store.archive_capture(user_id, capture_id):
                    self._send_json({"detail": "Capture not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"archived": True})
                return
            if method == "DELETE" and path.startswith("/v1/captures/"):
                capture_id = unquote(path.removeprefix("/v1/captures/"))
                if not store.delete_capture(user_id, capture_id):
                    self._send_json({"detail": "Capture not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json({"deleted": True})
                return
            if method == "GET" and path == "/v1/graph":
                self._send_json(store.graph(user_id, _int_param(params, "limit", 150, 10, 500)))
                return
            if method == "GET" and path.startswith("/v1/entity/") and path.endswith("/neighborhood"):
                entity_id = unquote(path[len("/v1/entity/"):-len("/neighborhood")])
                neighborhood = store.entity_neighborhood(user_id, entity_id, limit=_int_param(params, "limit", 8, 1, 24))
                if neighborhood is None:
                    self._send_json({"detail": "Entity not in graph"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(neighborhood)
                return
            if method == "GET" and path == "/v1/stats":
                self._send_json(store.stats(user_id))
                return
            if method == "GET" and path == "/v1/mirror":
                self._send_json({"insight": store.mirror_insight(user_id)})
                return
            if method == "POST" and path == "/v1/import-diff":
                # IMPORT-DIFF ("what the AIs think of you"): parse a vendor's SHORT memory export
                # and compare it against Cortex's own cited memory (confirmed / conflicting / stale
                # / missing per fact) plus what Cortex's Mirror knows that the vendor missed.
                # Read-only preview; nothing is imported. Cite-or-abstain: every confirmed/
                # conflicting/stale verdict carries a real Cortex memory id.
                from .import_diff import compare_vendor_export

                body = self._json_body()
                export = body.get("export")
                facts = body.get("facts")
                if export is None and facts is None:
                    self._send_json(
                        {"detail": "Provide either 'export' text/json or a pre-parsed 'facts' list."},
                        status=HTTPStatus.UNPROCESSABLE_ENTITY,
                    )
                    return
                if facts is not None and not isinstance(facts, list):
                    self._send_json({"detail": "'facts' must be a list."}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                self._send_json(
                    compare_vendor_export(
                        store,
                        user_id,
                        raw_export=export,
                        vendor_facts=facts,
                        vendor=str(body.get("vendor") or "")[:80],
                    )
                )
                return
            if method == "GET" and path == "/v1/person-map":
                include_pending = (params.get("include_pending") or ["false"])[0].strip().lower() in {"1", "true", "yes"}
                self._send_json(store.person_map(user_id, include_pending=include_pending, sector=(params.get("sector") or [None])[0]))
                return
            if method == "GET" and path == "/v1/profile":
                include_pending = (params.get("include_pending") or ["false"])[0].strip().lower() in {"1", "true", "yes"}
                self._send_json(
                    store.build_profile(
                        user_id,
                        limit=_int_param(params, "limit", 6, 1, 20),
                        include_pending=include_pending,
                        sector=(params.get("sector") or [None])[0],
                    )
                )
                return
            if method == "GET" and path == "/v1/memory/quality":
                self._send_json(store.memory_quality_report(user_id))
                return
            if method == "GET" and path == "/v1/memory/conflicts":
                self._send_json({"conflicts": store.detect_conflicts(user_id)})
                return
            if method == "POST" and path == "/v1/memory/conflicts/resolve":
                body = self._json_body()
                stale_id = str(body.get("stale_id") or "").strip()
                current_id = str(body.get("current_id") or "").strip()
                if not stale_id or not current_id:
                    self._send_json({"detail": "stale_id and current_id are required"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                elif store.resolve_conflict(user_id, stale_id=stale_id, current_id=current_id):
                    self._send_json({"resolved": True, "stale_id": stale_id, "current_id": current_id})
                else:
                    self._send_json({"detail": "Both memories must exist and differ"}, status=HTTPStatus.NOT_FOUND)
                return
            if method == "POST" and path == "/v1/memory/consolidate":
                body = self._json_body()
                raw_requests = body.get("hot_requests")
                hot_requests = [item for item in raw_requests if isinstance(item, dict)][:50] if isinstance(raw_requests, list) else []
                try:
                    auto_resolve_safe = _body_bool(body, "auto_resolve_safe", True)
                    max_conflicts = _body_int(body, "max_conflicts", 2000, 1, 10000)
                    max_hot_packs = _body_int(body, "max_hot_packs", 12, 0, 50)
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                self._send_json(store.run_memory_consolidation(
                    user_id,
                    hot_requests=hot_requests,
                    auto_resolve_safe=auto_resolve_safe,
                    max_conflicts=max_conflicts,
                    max_hot_packs=max_hot_packs,
                ))
                return
            if method == "GET" and path == "/v1/memory/consolidation":
                self._send_json(store.get_memory_consolidation(
                    user_id,
                    limit=_int_param(params, "limit", 10, 1, 100),
                ))
                return
            if method == "GET" and path == "/v1/settings":
                self._send_json(store.settings(user_id))
                return
            if method == "PUT" and path == "/v1/settings":
                self._send_json(store.update_settings(user_id, self._json_body()))
                return
            if method == "GET" and path == "/v1/trust/summary":
                self._send_json(store.trust_summary(user_id))
                return
            if method == "GET" and path == "/v1/privacy/lifecycle":
                self._send_json(store.data_lifecycle_report(user_id))
                return
            if method == "POST" and path == "/v1/integrations/mcp-token":
                body = self._json_body()
                self._send_json(store.ensure_mcp_token(
                    user_id,
                    str(body.get("token") or ""),
                    label=str(body.get("label") or "Local MCP integrations")[:120],
                    scopes=body.get("scopes"),
                ))
                return
            if method == "POST" and path == "/v1/pair":
                # Pair a browser extension (or any local client): generate a fresh read-scoped MCP
                # token and return the connection details. Auth already required maintenance scope,
                # so only the app (or a maintenance token) can pair — the extension receives the
                # minted token out-of-band. Read-only by default; the user can widen scope later.
                body = self._json_body()
                label = str(body.get("label") or "Browser extension")[:120]
                surface = str(body.get("surface") or "chat")[:40]
                new_token = "cxm_" + secrets.token_urlsafe(24)
                registered = store.ensure_mcp_token(user_id, new_token, label=label, scopes=["read"])
                base = settings.public_base_url.rstrip("/")
                self._send_json({
                    "token": new_token,
                    "base_url": base,
                    "mcp_endpoint": f"{base}/mcp",
                    "tools_schema_endpoint": f"{base}/v1/tools/schema",
                    "tools_call_endpoint": f"{base}/v1/tools/call",
                    "context_endpoint": f"{base}/v1/context",
                    "surface": surface,
                    "scopes": ["read"],
                    "token_id": registered.get("token_id") if isinstance(registered, dict) else None,
                })
                return
            if method == "POST" and path == "/v1/integrations/api-token":
                body = self._json_body()
                self._send_json(store.ensure_api_token(
                    user_id,
                    str(body.get("token") or ""),
                    label=str(body.get("label") or "REST API client")[:120],
                    scopes=body.get("scopes"),
                ))
                return
            if method == "GET" and path == "/v1/integrations/tokens":
                audience = (params.get("audience") or [None])[0]
                if audience not in {None, "api", "mcp"}:
                    self._send_json({"detail": "audience must be api or mcp"}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                include_revoked = (params.get("include_revoked") or ["false"])[0].strip().lower() in {"1", "true", "yes"}
                self._send_json({"results": store.list_tokens(user_id, audience=audience, include_revoked=include_revoked)})
                return
            if method == "DELETE" and path.startswith("/v1/integrations/tokens/"):
                token_id = unquote(path.rsplit("/", 1)[-1])
                revoked = store.revoke_token(user_id, token_id)
                if not revoked:
                    self._send_json({"detail": "Token not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(revoked)
                return
            if method == "GET" and path == "/v1/audit-log":
                self._send_json({"results": store.audit_log(user_id, _int_param(params, "limit", 80, 1, 300))})
                return
            if method == "GET" and path == "/v1/sync/changes":
                try:
                    shard = store.assignment_for(user_id).as_dict()
                except Exception:
                    shard = None
                payload = store.sync_change_feed(
                    user_id,
                    after=(params.get("after") or [""])[0][:120],
                    limit=_int_param(params, "limit", 100, 1, 1000),
                    device_id=(params.get("device_id") or [""])[0][:120],
                    signing_key=settings.sync_signing_key,
                    shard=shard,
                )
                self._send_json(payload)
                return
            if method == "GET" and path == "/v1/sync/captures":
                # Phase-2 local outbound feed: captures newer than a monotonic rowid cursor, WITH
                # content, for the desktop app to push to the signed-in user's hosted account.
                self._send_json(store.capture_change_page(
                    user_id,
                    _int_param(params, "after_seq", 0, 0, 2**63 - 1),
                    _int_param(params, "limit", 100, 1, 500),
                ))
                return
            if method == "POST" and path == "/v1/sync/ingest":
                # Phase-2 sync inbound (mirrors main.py): apply a batch of synced captures into the
                # local store. Idempotent — each item carries its stable capture id
                # (capture_id_override -> ON CONFLICT(id) DO UPDATE), so re-applying a batch is a
                # safe no-op upsert. This is the endpoint the desktop pull-sync worker applies
                # HOSTED pages to, so a second Mac converges on the signed-in account's memory.
                body = self._json_body()
                raw_items = body.get("items") if isinstance(body.get("items"), list) else []
                try:
                    if len(raw_items) > 500:
                        raise ValueError("items must contain at most 500 entries")
                    normalized_items: list[dict[str, Any]] = []
                    for raw in raw_items:
                        if not isinstance(raw, dict):
                            raise ValueError("each item must be an object")
                        client_capture_id = str(raw.get("client_capture_id") or "").strip()
                        if not client_capture_id or len(client_capture_id) > 80:
                            raise ValueError("client_capture_id is required (at most 80 characters)")
                        review_status = str(raw.get("review_status") or "").strip().lower() or None
                        # Trust passthrough is bounded to the two mirrorable review states.
                        if review_status is not None and review_status not in {"approved", "pending"}:
                            raise ValueError("review_status must be approved or pending")
                        # Zero-access (E2EE) blind relay (ADDITIVE, mirrors main.py): when the client
                        # sends ciphertext, `content` is optional and no plaintext exists server-side;
                        # otherwise this is today's plaintext path with the same content requirement.
                        raw_encrypted = raw.get("encrypted_payload")
                        ciphertext: bytes | None = None
                        enc_meta = None
                        if raw_encrypted is not None:
                            if not isinstance(raw_encrypted, str) or not raw_encrypted.strip():
                                raise ValueError("encrypted_payload must be non-empty base64 when present")
                            if len(raw_encrypted) > 2_000_000:
                                raise ValueError("encrypted_payload is too large")
                            try:
                                ciphertext = base64.b64decode(raw_encrypted, validate=True)
                            except (binascii.Error, ValueError):
                                raise ValueError("encrypted_payload must be valid base64")
                            if not ciphertext:
                                raise ValueError("encrypted_payload decodes to empty bytes")
                            raw_meta = raw.get("enc_meta")
                            if raw_meta is not None and not isinstance(raw_meta, dict):
                                raise ValueError("enc_meta must be an object")
                            # enc_meta is a tiny non-secret decrypt hint; bound it so it can't
                            # inflate the store / amplify pulls (mirrors the FastAPI validator).
                            if raw_meta is not None and len(json.dumps(raw_meta)) > 4096:
                                raise ValueError("enc_meta is too large")
                            enc_meta = raw_meta
                        content = str(raw.get("content") or "")
                        if ciphertext is None:
                            if not content.strip():
                                raise ValueError("content is required")
                            if len(content) > 200_000:
                                raise ValueError("content is too large")
                        normalized_items.append({
                            "client_capture_id": client_capture_id,
                            "content": content,
                            "source": str(raw.get("source") or "macos")[:80],
                            "source_url": str(raw.get("source_url") or "")[:500] or None,
                            "title": str(raw.get("title") or "")[:200] or None,
                            "captured_at": str(raw.get("captured_at") or "")[:40] or None,
                            "review_status": review_status,
                            "encrypted_payload": ciphertext,
                            "enc_meta": enc_meta,
                        })
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                aliases = store.settings(user_id).get("identity_aliases")
                results = []
                review_gated_sources: dict[str, bool] = {}
                for item in normalized_items:
                    # Anti-resurrection guard (Phase-2 Slice 3, mirrors main.py): never re-create a
                    # capture whose id was already forgotten (a sync_tombstone exists) — the deletion
                    # is authoritative and the tombstone re-propagates it. Skip the out-of-order create.
                    if store.is_tombstoned(user_id, "capture", item["client_capture_id"]):
                        results.append({
                            "client_capture_id": item["client_capture_id"],
                            "capture_id": "",
                            "status": "tombstoned",
                        })
                        continue
                    # Zero-access (E2EE) blind-relay branch (ADDITIVE, mirrors main.py): store the
                    # client's ciphertext opaquely, derive NO memory/task/entity/embedding, apply the
                    # same approved/pending passthrough gate. Absent ciphertext -> plaintext path below.
                    if item["encrypted_payload"] is not None:
                        auto_approve = settings.auto_approve_captures
                        force_review = False
                        if item["review_status"] == "approved":
                            source_gated = review_gated_sources.get(item["source"])
                            if source_gated is None:
                                source_gated = store.source_policy_requires_review(user_id, item["source"])
                                review_gated_sources[item["source"]] = source_gated
                            if source_gated:
                                force_review = True  # per-source policy wins over the passthrough
                            else:
                                auto_approve = True
                        elif item["review_status"] == "pending":
                            force_review = True
                        saved = store.save_encrypted_capture(
                            user_id=user_id,
                            client_capture_id=item["client_capture_id"],
                            encrypted_payload=item["encrypted_payload"],
                            enc_meta=item["enc_meta"],
                            source=item["source"],
                            captured_at=item["captured_at"],
                            auto_approve=auto_approve,
                            force_review=force_review,
                        )
                        results.append({
                            "client_capture_id": item["client_capture_id"],
                            "capture_id": saved.get("capture_id", ""),
                            "status": "accepted",
                        })
                        continue
                    extracted = extract_context(item["content"], item["source"], author_aliases=aliases)
                    if item["captured_at"]:
                        extracted["_timestamp"] = item["captured_at"]
                    # Trust passthrough (pull sync), same rules as main.py's /v1/sync/ingest:
                    # "approved" mirrors a review decision another device already granted;
                    # "pending" pins the origin's not-yet-approved state. SECURITY: a per-source
                    # review policy on THIS store wins over the passthrough, so a connector can
                    # never use it to bypass review on first ingest; and save_capture preserves
                    # the existing local decision for unchanged content, so an echoed re-apply
                    # can never escalate it. Absent -> today's normal review flow.
                    auto_approve = settings.auto_approve_captures
                    force_review = False
                    if item["review_status"] == "approved":
                        source_gated = review_gated_sources.get(item["source"])
                        if source_gated is None:
                            source_gated = store.source_policy_requires_review(user_id, item["source"])
                            review_gated_sources[item["source"]] = source_gated
                        if source_gated:
                            force_review = True  # per-source policy wins over the passthrough
                        else:
                            auto_approve = True
                    elif item["review_status"] == "pending":
                        force_review = True
                    saved = store.save_capture(
                        user_id=user_id,
                        content=item["content"],
                        source=item["source"],
                        source_url=item["source_url"],
                        title=item["title"],
                        extracted=extracted,
                        capture_id_override=item["client_capture_id"],
                        cite_capture_provenance=True,
                        auto_approve=auto_approve,
                        force_review=force_review,
                    )
                    results.append({
                        "client_capture_id": item["client_capture_id"],
                        "capture_id": saved.get("capture_id", ""),
                        "status": "accepted",
                    })
                device_id = str(body.get("device_id") or "").strip()[:80]
                cursor = str(body.get("cursor") or "").strip()[:160]
                if device_id and cursor:
                    try:
                        store.record_sync_receipt(
                            user_id, device_id, cursor=cursor,
                            status="accepted", stats={"count": len(results)},
                        )
                    except Exception:
                        pass  # best-effort high-watermark; a missing/revoked device must not fail ingest
                self._send_json({"applied": len(results), "cursor": cursor, "results": results})
                return
            if method == "GET" and path == "/v1/sync/deletions":
                # Phase-2 outbound DELETIONS feed (mirrors main.py): tombstones newer than a monotonic
                # seq cursor, CONTENT-FREE (just ids). A DELETE never bumps captures.rowid, so
                # deletions ride their own feed for the desktop app to push to / pull from hosted.
                self._send_json(store.capture_tombstone_page(
                    user_id,
                    _int_param(params, "after_seq", 0, 0, 2**63 - 1),
                    _int_param(params, "limit", 100, 1, 500),
                ))
                return
            if method == "POST" and path == "/v1/sync/deletions":
                # Phase-2 inbound DELETIONS apply (mirrors main.py): delete each named capture/memory
                # + derivatives via the SAME safe primitive the local forget path uses, and record a
                # local tombstone so a re-pull can't resurrect it and the delete propagates onward.
                # Idempotent (deleting an absent id is a no-op success), per-user.
                body = self._json_body()
                raw_items = body.get("items") if isinstance(body.get("items"), list) else []
                try:
                    if len(raw_items) > 500:
                        raise ValueError("items must contain at most 500 entries")
                    normalized_items: list[dict[str, Any]] = []
                    for raw in raw_items:
                        if not isinstance(raw, dict):
                            raise ValueError("each item must be an object")
                        object_type = str(raw.get("object_type") or "").strip()
                        if object_type not in {"capture", "memory"}:
                            raise ValueError("object_type must be capture or memory")
                        object_id = str(raw.get("object_id") or "").strip()
                        if not object_id or len(object_id) > 80:
                            raise ValueError("object_id is required (at most 80 characters)")
                        normalized_items.append({"object_type": object_type, "object_id": object_id})
                except ValueError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
                    return
                outcome = store.apply_sync_deletions(user_id, normalized_items)
                device_id = str(body.get("device_id") or "").strip()[:80]
                cursor = str(body.get("cursor") or "").strip()[:160]
                if device_id and cursor:
                    try:
                        store.record_sync_receipt(
                            user_id, device_id, cursor=cursor,
                            status="accepted", stats={"deletions": outcome["applied"]},
                        )
                    except Exception:
                        pass  # best-effort high-watermark; a missing/revoked device must not fail apply
                self._send_json({"applied": outcome["applied"], "cursor": cursor, "results": outcome["results"]})
                return
            if method == "GET" and path == "/v1/diagnostics":
                self._send_json(store.diagnostics(user_id))
                return
            if method == "GET" and path == "/v1/reliability/report":
                self._send_json(store.reliability_report(user_id))
                return
            if method == "GET" and path == "/v1/support/bundle":
                self._send_json(store.support_bundle(user_id))
                return
            if method == "POST" and path == "/v1/backups":
                self._send_json(store.create_backup(user_id))
                return
            if method == "POST" and path == "/v1/backups/restore-latest":
                try:
                    self._send_json(store.restore_latest_backup(user_id))
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
                return
            if method == "DELETE" and path == "/v1/backups":
                self._send_json(store.delete_backups(user_id))
                return
            if method == "DELETE" and path == "/v1/user-data":
                include_backups = (params.get("include_backups") or ["true"])[0].strip().lower() not in {"0", "false", "no"}
                self._send_json(store.delete_user_data(user_id, include_backups=include_backups))
                return
            if method == "POST" and path == "/v1/maintenance/repair-storage":
                self._send_json(store.repair_storage(user_id))
                return
            if method == "POST" and path == "/v1/maintenance/rebuild-search":
                self._send_json(store.rebuild_search_index(user_id))
                return
            if method == "POST" and path == "/v1/maintenance/rebuild-vectors":
                self._send_json(store.rebuild_vectors(user_id))
                return
            if method == "POST" and path == "/v1/maintenance/rebuild-index-from-vault":
                self._send_json(store.rebuild_index_from_vault(user_id))
                return
            if method == "POST" and path == "/v1/vault/reconcile":
                self._send_json(store.reconcile_vault_edits(user_id))
                return
            if method == "GET" and path == "/v1/export.json":
                self._send_json(store.export_json(user_id))
                return
            if method == "GET" and path == "/v1/export.md":
                self._send_text(store.export_markdown(user_id), media_type="text/markdown")
                return
            if method == "GET" and path == "/v1/integrity/digest":
                # Phase D: tamper-evident hash-chain head over the event log (read-only).
                self._send_json(store.integrity_digest(user_id))
                return
            if method == "GET" and path == "/v1/export/manifest":
                # Phase D: verifiable manifest (head + counts + payload sha256) for the export.
                self._send_json(store.export_manifest(user_id))
                return
            if method == "GET" and path == "/v1/export/bundle":
                # Phase D: whole memory as one self-verifying, restorable object (export scope).
                self._send_json(store.export_portable_bundle(user_id))
                return
            self._send_json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except _RequestTooLarge as exc:
            # The body was not read (rejected on Content-Length), so close the connection to avoid
            # a keep-alive protocol desync from leftover unread bytes.
            self.close_connection = True
            self._send_json({"detail": str(exc)}, status=HTTPStatus.REQUEST_ENTITY_TOO_LARGE)
        except _BadRequestBody as exc:
            # Malformed body escaped a per-endpoint handler (many read the body before their own
            # try): return 422 like the framework server, not a 500.
            self._send_json({"detail": str(exc)}, status=HTTPStatus.UNPROCESSABLE_ENTITY)
        except Exception as exc:
            self._send_json({"detail": self._safe_error_message(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_mcp(self, context: dict) -> None:
        user_id = context["user_id"]
        token_scopes = None if context.get("admin") else list(context.get("scopes") or [])
        request = self._json_body()
        if isinstance(request, list):
            # JSON-RPC batch arrays are not part of MCP; reject cleanly instead of tracebacking.
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch not supported"}})
            return
        if not isinstance(request, dict):
            self._send_json({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request must be a JSON-RPC object"}})
            return
        method = request.get("method")
        if request.get("id") is None or (isinstance(method, str) and method.startswith("notifications/")):
            # JSON-RPC notification (no id, e.g. notifications/initialized): remote clients POST
            # these directly; per the MCP streamable HTTP spec, accept with 202 and no body.
            self._send_bytes(b"", status=HTTPStatus.ACCEPTED, media_type="application/json")
            return
        try:
            if method == "initialize":
                params = request.get("params") if isinstance(request.get("params"), dict) else {}
                requested_version = params.get("protocolVersion")
                result = {
                    "protocolVersion": requested_version if requested_version in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0],
                    "serverInfo": {"name": "cortex", "version": BACKEND_VERSION},
                    "capabilities": {"tools": {"listChanged": False}, "resources": {"listChanged": False, "subscribe": False}, "prompts": {"listChanged": False}},
                }
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                # Spec params such as cursor are tolerated (ignored): the full list is one page,
                # so no nextCursor is ever returned.
                result = {"tools": tools_for_scopes(token_scopes, surface=settings.mcp_tool_surface)}
            elif method == "tools/call":
                params = request.get("params") or {}
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {}) or {}
                try:
                    value = call_tool(store, user_id, tool_name, arguments, token_scopes=token_scopes)
                    store.record_agent_event(user_id, tool_name, arguments, success=True, token=context, result=value)
                except Exception as exc:
                    store.record_agent_event(user_id, tool_name, arguments, success=False, error=str(exc), token=context)
                    raise
                result = tool_call_result(value)
            elif method == "resources/list":
                result = {"resources": list_resources(), "resourceTemplates": list_resource_templates()}
            elif method == "resources/read":
                params = request.get("params") or {}
                uri = str(params.get("uri") or "")
                try:
                    result = read_resource(store, user_id, uri, token_scopes=token_scopes)
                    store.record_agent_event(user_id, f"resource:{uri}", {}, success=True, token=context)
                except Exception as exc:
                    store.record_agent_event(user_id, f"resource:{uri}", {}, success=False, error=str(exc), token=context)
                    raise
            elif method == "prompts/list":
                result = {"prompts": list_prompts()}
            elif method == "prompts/get":
                params = request.get("params") or {}
                prompt_name = str(params.get("name") or "")
                prompt_args = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
                try:
                    result = get_prompt(store, user_id, prompt_name, prompt_args, token_scopes=token_scopes)
                    store.record_agent_event(user_id, f"prompt:{prompt_name}", {}, success=True, token=context)
                except Exception as exc:
                    store.record_agent_event(user_id, f"prompt:{prompt_name}", {}, success=False, error=str(exc), token=context)
                    raise
            else:
                self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32601, "message": f"Method not found: {method}"}})
                return
            self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        except Exception as exc:
            self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": self._safe_error_message(exc)}})

    def _bearer_has_export_scope(self) -> bool:
        """Whether the (already-authenticated) bearer may see the identity/persona layer of a
        context pack. The admin app token always may; scoped API tokens need the export scope.
        Read-only callers still get a pack — identity degrades to a visible omission record."""
        authorization = self.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            return False
        token = authorization.split(" ", 1)[1].strip()
        if settings.api_key and hmac.compare_digest(token, settings.api_key):
            return True
        try:
            scoped = store.authenticate_api_token(token)
        except TypeError:
            scoped = None
        return bool(scoped and "export" in set(scoped.get("scopes") or []))

    def _auth_user(self, method: str, path: str) -> str | None:
        authorization = self.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            self._send_json({"detail": f"Missing or invalid {APP_BRAND} API token"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        token = authorization.split(" ", 1)[1].strip()
        if settings.api_key:
            if hmac.compare_digest(token, settings.api_key):
                requested_user = self.headers.get("X-Cortex-User")
                if requested_user and requested_user != settings.default_user_id and settings.shard_mode != "local":
                    self._send_json({"detail": f"Global {APP_BRAND} API token cannot select another user in sharded mode; use a scoped user token"}, status=HTTPStatus.FORBIDDEN)
                    return None
                if requested_user and requested_user != settings.default_user_id and settings.require_scoped_api_tokens:
                    self._send_json({"detail": f"Global {APP_BRAND} API token cannot select another user when scoped API tokens are required"}, status=HTTPStatus.FORBIDDEN)
                    return None
                return requested_user or settings.default_user_id
        try:
            scoped = store.authenticate_api_token(token, user_id=self.headers.get("X-Cortex-User"))
        except TypeError:
            scoped = store.authenticate_api_token(token)
        if scoped:
            requested_user = self.headers.get("X-Cortex-User")
            if requested_user and scoped["user_id"] != requested_user:
                self._send_json({"detail": f"{APP_BRAND} API token does not match requested user"}, status=HTTPStatus.FORBIDDEN)
                return None
            required_scope = _required_api_scope(method, path)
            if not _api_token_has_scope(scoped, required_scope):
                self._send_json({"detail": f"{APP_BRAND} API token requires {required_scope} scope"}, status=HTTPStatus.FORBIDDEN)
                return None
            try:
                _require_api_token_trust(scoped["user_id"], required_scope)
            except PermissionError as exc:
                self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return None
            return scoped["user_id"]
        self._send_json({"detail": f"Missing or invalid {APP_BRAND} API token"}, status=HTTPStatus.UNAUTHORIZED)
        return None

    def _auth_mcp(self) -> dict | None:
        authorization = self.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            self._send_json({"detail": f"Missing or invalid {APP_BRAND} MCP token"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        token = authorization.split(" ", 1)[1].strip()
        if settings.api_key and hmac.compare_digest(token, settings.api_key):
            requested_user = self.headers.get("X-Cortex-User")
            if requested_user and requested_user != settings.default_user_id and settings.shard_mode != "local":
                self._send_json({"detail": f"Global {APP_BRAND} API token cannot select another user in sharded mode; use a scoped user token"}, status=HTTPStatus.FORBIDDEN)
                return None
            if requested_user and requested_user != settings.default_user_id and settings.require_scoped_api_tokens:
                self._send_json({"detail": f"Global {APP_BRAND} API token cannot select another user when scoped API tokens are required"}, status=HTTPStatus.FORBIDDEN)
                return None
            return {
                "user_id": requested_user or settings.default_user_id,
                "token_id": "admin",
                "label": f"{APP_BRAND} app token",
                "audience": "admin",
                "scopes": ["read", "write", "export", "maintenance", "destructive"],
                "admin": True,
            }
        try:
            scoped = store.authenticate_mcp_token(token, user_id=self.headers.get("X-Cortex-User"))
        except TypeError:
            scoped = store.authenticate_mcp_token(token)
        if scoped:
            return scoped
        self._send_json({"detail": f"Missing or invalid {APP_BRAND} MCP token"}, status=HTTPStatus.UNAUTHORIZED)
        return None

    def _auth_token(self, token: str | None, *, required_scope: str = "write") -> str | None:
        normalized = (token or "").strip()
        if settings.api_key and hmac.compare_digest(normalized, settings.api_key):
            return settings.default_user_id
        if settings.require_scoped_api_tokens:
            try:
                scoped = store.authenticate_api_token(normalized)
            except TypeError:
                scoped = None
            if scoped:
                if not _api_token_has_scope(scoped, required_scope):
                    return None
                _require_api_token_trust(scoped["user_id"], required_scope)
                return scoped["user_id"]
        # Fail closed whenever auth is required (global key, scoped-token mode, or non-local
        # shard mode); only genuine local single-user dev falls through to the default user.
        if settings.api_key or settings.require_scoped_api_tokens or settings.shard_mode != "local":
            return None
        return settings.default_user_id

    def _save_capture(self, user_id: str, content: str, source: str, title: str | None, source_url: str | None) -> dict:
        content = str(content or "").strip()
        if not content:
            raise ValueError("content is required")
        if len(content) > 200_000:
            raise ValueError("content is too large")
        normalized_source = str(source or "browser-capture")[:80]
        extracted = extract_context(content, normalized_source, author_aliases=store.settings(user_id).get("identity_aliases"))
        return store.save_capture(
            user_id=user_id,
            content=content,
            source=normalized_source,
            source_url=str(source_url or "")[:500] or None,
            title=str(title or "")[:200] or None,
            extracted=extracted,
            cite_capture_provenance=True,
            auto_approve=settings.auto_approve_captures,
        )

    def _read_body(self) -> bytes:
        try:
            length = int(self.headers.get("Content-Length", "0") or "0")
        except (TypeError, ValueError):
            length = 0
        if length <= 0:
            return b""
        if length > MAX_REQUEST_BODY_BYTES:
            raise _RequestTooLarge("Request body is too large")
        return self.rfile.read(length)

    def _json_body(self) -> dict:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise _BadRequestBody("Request body must be valid JSON") from exc

    def _form_body(self) -> dict[str, list[str]]:
        raw = self._read_body()
        if not raw:
            return {}
        try:
            return parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError as exc:
            raise _BadRequestBody("Request body must be valid form data") from exc

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK, retry_after: int | None = None) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, status=status, media_type="application/json", retry_after=retry_after)

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK, media_type: str = "text/plain") -> None:
        self._send_bytes(text.encode("utf-8"), status=status, media_type=f"{media_type}; charset=utf-8")

    def _send_bytes(self, data: bytes, status: HTTPStatus = HTTPStatus.OK, media_type: str = "application/octet-stream", retry_after: int | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        if retry_after is not None:
            self.send_header("Retry-After", str(retry_after))
        self.send_header("Content-Length", str(len(data)))
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and _cors_origin_allowed(origin):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Cortex-User")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)


def serve(host: str = "127.0.0.1", port: int | None = None) -> None:
    resolved_port = port or int(os.environ.get("CORTEX_PORT", "8766"))
    server = ThreadingHTTPServer((host, resolved_port), CortexRequestHandler)
    worker_thread = _start_standalone_worker()
    if worker_thread is not None:
        print(f"{APP_BRAND} standalone worker running for queued memory jobs", flush=True)
    print(f"{APP_BRAND} standalone backend running on http://{host}:{resolved_port}", flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Run the dependency-light {APP_BRAND} local backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
