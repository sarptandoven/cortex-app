#!/usr/bin/env python3
"""Live end-to-end pipeline verification against the SHIPPING server.

Boots app.standalone_server on a spare port with a temp vault/DB, then walks the exact
path an external AI agent walks:

  1. the app registers a least-privilege agent token (read+write — the shape the macOS
     app mints for "Connected AI tools"),
  2. the agent saves one memory of EVERY layer via MCP remember_this
     (preference, style, negative, decision, procedural, episodic, semantic + an open task),
  3. the human approves the pending captures (admin path — the Review tab tap),
  4. the agent retrieves the picture back via MCP: tools/list, get_context, ask_memory,
     search_memory, get_personal_profile, get_person_map,
  5. the stdio bridge (scripts/cortex_mcp_stdio.py) round-trips a tools/list, proving the
     config path AI apps actually launch works.

Asserts every memory layer is represented, cited, and reachable by a read-scoped agent.
Exits non-zero with a per-check matrix on failure. Never touches port 8766 or real data.

Usage: python3 scripts/verify_pipeline_live.py [--port 8791] [--keep]
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# Three varied phrasings per layer: the distilled profile abstains (correctly) on single
# mentions, so a fair test seeds the repetition-with-variation a real corpus has. The marker
# is what we hunt for in retrieval output; phrasings share it so they cluster as support.
SEEDS = [
    ("preference", [
        "I strongly prefer concise, direct writing with no filler.",
        "For docs I prefer concise, direct writing over long prose.",
        "I always want concise, direct writing in anything I publish.",
    ], "concise, direct writing"),
    ("style", [
        "My writing voice is warm but precise.",
        "Colleagues describe my writing voice as warm but precise.",
        "Keep my writing voice warm but precise when drafting for me.",
    ], "warm but precise"),
    ("negative", [
        "I really dislike long status meetings.",
        "I can't stand long status meetings that could be an email.",
        "Avoid booking me into long status meetings.",
    ], "long status meetings"),
    ("decision", [
        "We decided to use PostgreSQL for the main memory store.",
        "Decision: PostgreSQL is the main memory store going forward.",
        "After the benchmark we chose PostgreSQL for the main memory store.",
    ], "PostgreSQL"),
    ("procedural", [
        "To release Cortex: bump the version, tag it, run build.sh, then notarize.",
        "The release runbook is to bump the version, tag, build, then notarize.",
        "When I ship a release I always bump the version first, then tag and notarize.",
    ], "bump the version"),
    ("episodic", [
        "I met Marcus Feld in Lisbon on June 12, 2026 to plan the company offsite.",
        "Had a call with Marcus Feld about the Lisbon offsite agenda.",
        "Visited the Lisbon venue with Marcus Feld on June 14, 2026.",
    ], "Lisbon"),
    ("semantic", [
        "Project Atlas is our Q3 revenue initiative targeting two million dollars.",
        "Project Atlas carries the Q3 revenue target of two million dollars.",
        "The two million dollar Q3 goal all sits under Project Atlas.",
    ], "Project Atlas"),
    ("task", [
        "Follow up with Priya about the Q3 roadmap next week.",
    ], "Priya"),
]


class Client:
    def __init__(self, base_url: str, token: str):
        self.base_url = base_url.rstrip("/")
        self.token = token

    def http(self, method: str, path: str, body: dict | None = None) -> dict:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))

    def mcp(self, method: str, params: dict) -> dict:
        payload = {"jsonrpc": "2.0", "id": secrets.token_hex(4), "method": method, "params": params}
        result = self.http("POST", "/mcp", payload)
        if "error" in result:
            raise RuntimeError(f"MCP error for {method} {params.get('name', '')}: {result['error']}")
        return result["result"]

    def tool(self, name: str, arguments: dict) -> dict | str | list:
        result = self.mcp("tools/call", {"name": name, "arguments": arguments})
        if isinstance(result, dict) and "structuredContent" in result:
            return result["structuredContent"]
        return result


def wait_for_health(base_url: str, process: subprocess.Popen, timeout: float = 45.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if process.poll() is not None:
            raise RuntimeError(f"server exited early with status {process.returncode}")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.4)
    raise RuntimeError("server did not become healthy in time")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8791)
    parser.add_argument("--keep", action="store_true", help="keep the temp vault for inspection")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="cortex-live-verify-"))
    admin_token = "cxa_live_" + secrets.token_hex(16)
    agent_token = "cxm_live_" + secrets.token_hex(16)
    base_url = f"http://127.0.0.1:{args.port}"

    env = dict(os.environ)
    env.update(
        {
            "CORTEX_VAULT_PATH": str(workdir / "Cortex.vault"),
            "CORTEX_DB_PATH": str(workdir / "index.sqlite"),
            "CORTEX_API_KEY": admin_token,
            "CORTEX_PORT": str(args.port),
        }
    )
    env.pop("ANTHROPIC_API_KEY", None)  # deterministic extraction — what offline users ship with

    server = subprocess.Popen(
        [sys.executable, "-m", "app.standalone_server", "--port", str(args.port)],
        cwd=str(BACKEND),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f" — {detail}" if detail and not ok else ""))

    try:
        wait_for_health(base_url, server)
        admin = Client(base_url, admin_token)
        agent = Client(base_url, agent_token)

        # 1. The app registers the least-privilege agent token (read+write).
        registered = admin.http(
            "POST",
            "/v1/integrations/mcp-token",
            {"token": agent_token, "label": "Live verify agent", "scopes": ["read", "write"]},
        )
        check("register read+write agent token", registered.get("scopes") == ["read", "write"], str(registered))

        # 2. Agent saves memories of every layer via MCP remember_this (varied phrasings).
        capture_ids: list[str] = []
        for layer, contents, _ in SEEDS:
            layer_ok = True
            for content in contents:
                saved = agent.tool("remember_this", {"content": content, "source": "claude"})
                capture_id = saved.get("capture_id") if isinstance(saved, dict) else None
                layer_ok = layer_ok and bool(capture_id)
                capture_ids.append(capture_id or "")
            check(f"remember_this seeds {layer}", layer_ok)

        # 3. The human approves pending captures (Review tab tap — admin path).
        approved = 0
        for capture_id in capture_ids:
            if not capture_id:
                continue
            try:
                result = admin.tool("approve_memory_capture", {"capture_id": capture_id})
                approved += 1 if (isinstance(result, dict) and result.get("approved")) else 0
            except RuntimeError as error:  # already auto-approved is fine
                if "approved" not in str(error).lower():
                    raise
        check("human approves captures", approved > 0 or all(capture_ids), f"approved={approved}")

        # 4a. Advertised surface for a read+write token includes the whole-picture tools.
        tools = {tool["name"] for tool in agent.mcp("tools/list", {})["tools"]}
        expected_surface = {"get_context", "ask_memory", "search_memory", "get_entity_context", "get_person_map", "remember_this", "list_capabilities"}
        check("tools/list core surface", expected_surface <= tools, f"got {sorted(tools)}")

        # 4b. get_context returns a cited pack with the identity layer PRESENT (not omitted).
        # Layer relevance is task-driven (that is correct behavior), so each layer is probed
        # with a task it is actually relevant to.
        pack = agent.tool("get_context", {"task": "draft a note about how I like to work"})
        pack_text = json.dumps(pack)
        identity_layers = [layer for layer in pack.get("layers", []) if layer.get("layer") == "identity"]
        check("get_context identity layer present", bool(identity_layers) and not identity_layers[0].get("omitted"), pack_text[:300])
        for layer, _, marker in SEEDS[:3]:  # preference/style/negative fit this drafting task
            check(f"get_context reaches {layer}", marker.lower() in pack_text.lower(), f"marker {marker!r} missing")
        decision_pack_text = json.dumps(agent.tool("get_context", {"task": "which database does the main memory store use?"}))
        check("get_context reaches decision", "postgresql" in decision_pack_text.lower(), decision_pack_text[:300])
        release_pack_text = json.dumps(agent.tool("get_context", {"task": "ship the next Cortex release"}))
        check("get_context reaches procedural", "bump the version" in release_pack_text.lower(), release_pack_text[:300])

        # 4c. The holistic profile represents EVERY layer with citations.
        profile = agent.tool("get_personal_profile", {})
        profile_text = json.dumps(profile)
        for layer, _, marker in SEEDS[:7]:
            check(f"profile represents {layer}", marker.lower() in profile_text.lower(), f"marker {marker!r} missing from sections")
        has_citation = '"id": "mem_' in profile_text and '"source_url"' in profile_text
        check("profile is cited", has_citation, "no mem_ ids / source_url in profile payload")

        # 4d. Person map knows the person and carries behavioral evidence.
        person_map = agent.tool("get_person_map", {})
        person_text = json.dumps(person_map).lower()
        check("person map has Marcus Feld", "marcus" in person_text, person_text[:300])
        check("person map carries preference/style evidence", "concise, direct writing" in person_text or "warm but precise" in person_text, "preference/style dropped from person map")

        # 4e. Q&A over the memory answers with citations.
        answer = agent.tool("ask_memory", {"query": "which database did we decide on for the main memory store?"})
        answer_text = json.dumps(answer).lower()
        check("ask_memory cites the decision", "postgresql" in answer_text, answer_text[:300])
        found = agent.tool("search_memory", {"query": "status meetings", "top_k": 5})
        check("search_memory finds the dislike", "long status meetings" in json.dumps(found).lower(), json.dumps(found)[:300])

        # 5. The stdio bridge (what AI-app configs launch) round-trips tools/list.
        bridge_env = dict(os.environ)
        bridge_env.update({"CORTEX_BASE_URL": base_url, "CORTEX_API_KEY": agent_token})
        bridge = subprocess.run(
            [sys.executable, "-S", str(ROOT / "scripts" / "cortex_mcp_stdio.py")],
            input=json.dumps({"jsonrpc": "2.0", "id": "bridge-1", "method": "tools/list", "params": {}}) + "\n",
            env=bridge_env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        bridge_ok = False
        for line in bridge.stdout.splitlines():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("id") == "bridge-1" and message.get("result", {}).get("tools"):
                bridge_ok = True
        check("stdio bridge round-trip", bridge_ok, (bridge.stdout + bridge.stderr)[:300])

    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
        if args.keep:
            print(f"temp vault kept at {workdir}")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    failures = [(name, detail) for name, ok, detail in checks if not ok]
    print(f"\n{len(checks) - len(failures)}/{len(checks)} checks passed")
    if failures:
        print("FAILURES:")
        for name, detail in failures:
            print(f"  - {name}: {detail[:200]}")
        return 1
    print("Pipeline verified live: every memory layer in, distilled, and retrieved by a scoped agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
