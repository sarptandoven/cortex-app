from __future__ import annotations

import html
import hmac
import os
from typing import Any
from urllib.parse import parse_qs

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.middleware.cors import CORSMiddleware

from .config import load_settings
from .database import init_db
from .extractor import extract_context
from .mcp_tools import TOOLS, call_tool, tool_result_text
from .models import BackupResponse, CaptureRequest, CaptureResponse, ContextReuseRequest, ContextReuseResponse, DiagnosticsResponse, GraphResponse, JobRunResponse, ListResponse, MaintenanceResponse, MCPRequest, MCPTokenRegistrationRequest, MCPTokenRegistrationResponse, ProductLoopResponse, QueuedCaptureResponse, ReliabilityReportResponse, RepairStorageResponse, SearchResponse, SettingsResponse, SettingsUpdateRequest, SourceAnalyzeRequest, SourceAnalyzeResponse, SourceImportDeleteResponse, SourceImportRequest, SourceImportResponse, StatsResponse, SupportBundleResponse, VaultRebuildResponse, VectorRebuildResponse
from .storage import BACKEND_VERSION, CortexStore


settings = load_settings()
init_db(settings.db_path)
store = CortexStore(settings.db_path, settings.vault_path)
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


def auth(authorization: str | None = Header(default=None), x_cortex_user: str | None = Header(default=None)) -> str:
    if settings.api_key:
        expected = f"Bearer {settings.api_key}"
        if not authorization or not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=401, detail="Missing or invalid Cortex API token")
    return x_cortex_user or settings.default_user_id


def mcp_auth(authorization: str | None = Header(default=None), x_cortex_user: str | None = Header(default=None)) -> dict[str, Any]:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex MCP token")
    token = authorization.split(" ", 1)[1].strip()
    if settings.api_key and hmac.compare_digest(token, settings.api_key):
        user_id = x_cortex_user or settings.default_user_id
        return {
            "user_id": user_id,
            "token_id": "admin",
            "label": "Cortex app token",
            "audience": "admin",
            "scopes": ["read", "write", "export", "maintenance", "destructive"],
            "admin": True,
        }
    scoped = store.authenticate_mcp_token(token)
    if scoped:
        return scoped
    raise HTTPException(status_code=401, detail="Missing or invalid Cortex MCP token")


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


def _auth_query_token(token: str | None) -> str:
    if settings.api_key and not hmac.compare_digest(token or "", settings.api_key):
        raise HTTPException(status_code=401, detail="Missing or invalid Cortex capture token")
    return settings.default_user_id


def _save_capture_from_values(content: str, source: str, title: str | None, source_url: str | None, user_id: str) -> dict[str, Any]:
    content = content.strip()
    if not content:
        raise HTTPException(status_code=422, detail="content is required")
    if len(content) > 200_000:
        raise HTTPException(status_code=413, detail="content is too large")
    normalized_source = (source or "browser-capture")[:80]
    extracted = extract_context(content, normalized_source)
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
    return store.health_payload(mode="fastapi", auth=bool(settings.api_key))


@app.get("/ready")
def ready() -> dict[str, Any]:
    diagnostics = store.diagnostics(settings.default_user_id)
    if diagnostics["status"] != "ok":
        raise HTTPException(status_code=503, detail=diagnostics)
    return {"status": "ok", "diagnostics": diagnostics}


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
    if processing == "async":
        return store.enqueue_capture(
            user_id=user_id or request.user_id,
            content=request.content,
            source=request.source,
            source_url=request.source_url,
            title=request.title,
        )
    extracted = extract_context(request.content, request.source)
    return store.save_capture(
        user_id=user_id or request.user_id,
        content=request.content,
        source=request.source,
        source_url=request.source_url,
        title=request.title,
        extracted=extracted,
    )


