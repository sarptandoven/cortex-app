from __future__ import annotations

import argparse
import html
import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .config import load_settings
from .extractor import extract_context
from .hosted_readiness import hosted_readiness_contract
from .mcp_tools import TOOLS, call_tool, tool_call_result, tools_for_scopes
from .sharding import StoreRegistry
from .storage import BACKEND_VERSION


settings = load_settings()
store = StoreRegistry.from_settings(settings)
store.ensure_vault_backfilled(settings.default_user_id)
if settings.mcp_api_key:
    store.ensure_mcp_token(
        settings.default_user_id,
        settings.mcp_api_key,
        label="Local MCP integrations",
        scopes=settings.mcp_api_key_scopes or None,
        token_id="tok_local_mcp",
    )


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


def _required_api_scope(method: str, path: str) -> str:
    normalized_method = method.upper()
    normalized_path = path.rstrip("/") or "/"
    if normalized_path in {"/v1/export.json", "/v1/export.md", "/v1/context-pack", "/v1/personal-profile", "/v1/agent-adaptation", "/v1/support/bundle"}:
        return "export"
    if normalized_path == "/v1/settings" and normalized_method in {"PUT", "PATCH"}:
        return "maintenance"
    if normalized_path in {"/v1/diagnostics", "/v1/reliability/report", "/v1/jobs/health"}:
        return "maintenance"
    if normalized_path.startswith("/v1/maintenance/") or normalized_path in {"/v1/jobs/run", "/v1/maintenance/jobs/run"}:
        return "maintenance"
    if normalized_path in {"/v1/integrations/api-token", "/v1/integrations/mcp-token", "/v1/integrations/tokens"}:
        return "maintenance"
    if normalized_path.startswith("/v1/integrations/tokens/"):
        return "maintenance"
    if normalized_path == "/v1/source-accounts" and normalized_method == "POST":
        return "maintenance"
    if normalized_path.startswith("/v1/source-accounts/") and normalized_method == "DELETE":
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
    control_plane_status = getattr(store, "control_plane_status", None)
    if settings.shard_mode != "local" and callable(control_plane_status):
        runtime["control_plane"] = control_plane_status()
    return hosted_readiness_contract(settings, runtime=runtime)


