from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone
import html
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from typing import Any
from urllib.parse import parse_qs, urlencode

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from .accounts import iso_utc, utc_now
from .authn import (
    ACCESS_TOKEN_PREFIX as SESSION_TOKEN_PREFIX,
    GENERIC_AUTH_FAILURE,
    AccountsService,
    AuthError,
    RateLimited,
    token_verify_hash,
)
from .config import load_settings
from .extractor import extract_context
from .hosted_readiness import hosted_readiness_contract
from .mcp_tools import CORE_TOOL_NAMES, TOOLS, call_tool, tool_call_result, tools_for_scopes
from .observability import metrics, route_label
from .models import AgentSessionsSyncRequest, AgentSessionsSyncResponse, APITokenListResponse, APITokenRegistrationRequest, APITokenRegistrationResponse, APITokenRevokeResponse, AskResponse, BackupResponse, CalendarSyncRequest, CalendarSyncResponse, CaptureRequest, CaptureResponse, ContextReuseRequest, ContextReuseResponse, DataLifecycleReportResponse, DiagnosticsResponse, GitHubRepositoryDiscoveryRequest, GitHubRepositoryDiscoveryResponse, GitHubSyncRequest, GitHubSyncResponse, GmailSyncRequest, GmailSyncResponse, GoogleDriveSyncRequest, GoogleDriveSyncResponse, GoogleOAuthCompleteRequest, GoogleOAuthCompleteResponse, GoogleOAuthStartRequest, GoogleOAuthStartResponse, GraphResponse, JiraSyncRequest, JiraSyncResponse, JobRunResponse, LinearSyncRequest, LinearSyncResponse, ListResponse, MaintenanceResponse, ManagedOAuthCompleteRequest, ManagedOAuthCompleteResponse, ManagedOAuthStartRequest, ManagedOAuthStartResponse, MCPTokenRegistrationRequest, MCPTokenRegistrationResponse, MemoryQualityResponse, NotionSyncRequest, NotionSyncResponse, ObsidianVaultSyncRequest, ObsidianVaultSyncResponse, OutlookSyncRequest, OutlookSyncResponse, ProductLoopResponse, QueuedCaptureResponse, RaindropSyncRequest, RaindropSyncResponse, ReadwiseSyncRequest, ReadwiseSyncResponse, ReliabilityReportResponse, RepairStorageResponse, SearchResponse, SettingsResponse, SettingsUpdateRequest, SlackChannelDiscoveryRequest, SlackChannelDiscoveryResponse, SlackSyncRequest, SlackSyncResponse, SourceAccountListResponse, SourceAccountRequest, SourceAccountResponse, SourceAccountSyncRequest, SourceAccountSyncResponse, SourceAnalyzeRequest, SourceAnalyzeResponse, SourceImportDeleteResponse, SourceImportRequest, SourceImportResponse, SourceReadinessResponse, StatsResponse, SupportBundleResponse, SyncChangeFeedResponse, SyncCursorListResponse, SyncCursorRequest, SyncCursorResponse, SyncDeviceListResponse, SyncDeviceRequest, SyncDeviceResponse, SyncReceiptListResponse, SyncReceiptRequest, SyncReceiptResponse, VaultRebuildResponse, VectorRebuildResponse, ZoteroSyncRequest, ZoteroSyncResponse
from .models import UserListResponse, UserProvisionRequest, UserProvisionResponse, UserStatusResponse
from .models import CaptureChangePage, SyncIngestRequest, SyncIngestResponse
from .models import GradeAnswerRequest, WouldIRequest, DraftAsMeRequest, GradeTwinPredictionRequest
from .oauth_broker import register_oauth_broker_routes
from .oidc_registry import OidcError, OidcProviderRegistry
from .ratelimit import TokenBucketRateLimiter
from .sharding import StoreRegistry
from .storage import BACKEND_VERSION
from .webauth import (
    FAVICON_SVG,
    PUBLIC_PAGE_CSP,
    register_web_account_routes,
    render_public_page,
)


settings = load_settings()
metrics.configure(settings.observability_enabled)
store = StoreRegistry.from_settings(settings)
rate_limiter = TokenBucketRateLimiter(settings.rate_limit_per_minute)
store.ensure_vault_backfilled(settings.default_user_id)
if settings.mcp_api_key:
    store.ensure_mcp_token(
        settings.default_user_id,
        settings.mcp_api_key,
        label="Local MCP integrations",
        scopes=settings.mcp_api_key_scopes or None,
        token_id="tok_local_mcp",
    )

app = FastAPI(title="Cortex API", version="0.1.0")


