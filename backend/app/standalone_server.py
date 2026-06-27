from __future__ import annotations

import argparse
import html
import hmac
import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

from .config import load_settings
from .database import init_db
from .extractor import extract_context
from .mcp_tools import TOOLS, call_tool, tool_result_text
from .storage import BACKEND_VERSION, CortexStore


settings = load_settings()
init_db(settings.db_path)
store = CortexStore(settings.db_path, settings.vault_path)
store.ensure_vault_backfilled(settings.default_user_id)


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


def _capture_page(message: str = "", status: str = "ready", token: str = "", title: str = "", url: str = "", content: str = "") -> str:
    escaped_message = html.escape(message)
    escaped_status = html.escape(status)
    escaped_token = html.escape(token)
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
      <input name="token" value="{escaped_token}" autocomplete="off" />
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
                self._send_json(store.health_payload(mode="standalone", auth=bool(settings.api_key)))
                return
            if method == "GET" and path == "/ready":
                diagnostics = store.diagnostics(settings.default_user_id)
                if diagnostics["status"] != "ok":
                    self._send_json({"detail": diagnostics}, status=HTTPStatus.SERVICE_UNAVAILABLE)
                else:
                    self._send_json({"status": "ok", "diagnostics": diagnostics})
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
                    user_id = self._auth_token(token)
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
                user_id = self._auth_token(token)
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

            user_id = self._auth_user()
            if not user_id:
                return

            if method == "POST" and path == "/v1/captures":
                body = self._json_body()
                content = str(body.get("content", ""))
                source = str(body.get("source") or "macos")[:80]
                title = str(body.get("title") or "")[:200] or None
                source_url = str(body.get("source_url") or "")[:500] or None
                try:
                    self._send_json(self._save_capture(user_id, content, source, title, source_url))
                except ValueError as exc:
                    status = HTTPStatus.REQUEST_ENTITY_TOO_LARGE if "large" in str(exc) else HTTPStatus.UNPROCESSABLE_ENTITY
                    self._send_json({"detail": str(exc)}, status=status)
                return

            if method == "GET" and path == "/v1/recent":
                self._send_json({"results": store.recent(user_id, _int_param(params, "limit", 20, 1, 100))})
                return
            if method == "GET" and path == "/v1/inbox":
                self._send_json({"results": store.inbox(user_id, _int_param(params, "limit", 30, 1, 100))})
                return
            if method == "GET" and path == "/v1/search":
                query = (params.get("query") or [""])[0]
                kind = (params.get("kind") or [None])[0]
                self._send_json({"query": query, "results": store.search(user_id, query, _int_param(params, "limit", 10, 1, 50), kind)})
                return
            if method == "GET" and path == "/v1/tasks/open":
                self._send_json({"results": store.open_tasks(user_id, _int_param(params, "limit", 20, 1, 100))})
                return
            if method == "GET" and path == "/v1/topics":
                self._send_json({"results": store.list_topics(user_id, _int_param(params, "limit", 30, 1, 100))})
                return
            if method == "GET" and path == "/v1/entities":
                self._send_json({"results": store.list_entities(user_id, _int_param(params, "limit", 30, 1, 100))})
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
                self._send_text(store.context_pack(user_id, query=query, limit=_int_param(params, "limit", 12, 1, 50)), media_type="text/markdown")
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
            if method == "GET" and path == "/v1/graph":
                self._send_json(store.graph(user_id, _int_param(params, "limit", 150, 10, 500)))
                return
            if method == "GET" and path == "/v1/stats":
                self._send_json(store.stats(user_id))
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
            if method == "GET" and path == "/v1/audit-log":
                self._send_json({"results": store.audit_log(user_id, _int_param(params, "limit", 80, 1, 300))})
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
            if method == "POST" and path == "/v1/maintenance/repair-storage":
                self._send_json(store.repair_storage(user_id))
                return
            if method == "POST" and path == "/v1/maintenance/rebuild-search":
                self._send_json(store.rebuild_search_index(user_id))
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
            if method == "POST" and path == "/mcp":
                self._handle_mcp(user_id)
                return

            self._send_json({"detail": "Not found"}, status=HTTPStatus.NOT_FOUND)
        except Exception as exc:
            self._send_json({"detail": str(exc)}, status=HTTPStatus.INTERNAL_SERVER_ERROR)

    def _handle_mcp(self, user_id: str) -> None:
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
                result = {"tools": TOOLS}
            elif method == "tools/call":
                params = request.get("params") or {}
                tool_name = params.get("name", "")
                arguments = params.get("arguments", {}) or {}
                try:
                    value = call_tool(store, user_id, tool_name, arguments)
                    store.record_agent_event(user_id, tool_name, arguments, success=True)
                except Exception as exc:
                    store.record_agent_event(user_id, tool_name, arguments, success=False, error=str(exc))
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
                raise ValueError(f"Unsupported MCP method: {method}")
            self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "result": result})
        except Exception as exc:
            self._send_json({"jsonrpc": "2.0", "id": request.get("id"), "error": {"code": -32000, "message": str(exc)}})

    def _auth_user(self) -> str | None:
        if settings.api_key:
            expected = f"Bearer {settings.api_key}"
            authorization = self.headers.get("Authorization", "")
            if not hmac.compare_digest(authorization, expected):
                self._send_json({"detail": "Missing or invalid Cortex API token"}, status=HTTPStatus.UNAUTHORIZED)
                return None
        return self.headers.get("X-Cortex-User") or settings.default_user_id

    def _auth_token(self, token: str | None) -> str | None:
        if settings.api_key and not hmac.compare_digest(token or "", settings.api_key):
            return None
        return settings.default_user_id

    def _save_capture(self, user_id: str, content: str, source: str, title: str | None, source_url: str | None) -> dict:
        content = str(content or "").strip()
        if not content:
            raise ValueError("content is required")
        if len(content) > 200_000:
            raise ValueError("content is too large")
        normalized_source = str(source or "browser-capture")[:80]
        extracted = extract_context(content, normalized_source)
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
        self.send_header("Access-Control-Allow-Origin", "*")
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