ROOT_HTML = """
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
    return {"paths": paths, "source_hint": source_hint, "max_records": max_records, "processing": processing}


def _capture_page(message: str = "", status: str = "ready", token: str = "", title: str = "", url: str = "", content: str = "") -> str:
    escaped_message = html.escape(message)
    escaped_status = html.escape(status)
    escaped_title = html.escape(title)
    escaped_url = html.escape(url)
    escaped_content = html.escape(content)
    status_class = "ok" if status == "saved" else "err" if status == "error" else ""
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
    <p class="status {status_class}">{escaped_status}</p>
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


class CortexRequestHandler(BaseHTTPRequestHandler):
    server_version = "CortexStandalone/0.1"

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
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _handle(self, method: str) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        params = parse_qs(parsed.query)
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
                    "name": "Cortex",
                    "description": "Shared memory for AI assistants.",
                    "api": {"base_url": settings.public_base_url, "version": BACKEND_VERSION},
                    "health": store.health_payload(mode="standalone", auth=bool(settings.api_key)),
                    "mcp": {"endpoint": "/mcp", "tools": [tool["name"] for tool in TOOLS]},
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
                        self._send_text(_capture_page("Missing or invalid Cortex capture token", "error", token, title, source_url, payload), status=HTTPStatus.UNAUTHORIZED, media_type="text/html")
                        return
                    try:
                        saved = self._save_capture(user_id, payload, source, title, source_url)
                    except ValueError as exc:
                        self._send_text(_capture_page(str(exc), "error", token, title, source_url, payload), status=HTTPStatus.BAD_REQUEST, media_type="text/html")
                        return
                    self._send_text(_capture_page(f"Saved {len(saved.get('memories', []))} memories.", "saved", token, title, source_url), media_type="text/html")
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
                    self._send_text(_capture_page("Missing or invalid Cortex capture token", "error", token, title, source_url, content), status=HTTPStatus.UNAUTHORIZED, media_type="text/html")
                    return
                try:
                    saved = self._save_capture(user_id, content, source, title, source_url)
                except ValueError as exc:
                    self._send_text(_capture_page(str(exc), "error", token, title, source_url, content), status=HTTPStatus.BAD_REQUEST, media_type="text/html")
                    return
                self._send_text(_capture_page(f"Saved {len(saved.get('memories', []))} memories.", "saved", token, title, source_url), media_type="text/html")
                return
            if method == "POST" and path == "/mcp":
                context = self._auth_mcp()
                if not context:
                    return
                self._handle_mcp(context)
                return

            user_id = self._auth_user(method, path)
            if not user_id:
                return

            if method == "POST" and path == "/v1/captures/queue":
                body = self._json_body()
                try:
                    self._send_json(
                        store.enqueue_capture(
                            user_id=user_id,
                            content=str(body.get("content", "")),
                            source=str(body.get("source") or "macos")[:80],
                            source_url=str(body.get("source_url") or "")[:500] or None,
                            title=str(body.get("title") or "")[:200] or None,
                        ),
                        status=HTTPStatus.ACCEPTED,
                    )
                except ValueError as exc:
                    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "large" in str(exc) else HTTPStatus.UNPROCESSABLE_ENTITY
                    self._send_json({"detail": str(exc)}, status=status)
                return
            if method == "POST" and path == "/v1/captures":
                body = self._json_body()
                content = str(body.get("content", ""))
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
                    )
                    self._send_json(store.public_payload(user_id, result) if hasattr(store, "public_payload") else result)
                except FileNotFoundError as exc:
                    self._send_json({"detail": str(exc)}, status=HTTPStatus.NOT_FOUND)
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
                    repositories = body.get("repositories") if isinstance(body.get("repositories"), list) else []
                    result = store.sync_github_account(
                        user_id,
                        token=str(body.get("token") or ""),
                        repositories=[str(item) for item in repositories],
                        source_account_id=str(body.get("source_account_id") or "") or None,
                        account_label=str(body.get("account_label") or "") or None,
                        account_identifier=str(body.get("account_identifier") or "") or None,
                        since=str(body.get("since") or "") or None,
                        processing=str(body.get("processing") or "sync"),
                        max_records=max_records,
                        cursor_name=str(body.get("cursor_name") or "issues"),
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
                self._send_json(store.run_due_jobs(user_id, limit=_int_param(params, "limit", 10, 1, 100)))
                return
            if method == "POST" and path == "/v1/maintenance/jobs/run":
                self._send_json(store.run_due_jobs(user_id, limit=_int_param(params, "limit", 10, 1, 100)))
                return
            if method == "GET" and path.startswith("/v1/jobs/"):
                job_id = unquote(path.removeprefix("/v1/jobs/"))
                job = store.get_job(user_id, job_id)
                if not job:
                    self._send_json({"detail": "Job not found"}, status=HTTPStatus.NOT_FOUND)
                else:
                    self._send_json(job)
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
                limit = _int_param(params, "limit", 10, 1, 50)
                if hasattr(store, "public_search_payload"):
                    payload = store.public_search_payload(user_id, query, limit, kind, layer, sector=sector)
                elif hasattr(store, "public_search"):
                    results = store.public_search(user_id, query, limit, kind, layer, sector=sector)
                    payload = {"query": query, "sector": sector, "results": results, "retrieval": {"diagnostics_unavailable": True}}
                else:
                    results = store.search(user_id, query, limit, kind, layer, sector=sector)
                    payload = {"query": query, "sector": sector, "results": results, "retrieval": {"diagnostics_unavailable": True}}
                self._send_json(payload)
                return
            if method == "GET" and path == "/v1/ask":
                query = (params.get("query") or [""])[0]
                sector = (params.get("sector") or [None])[0]
                self._send_json(store.answer_query(user_id, query, _int_param(params, "limit", 8, 1, 20), sector=sector))
                return
            if method == "GET" and path == "/v1/tasks/open":
                self._send_json({"results": store.open_tasks(user_id, _int_param(params, "limit", 20, 1, 100))})
                return
            if method == "GET" and path == "/v1/topics":
                sector = (params.get("sector") or [None])[0]
                self._send_json({"sector": sector, "results": store.list_topics(user_id, _int_param(params, "limit", 30, 1, 100), sector=sector)})
                return
            if method == "GET" and path == "/v1/entities":
                sector = (params.get("sector") or [None])[0]
                self._send_json({"sector": sector, "results": store.list_entities(user_id, _int_param(params, "limit", 30, 1, 100), sector=sector)})
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
            if method == "GET" and path == "/v1/stats":
                self._send_json(store.stats(user_id))
                return
            if method == "GET" and path == "/v1/memory/quality":
                self._send_json(store.memory_quality_report(user_id))
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
            if method == "GET" and path == "/v1/export.json":
                self._send_json(store.export_json(user_id))
                return
            if method == "GET" and path == "/v1/export.md":
                self._send_text(store.export_markdown(user_id), media_type="text/markdown")
                return
            self._send_json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"detail": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_mcp(self, context: dict) -> None:
        user_id = context["user_id"]
        token_scopes = None if context.get("admin") else list(context.get("scopes") or [])
        request = self._json_body()
        try:
            method = request.get("method")
            if method == "initialize":
                result = {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "cortex", "version": BACKEND_VERSION},
                    "capabilities": {"tools": {}},
                }
            elif method == "tools/list":
                result = {"tools": tools_for_scopes(token_scopes)}
            elif method == "tools/call":
                params = request.get("params") or {}
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
                raise ValueError(f"Unsupported MCP method: {method}")
            self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        except Exception as exc:
            self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": str(exc)}})

    def _auth_user(self, method: str, path: str) -> str | None:
        authorization = self.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            self._send_json({"detail": "Missing or invalid Cortex API token"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        token = authorization.split(" ", 1)[1].strip()
        if settings.api_key:
            if hmac.compare_digest(token, settings.api_key):
                requested_user = self.headers.get("X-Cortex-User")
                if requested_user and requested_user != settings.default_user_id and settings.shard_mode != "local":
                    self._send_json({"detail": "Global Cortex API token cannot select another user in sharded mode; use a scoped user token"}, status=HTTPStatus.FORBIDDEN)
                    return None
                if requested_user and requested_user != settings.default_user_id and settings.require_scoped_api_tokens:
                    self._send_json({"detail": "Global Cortex API token cannot select another user when scoped API tokens are required"}, status=HTTPStatus.FORBIDDEN)
                    return None
                return requested_user or settings.default_user_id
        try:
            scoped = store.authenticate_api_token(token, user_id=self.headers.get("X-Cortex-User"))
        except TypeError:
            scoped = store.authenticate_api_token(token)
        if scoped:
            requested_user = self.headers.get("X-Cortex-User")
            if requested_user and scoped["user_id"] != requested_user:
                self._send_json({"detail": "Cortex API token does not match requested user"}, status=HTTPStatus.FORBIDDEN)
                return None
            required_scope = _required_api_scope(method, path)
            if not _api_token_has_scope(scoped, required_scope):
                self._send_json({"detail": f"Cortex API token requires {required_scope} scope"}, status=HTTPStatus.FORBIDDEN)
                return None
            try:
                _require_api_token_trust(scoped["user_id"], required_scope)
            except PermissionError as exc:
                self._send_json({"detail": str(exc)}, status=HTTPStatus.FORBIDDEN)
                return None
            return scoped["user_id"]
        self._send_json({"detail": "Missing or invalid Cortex API token"}, status=HTTPStatus.UNAUTHORIZED)
        return None

    def _auth_mcp(self) -> dict | None:
        authorization = self.headers.get("Authorization", "")
        if not authorization.lower().startswith("bearer "):
            self._send_json({"detail": "Missing or invalid Cortex MCP token"}, status=HTTPStatus.UNAUTHORIZED)
            return None
        token = authorization.split(" ", 1)[1].strip()
        if settings.api_key and hmac.compare_digest(token, settings.api_key):
            requested_user = self.headers.get("X-Cortex-User")
            if requested_user and requested_user != settings.default_user_id and settings.shard_mode != "local":
                self._send_json({"detail": "Global Cortex API token cannot select another user in sharded mode; use a scoped user token"}, status=HTTPStatus.FORBIDDEN)
                return None
            if requested_user and requested_user != settings.default_user_id and settings.require_scoped_api_tokens:
                self._send_json({"detail": "Global Cortex API token cannot select another user when scoped API tokens are required"}, status=HTTPStatus.FORBIDDEN)
                return None
            return {
                "user_id": requested_user or settings.default_user_id,
                "token_id": "admin",
                "label": "Cortex app token",
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
        self._send_json({"detail": "Missing or invalid Cortex MCP token"}, status=HTTPStatus.UNAUTHORIZED)
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
        if settings.api_key:
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
        )

    def _json_body(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def _form_body(self) -> dict[str, list[str]]:
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length <= 0:
            return {}
        return parse_qs(self.rfile.read(length).decode("utf-8"), keep_blank_values=True)

    def _send_json(self, payload: dict, status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._send_bytes(data, status=status, media_type="application/json")

    def _send_text(self, text: str, status: HTTPStatus = HTTPStatus.OK, media_type: str = "text/plain") -> None:
        self._send_bytes(text.encode("utf-8"), status=status, media_type=f"{media_type}; charset=utf-8")

    def _send_bytes(self, data: bytes, status: HTTPStatus = HTTPStatus.OK, media_type: str = "application/octet-stream") -> None:
        self.send_response(status)
        self.send_header("Content-Type", media_type)
        self.send_header("Content-Length", str(len(data)))
        origin = (self.headers.get("Origin") or "").rstrip("/")
        if origin and origin in ALLOWED_CORS_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Headers", "Authorization, Content-Type, X-Cortex-User")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(data)


def serve(host: str = "127.0.0.1", port: int | None = None) -> None:
    resolved_port = port or int(os.environ.get("CORTEX_PORT", "8766"))
    server = ThreadingHTTPServer((host, resolved_port), CortexRequestHandler)
    print(f"Cortex standalone backend running on http://{host}:{resolved_port}", flush=True)
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the dependency-light Cortex local backend.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    serve(args.host, args.port)


if __name__ == "__main__":
    main()
