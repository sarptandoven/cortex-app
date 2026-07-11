#!/usr/bin/env python3
"""Cortex 60-second cross-AI demo kit: one memory, three AI clients, cited answers.

The demo this prepares: a curated founder persona lives in ONE local Cortex memory;
three different AI clients (Claude Desktop, Cursor, and ChatGPT via the Cortex browser
extension) answer questions about that persona from the SAME memory — with citations.

Modes
  (default)     Make the machine demo-ready against the RUNNING Cortex app
                (http://127.0.0.1:8766): seed the persona through the real
                capture -> extract -> review pipeline, mint one MCP token per
                client, and write ready-to-paste configs.
  --verify      After the human has exercised the three clients, prove EACH client
                label performed >= 1 read-class tool call, using the real
                /v1/eval/scorecard read-model over the mcp:* event log.
                Prints a pass/fail table; exits nonzero if any client is dark.
  --self-test   Fully headless rehearsal (CI-runnable): boots a scratch standalone
                server on a spare port (NEVER 8766), seeds, simulates the three
                clients over their real transports (stdio bridge for Claude Desktop,
                HTTP /mcp JSON-RPC for Cursor, /v1/tools/call for the extension),
                then runs the same --verify assertions. Exits nonzero on failure.

Companion runbook: docs/DEMO_RUNBOOK.md
Python stdlib only (repo convention: backend/app is stdlib-only; scripts/ follows).
No external network calls; everything is loopback.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BACKEND = REPO / "backend"
STDIO_BRIDGE = REPO / "scripts" / "cortex_mcp_stdio.py"

# The live app owns 8766. We may TALK to it there (default mode), but any server this
# script BOOTS must use a spare port so a running app is never clobbered.
APP_PORT = 8766
DEFAULT_BASE_URL = f"http://127.0.0.1:{APP_PORT}"
SELF_TEST_PORT = 8809  # spare; e2e_external_connections.py uses 8804
SELF_TEST_KEY = "demo-kit-selftest-key"

CLIENT_LABELS = ("Claude Desktop", "Cursor", "ChatGPT (extension)")

# ---------------------------------------------------------------------------
# The persona vault: Maya Chen, founder of Driftline (an AI logistics copilot).
# Deterministic, curated content — every capture is written to extract cleanly
# (decisions, people, preferences, style, metrics) so "What do you know about me?"
# and "What did I decide about X?" produce specific, CITED answers.
# ---------------------------------------------------------------------------
PERSONA_NAME = "Maya Chen — founder & CEO of Driftline (AI logistics copilot)"
PERSONA_CAPTURES: tuple[dict[str, str], ...] = (
    {
        "title": "Vector store decision for Atlas",
        "source": "notes",
        "content": (
            "I decided to use Postgres with pgvector for the Atlas memory store instead of "
            "Pinecone, because it cuts our infra bill by roughly 70 percent and keeps everything "
            "in one operational database. Priya ran the latency bakeoff on June 12th and p95 "
            "stayed under 40ms at our scale."
        ),
    },
    {
        "title": "Pricing decision",
        "source": "notes",
        "content": (
            "We decided on usage-based pricing over per-seat licenses for Driftline, because our "
            "design partners kept asking to add read-only teammates for free."
        ),
    },
    {
        "title": "Priya Raman — cofounder",
        "source": "notes",
        "content": (
            "My cofounder Priya Raman owns all of our infrastructure and strongly prefers Go for "
            "new services. She pushes back hard on premature microservices."
        ),
    },
    {
        "title": "Jonas Wolf — design lead",
        "source": "notes",
        "content": (
            "Jonas Wolf leads design at Driftline. He insists every new screen ships with an "
            "empty state, and he prefers Figma prototypes over written specs."
        ),
    },
    {
        "title": "Elena Sørensen — Northlight Capital",
        "source": "notes",
        "content": (
            "Elena Sørensen at Northlight Capital led our seed round. She expects the weekly "
            "metrics email every Monday morning and cares most about net revenue retention."
        ),
    },
    {
        "title": "Meridian Freight pilot kickoff",
        "source": "meeting-notes",
        "content": (
            "The Meridian Freight pilot for Harbor kicked off on June 3rd. Their ops lead Tom "
            "Okafor needs SSO before their security review in August, so I decided to prioritize "
            "SAML SSO ahead of the mobile app."
        ),
    },
    {
        "title": "Deploy cadence",
        "source": "notes",
        "content": (
            "We ship deploys on Tuesdays only, and only after the reliability report is green."
        ),
    },
    {
        "title": "Writing style",
        "source": "notes",
        "content": (
            "I prefer concise, decision-first writing: lead with the decision, then two or three "
            "supporting bullets. No exclamation marks, and I never use the word synergy."
        ),
    },
    {
        "title": "Deep-work block",
        "source": "notes",
        "content": (
            "I hold a deep-work block every weekday from 8 to 11 in the morning and take no "
            "meetings before noon."
        ),
    },
    {
        "title": "June metrics",
        "source": "notes",
        "content": (
            "As of June, Driftline has 14 design partners, 18k dollars in MRR, and 128 percent "
            "net revenue retention."
        ),
    },
    {
        "title": "Personal",
        "source": "journal",
        "content": (
            "I am training for the Berlin Marathon in September, I am vegetarian, and I start "
            "every morning with a double espresso."
        ),
    },
    {
        "title": "Hiring decision",
        "source": "notes",
        "content": (
            "I decided not to hire a sales lead until we cross 40k in MRR; until then it is "
            "founder-led sales only. Priya agreed after we reviewed the pipeline on June 20th."
        ),
    },
)

# The question bank the runbook choreographs — each maps directly onto seeded captures.
QUESTION_BANK: tuple[tuple[str, str], ...] = (
    ("Claude Desktop", "What did I decide about the vector database, and why?"),
    ("Cursor", "Search my memory for the Meridian Freight pilot — what does Tom Okafor need?"),
    ("ChatGPT (extension)", "What do you know about me?"),
    ("any", "How should I write the investor update for Elena?"),
    ("any", "Who is Priya and what does she care about?"),
    ("any", "When do we ship deploys?"),
    ("any", "What did I decide about hiring a sales lead?"),
    ("any", "What are Driftline's latest metrics?"),
)

MIN_EXPECTED_NEW_MEMORIES = 8  # floor across the 12 captures; extraction usually yields more
HEADLINE_QUESTION = "What did I decide about the vector database for Atlas?"


def _cites_headline(citations: list) -> bool:
    return any(
        "pgvector" in json.dumps(c).lower() or "postgres" in json.dumps(c).lower()
        for c in citations
    )


# ---------------------------------------------------------------------------
# Tiny HTTP helpers (mirrors scripts/e2e_external_connections.py conventions)
# ---------------------------------------------------------------------------

def _req(base: str, method: str, path: str, *, token: str, body: dict | None = None, timeout: float = 60.0):
    url = base.rstrip("/") + path
    data = json.dumps(body).encode() if body is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, (json.loads(raw) if raw and raw[0] in "{[" else raw)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        return exc.code, (json.loads(raw) if raw and raw[0] in "{[" else raw)


def _rpc(base: str, token: str, method: str, params: dict | None = None, *, rpc_id: int = 1):
    return _req(base, "POST", "/mcp", token=token, body={"jsonrpc": "2.0", "id": rpc_id, "method": method, "params": params or {}})


def _tool_result_payload(rpc_response: dict) -> dict:
    """Unwrap a JSON-RPC tools/call response into the tool's own JSON payload."""
    try:
        text = (rpc_response.get("result") or {}).get("content", [{}])[0].get("text", "{}")
        parsed = json.loads(text)
        return parsed if isinstance(parsed, dict) else {"_value": parsed}
    except Exception:
        return {}


