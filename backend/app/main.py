from __future__ import annotations

from datetime import timedelta
import html
import hmac
import os
import time
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .extractor import extract_context
from .hosted_readiness import hosted_readiness_contract
from .mcp_tools import CORE_TOOL_NAMES, TOOLS, call_tool, tool_call_result, tools_for_scopes
from .observability import metrics, route_label
from .models import APITokenListResponse, APITokenRegistrationRequest, APITokenRegistrationResponse, APITokenRevokeResponse, AskResponse, BackupResponse, CalendarSyncRequest, CalendarSyncResponse, CaptureRequest, CaptureResponse, ContextReuseRequest, ContextReuseResponse, DataLifecycleReportResponse, DiagnosticsResponse, GitHubRepositoryDiscoveryRequest, GitHubRepositoryDiscoveryResponse, GitHubSyncRequest, GitHubSyncResponse, GmailSyncRequest, GmailSyncResponse, GoogleDriveSyncRequest, GoogleDriveSyncResponse, GoogleOAuthCompleteRequest, GoogleOAuthCompleteResponse, GoogleOAuthStartRequest, GoogleOAuthStartResponse, GraphResponse, JiraSyncRequest, JiraSyncResponse, JobRunResponse, LinearSyncRequest, LinearSyncResponse, ListResponse, MaintenanceResponse, ManagedOAuthCompleteRequest, ManagedOAuthCompleteResponse, ManagedOAuthStartRequest, ManagedOAuthStartResponse, MCPRequest, MCPTokenRegistrationRequest, MCPTokenRegistrationResponse, MemoryQualityResponse, NotionSyncRequest, NotionSyncResponse, ObsidianVaultSyncRequest, ObsidianVaultSyncResponse, OutlookSyncRequest, OutlookSyncResponse, ProductLoopResponse, QueuedCaptureResponse, RaindropSyncRequest, RaindropSyncResponse, ReadwiseSyncRequest, ReadwiseSyncResponse, ReliabilityReportResponse, RepairStorageResponse, SearchResponse, SettingsResponse, SettingsUpdateRequest, SlackChannelDiscoveryRequest, SlackChannelDiscoveryResponse, SlackSyncRequest, SlackSyncResponse, SourceAccountListResponse, SourceAccountRequest, SourceAccountResponse, SourceAccountSyncRequest, SourceAccountSyncResponse, SourceAnalyzeRequest, SourceAnalyzeResponse, SourceImportDeleteResponse, SourceImportRequest, SourceImportResponse, SourceReadinessResponse, StatsResponse, SupportBundleResponse, SyncChangeFeedResponse, SyncCursorListResponse, SyncCursorRequest, SyncCursorResponse, SyncDeviceListResponse, SyncDeviceRequest, SyncDeviceResponse, SyncReceiptListResponse, SyncReceiptRequest, SyncReceiptResponse, VaultRebuildResponse, VectorRebuildResponse, ZoteroSyncRequest, ZoteroSyncResponse
from .models import UserListResponse, UserProvisionRequest, UserProvisionResponse, UserStatusResponse
from .ratelimit import TokenBucketRateLimiter
from .sharding import StoreRegistry
from .storage import BACKEND_VERSION


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
    if normalized_path in {"/v1/export.json", "/v1/export.md", "/v1/context-pack", "/v1/personal-profile", "/v1/agent-adaptation", "/v1/support/bundle"}:
        return "export"
    if normalized_path == "/v1/context":
        # The context engine is a READ (POST only carries parameters); the identity layer is
        # export-gated inside the engine itself.
        return "read"
    if normalized_path == "/v1/settings" and normalized_method in {"PUT", "PATCH"}:
        return "maintenance"
    if normalized_path in {"/v1/diagnostics", "/v1/reliability/report", "/v1/jobs/health"}:
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