@app.post("/v1/captures/queue", response_model=QueuedCaptureResponse, status_code=202)
def queue_capture(request: CaptureRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.enqueue_capture(
        user_id=user_id or request.user_id,
        content=request.content,
        source=request.source,
        source_url=request.source_url,
        title=request.title,
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


@app.get("/v1/jobs/{job_id}")
def get_job(job_id: str, user_id: str = Depends(auth)) -> dict[str, Any]:
    job = store.get_job(user_id, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


@app.post("/v1/jobs/run", response_model=JobRunResponse)
def run_jobs(limit: int = Query(default=10, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.run_due_jobs(user_id, limit=limit)


@app.post("/v1/maintenance/jobs/run", response_model=JobRunResponse)
def run_maintenance_jobs(limit: int = Query(default=10, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.run_due_jobs(user_id, limit=limit)


@app.get("/v1/recent")
def recent(limit: int = Query(default=20, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.recent(user_id, limit)}


@app.get("/v1/inbox", response_model=ListResponse)
def inbox(limit: int = Query(default=30, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.inbox(user_id, limit)}


@app.get("/v1/search", response_model=SearchResponse)
def search(query: str, limit: int = Query(default=10, ge=1, le=50), kind: str | None = None, layer: str | None = None, user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"query": query, "results": store.search(user_id, query, limit, kind, layer)}


@app.get("/v1/tasks/open")
def open_tasks(limit: int = Query(default=20, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.open_tasks(user_id, limit)}


@app.get("/v1/topics")
def topics(limit: int = Query(default=30, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.list_topics(user_id, limit)}


@app.get("/v1/entities")
def entities(limit: int = Query(default=30, ge=1, le=100), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.list_entities(user_id, limit)}


@app.get("/v1/people/{name}")
def about_person(name: str, limit: int = Query(default=12, ge=1, le=50), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.about_person(user_id, name, limit)}


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
def context_pack(query: str = "", limit: int = Query(default=12, ge=1, le=50), user_id: str = Depends(auth)) -> Response:
    return Response(content=store.context_pack(user_id, query=query, limit=limit), media_type="text/markdown")


@app.get("/v1/personal-profile", response_model=None)
def personal_profile(
    query: str = "",
    limit: int = Query(default=6, ge=1, le=20),
    include_pending: bool = Query(default=False),
    format: str = Query(default="json", pattern="^(json|markdown)$"),
    user_id: str = Depends(auth),
) -> dict[str, Any] | Response:
    profile = store.personal_profile(user_id, query=query, limit=limit, include_pending=include_pending)
    if format == "markdown":
        return Response(content=profile["markdown"], media_type="text/markdown")
    return profile


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


@app.get("/v1/settings", response_model=SettingsResponse)
def get_settings(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.settings(user_id)


@app.put("/v1/settings", response_model=SettingsResponse)
def update_settings(request: SettingsUpdateRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.update_settings(user_id, request.model_dump(exclude_none=True))


@app.get("/v1/trust/summary")
def trust_summary(user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.trust_summary(user_id)


@app.post("/v1/integrations/mcp-token", response_model=MCPTokenRegistrationResponse)
def register_mcp_token(request: MCPTokenRegistrationRequest, user_id: str = Depends(auth)) -> dict[str, Any]:
    return store.ensure_mcp_token(
        user_id,
        request.token,
        label=request.label,
        scopes=request.scopes,
        token_id="tok_local_mcp",
    )


@app.get("/v1/audit-log", response_model=ListResponse)
def audit_log(limit: int = Query(default=80, ge=1, le=300), user_id: str = Depends(auth)) -> dict[str, Any]:
    return {"results": store.audit_log(user_id, limit)}


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
        "mcp": {"endpoint": "/mcp", "tools": [tool["name"] for tool in TOOLS]},
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
            result = {"tools": TOOLS}
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
            result = {
                "content": [
                    {
                        "type": "text",
                        "text": tool_result_text(value),
                    }
                ]
            }
        else:
            raise ValueError(f"Unsupported MCP method: {request.method}")
        return {"jsonrpc": "2.0", "id": request.id, "result": result}
    except Exception as exc:
        return {"jsonrpc": "2.0", "id": request.id, "error": {"code": -32000, "message": str(exc)}}