def _wait_ready(base: str, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            status, _ = _req(base, "GET", "/ready", token="", timeout=5)
            if status == 200:
                return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


# ---------------------------------------------------------------------------
# Seeding: the REAL capture path (POST /v1/captures -> extract_context + save_capture),
# then the REAL review path (POST /v1/captures/{id}/approve) so nothing sits pending.
# ---------------------------------------------------------------------------

def _memory_count(base: str, api_key: str) -> int:
    status, stats = _req(base, "GET", "/v1/stats", token=api_key)
    if status != 200 or not isinstance(stats, dict):
        return -1
    value = stats.get("memories")
    if value is None:
        value = stats.get("memory_count")
    return int(value or 0)


def _probe_headline(base: str, api_key: str) -> tuple[int, bool]:
    """ask_memory on the headline demo question; returns (citation_count, cites_persona)."""
    _, probe = _req(base, "POST", "/v1/tools/call", token=api_key, body={
        "name": "ask_memory",
        "arguments": {"query": HEADLINE_QUESTION},
    })
    citations = ((probe or {}).get("result") or {}).get("citations") or []
    return len(citations), _cites_headline(citations)


def seed_persona(base: str, api_key: str) -> tuple[int, int, int, bool]:
    """Returns (captures_saved, captures_approved, memories_after, headline_cited).

    Re-run tolerant: identical memory content dedupes server-side, so the readiness
    signal is the demo's own headline question coming back CITED — not a raw count delta.
    """
    capture_ids: list[str] = []
    for capture in PERSONA_CAPTURES:
        status, saved = _req(base, "POST", "/v1/captures", token=api_key, body={
            "content": capture["content"],
            "source": capture["source"],
            "title": capture["title"],
        })
        if status != 200 or not isinstance(saved, dict) or not saved.get("capture_id"):
            raise RuntimeError(f"seeding capture {capture['title']!r} failed: HTTP {status} {saved}")
        capture_ids.append(str(saved["capture_id"]))

    # Approve through the real review endpoint. 404 means the capture was already
    # active (server-side auto-approve) — both outcomes leave the memory live.
    approved = 0
    for capture_id in capture_ids:
        status, _ = _req(base, "POST", f"/v1/captures/{capture_id}/approve", token=api_key, body={})
        if status == 200:
            approved += 1

    # Wait until the persona actually answers: extraction is synchronous, indexing may lag.
    deadline = time.monotonic() + 90
    cited = False
    while time.monotonic() < deadline:
        _, cited = _probe_headline(base, api_key)
        if cited:
            break
        time.sleep(2.0)
    memories = _memory_count(base, api_key)
    if not cited:
        raise RuntimeError(
            f"seeded persona does not answer the headline question with citations yet "
            f"({memories} active memories); is capture review or retrieval blocked?"
        )
    return len(capture_ids), approved, memories, cited


# ---------------------------------------------------------------------------
# Per-app tokens: real machinery (POST /v1/integrations/mcp-token -> ensure_mcp_token).
# Token values are deterministic per machine (derived from the master key + label):
# re-running the seed is idempotent and never rotates a pasted client config.
# ---------------------------------------------------------------------------

def _demo_token(label: str, api_key: str) -> str:
    digest = hashlib.sha256(f"cortex-demo-kit:{api_key}:{label}".encode()).hexdigest()
    return "cxm_demo" + digest[:40]


def mint_tokens(base: str, api_key: str) -> dict[str, dict[str, str]]:
    tokens: dict[str, dict[str, str]] = {}
    for label in CLIENT_LABELS:
        token = _demo_token(label, api_key)
        status, meta = _req(base, "POST", "/v1/integrations/mcp-token", token=api_key, body={
            "token": token,
            "label": label,
            "scopes": ["read"],
        })
        if status != 200 or not isinstance(meta, dict) or not meta.get("token_id"):
            raise RuntimeError(f"minting token for {label!r} failed: HTTP {status} {meta}")
        tokens[label] = {"token": token, "token_id": str(meta["token_id"])}
    if len({entry["token"] for entry in tokens.values()}) != len(CLIENT_LABELS):
        raise RuntimeError("minted tokens are not distinct")
    return tokens


# ---------------------------------------------------------------------------
# Ready-to-paste configs (shapes mirror macos/Sources/CortexApp.swift
# mcpServerDefinition / mcpConfigJSON — stdio for Claude Desktop, streamable
# HTTP for Cursor, baseUrl+token pairing for the browser extension).
# ---------------------------------------------------------------------------

def claude_desktop_config(base_url: str, token: str) -> dict:
    return {
        "mcpServers": {
            "cortex": {
                "command": shutil.which("python3") or "/usr/bin/python3",
                "args": [str(STDIO_BRIDGE)],
                "env": {
                    "CORTEX_BASE_URL": base_url,
                    "CORTEX_API_KEY": token,
                },
            }
        }
    }


def cursor_config(base_url: str, token: str) -> dict:
    return {
        "mcpServers": {
            "cortex": {
                "type": "http",
                "url": f"{base_url}/mcp",
                "headers": {"Authorization": f"Bearer {token}"},
            }
        }
    }


def extension_pairing_text(base_url: str, token: str) -> str:
    return f"""Cortex browser extension pairing — ChatGPT tab
================================================

1. Load the extension (chrome://extensions -> Load unpacked -> {REPO / 'extension'})
   or use your already-installed Cortex extension.
2. Open the extension's Options page.
3. Paste these values:
     Base URL: {base_url}
     Token:    {token}
4. Click "Test connection" — it should report the token is valid (read scope).
5. Open chatgpt.com; the extension injects Cortex context / offers "Ask Cortex".

This token is read-only and scoped to the "ChatGPT (extension)" label, so its
tool calls show up under that name in --verify and in the app's activity feed.
"""


def write_kit(out_dir: Path, base_url: str, tokens: dict[str, dict[str, str]]) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []

    def _write(name: str, text: str) -> None:
        path = out_dir / name
        path.write_text(text)
        os.chmod(path, 0o600)  # configs embed bearer tokens
        files.append(path)

    _write("claude_desktop.mcp.json", json.dumps(claude_desktop_config(base_url, tokens["Claude Desktop"]["token"]), indent=2, sort_keys=True) + "\n")
    _write("cursor.mcp.json", json.dumps(cursor_config(base_url, tokens["Cursor"]["token"]), indent=2, sort_keys=True) + "\n")
    _write("chatgpt_extension_pairing.txt", extension_pairing_text(base_url, tokens["ChatGPT (extension)"]["token"]))
    _write("demo_tokens.json", json.dumps({"base_url": base_url, "tokens": tokens}, indent=2, sort_keys=True) + "\n")
    _write("README.txt", f"""Cortex 60-second demo kit
=========================
Paste targets:
  claude_desktop.mcp.json  -> merge into ~/Library/Application Support/Claude/claude_desktop_config.json
                              (then fully quit + relaunch Claude Desktop)
  cursor.mcp.json          -> merge into ~/.cursor/mcp.json (or Cursor Settings -> MCP)
  chatgpt_extension_pairing.txt -> follow the steps inside (extension Options page)

Then warm each client once, and check readiness with:
  python3 {REPO / 'scripts' / 'demo_seed.py'} --verify --api-key <same key>

Full choreography: {REPO / 'docs' / 'DEMO_RUNBOOK.md'}
""")
    return files


# ---------------------------------------------------------------------------
# Verify: the real scorecard read-model over the event log (/v1/eval/scorecard),
# which attributes every MCP tool call to the token (= host app) that made it.
# ---------------------------------------------------------------------------

def collect_verify_rows(base: str, api_key: str, tokens: dict[str, dict[str, str]] | None, window_days: int) -> list[tuple[str, int, bool]]:
    status, scorecard = _req(base, "GET", f"/v1/eval/scorecard?days={max(1, window_days)}", token=api_key)
    if status != 200 or not isinstance(scorecard, dict):
        raise RuntimeError(f"scorecard query failed: HTTP {status} {scorecard}")
    hosts = scorecard.get("hosts") or []
    rows: list[tuple[str, int, bool]] = []
    for label in CLIENT_LABELS:
        expected_id = ((tokens or {}).get(label) or {}).get("token_id")
        host = None
        for candidate in hosts:
            if expected_id and candidate.get("token_id") == expected_id:
                host = candidate
                break
            if candidate.get("token_label") == label and host is None:
                host = candidate
        reads = int((host or {}).get("read_calls") or 0)
        rows.append((label, reads, reads >= 1))
    return rows


def print_verify_table(rows: list[tuple[str, int, bool]], window_days: int) -> bool:
    print(f"\nCross-AI read verification (scorecard window: last {max(1, window_days)} day(s))")
    width = max(len(label) for label, _, _ in rows) + 2
    for label, reads, ok in rows:
        mark = "✅" if ok else "❌"
        detail = f"{reads} read{'s' if reads != 1 else ''}" if ok else "none yet"
        print(f"  {label:<{width}} {mark}  {detail}")
    summary = " · ".join(
        f"{label} {'✅' if ok else '❌'} {f'{reads} read' + ('s' if reads != 1 else '') if ok else 'none yet'}"
        for label, reads, ok in rows
    )
    print(f"\n{summary}")
    return all(ok for _, _, ok in rows)


def load_kit_tokens(out_dir: Path) -> tuple[str | None, dict[str, dict[str, str]] | None]:
    path = out_dir / "demo_tokens.json"
    if not path.exists():
        return None, None
    try:
        data = json.loads(path.read_text())
        return data.get("base_url"), data.get("tokens")
    except Exception:
        return None, None


# ---------------------------------------------------------------------------
# Simulated clients (self-test): each uses the REAL transport its client uses.
# ---------------------------------------------------------------------------

def simulate_claude_desktop(base: str, token: str) -> tuple[dict, dict]:
    """Claude Desktop spawns the stdio bridge; drive the actual bridge subprocess."""
    env = dict(os.environ)
    env.update({"CORTEX_BASE_URL": base, "CORTEX_API_KEY": token})
    ask = subprocess.run(
        [sys.executable, str(STDIO_BRIDGE)],
        input=json.dumps({
            "jsonrpc": "2.0", "id": 11, "method": "tools/call",
            "params": {"name": "ask_memory", "arguments": {"query": HEADLINE_QUESTION}},
        }) + "\n",
        capture_output=True, text=True, env=env, timeout=90,
    )
    try:
        ask_response = json.loads(ask.stdout.strip().splitlines()[0])
    except Exception:
        raise RuntimeError(f"stdio bridge produced no JSON-RPC response: {ask.stderr.strip()[:200]}")
    # A second read (context pack), as a real prep-for-a-task turn would issue.
    _, ctx_response = _rpc(base, token, "tools/call", {
        "name": "get_context",
        "arguments": {"task": "prep the Monday investor update for Elena at Northlight"},
    }, rpc_id=12)
    return ask_response, ctx_response


def simulate_cursor(base: str, token: str) -> dict:
    """Cursor speaks streamable-HTTP MCP: initialize + tools/call against POST /mcp."""
    _rpc(base, token, "initialize", {"protocolVersion": "2025-06-18"}, rpc_id=20)
    _, response = _rpc(base, token, "tools/call", {
        "name": "search_memory",
        "arguments": {"query": "Meridian Freight pilot SSO Tom Okafor"},
    }, rpc_id=21)
    return response


def simulate_chatgpt_extension(base: str, token: str) -> tuple[int, dict]:
    """The browser extension calls the HTTP tool API (/v1/tools/call use_cortex)."""
    return _req(base, "POST", "/v1/tools/call", token=token, body={
        "name": "use_cortex",
        "arguments": {"task": "What do you know about me?"},
    })


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_seed(args: argparse.Namespace) -> int:
    base = args.base_url.rstrip("/")
    if not args.api_key:
        print("error: --api-key (or CORTEX_API_KEY) is required to seed the live app.\n"
              "Copy it from Cortex -> Connections & Privacy -> Advanced Setup.", file=sys.stderr)
        return 2
    print("== Cortex 60-second cross-AI demo kit ==")
    print(f"[1/4] Checking backend at {base} ...")
    if not _wait_ready(base, timeout=10):
        print(f"error: no ready Cortex backend at {base}. Is the Cortex app running?", file=sys.stderr)
        return 1
    print("      ready.")

    print(f"[2/4] Seeding persona vault: {PERSONA_NAME}")
    saved, approved, memories, cited = seed_persona(base, args.api_key)
    print(f"      {saved} captures via the real capture pipeline ({approved} explicitly approved); "
          f"{memories} active memories total; headline question comes back cited.")

    print("[3/4] Minting per-app MCP tokens (read scope, one per client)...")
    tokens = mint_tokens(base, args.api_key)
    for label in CLIENT_LABELS:
        print(f"      {label:<22} token_id={tokens[label]['token_id']}")

    out_dir = Path(args.out_dir).expanduser()
    print(f"[4/4] Writing ready-to-paste configs to {out_dir}")
    for path in write_kit(out_dir, base, tokens):
        print(f"      wrote {path}")

    print("\n--- Claude Desktop (merge into claude_desktop_config.json, then relaunch) ---")
    print(json.dumps(claude_desktop_config(base, tokens["Claude Desktop"]["token"]), indent=2, sort_keys=True))
    print("\n--- Cursor (merge into ~/.cursor/mcp.json) ---")
    print(json.dumps(cursor_config(base, tokens["Cursor"]["token"]), indent=2, sort_keys=True))
    print("\n--- ChatGPT (browser extension pairing) ---")
    print(extension_pairing_text(base, tokens["ChatGPT (extension)"]["token"]))

    print("Question bank (see docs/DEMO_RUNBOOK.md for the 60-second choreography):")
    for client, question in QUESTION_BANK:
        print(f"  [{client}] {question}")
    print("\nNext: paste the three configs, warm each client once, then run:")
    print(f"  python3 {Path(__file__).resolve()} --verify --api-key <same key>")
    print("\nDEMO READY")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir).expanduser()
    kit_base, tokens = load_kit_tokens(out_dir)
    base = (args.base_url if args.base_url_set else (kit_base or args.base_url)).rstrip("/")
    if not args.api_key:
        print("error: --verify needs the app API key (--api-key or CORTEX_API_KEY) to read the scorecard.", file=sys.stderr)
        return 2
    rows = collect_verify_rows(base, args.api_key, tokens, args.window_days)
    ok = print_verify_table(rows, args.window_days)
    if not ok:
        print("\nA client is dark: ask it one seeded question (see the question bank), then re-run --verify.")
    return 0 if ok else 1


