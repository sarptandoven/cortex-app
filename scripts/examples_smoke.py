#!/usr/bin/env python3
"""Run every public Python example against an isolated local Cortex server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parent.parent


def _available_loopback_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _run_example(path: str, env: dict[str, str], *arguments: str) -> str:
    completed = subprocess.run(
        [sys.executable, path, *arguments],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    return completed.stdout


def _get_json(url: str, token: str) -> dict:
    request = Request(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
    )
    with urlopen(request, timeout=5) as response:  # noqa: S310 - loopback test server
        return json.loads(response.read().decode("utf-8"))


def _contains_demo_source(value: object) -> bool:
    if isinstance(value, dict):
        if value.get("source") == "cortex-demo":
            return True
        if "example.invalid/cortex-demo/" in str(value.get("source_url") or ""):
            return True
        return any(_contains_demo_source(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_demo_source(item) for item in value)
    return False


def _workflow_sections(output: str) -> dict[str, object]:
    sections: dict[str, object] = {}
    for chunk in output.split("\n## ")[1:]:
        label, raw = chunk.split("\n", 1)
        sections[label.strip()] = json.loads(raw.strip())
    return sections


def main() -> int:
    port = _available_loopback_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="cortex-examples-") as temp:
        vault = Path(temp) / "vault"
        env = dict(os.environ)
        env.update(
            {
                "CORTEX_ALLOW_INSECURE_DEV_TOKEN": "1",
                "CORTEX_ALLOW_DEMO_SEED": "0",
                "CORTEX_API_KEY": "dev-local-key",
                "CORTEX_AUTO_APPROVE_CAPTURES": "1",
                "CORTEX_BASE_URL": base_url,
                "CORTEX_DB_PATH": str(vault / "index.sqlite"),
                "CORTEX_VAULT_PATH": str(vault),
                "PYTHONPATH": os.pathsep.join(
                    filter(
                        None,
                        [str(ROOT / "sdk" / "python"), env.get("PYTHONPATH", "")],
                    )
                ),
            }
        )
        server = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "backend.app.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--log-level",
                "warning",
            ],
            cwd=ROOT,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        try:
            deadline = time.monotonic() + 15
            while True:
                if server.poll() is not None:
                    raise RuntimeError(f"example server exited with status {server.returncode}")
                try:
                    with urlopen(f"{base_url}/health", timeout=0.5) as response:  # noqa: S310
                        if response.status == 200:
                            break
                except URLError:
                    if time.monotonic() >= deadline:
                        raise RuntimeError("example server did not become healthy in 15 seconds")
                    time.sleep(0.1)

            unsafe_seed_env = {**env, "CORTEX_API_KEY": "cxa_not_for_demo_seeding"}
            unsafe_seed = subprocess.run(
                [sys.executable, "examples/seed_demo.py"],
                cwd=ROOT,
                env=unsafe_seed_env,
                check=False,
                capture_output=True,
                text=True,
                timeout=10,
            )
            if unsafe_seed.returncode != 2 or "requires the canonical dev-local-key" not in unsafe_seed.stderr:
                raise AssertionError("seed_demo.py did not reject a non-development token")

            seed = _run_example("examples/seed_demo.py", env)
            if "done: 3 deterministic synthetic captures are ready" not in seed:
                raise AssertionError("seed_demo.py did not report all fixture captures")
            first_stats = _get_json(f"{base_url}/v1/stats", "dev-local-key")
            second_seed = _run_example("examples/seed_demo.py", env)
            if "done: 3 deterministic synthetic captures are ready" not in second_seed:
                raise AssertionError("seed_demo.py was not repeatable")
            second_stats = _get_json(f"{base_url}/v1/stats", "dev-local-key")
            stable_counts = ("captures", "memories", "decisions", "tasks", "entities", "edges")
            if any(first_stats.get(key) != second_stats.get(key) for key in stable_counts):
                raise AssertionError(
                    "seed_demo.py changed stored record counts when run a second time: "
                    f"{first_stats} -> {second_stats}"
                )

            search = json.loads(_run_example("examples/minimal_search.py", env))
            results = search.get("results", [])
            if not results or not _contains_demo_source(search):
                raise AssertionError("minimal_search.py did not return the cited demo memory")

            workflow = _workflow_sections(_run_example("examples/memory_workflow.py", env))
            expected_sections = {"Search", "Cited answer or abstention", "Task context"}
            if set(workflow) != expected_sections:
                raise AssertionError(
                    f"memory_workflow.py returned {sorted(workflow)}, expected {sorted(expected_sections)}"
                )
            answer = workflow["Cited answer or abstention"]
            context = workflow["Task context"]
            if not isinstance(answer, dict) or answer.get("status") != "cited":
                raise AssertionError("memory_workflow.py did not produce a cited answer")
            if not _contains_demo_source(workflow["Search"]):
                raise AssertionError("memory_workflow.py search omitted the demo source")
            if not answer.get("citations") or not _contains_demo_source(answer):
                raise AssertionError("memory_workflow.py answer omitted source-bearing citations")
            if not isinstance(context, dict) or not context.get("citations"):
                raise AssertionError("memory_workflow.py context omitted citations")
            if not _contains_demo_source(context):
                raise AssertionError("memory_workflow.py context omitted the demo source")

            packs = json.loads(_run_example("examples/multi_agent_context.py", env))
            if set(packs) != {"planner", "researcher", "writer"}:
                raise AssertionError("multi_agent_context.py did not return all three role packs")
            for role, pack in packs.items():
                if not isinstance(pack, dict) or not pack.get("citations"):
                    raise AssertionError(f"multi_agent_context.py {role} pack omitted citations")
                if not _contains_demo_source(pack):
                    raise AssertionError(
                        f"multi_agent_context.py {role} pack omitted the demo source"
                    )

            tools = json.loads(_run_example("examples/tool_catalog.py", env))
            if not tools or not all(item.get("name") for item in tools):
                raise AssertionError("tool_catalog.py did not return named tools")
        finally:
            server.terminate()
            try:
                server.wait(timeout=5)
            except subprocess.TimeoutExpired:
                server.kill()
                server.wait(timeout=5)

    print("examples smoke: all public Python examples passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
