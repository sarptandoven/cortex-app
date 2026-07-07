#!/usr/bin/env python3
"""Live end-to-end test of Cortex's external output connections.

Boots the local backend exactly as the shipped app does (model2vec + query-plan + temporal-decay),
seeds realistic user memory, then drives EVERY outbound path a real user/app would use and asserts
each returns real, cited results — including pushing the user's data OUT to another service:

  1. MCP JSON-RPC over /mcp: initialize, tools/list, tools/call (get_context, ask_memory, use_cortex),
     resources/list+read, prompts/list+get
  2. Universal HTTP tool API: /v1/tools/schema (openai/anthropic/openapi), /v1/tools/call, /v1/tools/{name}
  3. The stdio bridge (scripts/cortex_mcp_stdio.py) a desktop client subprocess would spawn
  4. The Python SDK against the live server
  5. Browser-extension pairing (/v1/pair) + a paired read-only token + extension-origin CORS
  6. Delivery/push: /v1/delivery/preview + /v1/delivery/send to a REAL local webhook receiver
     (the "send my data to another service" proof)

Run from the repo root: python3 scripts/e2e_external_connections.py
Exits 0 iff every check passes.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
API_KEY = "e2e-external-key"
SERVER_PORT = 8804
CHECKS: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    CHECKS.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def _req(method: str, path: str, *, token: str = API_KEY, body: dict | None = None, origin: str | None = None, base: str | None = None):
    url = (base or f"http://127.0.0.1:{SERVER_PORT}") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if origin:
        headers["Origin"] = origin
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw and raw[0] in "{[" else raw), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, (json.loads(raw) if raw and raw[0] in "{[" else raw), dict(exc.headers)


def _rpc(method: str, params: dict | None = None, *, rpc_id: int = 1):
    return _req("POST", "/mcp", body={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}})


# --- local webhook receiver (stands in for the external service) --------------------------
_received: list[dict] = []


class _Receiver(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0") or "0")
        raw = self.rfile.read(length) if length else b""
        try:
            _received.append(json.loads(raw.decode()))
        except Exception:
            _received.append({"_raw": raw.decode(errors="replace")})
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args):
        pass


def _wait_ready(timeout: float = 25.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _, _ = _req("GET", "/ready", token="")
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def main() -> int:
    import tempfile

    data_dir = tempfile.mkdtemp()
    receiver = ThreadingHTTPServer(("127.0.0.1", 0), _Receiver)
    recv_port = receiver.server_address[1]
    threading.Thread(target=receiver.serve_forever, daemon=True).start()

    env = dict(os.environ)
    env.update({
        "CORTEX_DATA_DIR": data_dir,
        "CORTEX_API_KEY": API_KEY,
        "CORTEX_ALLOW_INSECURE_DEV_TOKEN": "1",
        "CORTEX_EMBEDDING_PROVIDER": "model2vec",
        "CORTEX_QUERY_PLAN": "1",
        "CORTEX_TEMPORAL_DECAY": "1",
        "CORTEX_DELIVERY_ALLOW_INTERNAL": "1",  # allow delivering to the local receiver
        "CORTEX_AUTO_APPROVE_CAPTURES": "1",    # seeded memory is active immediately (a trusted-setup user)
    })
    server = subprocess.Popen(
        [sys.executable, "-S", "-s", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", str(SERVER_PORT)],
        cwd=str(BACKEND), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        print("Booting backend (model2vec + query-plan + temporal-decay)...")
        if not _wait_ready():
            check("server /ready", False, "backend did not become ready")
            return _finish()
        check("server /ready", True)

        # --- seed realistic user memory via the write path an agent would use -------------
        print("Seeding user memory...")
        memories = [
            "I decided to use Postgres for the billing service because of its reliability and mature tooling.",
            "My colleague Alice owns the search infrastructure and strongly prefers Go for new services.",
            "We ship deploys on Tuesdays, only after the reliability report is green.",
            "I prefer concise, direct writing that leads with the decision.",
        ]
        for text in memories:
            status, _, _ = _req("POST", "/v1/tools/call", body={"name": "remember_this", "arguments": {"content": text, "source": "notes"}})
        check("seed memories via remember_this", status == 200)
        time.sleep(6)  # let the background worker extract + embed
        status, stats, _ = _req("GET", "/v1/stats")
        mem_count = (stats or {}).get("memory_count") or (stats or {}).get("memories") or 0
        check("memory persisted + indexed", mem_count >= 3, f"{mem_count} memories")

        # === 1. MCP JSON-RPC =============================================================
        print("\n[1] MCP over /mcp")
        _, init, _ = _rpc("initialize", {"protocolVersion": "2025-06-18"})
        caps = (init.get("result") or {}).get("capabilities", {})
        check("MCP initialize (tools+resources+prompts)", {"tools", "resources", "prompts"} <= set(caps), str(sorted(caps)))
        _, tl, _ = _rpc("tools/list")
        names = {t["name"] for t in (tl.get("result") or {}).get("tools", [])}
        check("MCP tools/list has core + use_cortex", {"use_cortex", "get_context", "ask_memory"} <= names)
        _, gc, _ = _rpc("tools/call", {"name": "get_context", "arguments": {"task": "prepare for a meeting about the billing service"}})
        gc_val = json.loads((gc.get("result") or {}).get("content", [{}])[0].get("text", "{}")) if gc.get("result") else {}
        check("MCP get_context returns a cited pack", bool(gc_val.get("layers")) and "citations" in gc_val)
        _, am, _ = _rpc("tools/call", {"name": "ask_memory", "arguments": {"query": "what database did I choose for billing?"}})
        am_val = json.loads((am.get("result") or {}).get("content", [{}])[0].get("text", "{}")) if am.get("result") else {}
        cited = am_val.get("citations") or []
        check("MCP ask_memory cites the Postgres decision", any("postgres" in json.dumps(c).lower() for c in cited), f"status={am_val.get('status')}, {len(cited)} citations")
        _, uc, _ = _rpc("tools/call", {"name": "use_cortex", "arguments": {"task": "tell me about Alice"}})
        uc_val = json.loads((uc.get("result") or {}).get("content", [{}])[0].get("text", "{}")) if uc.get("result") else {}
        check("MCP use_cortex routes the task", uc_val.get("routed_to") in {"get_entity_context", "ask_memory", "get_context", "search_memory"}, f"routed_to={uc_val.get('routed_to')}")
        _, rl, _ = _rpc("resources/list")
        check("MCP resources/list", any(r["uri"] == "cortex://profile/person-map" for r in (rl.get("result") or {}).get("resources", [])))
        _, rr, _ = _rpc("resources/read", {"uri": "cortex://profile/personal"})
        check("MCP resources/read returns JSON contents", bool((rr.get("result") or {}).get("contents")))
        _, pl, _ = _rpc("prompts/list")
        check("MCP prompts/list", any(p["name"] == "brief_me_on" for p in (pl.get("result") or {}).get("prompts", [])))
        _, pg, _ = _rpc("prompts/get", {"name": "brief_me_on", "arguments": {"subject": "Alice"}})
        check("MCP prompts/get returns grounded messages", bool((pg.get("result") or {}).get("messages")))

        # === 2. Universal HTTP tool API ==================================================
        print("\n[2] Universal HTTP tool API")
        for fmt, key in [("openai", "function"), ("anthropic", "name"), ("openapi", None)]:
            status, sc, _ = _req("GET", f"/v1/tools/schema?format={fmt}")
            payload = (sc or {}).get("schema")
            ok = status == 200 and (payload.get("openapi") == "3.1.0" if fmt == "openapi" else len(payload) > 5)
            check(f"/v1/tools/schema?format={fmt}", ok)
        status, tc, _ = _req("POST", "/v1/tools/call", body={"name": "search_memory", "arguments": {"query": "deploys"}})
        check("/v1/tools/call (generic)", status == 200 and (tc or {}).get("tool") == "search_memory" and "result" in (tc or {}))
        status, pt, _ = _req("POST", "/v1/tools/get_context", body={"task": "billing"})
        check("/v1/tools/{name} (per-tool, OpenAPI-style)", status == 200 and bool(pt.get("layers") if isinstance(pt, dict) else False))

        # === 3. stdio bridge (what a desktop client spawns) ==============================
        print("\n[3] stdio MCP bridge")
        bridge_env = dict(env)
        bridge_env.update({"CORTEX_BASE_URL": f"http://127.0.0.1:{SERVER_PORT}", "CORTEX_API_KEY": API_KEY})
        proc = subprocess.run(
            [sys.executable, str(REPO / "scripts" / "cortex_mcp_stdio.py")],
            input=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "ask_memory", "arguments": {"query": "when do we deploy?"}}}) + "\n",
            capture_output=True, text=True, env=bridge_env, timeout=30,
        )
        bridge_ok = False
        try:
            resp = json.loads(proc.stdout.strip().splitlines()[0])
            bridge_ok = resp.get("id") == 7 and "result" in resp
        except Exception:
            pass
        check("stdio bridge proxies a tools/call", bridge_ok, proc.stderr.strip()[:80])

        # === 4. Python SDK ================================================================
        print("\n[4] Python SDK")
        sys.path.insert(0, str(REPO / "sdk" / "python"))
        try:
            from cortex_client import CortexClient  # type: ignore

            cx = CortexClient(base_url=f"http://127.0.0.1:{SERVER_PORT}", token=API_KEY)
            pack = cx.context("prep the billing sync")
            answer = cx.ask("what database did I choose for billing?")
            tools = cx.openai_tools()
            check("SDK context() returns a pack", isinstance(pack, dict) and bool(pack.get("layers")))
            check("SDK ask() returns cited/answer", isinstance(answer, dict) and ("citations" in answer or "status" in answer))
            check("SDK openai_tools()", isinstance(tools, list) and len(tools) > 5)
        except Exception as exc:
            check("SDK works against live server", False, str(exc)[:100])

        # === 5. Browser-extension pairing + CORS =========================================
        print("\n[5] Browser-extension pairing")
        status, pair, _ = _req("POST", "/v1/pair", body={"label": "e2e ext"})
        ext_token = (pair or {}).get("token", "")
        check("/v1/pair mints a read token", status == 200 and ext_token.startswith("cxm_"))
        status, _, _ = _req("POST", "/v1/tools/call", token=ext_token, body={"name": "get_context", "arguments": {"task": "billing"}})
        check("paired token can pull context (read)", status == 200)
        status, _, _ = _req("POST", "/v1/tools/call", token=ext_token, body={"name": "remember_this", "arguments": {"content": "x"}})
        check("paired token is refused writes (403)", status == 403)
        _, _, hdrs = _req("OPTIONS", "/v1/context", token="", origin="chrome-extension://abcdef")
        check("extension-origin CORS preflight allowed", hdrs.get("Access-Control-Allow-Origin") == "chrome-extension://abcdef")

        # === 6. Delivery — send the user's data OUT to another service ===================
        print("\n[6] Delivery / push to an external service")
        recv_url = f"http://127.0.0.1:{recv_port}/hook"
        status, prev, _ = _req("POST", "/v1/delivery/preview", body={"task": "billing decisions", "token_budget": 800})
        check("/v1/delivery/preview builds a cited brief", status == 200 and bool((prev or {}).get("preview", {}).get("pack")))
        before = len(_received)
        status, snd, _ = _req("POST", "/v1/delivery/send", body={"url": recv_url, "task": "billing decisions"})
        check("/v1/delivery/send reports delivered", status == 200 and (snd or {}).get("delivered") is True, str((snd or {}).get("reason") or ""))
        time.sleep(0.5)
        delivered_ok = len(_received) > before and isinstance(_received[-1], dict) and "pack" in _received[-1]
        check("external service RECEIVED the cited brief", delivered_ok, f"payloads received: {len(_received)}")
        # Scheme-based refusal holds regardless of the allow-internal dev flag (guard checks scheme
        # first). Full internal-IP SSRF coverage lives in scripts/delivery_eval.py.
        status, blocked, _ = _req("POST", "/v1/delivery/send", body={"url": "ftp://evil.example/x", "task": "x"})
        check("unsafe delivery target refused (422)", status == 422)

        return _finish()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()
        receiver.shutdown()


def _finish() -> int:
    passed = sum(1 for _, ok, _ in CHECKS if ok)
    total = len(CHECKS)
    print(f"\n{'='*60}\nRESULT: {passed}/{total} checks passed")
    failures = [name for name, ok, _ in CHECKS if not ok]
    if failures:
        print("FAILED:", ", ".join(failures))
    return 0 if passed == total else 1


if __name__ == "__main__":
    raise SystemExit(main())