def cmd_self_test(args: argparse.Namespace) -> int:
    port = args.port
    if port == APP_PORT:
        print(f"error: refusing to boot the self-test server on {APP_PORT} (the live app's port).", file=sys.stderr)
        return 2
    base = f"http://127.0.0.1:{port}"
    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, bool(ok), detail))
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    data_dir = tempfile.mkdtemp(prefix="cortex-demo-selftest-")
    kit_dir = Path(tempfile.mkdtemp(prefix="cortex-demo-kit-selftest-"))
    log_path = Path(data_dir) / "server.log"
    env = dict(os.environ)
    env.update({
        # NOTE: the backend reads CORTEX_VAULT_PATH (config.py), not CORTEX_DATA_DIR —
        # this is what actually isolates the scratch store from backend/data/Cortex.vault.
        "CORTEX_VAULT_PATH": data_dir,
        "CORTEX_API_KEY": SELF_TEST_KEY,
        "CORTEX_ALLOW_INSECURE_DEV_TOKEN": "1",
        "CORTEX_PUBLIC_BASE_URL": base,
        # Hermetic + deterministic: the stdlib hash embedder needs no model files and
        # no network. (Override with CORTEX_DEMO_EMBEDDINGS=model2vec to rehearse the
        # shipped provider on a machine that has the model bundled.)
        "CORTEX_EMBEDDING_PROVIDER": os.environ.get("CORTEX_DEMO_EMBEDDINGS", "hash"),
    })
    log_file = open(log_path, "w")
    server = subprocess.Popen(
        [sys.executable, "-S", "-s", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND), env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    print(f"== Self-test: headless cross-AI demo rehearsal on port {port} ==")
    try:
        check("server /ready (spare port, scratch data dir)", _wait_ready(base, timeout=30))
        if not checks[-1][1]:
            return _finish(checks, log_path)

        try:
            saved, approved, memories, cited = seed_persona(base, SELF_TEST_KEY)
            check("persona seeded via real capture pipeline", saved == len(PERSONA_CAPTURES), f"{saved} captures")
            check("captures approved via real review endpoint", approved >= 1, f"{approved} approved")
            check("extraction produced active memories (fresh scratch store)",
                  memories >= MIN_EXPECTED_NEW_MEMORIES, f"{memories} memories")
            check("headline question cited before any client connects", cited)
        except RuntimeError as exc:
            check("persona seeded via real capture pipeline", False, str(exc)[:160])
            return _finish(checks, log_path)

        try:
            tokens = mint_tokens(base, SELF_TEST_KEY)
            check("three distinct per-app MCP tokens minted", True, ", ".join(CLIENT_LABELS))
        except RuntimeError as exc:
            check("three distinct per-app MCP tokens minted", False, str(exc)[:160])
            return _finish(checks, log_path)
        write_kit(kit_dir, base, tokens)
        check("ready-to-paste configs written", all((kit_dir / name).exists() for name in (
            "claude_desktop.mcp.json", "cursor.mcp.json", "chatgpt_extension_pairing.txt", "demo_tokens.json")))

        # --- Client 1: Claude Desktop (real stdio bridge subprocess) -------------------
        try:
            ask_response, ctx_response = simulate_claude_desktop(base, tokens["Claude Desktop"]["token"])
            answer = _tool_result_payload(ask_response)
            citations = answer.get("citations") or []
            check("Claude Desktop: ask_memory cites the pgvector decision", _cites_headline(citations),
                  f"status={answer.get('status')}, {len(citations)} citations")
            pack = _tool_result_payload(ctx_response)
            check("Claude Desktop: get_context returns a cited pack", bool(pack.get("layers")) and "citations" in pack)
        except Exception as exc:
            check("Claude Desktop: ask_memory cites the pgvector decision", False, str(exc)[:160])

        # --- Client 2: Cursor (HTTP /mcp JSON-RPC) --------------------------------------
        cursor_response = simulate_cursor(base, tokens["Cursor"]["token"])
        results = _tool_result_payload(cursor_response)
        hits = results.get("results") or results.get("memories") or []
        check("Cursor: search_memory finds the Meridian pilot", "error" not in cursor_response and len(hits) >= 1,
              f"{len(hits)} results")

        # --- Client 3: ChatGPT via browser extension (/v1/tools/call) --------------------
        status, ext = simulate_chatgpt_extension(base, tokens["ChatGPT (extension)"]["token"])
        routed = ((ext or {}).get("result") or {}).get("routed_to")
        check("ChatGPT (extension): use_cortex answers over HTTP tool API", status == 200 and bool(routed),
              f"routed_to={routed}")

        # --- The same --verify the human runs before the room ---------------------------
        rows = collect_verify_rows(base, SELF_TEST_KEY, tokens, window_days=1)
        all_read = print_verify_table(rows, window_days=1)
        check("scorecard shows >=1 read for EACH client label", all_read)

        return _finish(checks, log_path)
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except Exception:
            server.kill()
        log_file.close()
        shutil.rmtree(data_dir, ignore_errors=True)
        shutil.rmtree(kit_dir, ignore_errors=True)


def _finish(checks: list[tuple[str, bool, str]], log_path: Path) -> int:
    passed = sum(1 for _, ok, _ in checks if ok)
    total = len(checks)
    print(f"\n{'=' * 60}\nSELF-TEST RESULT: {passed}/{total} checks passed")
    failures = [name for name, ok, _ in checks if not ok]
    if failures:
        print("FAILED:", ", ".join(failures))
        try:
            tail = log_path.read_text().splitlines()[-20:]
            if tail:
                print("\nserver log tail:")
                for line in tail:
                    print(f"  {line}")
        except Exception:
            pass
    return 0 if passed == total else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=None,
                        help=f"Cortex backend base URL (default {DEFAULT_BASE_URL}, or CORTEX_BASE_URL)")
    parser.add_argument("--api-key", default=os.environ.get("CORTEX_API_KEY", ""),
                        help="Cortex app API key (Connections & Privacy -> Advanced Setup); or CORTEX_API_KEY")
    parser.add_argument("--out-dir", default=str(Path.home() / "cortex-demo-kit"),
                        help="where the ready-to-paste configs are written (default ~/cortex-demo-kit)")
    parser.add_argument("--window-days", type=int, default=1,
                        help="scorecard window for --verify (default 1 day, demo-scoped)")
    parser.add_argument("--port", type=int, default=int(os.environ.get("CORTEX_DEMO_PORT", SELF_TEST_PORT)),
                        help=f"spare port for --self-test (default {SELF_TEST_PORT}; never {APP_PORT})")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="check each client performed >=1 read-class call")
    mode.add_argument("--self-test", action="store_true", help="headless end-to-end rehearsal on a spare port")
    args = parser.parse_args()

    args.base_url_set = args.base_url is not None
    if args.base_url is None:
        args.base_url = os.environ.get("CORTEX_BASE_URL", DEFAULT_BASE_URL)

    if args.self_test:
        return cmd_self_test(args)
    if args.verify:
        return cmd_verify(args)
    return cmd_seed(args)


if __name__ == "__main__":
    raise SystemExit(main())
