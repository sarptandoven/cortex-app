"""Bundle-level MemoryTruth: boot the standalone server FROM the built .app bundle
(.pyc-only backend, same PYTHONPATH/env the Swift supervisor uses) and run the
bench over live HTTP against it, one fresh server+vault per seed.

This is the release gate for build 23: it proves the SHIPPED artifact, not the
repo checkout, holds every floor.
"""
import argparse
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

_REPO_DEFAULT = Path(__file__).resolve().parent.parent
_parser = argparse.ArgumentParser(description="Run MemoryTruth against the BUILT .app bundle (release gate).")
_parser.add_argument("--app", default=str(_REPO_DEFAULT / "macos/build/Cortex.app"))
_parser.add_argument("--repo", default=str(_REPO_DEFAULT), help="Repo root providing backend.bench (the grader)")
_parser.add_argument("--seeds", default="7,21,42,99,1234")
# The bundle's .pyc files are compiled by the same 3.12 framework the shipped app embeds,
# so the harness must boot them with that interpreter, not whatever python3 is on PATH.
_parser.add_argument("--python", default=os.environ.get(
    "CORTEX_BUNDLE_PYTHON", "/Library/Frameworks/Python.framework/Versions/3.12/bin/python3.12"))
_args = _parser.parse_args()

APP = Path(_args.app)
REPO = Path(_args.repo)
SEEDS = [int(s) for s in _args.seeds.split(",") if s.strip()]
TOKEN = "bundle-bench-token"
BUNDLE_PY = _args.python

BACKEND = APP / "Contents/Resources/backend"
RUNTIME_DEPS = APP / "Contents/Resources/python"
assert (BACKEND / "app").is_dir(), f"no bundled backend at {BACKEND}"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def wait_ready(port: int, timeout: float = 40) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            req = urllib.request.Request(
                f"http://127.0.0.1:{port}/v1/settings",
                headers={"Authorization": f"Bearer {TOKEN}"},
            )
            urllib.request.urlopen(req, timeout=2)
            return True
        except Exception:
            time.sleep(0.3)
    return False


overall_ok = True
for seed in SEEDS:
    with tempfile.TemporaryDirectory() as tmp:
        port = free_port()
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": f"{BACKEND}:{RUNTIME_DEPS}",
            "PYTHONNOUSERSITE": "1",
            "PYTHONDONTWRITEBYTECODE": "1",
            "CORTEX_DB_PATH": str(Path(tmp) / "vault" / "index.sqlite"),
            "CORTEX_VAULT_PATH": str(Path(tmp) / "vault"),
            "CORTEX_API_KEY": TOKEN,
            "CORTEX_STANDALONE_WORKER_ENABLED": "0",
        })
        env.pop("ANTHROPIC_API_KEY", None)  # deterministic local extraction only
        proc = subprocess.Popen(
            [BUNDLE_PY, "-S", "-s", "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", str(port)],
            cwd=str(BACKEND), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
        )
        try:
            if not wait_ready(port):
                proc.terminate()
                print(f"seed={seed} BUNDLE SERVER FAILED TO BOOT")
                print((proc.stdout.read() or "")[:1500])
                overall_ok = False
                continue
            out = subprocess.run(
                [sys.executable, "-m", "backend.bench.memorytruth",
                 "--seed", str(seed), "--url", f"http://127.0.0.1:{port}", "--token", TOKEN],
                capture_output=True, text=True, cwd=str(REPO), timeout=600,
            )
            if out.returncode != 0 or not out.stdout.strip():
                print(f"seed={seed} BENCH FAILED rc={out.returncode} stderr={out.stderr[-600:]}")
                overall_ok = False
                continue
            report = json.loads(out.stdout)
            cats = {k: v["score"] for k, v in report["categories"].items()}
            fails = [f for v in report["categories"].values() for f in v["failures"]]
            print(f"seed={seed:>5} overall={report['overall']} probes={report['probes']} {cats}")
            for f in fails:
                print("  FAIL:", f[:220])
                overall_ok = False
            if report["overall"] != 1.0:
                overall_ok = False
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()

print("BUNDLE_ALL_FLOORS_HELD" if overall_ok else "BUNDLE_FLOOR_BREAK")
sys.exit(0 if overall_ok else 1)