def _cors_origins() -> list[str]:
    configured = [item.strip() for item in os.environ.get("CORTEX_CORS_ORIGINS", "").split(",") if item.strip()]
    if configured:
        return configured
    public_base = settings.public_base_url.rstrip("/")
    origins = {
        public_base,
        "http://127.0.0.1:8766",
        "http://localhost:8766",
    }
    return sorted(origin for origin in origins if origin)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def _observability_middleware(request: Request, call_next):
    # No-op (single early return) unless CORTEX_OBSERVABILITY_ENABLED is set.
    if not metrics.enabled:
        return await call_next(request)
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        duration_ms = (time.perf_counter() - started) * 1000.0
        metrics.incr("http_requests_total", method=request.method, status="500")
        metrics.incr("http_request_errors_total", method=request.method)
        metrics.observe("http_request_duration_ms", duration_ms, method=request.method)
        metrics.log_event(
            "http_request_error", method=request.method, path=request.url.path, duration_ms=round(duration_ms, 2)
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000.0
    status = str(response.status_code)
    metrics.incr("http_requests_total", method=request.method, status=status)
    metrics.observe("http_request_duration_ms", duration_ms, method=request.method)
    if response.status_code >= 500:
        metrics.incr("http_request_errors_total", method=request.method)
    metrics.log_event(
        "http_request",
        method=request.method,
        route=route_label(request),
        status=response.status_code,
        duration_ms=round(duration_ms, 2),
    )
    return response

GOOGLE_OAUTH_PENDING_TTL = timedelta(minutes=10)


def _remember_google_oauth_pending(user_id: str, request: GoogleOAuthStartRequest, started: dict[str, Any]) -> None:
    state = str(started.get("state") or "").strip()
    if not state:
        return
    store.remember_oauth_pending(
        state=state,
        user_id=user_id,
        flow="google",
        ttl_seconds=int(GOOGLE_OAUTH_PENDING_TTL.total_seconds()),
        payload={
            "source": started.get("source") or request.source,
            "redirect_uri": started.get("redirect_uri") or request.redirect_uri,
            "client_id": request.client_id,
            "client_secret": request.client_secret,
            "token_endpoint": request.token_endpoint,
            "code_verifier": request.code_verifier,
            "source_account_id": request.source_account_id,
            "account_label": request.account_label,
            "account_identifier": request.account_identifier,
            "query": request.query,
            "label_ids": request.label_ids,
            "mime_types": request.mime_types,
            "include_body": request.include_body,
            "include_content": request.include_content,
        },
    )


def _pop_google_oauth_pending(state: str) -> dict[str, Any] | None:
    return store.pop_oauth_pending(state or "", flow="google")


def _remember_managed_oauth_pending(user_id: str, request: ManagedOAuthStartRequest, started: dict[str, Any]) -> None:
    state = str(started.get("state") or "").strip()
    if not state:
        return
    store.remember_oauth_pending(
        state=state,
        user_id=user_id,
        flow="managed",
        ttl_seconds=int(GOOGLE_OAUTH_PENDING_TTL.total_seconds()),
        payload={
            "source": started.get("source") or request.source,
            "redirect_uri": started.get("redirect_uri") or request.redirect_uri,
            "client_id": request.client_id,
            "client_secret": request.client_secret,
            "token_endpoint": request.token_endpoint,
            "source_account_id": request.source_account_id,
            "account_label": request.account_label,
            "account_identifier": request.account_identifier,
            "include_content": request.include_content,
            "api_base_url": request.api_base_url,
            "notion_version": request.notion_version,
        },
    )


def _pop_managed_oauth_pending(state: str) -> dict[str, Any] | None:
    return store.pop_oauth_pending(state or "", flow="managed")


def _google_oauth_callback_page(title: str, detail: str, *, success: bool) -> HTMLResponse:
    icon = "checkmark.circle.fill" if success else "exclamationmark.triangle.fill"
    color = "#136f45" if success else "#9a3412"
    safe_title = html.escape(title)
    safe_detail = html.escape(detail)
    safe_icon = html.escape(icon)
    content = f"""<!doctype html>
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
    .icon {{ color: {color}; font-size: 28px; font-weight: 700; margin-bottom: 12px; }}
    h1 {{ margin: 0 0 8px; font-size: 24px; letter-spacing: 0; }}
    p {{ margin: 0; color: #53606f; font-size: 15px; line-height: 1.5; }}
  </style>
</head>
<body>
  <main>
    <div class="icon">{safe_icon}</div>
    <h1>{safe_title}</h1>
    <p>{safe_detail}</p>
  </main>
</body>
</html>"""
    return HTMLResponse(content=content, status_code=200 if success else 422)


def _required_api_scope(method: str, path: str) -> str:
    normalized_method = method.upper()
    normalized_path = path.rstrip("/") or "/"
    # Raw bulk dumps of the corpus stay export-gated. The DISTILLED profile / adaptation / context
    # pack are reads — that is the holistic picture Cortex exists to hand an agent.
    if normalized_path in {"/v1/export.json", "/v1/export.md", "/v1/support/bundle"}:
        return "export"
    # Obsidian write-back persists distilled memory into user-owned vault files (egress out of
    # Cortex custody) — export-scoped like the bulk exports, parity with the MCP tool.
    if normalized_path == "/v1/connectors/obsidian/write-back":
        return "export"
    if normalized_path in {"/v1/context", "/v1/context-pack", "/v1/personal-profile", "/v1/agent-adaptation"}:
        return "read"
    if normalized_path == "/v1/settings" and normalized_method in {"PUT", "PATCH"}:
        return "maintenance"
    if normalized_path in {"/v1/diagnostics", "/v1/reliability/report", "/v1/jobs/health"}:
        return "maintenance"
    # Recompute-verify is a maintenance diagnostic (same scope as the MCP tool): it re-runs
    # the context engine and writes an audit event, beyond what plain read tokens are for.
    if normalized_path.startswith("/v1/context/packs/") and normalized_path.endswith("/verify") and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/maintenance/") or normalized_path in {"/v1/jobs/run", "/v1/maintenance/jobs/run", "/v1/sources/sync-due"}:
        return "maintenance"
    if normalized_path in {"/v1/integrations/api-token", "/v1/integrations/mcp-token", "/v1/integrations/tokens"}:
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


def _api_token_has_scope(scoped: dict[str, Any], required_scope: str) -> bool:
    scopes = set(scoped.get("scopes") or [])
    return required_scope in scopes


def _assert_api_token_scope(scoped: dict[str, Any], required_scope: str) -> None:
    if not _api_token_has_scope(scoped, required_scope):
        raise HTTPException(status_code=403, detail=f"Cortex API token requires {required_scope} scope")


def _assert_api_token_trust(user_id: str, required_scope: str) -> None:
    try:
        store.require_agent_access(user_id, required_scope)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc


def _global_token_user_id(x_cortex_user: str | None) -> str:
    requested_user = (x_cortex_user or "").strip()
    if requested_user and requested_user != settings.default_user_id and settings.shard_mode != "local":
        raise HTTPException(
            status_code=403,
            detail="Global Cortex API token cannot select another user in sharded mode; use a scoped user token",
        )
    if requested_user and requested_user != settings.default_user_id and settings.require_scoped_api_tokens:
        raise HTTPException(
            status_code=403,
            detail="Global Cortex API token cannot select another user when scoped API tokens are required",
        )
    return requested_user or settings.default_user_id


def _hosted_readiness_contract() -> dict[str, Any]:
    runtime: dict[str, Any] = {}
    runtime_storage_status = getattr(store, "runtime_storage_status", None)
    if callable(runtime_storage_status):
        runtime["storage"] = runtime_storage_status()
    if settings.shard_mode != "local":
        runtime["control_plane"] = store.control_plane_status()
        runtime["worker_queue"] = store.hosted_job_health()
    return hosted_readiness_contract(settings, runtime=runtime)


def _enforce_rate_limit(user_id: str) -> None:
    allowed, retry_after = rate_limiter.check(user_id)
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded for this user; slow down and retry.",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )


def _user_plan(user_id: str) -> str:
    """The caller's billing plan from the control registry (falls back to '' so
    quota_for_plan uses default_memory_quota). Cheap: one indexed lookup, only
    called on write paths that already touch the store."""
    getter = getattr(store, "get_user", None)
    if not callable(getter):
        return ""
    try:
        user = getter(user_id)
    except Exception:
        return ""
    return str((user or {}).get("plan") or "") if user else ""


def _quota_for_user(user_id: str) -> int:
    """Per-user memory quota (0 = unlimited). Plan-aware when a plan is present,
    else the flat CORTEX_DEFAULT_MEMORY_QUOTA — so a no-billing deployment keeps
    exactly today's behavior."""
    return settings.quota_for_plan(_user_plan(user_id))


def _enforce_memory_quota(user_id: str) -> None:
    quota = _quota_for_user(user_id)
    if quota <= 0:
        return
    if store.active_memory_count(user_id) >= quota:
        raise HTTPException(
            status_code=429,
            detail="Memory quota reached for this account; remove memories or raise the quota to add more.",
        )


def auth(request: Request, authorization: str | None = Header(default=None), x_cortex_user: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex API token")
    token = authorization.split(" ", 1)[1].strip()
    if settings.api_key:
        if hmac.compare_digest(token, settings.api_key):
            return _global_token_user_id(x_cortex_user)
    if token.startswith(SESSION_TOKEN_PREFIX) and auth_runtime is not None:
        # Interactive session access token (accounts plane): resolves to the
        # account's user_id with full user scopes but NEVER admin — admin_auth
        # and mcp_auth do not accept cxs_ tokens. When auth is disabled the
        # prefix falls through to the scoped path and fails 401 exactly as today.
        return _session_data_plane_user(token, x_cortex_user)
    scoped = store.authenticate_api_token(token, user_id=x_cortex_user)
    if scoped:
        if x_cortex_user and scoped["user_id"] != x_cortex_user:
            raise HTTPException(status_code=403, detail="Cortex API token does not match requested user")
        required_scope = _required_api_scope(request.method, request.url.path)
        _assert_api_token_scope(scoped, required_scope)
        _assert_api_token_trust(scoped["user_id"], required_scope)
        _enforce_rate_limit(scoped["user_id"])
        return scoped["user_id"]
    raise HTTPException(status_code=401, detail="Missing or invalid Cortex API token")


def mcp_auth(authorization: str | None = Header(default=None), x_cortex_user: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex MCP token")
    token = authorization.split(" ", 1)[1].strip()
    if settings.api_key and hmac.compare_digest(token, settings.api_key):
        user_id = _global_token_user_id(x_cortex_user)
        return {
            "user_id": user_id,
            "token_id": "admin",
            "label": "Cortex app token",
            "audience": "admin",
            "scopes": ["read", "write", "export", "maintenance", "destructive"],
            "admin": True,
        }
    scoped = store.authenticate_mcp_token(token, user_id=x_cortex_user)
    if scoped:
        if x_cortex_user and scoped["user_id"] != x_cortex_user:
            raise HTTPException(status_code=403, detail="Cortex MCP token does not match requested user")
        _enforce_rate_limit(scoped["user_id"])
        return scoped
    raise HTTPException(status_code=401, detail="Missing or invalid Cortex MCP token")


def admin_auth(authorization: str | None = Header(default=None)) -> bool:
    """Gate control-plane/admin operations (user provisioning, listing) behind the
    operator's global CORTEX_API_KEY. Scoped per-user tokens can never perform
    these actions."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex admin token")
    token = authorization.split(" ", 1)[1].strip()
    if settings.api_key and hmac.compare_digest(token, settings.api_key):
        return True
    raise HTTPException(status_code=403, detail="Cortex admin operations require the control-plane admin token")


def _capture_page(message: str = "", status: str = "ready", token: str = "", title: str = "", url: str = "", content: str = "") -> str:
    escaped_message = html.escape(message)
    escaped_status = html.escape(status)
    escaped_title = html.escape(title)
    escaped_url = html.escape(url)
    escaped_content = html.escape(content)
    return f"""
    <!doctype html>
    <html>
      <head>
        <title>Doppl Capture</title>
        <style>
          body {{ font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 32px; line-height: 1.45; max-width: 760px; color: #1f2328; }}
          label {{ display: block; font-weight: 600; margin-top: 14px; }}
          input, textarea {{ width: 100%; box-sizing: border-box; font: inherit; padding: 9px; border: 1px solid #d0d7de; border-radius: 8px; }}
          textarea {{ min-height: 220px; }}
          button {{ margin-top: 16px; padding: 9px 14px; border-radius: 8px; border: 0; background: #0969da; color: white; font-weight: 700; }}
          .status {{ display: inline-block; padding: 4px 8px; border-radius: 999px; background: #ddf4ff; color: #0969da; font-weight: 700; }}
          .ok {{ background: #dafbe1; color: #116329; }}
          .err {{ background: #ffebe9; color: #cf222e; }}
          .hint {{ color: #57606a; }}
        </style>
      </head>
      <body>
        <p class="status {'ok' if status == 'saved' else 'err' if status == 'error' else ''}">{escaped_status}</p>
        <h1>Save to Doppl</h1>
        <p class="hint">Capture selected text, page context, links, or notes into your local Doppl memory.</p>
        {f"<p><strong>{escaped_message}</strong></p>" if escaped_message else ""}
        <form method="post" action="/capture">
          <label>Token</label>
          <input name="token" value="" autocomplete="off" placeholder="Paste your Doppl token" />
          <label>Title</label>
          <input name="title" value="{escaped_title}" />
          <label>Source URL</label>
          <input name="url" value="{escaped_url}" />
          <label>Content</label>
          <textarea name="content">{escaped_content}</textarea>
          <input type="hidden" name="source" value="browser-capture" />
          <button type="submit">Save to Doppl</button>
        </form>
      </body>
    </html>
    """


def _auth_query_token(token: str | None, *, required_scope: str = "write") -> str:
    normalized = (token or "").strip()
    if settings.api_key and hmac.compare_digest(normalized, settings.api_key):
        return settings.default_user_id
    if settings.require_scoped_api_tokens:
        scoped = store.authenticate_api_token(normalized)
        if scoped:
            _assert_api_token_scope(scoped, required_scope)
            _assert_api_token_trust(scoped["user_id"], required_scope)
            return scoped["user_id"]
    # Fail closed whenever auth is actually required — a configured global key, scoped-token
    # mode, or any non-local (hosted) shard mode. Only genuine local single-user dev
    # (no key, local shard, scoped tokens off) may fall through to the default user, matching
    # every other auth path which never grants access on a missing/invalid token.
    if settings.api_key or settings.require_scoped_api_tokens or settings.shard_mode != "local":
        raise HTTPException(status_code=401, detail="Missing or invalid Doppl capture token")
    return settings.default_user_id


def _save_capture_from_values(content: str, source: str, title: str | None, source_url: str | None, user_id: str) -> dict[str, Any]:
    content = content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is required")
    if len(content) > 200_000:
        raise HTTPException(status_code=413, detail="content is too large")
    normalized_source = (source or "browser-capture")[:80]
    extracted = extract_context(content, normalized_source, author_aliases=store.settings(user_id).get("identity_aliases"))
    return store.save_capture(
        user_id=user_id,
        content=content,
        source=normalized_source,
        source_url=(source_url or "")[:500] or None,
        title=(title or "")[:200] or None,
        extracted=extracted,
    )


@app.get("/", response_class=HTMLResponse)
def root() -> Response:
    """Branded landing at the domain root. Account CTAs render only when auth is enabled (the hosted
    plane); a plain local backend just shows the download + developer health hints."""
    account_cta = (
        '      <div class="cta">\n'
        '        <a class="button primary" href="/account/login">Sign in</a>\n'
        '        <a class="button secondary" href="/account/signup" style="margin-left:10px">Create an account</a>\n'
        "      </div>\n"
        if settings.auth_enabled
        else ""
    )
    body = (
        "    <h1>Your memory, everywhere you think.</h1>\n"
        '    <p class="lede">Doppl is your private, local-first AI memory — it remembers what you '
        "learn and decide, and gives it back, cited, to the tools you already use.</p>\n"
        f"{account_cta}"
        '    <div class="cta"><a class="button secondary" href="/download" '
        'style="width:auto;padding:12px 22px">Download the Mac app</a></div>\n'
        '    <div class="foot-links">\n'
        '      <a href="/terms">Terms</a><a href="/privacy">Privacy</a>\n'
        '      <p class="muted" style="margin-top:12px">Service health: '
        "<code>/health</code> · <code>/ready</code> · <code>/.well-known/cortex.json</code>. "
        "Authenticated API access uses a token minted from your account.</p>\n"
        "    </div>\n"
    )
    return _public_html(render_public_page("Doppl — your private AI memory", body))


def _public_html(document: str) -> HTMLResponse:
    """A public content page (landing/terms/privacy/download) with its own route-scoped CSP so the
    inline <style> renders (Caddy's set-default CSP defers to this)."""
    response = HTMLResponse(content=document)
    response.headers["Content-Security-Policy"] = PUBLIC_PAGE_CSP
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def _capture_html(body: str, status_code: int = 200) -> HTMLResponse:
    """The /capture quick-capture page: inline <style> + a native form, no JS. Carries the same
    route-scoped CSP as the public pages so it renders styled instead of raw under Caddy's strict
    default CSP."""
    response = HTMLResponse(content=body, status_code=status_code)
    response.headers["Content-Security-Policy"] = PUBLIC_PAGE_CSP
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/favicon.svg")
def favicon_svg() -> Response:
    """The Doppl brand mark, so every page's <link rel=icon> resolves instead of 404ing."""
    return Response(
        content=FAVICON_SVG,
        media_type="image/svg+xml",
        headers={"Cache-Control": "public, max-age=86400"},
    )


def _public_footer(links: list[tuple[str, str]]) -> str:
    """A foot-links row for the public pages. Account links (/account/*) are dropped when auth is
    disabled (a plain local backend has no /account routes), so no footer link ever dead-ends."""
    parts = [
        f'<a href="{href}">{html.escape(label)}</a>'
        for href, label in links
        if settings.auth_enabled or not href.startswith("/account/")
    ]
    return '    <div class="foot-links">' + "".join(parts) + "</div>\n"


@app.get("/download", response_class=HTMLResponse)
def download_page() -> Response:
    """Where to get the Mac app. Points at the live product site (trydoppl.com), which hosts the
    signed DMG + update feed (see docs/DISTRIBUTION.md)."""
    body = (
        "    <h1>Download Doppl for Mac</h1>\n"
        '    <p class="lede">Doppl runs as a native macOS app with a local vault. Download it, open '
        "it, and sign in with your account to sync.</p>\n"
        '    <div class="cta">\n'
        '      <a class="button primary" href="https://trydoppl.com" '
        'style="width:auto;padding:12px 22px">Get Doppl for Mac</a>\n'
        "    </div>\n"
        "    <h2>Install</h2>\n"
        "    <ul>\n"
        "      <li>Download the <code>.dmg</code> from the site.</li>\n"
        "      <li>Open it and drag Doppl to Applications.</li>\n"
        "      <li>Launch Doppl and sign in — your memory stays on your Mac.</li>\n"
        "    </ul>\n"
        + _public_footer([("/", "Home"), ("/account/login", "Sign in"), ("/terms", "Terms"), ("/privacy", "Privacy")])
    )
    return _public_html(render_public_page("Download · Doppl", body))


@app.get("/terms", response_class=HTMLResponse)
def terms_page() -> Response:
    body = _TERMS_BODY + _public_footer(
        [("/", "Home"), ("/privacy", "Privacy"), ("/account/signup", "Create an account")]
    )
    return _public_html(render_public_page("Terms of Service · Doppl", body))


@app.get("/privacy", response_class=HTMLResponse)
def privacy_page() -> Response:
    body = _PRIVACY_BODY + _public_footer(
        [("/", "Home"), ("/terms", "Terms"), ("/account/signup", "Create an account")]
    )
    return _public_html(render_public_page("Privacy Policy · Doppl", body))


# Plain-language Terms + Privacy that reflect how Doppl ACTUALLY works (local-first storage, per-user
# encryption, crypto-shred deletion, no data sale). A solid, honest baseline the founder should have
# reviewed by counsel before broad launch; far better than the 404 the signup page linked to.
_TERMS_BODY = (
    "    <h1>Terms of Service</h1>\n"
    '    <p class="lede">These terms cover your use of Doppl (the “Service”), a personal AI-memory '
    "app and the account that syncs it. By creating an account you agree to them.</p>\n"
    "    <h2>Eligibility</h2>\n"
    "    <p>You must be at least 16 years old to use Doppl. By signing up you confirm that you are.</p>\n"
    "    <h2>Your account</h2>\n"
    "    <p>You are responsible for keeping your credentials secure and for activity under your "
    "account. Tell us promptly if you suspect unauthorized use. You may delete your account at any "
    "time from your account page; deletion is permanent.</p>\n"
    "    <h2>Your content</h2>\n"
    "    <p>The notes, captures, and memories you store are <strong>yours</strong>. You grant Doppl "
    "only the limited permission needed to store, index, and sync that content to provide the "
    "Service to you. We do not sell your content and we do not use it to train models for others.</p>\n"
    "    <h2>Acceptable use</h2>\n"
    "    <ul>\n"
    "      <li>Don’t use Doppl to break the law or infringe others’ rights.</li>\n"
    "      <li>Don’t attempt to disrupt, overload, or reverse-engineer the hosted service.</li>\n"
    "      <li>Don’t store content you have no right to store.</li>\n"
    "    </ul>\n"
    "    <h2>Service changes &amp; availability</h2>\n"
    "    <p>Doppl is offered on an “as is” and “as available” basis, without warranties of any kind. "
    "During beta, features may change and availability isn’t guaranteed. To the extent permitted by "
    "law, Doppl isn’t liable for indirect or consequential damages.</p>\n"
    "    <h2>Termination</h2>\n"
    "    <p>You can stop using Doppl and delete your account any time. We may suspend accounts that "
    "violate these terms.</p>\n"
    "    <h2>Contact</h2>\n"
    '    <p>Questions about these terms? Email <a href="mailto:support@signindoppl.com">'
    "support@signindoppl.com</a>.</p>\n"
)

_PRIVACY_BODY = (
    "    <h1>Privacy Policy</h1>\n"
    '    <p class="lede">Doppl is built local-first: your memory lives on your device, and your '
    "account exists to identify you and sync your own data. Here’s exactly what that means.</p>\n"
    "    <h2>What we collect</h2>\n"
    "    <ul>\n"
    "      <li><strong>Account details</strong> — your email address and (optionally) your name, to "
    "create and secure your account.</li>\n"
    "      <li><strong>Sign-in metadata</strong> — session and device info needed to keep you signed "
    "in and to show you your active sessions.</li>\n"
    "      <li><strong>Your memory content</strong> — the notes and captures you choose to save. "
    "This is stored for you and synced to your devices.</li>\n"
    "    </ul>\n"
    "    <h2>How your content is protected</h2>\n"
    "    <p>Your memory is stored with per-user isolation and encrypted at rest with a key unique to "
    "your account. We don’t sell your data, we don’t share it with advertisers, and we don’t use it "
    "to train models for other people.</p>\n"
    "    <h2>Deletion &amp; your control</h2>\n"
    "    <p>You can delete your account from your account page at any time. Deletion "
    "<strong>crypto-shreds</strong> your encryption keys — making your stored content permanently "
    "unreadable — and removes your data. You can also sign out of individual sessions.</p>\n"
    "    <h2>Third parties</h2>\n"
    "    <p>If you sign in with Google, GitHub, or Apple, we receive only the basic profile "
    "(identifier and email) needed to create your account. Optional payment processing, if you "
    "subscribe, is handled by a third-party processor — we never store your card details.</p>\n"
    "    <h2>Contact</h2>\n"
    '    <p>Privacy questions or a data request? Email <a href="mailto:support@signindoppl.com">'
    "support@signindoppl.com</a>.</p>\n"
)


@app.get("/health")
def health() -> dict[str, Any]:
    payload = store.health_payload(mode="fastapi", auth=bool(settings.api_key))
    payload["hosted_readiness"] = _hosted_readiness_contract()
    return payload


@app.get("/v1/metrics")
def metrics_snapshot(_admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    """Operator-only observability snapshot (counters, gauges, latency histograms, recent
    structured events). Returns ``enabled: false`` with empty series when
    CORTEX_OBSERVABILITY_ENABLED is unset, so operators can see the flag state."""
    snapshot = metrics.snapshot()
    snapshot["backend_version"] = BACKEND_VERSION
    return snapshot


@app.get("/ready")
def ready() -> dict[str, Any]:
    hosted_readiness = _hosted_readiness_contract()
    if hosted_readiness["status"] != "ok":
        raise HTTPException(
            status_code=503,
            detail={
                "status": "needs_configuration",
                "hosted_readiness": hosted_readiness,
            },
        )
    diagnostics = store.diagnostics(settings.default_user_id)
    if diagnostics["status"] != "ok":
        raise HTTPException(status_code=503, detail=diagnostics)
    return {"status": "ok", "diagnostics": diagnostics, "hosted_readiness": hosted_readiness}


@app.get("/capture", response_class=HTMLResponse)
def capture_page(
    token: str = "",
    text: str = "",
    content: str = "",
    title: str = "",
    url: str = "",
    source: str = "browser-capture",
) -> HTMLResponse:
    payload = content or text
    if payload.strip():
        try:
            user_id = _auth_query_token(token)
            response = _save_capture_from_values(payload, source, title, url, user_id)
            return _capture_html(
                _capture_page(
                    message=f"Saved {len(response.get('memories', []))} memories.",
                    status="saved",
                    token=token,
                    title=title,
                    url=url,
                )
            )
        except HTTPException as exc:
            return _capture_html(
                _capture_page(message=str(exc.detail), status="error", token=token, title=title, url=url, content=payload),
                status_code=exc.status_code,
            )
    return _capture_html(_capture_page(token=token, title=title, url=url, content=payload))


@app.post("/capture", response_class=HTMLResponse)
async def capture_form(request: Request) -> HTMLResponse:
    raw = (await request.body()).decode("utf-8")
    params = parse_qs(raw, keep_blank_values=True)
    value = lambda name: (params.get(name) or [""])[0]
    token = value("token")
    content = value("content") or value("text")
    title = value("title")
    url = value("url")
    source = value("source") or "browser-capture"
    try:
        user_id = _auth_query_token(token)
        response = _save_capture_from_values(content, source, title, url, user_id)
        return _capture_html(
            _capture_page(
                message=f"Saved {len(response.get('memories', []))} memories.",
                status="saved",
                token=token,
                title=title,
                url=url,
            )
        )
    except HTTPException as exc:
        return _capture_html(
            _capture_page(message=str(exc.detail), status="error", token=token, title=title, url=url, content=content),
            status_code=exc.status_code,
        )


@app.post("/v1/captures", response_model=CaptureResponse | QueuedCaptureResponse)
def create_capture(
    request: CaptureRequest,
    processing: str = Query(default="sync", pattern="^(sync|async)$"),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    resolved_user_id = user_id or request.user_id
    _enforce_memory_quota(resolved_user_id)
    if processing == "async":
        return store.enqueue_capture(
            user_id=resolved_user_id,
            content=request.content,
            source=request.source,
            source_url=request.source_url,
            title=request.title,
            cite_capture_provenance=True,
            auto_approve=settings.auto_approve_captures,
        )
    extracted = extract_context(
        request.content,
        request.source,
        author_aliases=store.settings(resolved_user_id).get("identity_aliases"),
    )
    if request.captured_at:
        extracted["_timestamp"] = request.captured_at  # pin timestamp so re-push stays idempotent
    return store.save_capture(
        user_id=resolved_user_id,
        content=request.content,
        source=request.source,
        source_url=request.source_url,
        title=request.title,
        extracted=extracted,
        capture_id_override=request.capture_id_override,
        cite_capture_provenance=True,
        auto_approve=settings.auto_approve_captures,
    )


@app.post("/v1/captures/queue", response_model=QueuedCaptureResponse, status_code=202)
def queue_capture(request: CaptureRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    resolved_user_id = user_id or request.user_id
    _enforce_memory_quota(resolved_user_id)
    return store.enqueue_capture(
        user_id=resolved_user_id,
        content=request.content,
        source=request.source,
        source_url=request.source_url,
        title=request.title,
        cite_capture_provenance=True,
        auto_approve=settings.auto_approve_captures,
    )


@app.get("/v1/captures/{capture_id}/status")
def capture_status(capture_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.capture_status(user_id, capture_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/imports/sources")
def supported_import_sources(user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.supported_import_sources()}


@app.get("/v1/source-accounts/catalog")
def source_account_catalog(user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.source_connector_catalog()}


@app.get("/v1/sources/readiness", response_model=SourceReadinessResponse)
def source_readiness(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.source_readiness_report(user_id)


@app.get("/v1/source-accounts", response_model=SourceAccountListResponse)
def list_source_accounts(include_disconnected: bool = Query(default=False), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.public_payload(user_id, store.list_source_accounts(user_id, include_disconnected=include_disconnected))}


@app.post("/v1/source-accounts", response_model=SourceAccountResponse)
def upsert_source_account(request: SourceAccountRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        account = store.upsert_source_account(
            user_id,
            source=request.source,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            connection_type=request.connection_type,
            status=request.status,
            auth_state=request.auth_state,
            policy=request.policy,
            metadata=request.metadata,
            last_error=request.last_error,
        )
        return store.public_payload(user_id, account)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/source-accounts/{account_id}", response_model=SourceAccountResponse)
def disconnect_source_account(account_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    disconnected = store.disconnect_source_account(user_id, account_id)
    if not disconnected:
        raise HTTPException(status_code=404, detail="Source account not found")
    return store.public_payload(user_id, disconnected)


@app.post("/v1/source-accounts/{account_id}/disconnect", response_model=SourceAccountResponse)
def pause_source_account_sync(account_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    disconnected = store.disconnect_source_account(user_id, account_id)
    if not disconnected:
        raise HTTPException(status_code=404, detail="Source account not found")
    return store.public_payload(user_id, disconnected)


@app.post("/v1/source-accounts/{account_id}/resume", response_model=SourceAccountResponse)
def resume_source_account_sync(account_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    resumed = store.resume_source_account(user_id, account_id)
    if not resumed:
        raise HTTPException(status_code=404, detail="Source account not found")
    return store.public_payload(user_id, resumed)


@app.post("/v1/source-accounts/{account_id}/sync", response_model=SourceAccountSyncResponse)
def sync_source_account(account_id: str, request: SourceAccountSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_source_account_records(
            user_id,
            account_id,
            records=[record.model_dump() for record in request.records],
            cursor_name=request.cursor_name,
            cursor_value=request.cursor_value,
            high_water_mark=request.high_water_mark,
            state=request.state,
            processing=request.processing,
            archive_missing=request.archive_missing,
            complete_snapshot=request.complete_snapshot,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/obsidian/sync", response_model=ObsidianVaultSyncResponse)
def sync_obsidian_vault(request: ObsidianVaultSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_obsidian_vault(
            user_id,
            vault_path=request.vault_path,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            review_required=request.review_required,
        )
        return store.public_payload(user_id, result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/agent-sessions/sync", response_model=AgentSessionsSyncResponse)
def sync_agent_sessions(request: AgentSessionsSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    """Harvest the user's own messages from local coding-agent session logs (Claude Code, Codex,
    Cursor) into the review queue. Write-scoped like the other connector syncs; user words only."""
    try:
        result = store.sync_agent_sessions(
            user_id,
            agents=list(request.agents) if request.agents else None,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            processing=request.processing,
            max_records=request.max_records,
            per_session_limit=request.per_session_limit,
            cursor_name=request.cursor_name,
            review_required=request.review_required,
        )
        return store.public_payload(user_id, result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/obsidian/write-back", response_model=None)
def write_obsidian_pages(body: dict[str, Any] | None = None, user_id: str = Depends(auth)) -> Any:
    """Obsidian write-back: refresh the distilled, cited Cortex/ pages inside the user's vault.
    Export-scoped (memory egress into user-owned files), parity with the MCP tool."""
    payload = body or {}
    try:
        people_limit = int(payload.get("people_limit") or 10)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="people_limit must be an integer")
    try:
        result = store.write_obsidian_pages(
            user_id,
            vault_path=str(payload.get("vault_path") or "") or None,
            people_limit=people_limit,
        )
        return store.public_payload(user_id, result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/github/sync", response_model=GitHubSyncResponse)
def sync_github_account(request: GitHubSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_github_account(
            user_id,
            token=request.token,
            repositories=request.repositories,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            processing=request.processing,
            max_records=request.max_records,
            include_comments=request.include_comments,
            max_comments_per_item=request.max_comments_per_item,
            cursor_name=request.cursor_name,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/github/discover", response_model=GitHubRepositoryDiscoveryResponse)
def discover_github_account_repositories(request: GitHubRepositoryDiscoveryRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    del user_id
    try:
        from .connectors.github import discover_github_repositories

        return discover_github_repositories(
            token=request.token,
            limit=request.limit,
            page=request.page,
            api_base_url=request.api_base_url or "https://api.github.com",
        ).to_summary()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/google/oauth/start", response_model=GoogleOAuthStartResponse)
def start_google_oauth(request: GoogleOAuthStartRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        target_store = store.store_for_user(user_id) if hasattr(store, "store_for_user") else store
        started = target_store.start_google_oauth(
            request.source,
            redirect_uri=request.redirect_uri,
            state=request.state,
            client_id=request.client_id,
            code_challenge=request.code_challenge,
            code_challenge_method=request.code_challenge_method,
            scopes=request.scopes,
        )
        _remember_google_oauth_pending(user_id, request, started)
        return started
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/connectors/google/oauth/callback", response_class=HTMLResponse)
def google_oauth_callback(
    code: str | None = Query(default=None, max_length=4000),
    state: str | None = Query(default=None, max_length=500),
    error: str | None = Query(default=None, max_length=500),
    error_description: str | None = Query(default=None, max_length=1000),
) -> HTMLResponse:
    if error:
        detail = error_description or "Google did not authorize Cortex."
        return _google_oauth_callback_page("Google sign-in was not completed", detail, success=False)

    pending = _pop_google_oauth_pending(state or "")
    if not pending:
        return _google_oauth_callback_page(
            "Google sign-in expired",
            "Return to Cortex and start the connection again. No account was connected.",
            success=False,
        )
    if not code:
        return _google_oauth_callback_page(
            "Google sign-in did not return a code",
            "Return to Cortex and start the connection again. No account was connected.",
            success=False,
        )

    user_id = str(pending.get("user_id") or settings.default_user_id)
    source = str(pending.get("source") or "")
    try:
        result = store.complete_google_oauth(
            user_id,
            source,
            code=code,
            redirect_uri=pending.get("redirect_uri"),
            state=state,
            expected_state=state,
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
            include_body=bool(pending.get("include_body", True)),
            include_content=bool(pending.get("include_content", True)),
        )
        account = result.get("source_account") or {}
        if account.get("id"):
            try:
                store.enqueue_source_account_sync(user_id, account["id"], processing="async", max_records=200)
            except ValueError:
                pass
        label = str(account.get("account_label") or "Google")
        return _google_oauth_callback_page(
            "Google is connected",
            f"{label} is connected. Return to Cortex; the first sync will start automatically.",
            success=True,
        )
    except ValueError as exc:
        return _google_oauth_callback_page(
            "Google sign-in could not finish",
            str(exc),
            success=False,
        )


@app.post("/v1/connectors/google/oauth/complete", response_model=GoogleOAuthCompleteResponse)
def complete_google_oauth(request: GoogleOAuthCompleteRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.public_payload(
            user_id,
            store.complete_google_oauth(
                user_id,
                request.source,
                code=request.code,
                redirect_uri=request.redirect_uri,
                state=request.state,
                expected_state=request.expected_state,
                client_id=request.client_id,
                client_secret=request.client_secret,
                token_endpoint=request.token_endpoint,
                code_verifier=request.code_verifier,
                source_account_id=request.source_account_id,
                account_label=request.account_label,
                account_identifier=request.account_identifier,
                query=request.query,
                label_ids=request.label_ids,
                mime_types=request.mime_types,
                include_body=request.include_body,
                include_content=request.include_content,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/oauth/start", response_model=ManagedOAuthStartResponse)
def start_managed_oauth(request: ManagedOAuthStartRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        target_store = store.store_for_user(user_id) if hasattr(store, "store_for_user") else store
        started = target_store.start_managed_oauth(
            request.source,
            redirect_uri=request.redirect_uri,
            state=request.state,
            client_id=request.client_id,
            scopes=request.scopes,
        )
        _remember_managed_oauth_pending(user_id, request, started)
        return started
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/connectors/oauth/callback", response_class=HTMLResponse)
def managed_oauth_callback(
    code: str | None = Query(default=None, max_length=4000),
    state: str | None = Query(default=None, max_length=500),
    error: str | None = Query(default=None, max_length=500),
    error_description: str | None = Query(default=None, max_length=1000),
) -> HTMLResponse:
    if error:
        detail = error_description or "The service did not authorize Cortex."
        return _google_oauth_callback_page("Sign-in was not completed", detail, success=False)

    pending = _pop_managed_oauth_pending(state or "")
    if not pending:
        return _google_oauth_callback_page(
            "Sign-in expired",
            "Return to Cortex and start the connection again. No account was connected.",
            success=False,
        )
    if not code:
        return _google_oauth_callback_page(
            "Sign-in did not return a code",
            "Return to Cortex and start the connection again. No account was connected.",
            success=False,
        )

    user_id = str(pending.get("user_id") or settings.default_user_id)
    source = str(pending.get("source") or "")
    try:
        result = store.complete_managed_oauth(
            user_id,
            source,
            code=code,
            redirect_uri=pending.get("redirect_uri"),
            state=state,
            expected_state=state,
            client_id=pending.get("client_id"),
            client_secret=pending.get("client_secret"),
            token_endpoint=pending.get("token_endpoint"),
            source_account_id=pending.get("source_account_id"),
            account_label=pending.get("account_label"),
            account_identifier=pending.get("account_identifier"),
            include_content=bool(pending.get("include_content", True)),
            api_base_url=pending.get("api_base_url"),
            notion_version=pending.get("notion_version"),
        )
        account = result.get("source_account") or {}
        if account.get("id"):
            try:
                store.enqueue_source_account_sync(user_id, account["id"], processing="async", max_records=200)
            except ValueError:
                pass
        label = str(account.get("account_label") or "Source")
        return _google_oauth_callback_page(
            "Source is connected",
            f"{label} is connected. Return to Cortex; the first sync will start automatically.",
            success=True,
        )
    except ValueError as exc:
        return _google_oauth_callback_page("Sign-in could not finish", str(exc), success=False)


@app.post("/v1/connectors/oauth/complete", response_model=ManagedOAuthCompleteResponse)
def complete_managed_oauth(request: ManagedOAuthCompleteRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.public_payload(
            user_id,
            store.complete_managed_oauth(
                user_id,
                request.source,
                code=request.code,
                redirect_uri=request.redirect_uri,
                state=request.state,
                expected_state=request.expected_state,
                client_id=request.client_id,
                client_secret=request.client_secret,
                token_endpoint=request.token_endpoint,
                source_account_id=request.source_account_id,
                account_label=request.account_label,
                account_identifier=request.account_identifier,
                include_content=request.include_content,
                api_base_url=request.api_base_url,
                notion_version=request.notion_version,
            ),
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/gmail/sync", response_model=GmailSyncResponse)
def sync_gmail_account(request: GmailSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_gmail_account(
            user_id,
            access_token=request.access_token,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            query=request.query,
            label_ids=request.label_ids,
            since=request.since,
            page_token=request.page_token,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            include_body=request.include_body,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/google-drive/sync", response_model=GoogleDriveSyncResponse)
def sync_google_drive_account(request: GoogleDriveSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_google_drive_account(
            user_id,
            access_token=request.access_token,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            query=request.query,
            mime_types=request.mime_types,
            since=request.since,
            page_token=request.page_token,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            include_content=request.include_content,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/outlook/sync", response_model=OutlookSyncResponse)
def sync_outlook_account(request: OutlookSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_outlook_account(
            user_id,
            access_token=request.access_token,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            query=request.query,
            since=request.since,
            page_token=request.page_token,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            include_body=request.include_body,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/slack/sync", response_model=SlackSyncResponse)
def sync_slack_account(request: SlackSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_slack_account(
            user_id,
            token=request.token,
            channels=request.channels,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            workspace_url=request.workspace_url,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/slack/discover", response_model=SlackChannelDiscoveryResponse)
def discover_slack_account_channels(request: SlackChannelDiscoveryRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    del user_id
    try:
        from .connectors.slack import discover_slack_channels

        return discover_slack_channels(
            token=request.token,
            limit=request.limit,
            include_private=request.include_private,
            cursor=request.cursor,
            api_base_url=request.api_base_url or "https://slack.com/api",
        ).to_summary()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/readwise/sync", response_model=ReadwiseSyncResponse)
def sync_readwise_account(request: ReadwiseSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_readwise_account(
            user_id,
            token=request.token,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            page_cursor=request.page_cursor,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/calendar/sync", response_model=CalendarSyncResponse)
def sync_calendar_account(request: CalendarSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_calendar_account(
            user_id,
            ics_path=request.ics_path,
            feed_url=request.feed_url,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            complete_snapshot=request.complete_snapshot,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/raindrop/sync", response_model=RaindropSyncResponse)
def sync_raindrop_account(request: RaindropSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_raindrop_account(
            user_id,
            token=request.token,
            collection_id=request.collection_id,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            page=request.page,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            include_highlights=request.include_highlights,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/zotero/sync", response_model=ZoteroSyncResponse)
def sync_zotero_account(request: ZoteroSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_zotero_account(
            user_id,
            token=request.token,
            library_type=request.library_type,
            library_id=request.library_id,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            cursor=request.cursor,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            include_attachments=request.include_attachments,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/linear/sync", response_model=LinearSyncResponse)
def sync_linear_account(request: LinearSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_linear_account(
            user_id,
            token=request.token,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            cursor=request.cursor,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            complete_snapshot=request.complete_snapshot,
            api_url=request.api_url,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/jira/sync", response_model=JiraSyncResponse)
def sync_jira_account(request: JiraSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_jira_account(
            user_id,
            email=request.email,
            api_token=request.api_token,
            site_url=request.site_url,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            jql=request.jql,
            since=request.since,
            page_token=request.page_token,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            complete_snapshot=request.complete_snapshot,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.post("/v1/connectors/notion/sync", response_model=NotionSyncResponse)
def sync_notion_account(request: NotionSyncRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        result = store.sync_notion_account(
            user_id,
            token=request.token,
            source_account_id=request.source_account_id,
            account_label=request.account_label,
            account_identifier=request.account_identifier,
            since=request.since,
            cursor=request.cursor,
            processing=request.processing,
            max_records=request.max_records,
            cursor_name=request.cursor_name,
            include_content=request.include_content,
            complete_snapshot=request.complete_snapshot,
            api_base_url=request.api_base_url,
            notion_version=request.notion_version,
        )
        return store.public_payload(user_id, result)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/sync-cursors", response_model=SyncCursorListResponse)
def list_sync_cursors(source_account_id: str | None = None, user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.list_sync_cursors(user_id, source_account_id=source_account_id)}


@app.post("/v1/sync-cursors", response_model=SyncCursorResponse)
def upsert_sync_cursor(request: SyncCursorRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.upsert_sync_cursor(
            user_id,
            source=request.source,
            cursor_name=request.cursor_name,
            cursor_value=request.cursor_value,
            high_water_mark=request.high_water_mark,
            state=request.state,
            source_account_id=request.source_account_id,
            last_error=request.last_error,
            completed=request.completed,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/sync/devices", response_model=SyncDeviceListResponse)
def list_sync_devices(include_revoked: bool = Query(default=False), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.list_sync_devices(user_id, include_revoked=include_revoked)}


@app.post("/v1/sync/devices", response_model=SyncDeviceResponse)
def register_sync_device(request: SyncDeviceRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.register_sync_device(
            user_id,
            device_name=request.device_name,
            platform=request.platform,
            device_key=request.device_key,
            public_key=request.public_key,
            capabilities=request.capabilities,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.delete("/v1/sync/devices/{device_id}", response_model=SyncDeviceResponse)
def revoke_sync_device(device_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    revoked = store.revoke_sync_device(user_id, device_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Sync device not found")
    return revoked


@app.get("/v1/sync/devices/{device_id}/receipts", response_model=SyncReceiptListResponse)
def list_sync_receipts(device_id: str, limit: int = Query(default=50, ge=1, le=200), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.list_sync_receipts(user_id, device_id, limit=limit)}


@app.post("/v1/sync/devices/{device_id}/receipts", response_model=SyncReceiptResponse)
def record_sync_receipt(device_id: str, request: SyncReceiptRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.record_sync_receipt(
            user_id,
            device_id,
            cursor=request.cursor,
            status=request.status,
            manifest_hash=request.manifest_hash,
            remote_ref=request.remote_ref,
            error=request.error,
            stats=request.stats,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@app.get("/v1/imports")
def list_imports(limit: int = Query(default=50, ge=1, le=100), include_deleted: bool = True, user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.list_imports(user_id, limit=limit, include_deleted=include_deleted)}


@app.post("/v1/imports/analyze", response_model=SourceAnalyzeResponse)
def analyze_import_sources(request: SourceAnalyzeRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.analyze_import_sources(
        request.paths,
        source_hint=request.source_hint,
        max_records=min(request.max_records, 500),
    )


@app.post("/v1/imports", response_model=SourceImportResponse)
def import_sources(request: SourceImportRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.import_sources(
        user_id=user_id or request.user_id,
        paths=request.paths,
        source_hint=request.source_hint,
        processing=request.processing,
        max_records=request.max_records,
        offset=request.offset,
        auto_approve=request.auto_approve,
    )


@app.get("/v1/imports/{import_id}")
def get_import(import_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    result = store.get_import(user_id, import_id)
    if not result:
        raise HTTPException(status_code=404, detail="Import not found")
    return result


@app.delete("/v1/imports/{import_id}", response_model=SourceImportDeleteResponse)
def delete_import(import_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.delete_import(user_id, import_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/v1/jobs")
def list_jobs(
    status: str | None = None,
    job_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=100),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return {"results": store.list_jobs(user_id, status=status, job_type=job_type, limit=limit)}


@app.get("/v1/jobs/health")
def job_health(
    failed_limit: int = Query(default=10, ge=0, le=50),
    stale_after_seconds: int = Query(default=900, ge=60, le=86_400),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.job_health(user_id, failed_limit=failed_limit, stale_after_seconds=stale_after_seconds)


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    job = store.get_job(user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/v1/jobs/run", response_model=JobRunResponse)
def run_jobs(
    limit: int = Query(default=10, ge=1, le=100),
    schedule_source_syncs: bool = Query(default=True),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.run_due_jobs(user_id, limit=limit, schedule_source_syncs=schedule_source_syncs)


@app.post("/v1/maintenance/jobs/run", response_model=JobRunResponse)
def run_maintenance_jobs(
    limit: int = Query(default=10, ge=1, le=100),
    schedule_source_syncs: bool = Query(default=True),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.run_due_jobs(user_id, limit=limit, schedule_source_syncs=schedule_source_syncs)


@app.post("/v1/sources/sync-due", response_model=JobRunResponse)
def sync_due_sources(limit: int = Query(default=10, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.run_due_source_sync_jobs(user_id, limit=limit, worker_id="api-source-sync")


@app.get("/v1/recent")
def recent(limit: int = Query(default=20, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.public_recent(user_id, limit)}


@app.get("/v1/inbox", response_model=ListResponse)
def inbox(limit: int = Query(default=30, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.inbox(user_id, limit)}


@app.get("/v1/search", response_model=SearchResponse)
def search(
    query: str,
    limit: int = Query(default=10, ge=1, le=50),
    kind: str | None = None,
    layer: str | None = None,
    sector: str | None = Query(default=None, max_length=120),
    source: str | None = Query(default=None, max_length=80),
    source_account_id: str | None = Query(default=None, max_length=120),
    as_of: str | None = Query(default=None, max_length=80),
    repository: str | None = Query(default=None, max_length=240),
    channel: str | None = Query(default=None, max_length=240),
    record_scope: str | None = Query(default=None, max_length=80),
    state: str | None = Query(default=None, max_length=80),
    project: str | None = Query(default=None, max_length=240),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.public_search_payload(
        user_id,
        query,
        limit,
        kind,
        layer,
        sector=sector,
        source=source,
        source_account_id=source_account_id,
        as_of=as_of,
        metadata_filters={
            "repository": repository,
            "channel": channel,
            "record_scope": record_scope,
            "state": state,
            "project": project,
        },
    )


@app.get("/v1/ask", response_model=AskResponse)
def ask(
    query: str,
    limit: int = Query(default=8, ge=1, le=20),
    sector: str | None = Query(default=None, max_length=120),
    source: str | None = Query(default=None, max_length=80),
    source_account_id: str | None = Query(default=None, max_length=120),
    as_of: str | None = Query(default=None, max_length=80),
    repository: str | None = Query(default=None, max_length=240),
    channel: str | None = Query(default=None, max_length=240),
    record_scope: str | None = Query(default=None, max_length=80),
    state: str | None = Query(default=None, max_length=80),
    project: str | None = Query(default=None, max_length=240),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.answer_query(
        user_id,
        query,
        limit,
        sector=sector,
        source=source,
        source_account_id=source_account_id,
        as_of=as_of,
        metadata_filters={
            "repository": repository,
            "channel": channel,
            "record_scope": record_scope,
            "state": state,
            "project": project,
        },
    )


@app.get("/v1/action-brief", response_model=None)
def action_brief(
    task: str = Query(..., max_length=500),
    limit: int = Query(default=8, ge=1, le=20),
    sector: str | None = Query(default=None, max_length=120),
    as_of: str | None = Query(default=None, max_length=80),
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    user_id: str = Depends(auth),
) -> dict[str, Any] | Response:
    brief = store.action_brief(user_id, task, limit=limit, sector=sector, as_of=as_of)
    if format == "markdown":
        return Response(content=brief["markdown"], media_type="text/markdown")
    return brief


@app.get("/v1/decisions/history")
def decision_history(
    query: str = Query(default="", max_length=240),
    limit: int = Query(default=12, ge=1, le=30),
    sector: str | None = Query(default=None, max_length=120),
    include_superseded: bool = Query(default=True),
    as_of: str | None = Query(default=None, max_length=80),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.decision_history(user_id, query, limit=limit, sector=sector, include_superseded=include_superseded, as_of=as_of)


@app.get("/v1/beliefs/timeline")
def belief_timeline(
    topic: str = Query(default="", max_length=240),
    limit: int = Query(default=20, ge=1, le=50),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.get_belief_timeline(user_id, topic, limit=limit)


@app.get("/v1/eval/scorecard")
def tool_scorecard(
    days: int = Query(default=7, ge=1, le=90),
    token_id: str | None = Query(default=None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.get_tool_scorecard(user_id, days=days, token_id=token_id)


@app.get("/v1/sources/reputation")
def source_reputation(
    days: int = Query(default=90, ge=1, le=365),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    """Phase C: per-source approve/reject reputation from the review ledger. Read-only; it
    recommends promoting a reliable source or demoting a noisy trusted one, never auto-flips."""
    return store.source_reputation(user_id, days=days)


@app.post("/v1/eval/grade-answer")
def grade_answer(payload: GradeAnswerRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.grade_answer(user_id, payload.answer_text, session_id=payload.session_id, pack_sha=payload.pack_sha)


@app.post("/v1/twin/would-i")
def twin_would_i(payload: WouldIRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.would_i(user_id, payload.question, limit=payload.limit)


@app.post("/v1/twin/draft-as-me")
def twin_draft_as_me(payload: DraftAsMeRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.draft_as_me(user_id, payload.prompt, medium=payload.medium, limit=payload.limit)


@app.post("/v1/twin/grade")
def twin_grade(payload: GradeTwinPredictionRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.grade_twin_prediction(user_id, payload.prediction_id, payload.outcome, actual=payload.actual)


@app.get("/v1/twin/scorecard")
def twin_scorecard(
    days: int = Query(default=90, ge=1, le=365),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.get_twin_scorecard(user_id, days=days)


@app.get("/v1/alerts")
def proactive_alerts(
    status: str = Query(default="pending", max_length=20),
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return {"alerts": store.list_proactive_alerts(user_id, status=None if status == "all" else status, limit=limit)}


@app.post("/v1/alerts/{alert_id}/resolve")
def resolve_alert(
    alert_id: str,
    resolution: str = Query(..., max_length=20),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.resolve_proactive_alert(user_id, alert_id, resolution)


@app.get("/v1/alerts/precision")
def alert_precision(
    days: int = Query(default=30, ge=1, le=365),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.get_alert_precision(user_id, days=days)


@app.get("/v1/prefetch/hit-rate")
def prefetch_hit_rate(
    days: int = Query(default=30, ge=1, le=365),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.get_prefetch_hit_rate(user_id, days=days)


@app.get("/v1/tasks/open")
def open_tasks(limit: int = Query(default=20, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.open_tasks(user_id, limit)}


@app.get("/v1/topics")
def topics(
    limit: int = Query(default=30, ge=1, le=100),
    sector: str | None = Query(default=None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return {"sector": sector, "results": store.list_topics(user_id, limit, sector=sector)}


@app.get("/v1/entities")
def entities(
    limit: int = Query(default=30, ge=1, le=100),
    sector: str | None = Query(default=None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return {"sector": sector, "results": store.list_entities(user_id, limit, sector=sector)}


@app.get("/v1/people/{name}")
def about_person(name: str, limit: int = Query(default=12, ge=1, le=50), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.about_person(user_id, name, limit)}


@app.get("/v1/people/{name}/context")
def person_context(name: str, limit: int = Query(default=8, ge=1, le=20), user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.person_context(user_id, name, limit=limit)


@app.get("/v1/entities/{name}")
def about_entity(name: str, limit: int = Query(default=12, ge=1, le=50), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.about_entity(user_id, name, limit)}


@app.get("/v1/review/today")
def daily_review(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.daily_review(user_id)


@app.get("/v1/loop", response_model=ProductLoopResponse)
def product_loop(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.product_loop(user_id)


@app.post("/v1/loop/reuse", response_model=ContextReuseResponse)
def record_context_reuse(request: ContextReuseRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.record_context_reuse(user_id, surface=request.surface, query=request.query, target=request.target)


@app.get("/v1/context-pack")
def context_pack(query: str = "", limit: int = Query(default=12, ge=1, le=50), sector: str | None = Query(default=None, max_length=120), user_id: str = Depends(auth)) -> Response:
    return Response(content=store.context_pack(user_id, query=query, limit=limit, sector=sector), media_type="text/markdown")


def _bearer_has_export_scope(request: Request) -> bool:
    """Whether the (already-authenticated) bearer may see the identity/persona layer.
    The admin app token always may; scoped API tokens need the export scope. Read-only
    callers still get a pack — the identity layer degrades to a visible omission record."""
    authorization = request.headers.get("authorization") or ""
    token = authorization.split(" ", 1)[1].strip() if " " in authorization else ""
    if not token:
        return False
    if settings.api_key and hmac.compare_digest(token, settings.api_key):
        return True
    scoped = store.authenticate_api_token(token)
    return bool(scoped and "export" in set(scoped.get("scopes") or []))


def _context_response(pack: dict[str, Any] | str, format: str) -> Any:
    if format == "markdown":
        return Response(content=pack, media_type="text/markdown")
    return pack


@app.get("/v1/context", response_model=None)
def get_context(
    request: Request,
    task: str = Query(default="", max_length=500),
    surface: str = Query(default="agent", max_length=40),
    token_budget: int = Query(default=2000, ge=1, le=100000),
    intent: str | None = Query(default=None, max_length=16),
    sector: str | None = Query(default=None, max_length=120),
    project: str | None = Query(default=None, max_length=160),
    as_of: str | None = Query(default=None, max_length=40),
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    user_id: str = Depends(auth),
) -> Any:
    pack = store.assemble_context(
        user_id,
        task,
        surface=surface,
        token_budget=token_budget,
        sector=sector,
        project=project,
        as_of=as_of,
        intent=intent,
        # Reaching this endpoint already required the read scope, and the identity layer is a read
        # of distilled context — so it is always included here.
        include_identity=True,
        format=format,
    )
    return _context_response(pack, format)


@app.post("/v1/context", response_model=None)
def post_context(body: dict[str, Any], request: Request, user_id: str = Depends(auth)) -> Any:
    format = str(body.get("format") or "json").strip().lower()
    if format not in {"json", "markdown"}:
        raise HTTPException(status_code=422, detail="format must be json or markdown")
    try:
        token_budget = int(body.get("token_budget") or 2000)
    except (TypeError, ValueError):
        raise HTTPException(status_code=422, detail="token_budget must be an integer")
    pack = store.assemble_context(
        user_id,
        str(body.get("task") or ""),
        surface=str(body.get("surface") or "agent"),
        token_budget=token_budget,
        sector=str(body.get("sector") or "") or None,
        project=str(body.get("project") or "") or None,
        as_of=str(body.get("as_of") or "") or None,
        intent=str(body.get("intent") or "") or None,
        include_identity=_bearer_has_export_scope(request),
        format=format,
        pin=bool(body.get("pin")),
        session_id=str(body.get("session_id") or "") or None,
    )
    return _context_response(pack, format)


@app.get("/v1/context/packs", response_model=None)
def list_context_packs(
    session_id: str | None = Query(default=None, max_length=80),
    limit: int = Query(default=20, ge=1, le=100),
    user_id: str = Depends(auth),
) -> Any:
    return store.list_context_packs(user_id, session_id=session_id, limit=limit)


@app.get("/v1/context/packs/{pack_sha}", response_model=None)
def get_context_pack(pack_sha: str, user_id: str = Depends(auth)) -> Any:
    try:
        return store.get_context_pack(user_id, pack_sha)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.post("/v1/context/packs/{pack_sha}/verify", response_model=None)
def verify_context_pack(pack_sha: str, user_id: str = Depends(auth)) -> Any:
    """Phase 2b diagnostic: recompute the pack with its stored inputs and diff. POST because
    it does work (a full context assembly) and emits an audit event, unlike the pure reads."""
    try:
        return store.verify_context_pack(user_id, pack_sha)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.get("/v1/personal-profile", response_model=None)
def personal_profile(
    query: str = "",
    limit: int = Query(default=6, ge=1, le=20),
    include_pending: bool = Query(default=False),
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    sector: str | None = Query(default=None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any] | Response:
    profile = store.personal_profile(user_id, query=query, limit=limit, include_pending=include_pending, sector=sector)
    if format == "markdown":
        return Response(content=profile["markdown"], media_type="text/markdown")
    return profile


@app.get("/v1/agent-adaptation", response_model=None)
def agent_adaptation(
    query: str = "",
    target: str = Query(default="assistant", max_length=80),
    limit: int = Query(default=8, ge=1, le=20),
    include_pending: bool = Query(default=False),
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    sector: str | None = Query(default=None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any] | Response:
    adaptation = store.agent_adaptation(user_id, query=query, target=target, limit=limit, include_pending=include_pending, sector=sector)
    if format == "markdown":
        return Response(content=adaptation["markdown"], media_type="text/markdown")
    return adaptation


@app.delete("/v1/memories/{memory_id}")
def forget_memory(memory_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    deleted = store.delete_memory(user_id, memory_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Memory not found")
    return {"deleted": True}


@app.post("/v1/captures/{capture_id}/approve")
def approve_capture(capture_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    approved = store.approve_capture(user_id, capture_id)
    if not approved:
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"approved": True}


@app.post("/v1/captures/{capture_id}/archive")
def archive_capture(capture_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    archived = store.archive_capture(user_id, capture_id)
    if not archived:
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"archived": True}


@app.delete("/v1/captures/{capture_id}")
def delete_capture(capture_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    deleted = store.delete_capture(user_id, capture_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Capture not found")
    return {"deleted": True}


@app.get("/v1/graph", response_model=GraphResponse)
def graph(limit: int = Query(default=150, ge=10, le=500), user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.graph(user_id, limit)


@app.get("/v1/entity/{entity_id}/neighborhood", response_model=None)
def entity_neighborhood(
    entity_id: str,
    limit: int = Query(default=8, ge=1, le=24),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    result = store.entity_neighborhood(user_id, entity_id, limit=limit)
    if result is None:
        raise HTTPException(status_code=404, detail="Entity not in graph")
    return result


@app.get("/v1/stats", response_model=StatsResponse)
def stats(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.stats(user_id)


@app.get("/v1/mirror")
def mirror(user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"insight": store.mirror_insight(user_id)}


@app.get("/v1/profile", response_model=None)
def profile(
    limit: int = Query(6, ge=1, le=20),
    include_pending: bool = Query(False),
    sector: str | None = Query(None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.build_profile(user_id, limit=limit, include_pending=include_pending, sector=sector)


@app.get("/v1/person-map", response_model=None)
def person_map(
    include_pending: bool = Query(False),
    sector: str | None = Query(None, max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return store.person_map(user_id, include_pending=include_pending, sector=sector)


@app.get("/v1/memory/quality", response_model=MemoryQualityResponse)
def memory_quality(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.memory_quality_report(user_id)


@app.get("/v1/memory/conflicts", response_model=None)
def memory_conflicts(user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"conflicts": store.detect_conflicts(user_id)}


@app.post("/v1/memory/conflicts/resolve", response_model=None)
def resolve_memory_conflict(payload: dict[str, Any], user_id: str = Depends(auth)) -> dict[str, Any]:
    stale_id = str(payload.get("stale_id") or "").strip()
    current_id = str(payload.get("current_id") or "").strip()
    if not stale_id or not current_id:
        raise HTTPException(status_code=422, detail="stale_id and current_id are required")
    resolved = store.resolve_conflict(user_id, stale_id=stale_id, current_id=current_id)
    if not resolved:
        raise HTTPException(status_code=404, detail="Both memories must exist and differ")
    return {"resolved": True, "stale_id": stale_id, "current_id": current_id}


@app.get("/v1/settings", response_model=SettingsResponse)
def get_settings(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.settings(user_id)


@app.put("/v1/settings", response_model=SettingsResponse)
def update_settings(request: SettingsUpdateRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.update_settings(user_id, request.model_dump(exclude_none=True))


@app.get("/v1/trust/summary")
def trust_summary(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.trust_summary(user_id)


@app.get("/v1/privacy/lifecycle", response_model=DataLifecycleReportResponse)
def data_lifecycle_report(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.data_lifecycle_report(user_id)


@app.post("/v1/integrations/mcp-token", response_model=MCPTokenRegistrationResponse)
def register_mcp_token(request: MCPTokenRegistrationRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.ensure_mcp_token(
        user_id,
        request.token,
        label=request.label,
        scopes=request.scopes,
    )


@app.post("/v1/integrations/api-token", response_model=APITokenRegistrationResponse)
def register_api_token(request: APITokenRegistrationRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.ensure_api_token(
        user_id,
        request.token,
        label=request.label,
        scopes=request.scopes,
    )


@app.get("/v1/integrations/tokens", response_model=APITokenListResponse)
def list_integration_tokens(
    audience: str | None = Query(default=None, pattern="^(api|mcp)$"),
    include_revoked: bool = Query(default=False),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    return {"results": store.list_tokens(user_id, audience=audience, include_revoked=include_revoked)}


@app.delete("/v1/integrations/tokens/{token_id}", response_model=APITokenRevokeResponse)
def revoke_integration_token(token_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    revoked = store.revoke_token(user_id, token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Token not found")
    return revoked


@app.post("/v1/admin/users", response_model=UserProvisionResponse, status_code=201)
def admin_provision_user(request: UserProvisionRequest, _admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    try:
        return store.provision_user(
            request.user_id,
            display_name=request.display_name,
            plan=request.plan,
            metadata=request.metadata,
            api_scopes=request.api_scopes,
            mcp_scopes=request.mcp_scopes if request.mcp_scopes is not None else ["read"],
            allow_existing=request.allow_existing,
        )
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/v1/admin/users", response_model=UserListResponse)
def admin_list_users(
    status_filter: str | None = Query(default=None, alias="status", pattern="^(active|suspended|deleted)$"),
    limit: int = Query(default=100, ge=1, le=1000),
    _admin: bool = Depends(admin_auth),
) -> dict[str, Any]:
    users = store.list_users(limit=limit, status=status_filter)
    return {"results": users, "total": len(users)}


@app.post("/v1/admin/users/{user_id}/suspend", response_model=UserStatusResponse)
def admin_suspend_user(user_id: str, _admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    user = store.suspend_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user}


@app.post("/v1/admin/users/{user_id}/reactivate", response_model=UserStatusResponse)
def admin_reactivate_user(user_id: str, _admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    user = store.reactivate_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"user": user}


@app.delete("/v1/admin/users/{user_id}")
def admin_deprovision_user(user_id: str, _admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    try:
        deleted = store.deprovision_user(user_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"user_id": user_id, "deprovisioned": True, "deleted": deleted}


@app.get("/v1/audit-log", response_model=ListResponse)
def audit_log(limit: int = Query(default=80, ge=1, le=300), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.audit_log(user_id, limit)}


@app.get("/v1/sync/changes", response_model=SyncChangeFeedResponse)
def sync_changes(
    after: str = Query(default="", max_length=120),
    limit: int = Query(default=100, ge=1, le=1000),
    device_id: str = Query(default="", max_length=120),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    try:
        shard = store.assignment_for(user_id).as_dict()
    except Exception:
        shard = None
    return store.sync_change_feed(
        user_id,
        after=after,
        limit=limit,
        device_id=device_id,
        signing_key=settings.sync_signing_key,
        shard=shard,
    )


@app.get("/v1/sync/captures", response_model=CaptureChangePage)
def sync_captures(
    after_seq: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=500),
    user_id: str = Depends(auth),
) -> dict[str, Any]:
    """Phase-2 LOCAL outbound feed: captures created after `after_seq` (monotonic rowid), WITH
    content, so the desktop app can push them to the signed-in user's hosted account. Oldest first."""
    return store.capture_change_page(user_id, after_seq, limit)


@app.post("/v1/sync/ingest", response_model=SyncIngestResponse)
def sync_ingest(request: SyncIngestRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    """Phase-2 HOSTED inbound: apply a batch of pushed captures into the signed-in user's store.
    Idempotent — each item carries the client's stable capture id (capture_id_override ->
    ON CONFLICT(id) DO UPDATE), so re-pushing a batch is a safe no-op upsert. Re-uses the mature
    extract + save path, and records a device high-watermark receipt (best-effort)."""
    _enforce_memory_quota(user_id)
    aliases = store.settings(user_id).get("identity_aliases")
    results: list[dict[str, Any]] = []
    for item in request.items:
        extracted = extract_context(item.content, item.source, author_aliases=aliases)
        if item.captured_at:
            extracted["_timestamp"] = item.captured_at
        saved = store.save_capture(
            user_id=user_id,
            content=item.content,
            source=item.source,
            source_url=item.source_url,
            title=item.title,
            extracted=extracted,
            capture_id_override=item.client_capture_id,
            cite_capture_provenance=True,
            auto_approve=settings.auto_approve_captures,
        )
        results.append({
            "client_capture_id": item.client_capture_id,
            "capture_id": saved.get("capture_id", ""),
            "status": "accepted",
        })
    if request.device_id and request.cursor:
        try:
            store.record_sync_receipt(
                user_id, request.device_id, cursor=request.cursor,
                status="accepted", stats={"count": len(results)},
            )
        except Exception:
            pass  # best-effort high-watermark; a missing/revoked device must not fail ingest
    return {"applied": len(results), "cursor": request.cursor, "results": results}


@app.get("/v1/diagnostics", response_model=DiagnosticsResponse)
def diagnostics(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.diagnostics(user_id)


@app.get("/v1/reliability/report", response_model=ReliabilityReportResponse)
def reliability_report(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.reliability_report(user_id)


@app.get("/v1/support/bundle", response_model=SupportBundleResponse)
def support_bundle(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.support_bundle(user_id)


@app.post("/v1/backups", response_model=BackupResponse)
def create_backup(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.create_backup(user_id)


@app.post("/v1/backups/restore-latest")
def restore_latest_backup(user_id: str = Depends(auth)) -> dict[str, Any]:
    try:
        return store.restore_latest_backup(user_id)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.delete("/v1/backups")
def delete_backups(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.delete_backups(user_id)


@app.delete("/v1/user-data")
def delete_user_data(include_backups: bool = Query(default=True), user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.delete_user_data(user_id, include_backups=include_backups)


@app.post("/v1/maintenance/repair-storage", response_model=RepairStorageResponse)
def repair_storage(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.repair_storage(user_id)


@app.post("/v1/maintenance/rebuild-search", response_model=MaintenanceResponse)
def rebuild_search(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.rebuild_search_index(user_id)


@app.post("/v1/maintenance/rebuild-vectors", response_model=VectorRebuildResponse)
def rebuild_vectors(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.rebuild_vectors(user_id)


@app.post("/v1/maintenance/rebuild-index-from-vault", response_model=VaultRebuildResponse)
def rebuild_index_from_vault(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.rebuild_index_from_vault(user_id)


@app.post("/v1/vault/reconcile")
def reconcile_vault_edits(user_id: str = Depends(auth)) -> dict[str, Any]:
    """Two-way editing: reconcile the index with the user's hand-edited Markdown notes. The app's
    vault file-watcher (or a manual "Sync from vault" action) calls this after edits settle."""
    return store.reconcile_vault_edits(user_id)


@app.get("/v1/export.json")
def export_json(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.export_json(user_id)


@app.get("/v1/export.md")
def export_markdown(user_id: str = Depends(auth)) -> Response:
    return Response(content=store.export_markdown(user_id), media_type="text/markdown")


@app.get("/.well-known/cortex.json")
def manifest() -> dict[str, Any]:
    return {
        "name": "Cortex",
        "description": "Shared memory for AI assistants.",
        "api": {"base_url": settings.public_base_url, "version": BACKEND_VERSION},
        "health": store.health_payload(mode="fastapi", auth=bool(settings.api_key)),
        "mcp": {
            "endpoint": "/mcp",
            "tools": [tool["name"] for tool in TOOLS],
            "core_tools": sorted(CORE_TOOL_NAMES),
        },
        "context_engine": {"endpoint": "/v1/context"},
    }


# ===========================================================================
# Accounts + first-party auth (docs/ACCOUNTS_ENCRYPTION_DESIGN.md sections 1-4,
# build-plan steps 5-7). Hosted plane only: everything below is inert unless
# settings.auth_enabled — when disabled, auth_runtime stays None and every
# /v1/auth endpoint answers exactly like an unregistered route (404 Not Found),
# so the server behaves byte-identically to a build without this section.
# ===========================================================================

_auth_logger = logging.getLogger("cortex.auth")

APP_LOGIN_FLOW_TTL_SECONDS = 600
ACCOUNT_CLAIM_TTL_SECONDS = 72 * 60 * 60
DEFAULT_SELF_SERVE_API_SCOPES = ("read", "write")
DEFAULT_SELF_SERVE_MCP_SCOPES = ("read",)


class AuthRuntime:
    """All accounts-plane singletons, built once at startup (and rebuilt by
    tests via init_auth_runtime after swapping module globals):

    - SQLiteControlStore at settings.accounts_db_path (default:
      <shard_root>/control/accounts.sqlite, else <vault parent>/accounts.sqlite)
    - AccountsService with TTL overrides from settings, a limiter bridged onto
      the existing token-bucket machinery (keyed per action + identifier/IP,
      budget settings.auth_rate_limit_per_minute), and a log-mode flow delivery
      that prints emailed single-use tokens clearly (self-hosting default).
    - UserKeyring when a KEK is configured (CORTEX_KEK / CORTEX_KEK_FILE); it
      is also handed to the StoreRegistry so shard vaults encrypt credentials.
    - The OIDC/OAuth2 provider registry (Google, GitHub, disabled 'openai').
    """

    def __init__(self, active_settings: Any, registry: StoreRegistry) -> None:
        from .accounts import SQLiteControlStore

        self.settings = active_settings
        accounts_db = active_settings.accounts_db_path
        if accounts_db is None:
            if active_settings.shard_root is not None:
                accounts_db = active_settings.shard_root / "control" / "accounts.sqlite"
            else:
                accounts_db = active_settings.vault_path.parent / "accounts.sqlite"
        self.accounts_db_path = accounts_db
        self.control_store = SQLiteControlStore(accounts_db)
        self.limiter = TokenBucketRateLimiter(active_settings.auth_rate_limit_per_minute)
        # Log-mode deliveries also land here so operators/tests can retrieve a
        # just-issued token without scraping stdout; bounded, newest last.
        self.outbox: deque[dict[str, str]] = deque(maxlen=50)
        # Email sender (email_verify / password_reset / account_claim links).
        # Built once from auth_email_mode; a misconfigured smtp mode degrades to
        # the log sink so a bad mail setup never breaks auth. Tests inject via
        # the module-level _email_sender_factory seam.
        self.email_sender = self._build_email_sender(active_settings)
        self.keyring = self._build_keyring(accounts_db)
        if self.keyring is not None:
            registry.keyring = self.keyring
        ttl_overrides: dict[str, int] = {}
        if active_settings.auth_access_ttl_seconds > 0:
            ttl_overrides["access_ttl_seconds"] = active_settings.auth_access_ttl_seconds
        if active_settings.auth_refresh_idle_ttl_seconds > 0:
            ttl_overrides["refresh_idle_ttl_seconds"] = active_settings.auth_refresh_idle_ttl_seconds
        if active_settings.auth_refresh_absolute_ttl_seconds > 0:
            ttl_overrides["refresh_absolute_ttl_seconds"] = active_settings.auth_refresh_absolute_ttl_seconds
        self.service = AccountsService(
            self.control_store,
            limiter=self._limit,
            flow_delivery=self._deliver_flow,
            **ttl_overrides,
        )
        self.oidc = OidcProviderRegistry.from_settings(active_settings, self.control_store)

    @staticmethod
    def _build_keyring(accounts_db: Any):
        # Lazy import keeps `cryptography` off the path until auth is enabled.
        from .keyring import LocalKekProvider, UserKeyring

        provider = LocalKekProvider()  # KekConfigError on malformed KEK: fail loudly at boot
        if not provider.available:
            return None
        return UserKeyring(accounts_db.parent / "keyring.sqlite", provider)

    def _build_email_sender(self, active_settings: Any):
        """Pick the delivery backend once at startup.

        - A test-installed ``_email_sender_factory`` wins (dependency-injection
          seam), receiving (settings, outbox) and returning any EmailSender.
        - ``auth_email_mode == "smtp"`` with a configured host -> SmtpEmailSender.
        - ``smtp`` mode WITHOUT a host -> warn + LogEmailSender (misconfig degrades
          to the current behavior rather than breaking auth).
        - anything else -> LogEmailSender (the beta/self-hosting default).
        """
        # Lazy import: keep email_sender off the path unless auth is enabled.
        from .email_sender import LogEmailSender, SmtpEmailSender

        factory = _email_sender_factory
        if factory is not None:
            return factory(active_settings, self.outbox)
        if active_settings.auth_email_mode == "smtp":
            if active_settings.smtp_host:
                return SmtpEmailSender(
                    active_settings.smtp_host,
                    active_settings.smtp_port,
                    username=active_settings.smtp_username,
                    password=active_settings.smtp_password,
                    from_addr=active_settings.smtp_from_addr,
                    use_starttls=active_settings.smtp_use_starttls,
                    use_ssl=active_settings.smtp_use_ssl,
                )
            _auth_logger.warning(
                "CORTEX_AUTH_EMAIL_MODE=smtp but CORTEX_SMTP_HOST is unset; "
                "degrading to the log sink so auth still works."
            )
        # LogEmailSender keeps its own deque; _deliver_flow already writes the
        # canonical {kind, email, token} record to self.outbox that tests read.
        return LogEmailSender()

    # -- injectable hooks ---------------------------------------------------
    def _limit(self, action: str, key: str) -> bool:
        allowed, _retry = self.limiter.check(f"{action}:{key}")
        return allowed

    def _deliver_flow(self, kind: str, email: str, token: str) -> None:
        # Always keep the canonical record so nothing is lost — tests and
        # beta/log mode retrieve just-issued tokens from here without scraping
        # stdout, and it survives a failed SMTP send below.
        self.outbox.append({"kind": kind, "email": email, "token": token})
        # Render the email once (pure function) and hand it to the sender. The
        # front-door link is built from public_app_url, falling back to
        # public_base_url when unset.
        from .email_sender import EmailSendError, render_auth_email

        app_url = self.settings.public_app_url or self.settings.public_base_url
        try:
            subject, body = render_auth_email(kind, email, token, app_url=app_url)
            self.email_sender.send(email, subject, body)
        except EmailSendError as exc:
            # SMTP outage: log and keep the outbox entry. Never raise out of the
            # delivery hook — signup/login must not 500 on a mail failure.
            _auth_logger.error("email delivery failed for %s (%s): %s", email, kind, exc)
        except Exception as exc:  # noqa: BLE001 - defensive: never break auth on delivery
            _auth_logger.error("unexpected error delivering %s email to %s: %s", kind, email, exc)


auth_runtime: AuthRuntime | None = None

# Dependency-injection seam for tests: when set to a callable (settings, outbox)
# -> EmailSender, AuthRuntime uses it instead of building an SMTP/log sender from
# settings. None in production. Set/reset around a test; init_auth_runtime picks
# it up on the next rebuild.
_email_sender_factory: Any = None


def init_auth_runtime() -> AuthRuntime | None:
    """(Re)build the auth runtime from the CURRENT module globals. Called once
    at import; tests that swap main_module.settings/store call it again (and
    reset auth_runtime = None on teardown)."""
    global auth_runtime
    auth_runtime = AuthRuntime(settings, store) if settings.auth_enabled else None
    return auth_runtime


init_auth_runtime()


def _auth_runtime_or_404() -> AuthRuntime:
    if auth_runtime is None:
        # Auth disabled: indistinguishable from a route that was never
        # registered (FastAPI's default 404 body is {"detail": "Not Found"}).
        raise HTTPException(status_code=404, detail="Not Found")
    return auth_runtime


def _session_data_plane_user(token: str, x_cortex_user: str | None) -> str:
    """auth() branch for Bearer cxs_: one indexed session lookup -> user_id,
    honoring account status (inside verify_session) AND control-plane user
    status. Never grants admin; never accepted by admin_auth/mcp_auth."""
    runtime = auth_runtime
    if runtime is None:
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex API token")
    session = runtime.service.verify_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex API token")
    user_id = str(session["user_id"])
    if x_cortex_user and x_cortex_user != user_id:
        raise HTTPException(status_code=403, detail="Cortex session does not match requested user")
    registry_user = store.get_user(user_id)
    if registry_user is not None and str(registry_user.get("status") or "") != "active":
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex API token")
    _enforce_rate_limit(user_id)
    return user_id


def session_auth(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    """Dependency for the session-authed account surface (/v1/auth/*). Returns
    verify_session's payload: {'account', 'session_id', 'user_id', 'client'}."""
    runtime = _auth_runtime_or_404()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    token = authorization.split(" ", 1)[1].strip()
    if not token.startswith(SESSION_TOKEN_PREFIX):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    session = runtime.service.verify_session(token)
    if session is None:
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    return session


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _auth_rate_limit(runtime: AuthRuntime, action: str, request: Request) -> None:
    """Per-(action, client IP) throttle applied BEFORE any hashing work; the
    AccountsService limiter additionally throttles per (action, identifier)."""
    allowed, retry_after = runtime.limiter.check(f"{action}:ip:{_client_ip(request)}")
    if not allowed:
        raise HTTPException(
            status_code=429,
            detail="Too many authentication attempts; slow down and retry.",
            headers={"Retry-After": str(max(1, int(retry_after) + 1))},
        )


_TURNSTILE_SITEVERIFY_URL = "https://challenges.cloudflare.com/turnstile/v0/siteverify"


def _verify_turnstile(request: Request, token: str) -> None:
    """Cloudflare Turnstile server-side gate for public signup. DORMANT unless
    ``settings.turnstile_enabled`` (both site key + secret set) — when disabled
    this is a no-op and signup behaves exactly as before.

    When enabled, a missing token is rejected (400) and the token is verified
    against the Turnstile siteverify endpoint (with the secret + client IP). A
    verification failure is 403. Called BEFORE any argon2 hashing so a bot flood
    cannot burn CPU. A siteverify network/parse error fails closed (403) so a
    flood can't slip through by knocking the verifier offline."""
    if not settings.turnstile_enabled:
        return
    token = (token or "").strip()
    if not token:
        raise HTTPException(status_code=400, detail="Turnstile verification required.")
    import urllib.error
    import urllib.parse
    import urllib.request

    fields = {"secret": settings.turnstile_secret, "response": token}
    remote_ip = _client_ip(request)
    if remote_ip and remote_ip != "unknown":
        fields["remoteip"] = remote_ip
    data = urllib.parse.urlencode(fields).encode("ascii")
    req = urllib.request.Request(
        _TURNSTILE_SITEVERIFY_URL,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:  # noqa: S310 (fixed https URL)
            body = resp.read().decode("utf-8")
        outcome = json.loads(body)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
        _auth_logger.warning("Turnstile siteverify failed: %s", exc)
        raise HTTPException(status_code=403, detail="Turnstile verification failed.") from exc
    if not (isinstance(outcome, dict) and outcome.get("success") is True):
        raise HTTPException(status_code=403, detail="Turnstile verification failed.")


def _public_account(account: dict[str, Any]) -> dict[str, Any]:
    return {
        "account_id": account.get("account_id"),
        "user_id": account.get("user_id"),
        "email": account.get("primary_email"),
        "display_name": account.get("display_name") or "",
        "status": account.get("status"),
        "email_verified": bool(account.get("email_verified_at")),
        "created_at": account.get("created_at"),
    }


def _session_pair_payload(session: dict[str, Any]) -> dict[str, Any]:
    return {
        "token_type": "bearer",
        "access_token": session["access"],
        "refresh_token": session["refresh"],
        "session_id": session["session_id"],
        "account": _public_account(session["account"]),
    }


def _ensure_account_provisioned(account: dict[str, Any]) -> None:
    """Activation -> provisioning: run the provision_user internals WITHOUT
    auto-minting tokens (sessions are the token factory now). Users that were
    already provisioned (invite/claim path) only get their shard materialized —
    re-registering would clobber operator-set display_name/plan."""
    user_id = str(account.get("user_id") or "").strip()
    if not user_id:
        return
    if store.get_user(user_id) is None:
        store.provision_user(
            user_id,
            display_name=str(account.get("display_name") or ""),
            mint_tokens=False,
        )
    else:
        store.store_for_user(user_id)  # materialize the shard (db + vault)


def _consume_secret_flow(runtime: AuthRuntime, token: str, expected_kind: str) -> dict[str, Any]:
    """Validate + atomically consume a '<flow_id>.<secret>' bearer token
    (same salted-hash discipline as AccountsService flows). Uniform failure."""
    flow_id, sep, secret = (token or "").partition(".")
    if not sep or not flow_id or not secret:
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    flow = runtime.control_store.get_flow(flow_id)
    if flow is None or str(flow.get("kind") or "") != expected_kind:
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    stored = str(flow.get("secret_hash") or "")
    salt, sep2, digest = stored.partition("$")
    if not sep2 or not hmac.compare_digest(token_verify_hash(secret, salt), digest):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    now_iso = iso_utc(utc_now())
    if str(flow.get("expires_at") or "") <= now_iso:
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    consumed = runtime.control_store.consume_flow(flow_id, now_iso)
    if consumed is None:
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    return consumed


def _default_oauth_redirect(provider: str) -> str:
    return f"{settings.public_app_url.rstrip('/')}/v1/auth/oauth/{provider}/callback"


# --------------------------------------------------------- email + password

@app.post("/v1/auth/signup")
def auth_signup(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "signup", request)
    # Bot/DoS gate: verify the Cloudflare Turnstile token (if enabled) BEFORE any
    # argon2id hashing so a bot flood can't burn CPU. No-op when unconfigured.
    _verify_turnstile(request, str(payload.get("turnstile_token") or ""))
    email = str(payload.get("email") or "")
    try:
        result = runtime.service.signup(
            email,
            str(payload.get("password") or ""),
            display_name=str(payload.get("display_name") or "")[:160],
        )
    except RateLimited:
        raise HTTPException(status_code=429, detail="rate limited")
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if settings.auth_autoverify:
        # BETA mode (CORTEX_AUTH_AUTOVERIFY=1): no email infrastructure — activate and
        # provision at signup. The response stays the same generic shape either way, so
        # enumeration resistance is unchanged; only server-side state differs.
        try:
            normalized = email.strip().lower()
            account = runtime.control_store.get_account_by_email(normalized)
            if account and account.get("status") == "pending_verification":
                now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
                account = runtime.control_store.update_account_fields(
                    account["account_id"], now=now_iso, status="active", email_verified_at=now_iso
                )
                _ensure_account_provisioned(account)
        except Exception:
            # Best-effort: a failed autoverify leaves a pending account, never a 500 on signup.
            pass
    return result


@app.post("/v1/auth/verify-email")
def auth_verify_email(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "verify_email", request)
    try:
        account = runtime.service.verify_email(str(payload.get("token") or ""))
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    _ensure_account_provisioned(account)
    return {"ok": True, "account": _public_account(account)}


@app.post("/v1/auth/login")
def auth_login(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "login", request)
    try:
        session = runtime.service.login(
            str(payload.get("email") or ""),
            str(payload.get("password") or ""),
            client=str(payload.get("client") or "web")[:20],
            ip=_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        )
    except RateLimited:
        raise HTTPException(status_code=429, detail="rate limited")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _session_pair_payload(session)


@app.post("/v1/auth/refresh")
def auth_refresh(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "refresh", request)
    try:
        session = runtime.service.refresh(str(payload.get("refresh_token") or ""))
    except RateLimited:
        raise HTTPException(status_code=429, detail="rate limited")
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return _session_pair_payload(session)


@app.post("/v1/auth/logout")
def auth_logout(authorization: str | None = Header(default=None)) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    try:
        runtime.service.logout(authorization.split(" ", 1)[1].strip())
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True}


@app.get("/v1/auth/session")
def auth_session(session: dict[str, Any] = Depends(session_auth)) -> dict[str, Any]:
    return {
        "account": _public_account(session["account"]),
        "session_id": session["session_id"],
        "user_id": session["user_id"],
        "client": session["client"],
    }


@app.post("/v1/auth/password/reset/request")
def auth_password_reset_request(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "password_reset", request)
    try:
        return runtime.service.request_password_reset(str(payload.get("email") or ""))
    except RateLimited:
        raise HTTPException(status_code=429, detail="rate limited")


@app.post("/v1/auth/password/reset/confirm")
def auth_password_reset_confirm(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "password_reset_confirm", request)
    try:
        runtime.service.confirm_password_reset(
            str(payload.get("token") or ""), str(payload.get("new_password") or "")
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"ok": True}


# ------------------------------------------------------------------- OAuth

@app.get("/v1/auth/providers")
def auth_providers() -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    return {"results": runtime.oidc.enabled_providers()}


APP_FLOW_COOKIE_NAME = "df_af"


def _app_flow_cookie(flow_id: str) -> str:
    """Signed value that binds a browser to a desktop app-login poll flow. It is set as an HttpOnly
    cookie when the browser opens /account/login?app_flow=<flow_id> (webauth.py) and required back at
    the OAuth start below. This prevents a login-CSRF / flow-fixation account takeover: without it,
    an attacker who started their own poll flow could send a victim a provider-start link and have
    the victim's OAuth completion attach the victim's account to the attacker's flow."""
    key = (settings.sync_signing_key or "").encode()
    sig = hmac.new(key, flow_id.encode(), hashlib.sha256).hexdigest()
    return f"{flow_id}.{sig}"


def _app_flow_cookie_valid(cookie: str | None, flow_id: str) -> bool:
    if not cookie or not flow_id or not (settings.sync_signing_key or ""):
        return False
    return hmac.compare_digest(cookie, _app_flow_cookie(flow_id))


def _wants_html(request: Request) -> bool:
    """True when the caller is a browser navigation (Accept: text/html), false for the desktop app /
    JSON API clients (Accept: application/json or */*). Lets the OAuth start/callback endpoints keep
    their JSON contract for the app while giving a real browser a 302 redirect through the flow."""
    return "text/html" in (request.headers.get("accept") or "").lower()


@app.get("/v1/auth/oauth/{provider}/start")
def auth_oauth_start(
    provider: str,
    request: Request,
    redirect_uri: str = Query(default="", max_length=500),
    app_flow: str = Query(default="", max_length=120),
) -> Any:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "oauth_start", request)
    # Login-CSRF / flow-fixation guard for the desktop app-login handoff: only honor app_flow when
    # this browser opened the app-login page and carries the matching signed cookie. Otherwise drop
    # it and treat this as an ordinary web sign-in (mints a session for THIS browser only), so a
    # victim's OAuth completion can never be attached to an attacker-owned poll flow.
    bound_flow = app_flow if _app_flow_cookie_valid(request.cookies.get(APP_FLOW_COOKIE_NAME), app_flow) else ""
    try:
        result = runtime.oidc.start(
            provider,
            redirect_uri or _default_oauth_redirect(provider),
            app_flow_id=bound_flow or None,
        )
    except OidcError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # A browser (the "Continue with X" button on the web login page) must be REDIRECTED to the
    # provider; only the app/JSON clients get the {authorize_url,...} body.
    if _wants_html(request):
        return RedirectResponse(result["authorize_url"], status_code=302)
    return result


# Shown in the browser after a successful desktop app-login OAuth handoff. Tokens are delivered to
# the app via its poll (never in a URL), so this page only tells the user they can return to the app.
_APP_LOGIN_DONE_HTML = (
    '<!doctype html><html lang="en"><head><meta charset="utf-8">'
    '<meta name="viewport" content="width=device-width, initial-scale=1">'
    "<title>Signed in · Doppl</title>"
    "<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;"
    "background:#faf9f7;color:#1a1a1a;display:flex;min-height:100vh;margin:0;"
    "align-items:center;justify-content:center;text-align:center}"
    ".card{max-width:420px;padding:40px}h1{font-size:22px;margin:0 0 10px}"
    "p{color:#666;line-height:1.5}</style></head><body><div class=\"card\">"
    "<h1>You’re signed in ✓</h1><p>You can close this tab and return to Doppl — "
    "your account is ready.</p></div></body></html>"
)


@app.get("/v1/auth/oauth/{provider}/callback")
def auth_oauth_callback(
    provider: str,
    request: Request,
    code: str = Query(default="", max_length=4000),
    state: str = Query(default="", max_length=500),
    error: str = Query(default="", max_length=500),
) -> Any:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "oauth_callback", request)
    wants_html = _wants_html(request)

    def _fail(detail: str, status: int) -> Any:
        # A browser gets bounced to the completion page (no tokens) — its JS shows a friendly
        # "couldn't complete sign in" with a link back to login; the JSON API keeps its error code.
        if wants_html:
            return RedirectResponse("/account/oauth/complete", status_code=302)
        raise HTTPException(status_code=status, detail=detail)

    if error:
        return _fail("The provider did not authorize Cortex.", 400)
    try:
        identity = runtime.oidc.complete(provider, state=state, code=code)
    except OidcError as exc:
        _auth_logger.info("oauth callback rejected for %s: %s", provider, exc)
        return _fail(GENERIC_AUTH_FAILURE, 401)
    try:
        result = runtime.service.find_or_challenge_identity(
            provider,
            str(identity["subject"]),
            email=identity.get("email"),
            email_verified=bool(identity.get("email_verified")),
            display_name=str(identity.get("display_name") or "")[:160],
            profile={"provider": provider},
        )
    except AuthError as exc:
        return _fail(str(exc), 401)
    if result["action"] == "link_required":
        # Never-silent-auto-link: the user must authenticate with the existing
        # method, then complete the link via POST /v1/auth/oauth/{provider}/link.
        if wants_html:
            # The browser flow has no in-page challenge handoff; bounce to the completion
            # page with a marker so its JS explains "already have an account — sign in first".
            return RedirectResponse(
                "/account/oauth/complete?link_required=1", status_code=302
            )
        return JSONResponse(
            status_code=409,
            content={
                "action": "link_required",
                "flow_id": result["flow_id"],
                "challenge": result["challenge"],
                "detail": "Sign in with your existing method, then link this provider.",
            },
        )
    account = result["account"]
    if str(account.get("status") or "") == "active":
        # OAuth auto-signup with a provider-verified email activates directly;
        # provision the user now (no tokens minted).
        _ensure_account_provisioned(account)
    app_flow_id = identity.get("app_flow_id")
    if app_flow_id:
        # App login: NO token ever rides a redirect URL. The callback completes
        # the flow server-side; the app polls the pair out with its poll_secret.
        _complete_app_login_flow(runtime, str(app_flow_id), account)
        if wants_html:
            # The desktop handoff finished in the browser: show the "return to Doppl" page.
            return HTMLResponse(content=_APP_LOGIN_DONE_HTML)
        return {"action": result["action"], "status": "complete_in_app"}
    session = runtime.service.mint_session(
        account,
        client="web",
        ip=_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
    )
    if wants_html:
        # Web sign-in: hand the session pair to the browser in the URL FRAGMENT (never the
        # query) so tokens never reach the server access log or Referer header. The completion
        # page's JS reads them out of location.hash into localStorage, then scrubs the hash.
        fragment = urlencode(
            {"access_token": session["access"], "refresh_token": session["refresh"]}
        )
        return RedirectResponse(
            f"/account/oauth/complete#{fragment}", status_code=302
        )
    return {"action": result["action"], **_session_pair_payload(session)}


@app.post("/v1/auth/oauth/{provider}/native")
def auth_oauth_native(provider: str, payload: dict[str, Any], request: Request) -> Any:
    """Native-client sign-in (e.g. Sign in with Apple on macOS/iOS): the app performs the provider
    flow with the OS and posts the resulting id_token here. We verify it (full OIDC), then take the
    same find-or-create-identity + mint-session path as the browser callback. No token ever rides a
    redirect; the app receives the session pair directly in this response."""
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "oauth_native", request)
    try:
        identity = runtime.oidc.verify_native_id_token(
            provider,
            str(payload.get("id_token") or ""),
            nonce=str(payload.get("nonce") or "") or None,
        )
    except OidcError as exc:
        _auth_logger.info("native oauth rejected for %s: %s", provider, exc)
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE) from exc
    try:
        result = runtime.service.find_or_challenge_identity(
            provider,
            str(identity["subject"]),
            email=identity.get("email"),
            email_verified=bool(identity.get("email_verified")),
            # Apple only returns the name on the FIRST authorization (never in the id_token), so the
            # app forwards it in the payload; fall back to any name the token carried.
            display_name=str(payload.get("display_name") or identity.get("display_name") or "")[:160],
            profile={"provider": provider},
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    if result["action"] == "link_required":
        # Never silent-auto-link: the user must authenticate with the existing method first.
        return JSONResponse(
            status_code=409,
            content={
                "action": "link_required",
                "flow_id": result["flow_id"],
                "challenge": result["challenge"],
                "detail": "Sign in with your existing method, then link this provider.",
            },
        )
    account = result["account"]
    if str(account.get("status") or "") == "active":
        _ensure_account_provisioned(account)
    session = runtime.service.mint_session(
        account,
        client=str(payload.get("client") or "macos")[:20],
        ip=_client_ip(request),
        user_agent=(request.headers.get("user-agent") or "")[:200] or None,
    )
    return {"action": result["action"], **_session_pair_payload(session)}


@app.post("/v1/auth/oauth/{provider}/link")
def auth_oauth_link(
    provider: str, payload: dict[str, Any], session: dict[str, Any] = Depends(session_auth)
) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    challenge = str(payload.get("challenge") or "")
    flow_id = challenge.partition(".")[0]
    pending = runtime.control_store.get_flow(flow_id) if flow_id else None
    if pending is not None and (pending.get("provider") or provider) != provider:
        raise HTTPException(status_code=409, detail="challenge belongs to another provider")
    try:
        identity = runtime.service.complete_link_challenge(
            challenge, str(session["account"]["account_id"])
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {
        "ok": True,
        "identity": {"identity_id": identity["identity_id"], "provider": identity["provider"]},
    }


# ----------------------------------------------------------- admin / user metrics

@app.get("/v1/admin/metrics")
def admin_metrics_endpoint(_admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    """Aggregate user metrics: total/active/pending/verified accounts, sign-in provider breakdown,
    accounts with an active session, and a daily signup series. Auth: `Authorization: Bearer
    <CORTEX_API_KEY>` (the same control-plane admin token as every other /v1/admin operation)."""
    runtime = _auth_runtime_or_404()
    return runtime.control_store.admin_metrics()


@app.get("/v1/admin/accounts")
def admin_accounts_endpoint(
    _admin: bool = Depends(admin_auth),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str = Query(default="", max_length=40),
    q: str = Query(default="", max_length=200),
) -> dict[str, Any]:
    """Paginated listing of AUTH accounts (email / name / status / linked sign-in providers /
    signup date), newest first, searchable by email or name. This is the sign-up/identity view —
    distinct from the memory-tier provisioning list at GET /v1/admin/users."""
    runtime = _auth_runtime_or_404()
    return runtime.control_store.list_accounts(
        limit=limit, offset=offset, status=status or None, query=q or None
    )


# Route-scoped CSP for the admin dashboard: the JS lives at /admin/app.js (script-src 'self', no
# inline script or on* handlers), inline <style>/style= is allowed, and fetch() to the same-origin
# /v1/admin/* is permitted. Caddy's set-default CSP defers to this, so the page renders + works.
_ADMIN_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "base-uri 'none'; "
    "form-action 'self'"
)


@app.get("/admin", response_class=HTMLResponse)
def admin_dashboard() -> Response:
    """Self-contained admin dashboard. It prompts for the admin key (kept only in this browser's
    sessionStorage) and calls the authed /v1/admin/* JSON endpoints — no key is embedded here."""
    response = HTMLResponse(content=_ADMIN_DASHBOARD_HTML)
    response.headers["Content-Security-Policy"] = _ADMIN_CSP
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.get("/admin/app.js")
def admin_app_js() -> Response:
    """The admin dashboard's JS, external so /admin can keep script-src 'self' (no inline script)."""
    response = PlainTextResponse(content=_ADMIN_APP_JS, media_type="application/javascript")
    response.headers["Content-Security-Policy"] = _ADMIN_CSP
    response.headers["Cache-Control"] = "no-store"
    return response


_ADMIN_DASHBOARD_HTML = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"/><meta name="viewport" content="width=device-width, initial-scale=1"/>
<title>Doppl - Admin</title>
<style>
  :root{--ink:#2b2622;--muted:#8a817a;--line:#e7e1da;--accent:#7c3b2e;--bg:#f6f3ee;--card:#fff;--moss:#5f6f52}
  *{box-sizing:border-box} body{margin:0;font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--bg);color:var(--ink)}
  header{padding:18px 28px;border-bottom:1px solid var(--line);display:flex;align-items:center;gap:12px;background:var(--card)}
  header h1{font-size:18px;margin:0}
  main{padding:24px 28px;max-width:1100px;margin:0 auto}
  .gate{max-width:380px;margin:12vh auto;background:var(--card);padding:28px;border:1px solid var(--line);border-radius:14px}
  input,button{font:inherit} input{padding:9px 11px;border:1px solid var(--line);border-radius:8px;width:100%}
  button{padding:9px 14px;border:1px solid var(--accent);background:var(--accent);color:#fff;border-radius:8px;cursor:pointer}
  button.ghost{background:transparent;color:var(--accent)}
  .kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:14px;margin:8px 0 22px}
  .kpi{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px}
  .kpi .n{font-size:28px;font-weight:700} .kpi .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px}
  .row{display:flex;gap:24px;flex-wrap:wrap;margin-bottom:22px}
  .card{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px;flex:1;min-width:280px}
  .card h3{margin:0 0 12px;font-size:13px;color:var(--muted);text-transform:uppercase;letter-spacing:.4px}
  .bars{display:flex;align-items:flex-end;gap:4px;height:120px}
  .bars .b{flex:1;background:var(--moss);border-radius:3px 3px 0 0;min-height:2px}
  table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:12px;overflow:hidden}
  th,td{text-align:left;padding:10px 12px;border-bottom:1px solid var(--line);font-size:13px} th{color:var(--muted);font-weight:600}
  .pill{display:inline-block;padding:1px 8px;border-radius:20px;background:#efe9e2;font-size:11px;margin-right:4px}
  .status-active{color:var(--moss)} .status-pending_verification{color:#b8860b} .toolbar{display:flex;gap:10px;align-items:center}
  .err{color:#b23b2e;margin-top:10px}
</style></head><body>
<div id="gate" class="gate">
  <h2 style="margin-top:0">Doppl Admin</h2>
  <p style="color:var(--muted)">Enter your admin key to view users and metrics.</p>
  <input id="key" type="password" placeholder="Operator API key (CORTEX_API_KEY)" autocomplete="off"/>
  <div style="margin-top:12px"><button id="enter-btn">Sign in</button></div>
  <div id="gateErr" class="err"></div>
</div>
<div id="app" style="display:none">
  <header><h1>Doppl - Admin</h1><div style="flex:1"></div>
    <div class="toolbar"><input id="q" placeholder="Search email/name" style="width:220px"/>
    <button class="ghost" id="refresh-btn">Refresh</button><button class="ghost" id="logout-btn">Lock</button></div>
  </header>
  <main>
    <div id="kpis" class="kpis"></div>
    <div class="row">
      <div class="card"><h3>Signups (last 30 days)</h3><div id="chart" class="bars"></div></div>
      <div class="card"><h3>By sign-in provider</h3><div id="providers"></div></div>
    </div>
    <div class="card" style="padding:0"><table><thead><tr><th>Email</th><th>Name</th><th>Status</th><th>Providers</th><th>Signed up</th><th>Member for</th><th>Last active</th></tr></thead><tbody id="rows"></tbody></table></div>
    <div id="err" class="err"></div>
  </main>
</div>
<script src="/admin/app.js"></script></body></html>"""


# Externalized so the /admin CSP can stay at script-src 'self' (no inline script, no on* handlers) —
# the operator dashboard renders account emails/names, so keeping the CSP net intact matters.
_ADMIN_APP_JS = """'use strict';
const K="doppl_admin_key";
function key(){return sessionStorage.getItem(K)||""}
async function api(path){
  const r=await fetch(path,{headers:{"Authorization":"Bearer "+key()}});
  if(r.status===401||r.status===403){logout();throw new Error("Unauthorized - check your operator API key")}
  if(!r.ok)throw new Error("HTTP "+r.status);
  return r.json();
}
function enter(){const v=document.getElementById("key").value.trim();if(!v)return;sessionStorage.setItem(K,v);show()}
function logout(){sessionStorage.removeItem(K);document.getElementById("app").style.display="none";document.getElementById("gate").style.display="block"}
function esc(s){return (s==null?"":String(s)).replace(/[&<>]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;"}[c]))}
function since(iso){if(!iso)return null;const s=(Date.now()-new Date(iso).getTime())/1000;return isNaN(s)?null:s}
function ago(iso){const s=since(iso);if(s==null)return '<span style="color:var(--muted)">never</span>';if(s<60)return 'just now';if(s<3600)return Math.floor(s/60)+'m ago';if(s<86400)return Math.floor(s/3600)+'h ago';if(s<604800)return Math.floor(s/86400)+'d ago';return Math.floor(s/604800)+'w ago'}
function forlen(iso){const s=since(iso);if(s==null)return '-';if(s<86400)return 'today';if(s<2592000)return Math.floor(s/86400)+'d';if(s<31536000)return Math.floor(s/2592000)+'mo';return (s/31536000).toFixed(1)+'y'}
async function show(){
  try{
    const m=await api("/v1/admin/metrics");
    document.getElementById("gate").style.display="none";document.getElementById("app").style.display="block";
    renderMetrics(m);await loadUsers();
  }catch(e){document.getElementById("gateErr").textContent=e.message}
}
function kpi(n,l){return '<div class="kpi"><div class="n">'+n+'</div><div class="l">'+l+'</div></div>'}
function renderMetrics(m){
  document.getElementById("kpis").innerHTML=
    kpi(m.total_accounts,"Total users")+kpi(m.active,"Active")+kpi(m.active_last_7d!=null?m.active_last_7d:"-","Active (7d)")+
    kpi(m.pending,"Pending")+kpi(m.email_verified,"Email verified")+kpi(m.accounts_with_active_session,"Signed-in now");
  const days=m.signups_by_day||[];const max=Math.max(1,...days.map(d=>d.count));
  document.getElementById("chart").innerHTML=days.map(d=>'<div class="b" style="height:'+(6+94*d.count/max)+'%" title="'+d.day+': '+d.count+'"></div>').join("")||'<span style="color:var(--muted)">No signups yet</span>';
  const p=m.by_provider||{};const keys=Object.keys(p);
  document.getElementById("providers").innerHTML=keys.length?keys.map(k=>'<div style="display:flex;justify-content:space-between;padding:4px 0"><span>'+esc(k)+'</span><b>'+p[k]+'</b></div>').join(""):'<span style="color:var(--muted)">Email / password only so far</span>';
}
async function loadUsers(){
  try{
    const q=encodeURIComponent(document.getElementById("q").value.trim());
    const u=await api("/v1/admin/accounts?limit=100&q="+q);
    document.getElementById("rows").innerHTML=(u.accounts||[]).map(a=>
      '<tr><td>'+esc(a.primary_email||"-")+'</td><td>'+esc(a.display_name||"-")+'</td>'+
      '<td class="status-'+esc(a.status)+'">'+esc(a.status)+'</td>'+
      '<td>'+((a.providers||[]).map(x=>'<span class="pill">'+esc(x)+'</span>').join("")||'<span class="pill">email</span>')+'</td>'+
      '<td>'+esc((a.created_at||"").slice(0,10))+'</td>'+
      '<td title="'+esc(a.created_at||"")+'">'+forlen(a.created_at)+'</td>'+
      '<td title="sessions: '+(a.session_count!=null?a.session_count:0)+'">'+ago(a.last_active_at)+'</td></tr>').join("")||'<tr><td colspan="7" style="color:var(--muted)">No users yet</td></tr>';
    document.getElementById("err").textContent="";
  }catch(e){document.getElementById("err").textContent=e.message}
}
let t;function debouncedUsers(){clearTimeout(t);t=setTimeout(loadUsers,300)}
function refresh(){show()}
// Wire events here (no inline on* handlers) so the CSP stays at script-src 'self'.
document.getElementById("enter-btn").addEventListener("click",enter);
document.getElementById("refresh-btn").addEventListener("click",refresh);
document.getElementById("logout-btn").addEventListener("click",logout);
document.getElementById("q").addEventListener("input",debouncedUsers);
document.getElementById("key").addEventListener("keydown",function(e){if(e.key==="Enter")enter();});
if(key())show();
"""


@app.delete("/v1/auth/oauth/{provider}/unlink")
def auth_oauth_unlink(provider: str, session: dict[str, Any] = Depends(session_auth)) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    account_id = str(session["account"]["account_id"])
    identities = runtime.control_store.list_identities(account_id)
    target = next((row for row in identities if row["provider"] == provider), None)
    if target is None:
        raise HTTPException(status_code=404, detail="provider is not linked to this account")
    try:
        runtime.service.unlink_identity(account_id, str(target["identity_id"]))
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True}


# ------------------------------------------------------------- app login flow

@app.post("/v1/auth/app/start")
def auth_app_start(request: Request) -> dict[str, Any]:
    """macOS app login handoff: the app opens browser_url, the OAuth callback
    completes the flow server-side, and the app polls the session pair out with
    its poll_secret (salted-hashed at rest, single-use). No token in any URL."""
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "app_start", request)
    flow_id = f"flw_{secrets.token_hex(13)}"
    poll_secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    now = utc_now()
    expires_at = iso_utc(now + timedelta(seconds=APP_LOGIN_FLOW_TTL_SECONDS))
    runtime.control_store.create_flow(
        flow_id=flow_id,
        kind="app_login",
        payload={"status": "pending"},
        secret_hash=f"{salt}${token_verify_hash(poll_secret, salt)}",
        expires_at=expires_at,
        now=iso_utc(now),
    )
    base = settings.public_app_url.rstrip("/")
    return {
        "flow_id": flow_id,
        "poll_secret": poll_secret,
        # The web sign-in front door is served at /account/login (webauth.py); it renders the
        # "Continue with <provider>" buttons and threads app_flow into the OAuth start so the
        # callback completes this flow server-side. (Was /login, which 404s.)
        "browser_url": f"{base}/account/login?app_flow={flow_id}",
        "expires_at": expires_at,
    }


def _complete_app_login_flow(runtime: AuthRuntime, flow_id: str, account: dict[str, Any]) -> None:
    """Attach the authenticated account to a pending app-login flow by writing
    a companion result row (same secret hash, same expiry). The session pair is
    minted only at poll time, so no session token is ever at rest."""
    flow = runtime.control_store.get_flow(flow_id)
    now_iso = iso_utc(utc_now())
    if (
        flow is None
        or str(flow.get("kind") or "") != "app_login"
        or flow.get("consumed_at")
        or str(flow.get("expires_at") or "") <= now_iso
    ):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    try:
        runtime.control_store.create_flow(
            flow_id=f"{flow_id}.result",
            kind="app_login",
            payload={"account_id": account["account_id"], "client": "macos"},
            secret_hash=flow.get("secret_hash"),
            expires_at=str(flow["expires_at"]),
            now=now_iso,
        )
    except Exception as exc:  # duplicate completion of the same flow
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE) from exc


@app.post("/v1/auth/app/poll")
def auth_app_poll(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "app_poll", request)
    flow_id = str(payload.get("flow_id") or "")
    poll_secret = str(payload.get("poll_secret") or "")
    flow = runtime.control_store.get_flow(flow_id)
    if flow is None or str(flow.get("kind") or "") != "app_login":
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    stored = str(flow.get("secret_hash") or "")
    salt, sep, digest = stored.partition("$")
    if not sep or not hmac.compare_digest(token_verify_hash(poll_secret, salt), digest):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    now_iso = iso_utc(utc_now())
    if str(flow.get("expires_at") or "") <= now_iso or flow.get("consumed_at"):
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    result = runtime.control_store.get_flow(f"{flow_id}.result")
    if result is None:
        return {"status": "pending"}
    consumed = runtime.control_store.consume_flow(str(result["flow_id"]), now_iso)
    if consumed is None:  # single-use: only the first successful poll wins
        raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    runtime.control_store.consume_flow(flow_id, now_iso)
    try:
        result_payload = json.loads(consumed.get("payload_json") or "{}")
    except ValueError:
        result_payload = {}
    try:
        session = runtime.service.mint_session(
            str(result_payload.get("account_id") or ""),
            client=str(result_payload.get("client") or "macos"),
            ip=_client_ip(request),
            user_agent=(request.headers.get("user-agent") or "")[:200] or None,
        )
    except AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    return {"status": "complete", **_session_pair_payload(session)}


# --------------------------------------------------- self-serve token minting

@app.get("/v1/auth/tokens")
def auth_list_tokens(
    audience: str | None = Query(default=None, pattern="^(api|mcp)$"),
    include_revoked: bool = Query(default=False),
    session: dict[str, Any] = Depends(session_auth),
) -> dict[str, Any]:
    return {
        "results": store.list_tokens(
            str(session["user_id"]), audience=audience, include_revoked=include_revoked
        )
    }


@app.post("/v1/auth/tokens", status_code=201)
def auth_mint_token(
    payload: dict[str, Any], session: dict[str, Any] = Depends(session_auth)
) -> dict[str, Any]:
    """Session-authed self-serve mint of the EXISTING cxa_/cxm_ token kinds via
    the registry (control-index row carries account_id linkage). The plaintext
    token is returned exactly once."""
    account = session["account"]
    if str(account.get("status") or "") != "active":
        raise HTTPException(status_code=403, detail="verify your email before minting tokens")
    user_id = str(session["user_id"])
    audience = str(payload.get("audience") or "api").strip().lower()
    label = str(payload.get("label") or "").strip()[:120]
    scopes = payload.get("scopes")
    account_id = str(account["account_id"])
    _ensure_account_provisioned(account)
    if audience == "api":
        minted = store.create_api_token(
            user_id,
            label=label or "Self-serve REST token",
            scopes=scopes if scopes is not None else list(DEFAULT_SELF_SERVE_API_SCOPES),
            account_id=account_id,
        )
    elif audience == "mcp":
        minted = store.create_mcp_token(
            user_id,
            label=label or "Self-serve MCP token",
            scopes=scopes if scopes is not None else list(DEFAULT_SELF_SERVE_MCP_SCOPES),
            account_id=account_id,
        )
    else:
        raise HTTPException(status_code=422, detail="audience must be 'api' or 'mcp'")
    return {
        "token": minted["token"],
        "token_id": minted.get("token_id"),
        "audience": audience,
        "label": minted.get("label"),
        "scopes": minted.get("scopes"),
        "account_id": account_id,
    }


@app.delete("/v1/auth/tokens/{token_id}")
def auth_revoke_token(token_id: str, session: dict[str, Any] = Depends(session_auth)) -> dict[str, Any]:
    revoked = store.revoke_token(str(session["user_id"]), token_id)
    if not revoked:
        raise HTTPException(status_code=404, detail="Token not found")
    return revoked


# --------------------------------------------------------------- account plan

@app.get("/v1/account/plan")
def account_plan(session: dict[str, Any] = Depends(session_auth)) -> dict[str, Any]:
    """The caller's current plan + resolved memory quota, so the app/web can
    show it. Session-authed via the same cxs_ dispatch as the rest of /v1/auth.
    Available whenever auth is enabled (independent of whether billing is
    configured) — it just reflects the plan field."""
    user_id = str(session["user_id"])
    user = store.get_user(user_id)
    plan = str((user or {}).get("plan") or "free")
    quota = settings.quota_for_plan(plan)
    return {
        "user_id": user_id,
        "plan": plan,
        "memory_quota": quota,  # 0 = unlimited
        "memory_quota_unlimited": quota == 0,
        "billing_enabled": settings.billing_enabled,
    }


# ------------------------------------------------------------- billing webhook

def _billing_verifier_or_404() -> "BillingWebhookVerifier":
    """Return the configured billing verifier, or 404 exactly like other gated
    features when billing is unconfigured (byte-identical to a no-billing
    deployment). Import is lazy so billing.py never loads at module scope."""
    if not settings.billing_enabled:
        raise HTTPException(status_code=404, detail="Not Found")
    from .billing import BillingWebhookVerifier

    try:
        return BillingWebhookVerifier.from_settings(settings)
    except ValueError as exc:  # provider mismatch despite billing_enabled
        raise HTTPException(status_code=404, detail="Not Found") from exc


@app.post("/v1/billing/webhook")
async def billing_webhook(request: Request) -> dict[str, Any]:
    """Provider webhook (Paddle-first). Verifies the HMAC signature over the RAW
    body (400 on bad/missing signature), maps the event to a plan transition,
    applies it via store.set_user_plan, and returns 200 quickly. Idempotent: the
    provider event id is recorded so a replayed delivery is a single effect.
    Unknown users are accepted (200) with no plan change so the provider does not
    retry forever. 404 when billing is not configured."""
    from .billing import BillingError, SignatureVerificationError, apply_billing_event

    verifier = _billing_verifier_or_404()
    raw_body = await request.body()
    headers = {key: value for key, value in request.headers.items()}
    try:
        event = verifier.verify_and_parse(raw_body=raw_body, headers=headers)
    except SignatureVerificationError as exc:
        raise HTTPException(status_code=400, detail="invalid billing webhook signature") from exc
    except BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # Idempotent replay guard: a previously-processed event id is a no-op.
    if event.event_id and store.billing_event_processed(event.event_id):
        return {"status": "ok", "action": "duplicate", "event_id": event.event_id}

    result = apply_billing_event(store, event, email_resolver=_billing_email_resolver)
    store.record_billing_event(
        event_id=event.event_id,
        provider=event.provider,
        event_type=event.event_type,
        user_id=result.get("user_id"),
        plan=result.get("plan"),
        action=str(result.get("action") or ""),
    )
    return {"status": "ok", **result, "event_id": event.event_id}


def _billing_email_resolver(email: str) -> str | None:
    """Resolve a Paddle customer email to a Cortex user_id via the accounts
    control store. Only used when the checkout did not carry the user_id in
    custom_data. Returns None (no mapping) when auth is disabled or no account
    matches — the webhook then accepts the event without a plan change."""
    runtime = auth_runtime
    if runtime is None or not email:
        return None
    getter = getattr(runtime.control_store, "get_account_by_email", None)
    if not callable(getter):
        return None
    try:
        account = getter(email.strip().lower())
    except Exception:
        return None
    if not account:
        return None
    return str(account.get("user_id") or "") or None


# ------------------------------------------------------------ account deletion

@app.delete("/v1/auth/account")
def auth_delete_account(
    request: Request,
    payload: dict[str, Any] | None = None,
    session: dict[str, Any] = Depends(session_auth),
) -> dict[str, Any]:
    """Step-up account deletion: requires the password in the body when one
    exists. Order (design doc section 4): crypto-shred the user's key material
    FIRST (every CXE1 blob — including inside backups — becomes permanently
    unreadable), then the conventional deprovision sweep, then mark_deleted
    (which revokes every session)."""
    runtime = _auth_runtime_or_404()
    account = session["account"]
    account_id = str(account["account_id"])
    user_id = str(session["user_id"])
    credential = runtime.control_store.get_password_credential(account_id)
    if credential is not None:
        supplied = str((payload or {}).get("password") or "")
        if not supplied or not runtime.service.engine.verify(
            str(credential["password_hash"]), supplied
        ):
            raise HTTPException(status_code=401, detail=GENERIC_AUTH_FAILURE)
    shred: dict[str, Any] | None = None
    if runtime.keyring is not None and runtime.keyring.available:
        shred = runtime.keyring.crypto_shred(user_id)
    try:
        if store.get_user(user_id) is not None:
            data_report = store.deprovision_user(user_id)
        else:
            data_report = store.delete_user_data(
                user_id, include_backups=(store.router.mode != "bucket")
            )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    marked = runtime.service.mark_deleted(account_id)
    return {
        "deleted": True,
        "account_id": account_id,
        "user_id": user_id,
        "account_status": marked.get("status"),
        "crypto_shred": shred,
        "data": data_report,
    }


# ---------------------------------------------------- invite / claim / backfill

@app.post("/v1/admin/users/{user_id}/invite", status_code=201)
def admin_invite_user(user_id: str, _admin: bool = Depends(admin_auth)) -> dict[str, Any]:
    """Mint a single-use claim code binding a login to an EXISTING provisioned
    user_id (legacy operator-provisioned users keep their shard and tokens)."""
    runtime = _auth_runtime_or_404()
    if store.get_user(user_id) is None:
        raise HTTPException(status_code=404, detail="User not found")
    if runtime.control_store.get_account_by_user_id(user_id) is not None:
        raise HTTPException(status_code=409, detail="user already has an account")
    flow_id = f"flw_{secrets.token_hex(13)}"
    secret = secrets.token_urlsafe(32)
    salt = secrets.token_hex(16)
    now = utc_now()
    expires_at = iso_utc(now + timedelta(seconds=ACCOUNT_CLAIM_TTL_SECONDS))
    runtime.control_store.create_flow(
        flow_id=flow_id,
        kind="account_claim",
        payload={"user_id": user_id},
        secret_hash=f"{salt}${token_verify_hash(secret, salt)}",
        expires_at=expires_at,
        now=iso_utc(now),
    )
    return {"user_id": user_id, "claim_code": f"{flow_id}.{secret}", "expires_at": expires_at}


@app.post("/v1/auth/claim")
def auth_claim(payload: dict[str, Any], request: Request) -> dict[str, Any]:
    runtime = _auth_runtime_or_404()
    _auth_rate_limit(runtime, "claim", request)
    flow = _consume_secret_flow(runtime, str(payload.get("code") or ""), "account_claim")
    try:
        flow_payload = json.loads(flow.get("payload_json") or "{}")
    except ValueError:
        flow_payload = {}
    try:
        account = runtime.service.claim_account_for_user(
            str(flow_payload.get("user_id") or ""),
            str(payload.get("email") or ""),
            str(payload.get("password") or "") or None,
            display_name=str(payload.get("display_name") or "")[:160],
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except AuthError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"ok": True, "next": "verify_email", "account": _public_account(account)}


@app.post("/v1/admin/encryption/backfill")
def admin_encryption_backfill(
    limit: int = Query(default=1000, ge=1, le=10000), _admin: bool = Depends(admin_auth)
) -> dict[str, Any]:
    """Ensure DEKs exist for every provisioned user and lazily re-encrypt any
    legacy plaintext credentials through the vault read/write-back path.
    Reports the remaining plaintext gauge (release gate: reach 0, then flip
    CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS)."""
    runtime = _auth_runtime_or_404()
    keyring = runtime.keyring
    if keyring is None or not keyring.available:
        raise HTTPException(status_code=409, detail="no KEK configured; encryption backfill unavailable")
    from .keyring import ShreddedKeyError

    users_processed = 0
    deks_ensured = 0
    skipped_shredded = 0
    migrated = 0
    remaining_plaintext = 0
    for user in store.list_users(limit=limit):
        user_id = str(user["user_id"])
        users_processed += 1
        try:
            # encrypt_blob lazily mints the user's DEK when missing.
            keyring.encrypt_blob(user_id, "credentials", b"cortex-dek-backfill-probe")
            deks_ensured += 1
        except ShreddedKeyError:
            skipped_shredded += 1
            continue
        vault = getattr(store.store_for_user(user_id), "vault", None)
        if vault is None:
            continue
        before = _plaintext_credential_records(vault, user_id)
        for source_account_id in before:
            # The encrypting read path performs the write-back migration.
            vault.read_source_credential(user_id=user_id, source_account_id=source_account_id)
        after = _plaintext_credential_records(vault, user_id)
        migrated += max(0, len(before) - len(after))
        remaining_plaintext += len(after)
    return {
        "status": "ok" if remaining_plaintext == 0 else "attention",
        "users_processed": users_processed,
        "deks_ensured": deks_ensured,
        "skipped_shredded": skipped_shredded,
        "migrated": migrated,
        "remaining_plaintext": remaining_plaintext,
    }


def _plaintext_credential_records(vault: Any, user_id: str) -> list[str]:
    try:
        data = json.loads(vault.credentials_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    records = (data.get("users") or {}).get(user_id)
    if not isinstance(records, dict):
        return []
    return [
        key
        for key, record in records.items()
        if isinstance(record, dict)
        and isinstance(record.get("payload"), dict)
        and not record.get("payload_cxe1")
    ]


# ------------------------------------------------ web account front-door pages
# Server-rendered /account* browser pages (login/signup/verify/reset/home/oauth
# complete) served ONLY when auth is enabled — every handler routes through
# _auth_runtime_or_404 so it 404s exactly like the /v1/auth JSON API when
# disabled. Markup + one inline <style>; JS is external at /account/app.js so
# the global strict CSP can stay put while these pages carry a route-scoped
# relaxed CSP (script-src 'self', connect-src 'self'). See backend/app/webauth.py.
register_web_account_routes(
    app,
    runtime_or_404=_auth_runtime_or_404,
    list_providers=lambda: _auth_runtime_or_404().oidc.enabled_providers(),
    # Cloudflare Turnstile site key, read at request time so a settings swap
    # (tests/reboot) takes effect. "" when Turnstile is unconfigured -> the
    # signup page is byte-identical to today.
    turnstile_site_key=lambda: settings.turnstile_site_key if settings.turnstile_enabled else "",
    # Signs the app-login flow-binding cookie (login-CSRF guard for the desktop OAuth handoff).
    app_flow_cookie=_app_flow_cookie,
)

# OAuth token-exchange broker for confidential-client connectors (Notion, GitHub) — hosted-only.
# No-op surface until CORTEX_BROKER_<PROVIDER>_CLIENT_ID/_CLIENT_SECRET are set + this app is deployed.
register_oauth_broker_routes(app)


# MCP protocol revisions /mcp can serve, newest first. The JSON-RPC shapes Cortex uses
# (initialize, tools/list, tools/call, ping) are identical across these revisions, so
# initialize echoes whichever revision the client requested and offers the newest otherwise.
MCP_PROTOCOL_VERSIONS = ("2025-03-26", "2024-11-05")


@app.post("/mcp")
async def mcp(request: Request, context: dict[str, Any] = Depends(mcp_auth)) -> Response:
    user_id = context["user_id"]
    token_scopes = None if context.get("admin") else list(context.get("scopes") or [])
    raw = await request.body()
    try:
        message = json.loads(raw.decode("utf-8")) if raw else {}
    except (json.JSONDecodeError, UnicodeDecodeError):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Request body must be valid JSON"}})
    if isinstance(message, list):
        # JSON-RPC batch arrays are not part of MCP; reject cleanly instead of tracebacking.
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "batch not supported"}})
    if not isinstance(message, dict):
        return JSONResponse({"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "request must be a JSON-RPC object"}})
    method = message.get("method")
    if message.get("id") is None or (isinstance(method, str) and method.startswith("notifications/")):
        # JSON-RPC notification (no id, e.g. notifications/initialized): remote clients POST
        # these directly; per the MCP streamable HTTP spec, accept with 202 and no body.
        return Response(status_code=202)
    try:
        if method == "initialize":
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            requested_version = params.get("protocolVersion")
            result = {
                "protocolVersion": requested_version if requested_version in MCP_PROTOCOL_VERSIONS else MCP_PROTOCOL_VERSIONS[0],
                "serverInfo": {"name": "cortex", "version": BACKEND_VERSION},
                "capabilities": {"tools": {}},
            }
        elif method == "ping":
            result = {}
        elif method == "tools/list":
            # Spec params such as cursor are tolerated (ignored): the full list is one page,
            # so no nextCursor is ever returned.
            result = {"tools": tools_for_scopes(token_scopes, surface=settings.mcp_tool_surface)}
        elif method == "tools/call":
            params = message.get("params") or {}
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            try:
                value = call_tool(store, user_id, tool_name, arguments, token_scopes=token_scopes)
                store.record_agent_event(user_id, tool_name, arguments, success=True, token=context, result=value)
            except Exception as exc:
                store.record_agent_event(user_id, tool_name, arguments, success=False, error=str(exc), token=context)
                raise
            result = tool_call_result(value)
        else:
            return JSONResponse({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32601, "message": f"Method not found: {method}"}})
        return JSONResponse({"jsonrpc": "2.0", "id": message.get("id"), "result": jsonable_encoder(result)})
    except Exception as exc:
        return JSONResponse({"jsonrpc": "2.0", "id": message.get("id"), "error": {"code": -32000, "message": str(exc)}})