def _enforce_memory_quota(user_id: str) -> None:
    quota = settings.default_memory_quota
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
        <title>Cortex Capture</title>
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
        <h1>Save to Cortex</h1>
        <p class="hint">Capture selected text, page context, links, or notes into your local Cortex memory.</p>
        {f"<p><strong>{escaped_message}</strong></p>" if escaped_message else ""}
        <form method="post" action="/capture">
          <label>Token</label>
          <input name="token" value="" autocomplete="off" placeholder="Paste Cortex token" />
          <label>Title</label>
          <input name="title" value="{escaped_title}" />
          <label>Source URL</label>
          <input name="url" value="{escaped_url}" />
          <label>Content</label>
          <textarea name="content">{escaped_content}</textarea>
          <input type="hidden" name="source" value="browser-capture" />
          <button type="submit">Save to Cortex</button>
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
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex capture token")
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
def root() -> str:
    return """
    <!doctype html>
    <html>
      <head>
        <title>Cortex Local API</title>
        <style>
          body { font-family: -apple-system, BlinkMacSystemFont, sans-serif; margin: 40px; line-height: 1.45; max-width: 760px; }
          code { background: #f3f3f3; padding: 2px 5px; border-radius: 4px; }
          .status { display: inline-block; padding: 4px 8px; border-radius: 999px; background: #e8f7ed; color: #116329; font-weight: 600; }
        </style>
      </head>
      <body>
        <p class="status">Cortex backend is running</p>
        <h1>Cortex Local API</h1>
        <p>This local service stores and retrieves shared AI memory for the macOS app and MCP-compatible tools.</p>
        <p>Useful checks: <code>/health</code>, <code>/ready</code>, <code>/.well-known/cortex.json</code>.</p>
        <p>Authenticated API endpoints require the Cortex token configured in the app.</p>
      </body>
    </html>
    """


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
            return HTMLResponse(
                _capture_page(
                    message=f"Saved {len(response.get('memories', []))} memories.",
                    status="saved",
                    token=token,
                    title=title,
                    url=url,
                )
            )
        except HTTPException as exc:
            return HTMLResponse(
                _capture_page(message=str(exc.detail), status="error", token=token, title=title, url=url, content=payload),
                status_code=exc.status_code,
            )
    return HTMLResponse(_capture_page(token=token, title=title, url=url, content=payload))


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
        return HTMLResponse(
            _capture_page(
                message=f"Saved {len(response.get('memories', []))} memories.",
                status="saved",
                token=token,
                title=title,
                url=url,
            )
        )
    except HTTPException as exc:
        return HTMLResponse(
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
        )
    extracted = extract_context(
        request.content,
        request.source,
        author_aliases=store.settings(resolved_user_id).get("identity_aliases"),
    )
    return store.save_capture(
        user_id=resolved_user_id,
        content=request.content,
        source=request.source,
        source_url=request.source_url,
        title=request.title,
        extracted=extracted,
        cite_capture_provenance=True,
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
        )
        return store.public_payload(user_id, result)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
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
        include_identity=_bearer_has_export_scope(request),
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
    )
    return _context_response(pack, format)


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


@app.post("/mcp")
def mcp(request: MCPRequest, context: dict[str, Any] = Depends(mcp_auth)) -> dict[str, Any]:
    user_id = context["user_id"]
    token_scopes = None if context.get("admin") else list(context.get("scopes") or [])
    try:
        if request.method == "initialize":
            result = {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "cortex", "version": BACKEND_VERSION},
                "capabilities": {"tools": {}},
            }
        elif request.method == "tools/list":
            result = {"tools": tools_for_scopes(token_scopes, surface=settings.mcp_tool_surface)}
        elif request.method == "tools/call":
            params = request.params or {}
            tool_name = params.get("name", "")
            arguments = params.get("arguments", {}) or {}
            try:
                value = call_tool(store, user_id, tool_name, arguments, token_scopes=token_scopes)
                store.record_agent_event(user_id, tool_name, arguments, success=True, token=context)
            except Exception as exc:
                store.record_agent_event(user_id, tool_name, arguments, success=False, error=str(exc), token=context)
                raise
            result = tool_call_result(value)
        else:
            raise ValueError(f"Unsupported MCP method: {request.method}")
        return {"jsonrpc": "2.0", "id": request.id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request.id, "error": {"code": -32000, "message": str(exc)}}
