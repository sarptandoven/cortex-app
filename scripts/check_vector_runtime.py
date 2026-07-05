#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import sysconfig
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

RUNTIME_CHECK = r"""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

from app.database import connect, init_db, sqlite_vec_status
from app.sqlite_runtime import SQLITE_RUNTIME, sqlite3

payload: dict[str, object] = {
    "status": "failed",
    "python": sys.executable,
    "sqlite_runtime": SQLITE_RUNTIME,
    "sqlite_version": sqlite3.sqlite_version,
}

with tempfile.TemporaryDirectory(prefix="cortex-vector-runtime-") as tmp:
    db_path = Path(tmp) / "index.sqlite"
    init_db(db_path)
    with connect(db_path) as conn:
        vector = sqlite_vec_status(conn)
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual table')"
            ).fetchall()
        }
        payload["vector"] = vector
        payload["has_memory_vec"] = "memory_vec" in tables
        payload["has_memory_vec_map"] = "memory_vec_map" in tables
        payload["tables_checked"] = sorted(tables)
        payload["status"] = (
            "ok"
            if vector.get("available") and payload["has_memory_vec"] and payload["has_memory_vec_map"]
            else "failed"
        )

print(json.dumps(payload, sort_keys=True))
raise SystemExit(0 if payload["status"] == "ok" else 1)
"""


def app_runtime(app: Path) -> tuple[Path, list[Path]]:
    resources = app / "Contents" / "Resources"
    versions = app / "Contents" / "Frameworks" / "Python.framework" / "Versions"
    candidates = sorted(
        path / "bin" / "python3"
        for path in versions.iterdir()
        if path.is_dir() and path.name != "Current"
    ) if versions.exists() else []
    python = next((candidate for candidate in candidates if candidate.exists()), None)
    if python is None:
        expected = versions / "<version>" / "bin" / "python3"
        raise FileNotFoundError(f"Bundled Python is missing: {expected}")
    return python, [resources / "backend", resources / "python"]


def source_runtime() -> tuple[Path, list[Path]]:
    site_paths = {
        Path(str(value))
        for key, value in sysconfig.get_paths().items()
        if key in {"purelib", "platlib"} and value
    }
    return Path(sys.executable), [ROOT / "backend", *sorted(site_paths)]


def run_runtime_check(python: Path, python_paths: list[Path]) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in python_paths if path.exists())
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    completed = subprocess.run(
        [str(python), "-S", "-c", RUNTIME_CHECK],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )
    payload: dict[str, object]
    try:
        payload = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError):
        payload = {
            "status": "failed",
            "python": str(python),
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        }
    payload["returncode"] = completed.returncode
    payload["pythonpath"] = env["PYTHONPATH"]
    if completed.stderr.strip():
        payload["stderr"] = completed.stderr[-2000:]
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify Cortex's sqlite-vec runtime against source or a built .app.")
    parser.add_argument("--app", type=Path, help="Path to Cortex.app. When provided, checks the bundled Python runtime.")
    args = parser.parse_args()

    try:
        if args.app:
            python, python_paths = app_runtime(args.app.expanduser().resolve())
            mode = "app"
        else:
            python, python_paths = source_runtime()
            mode = "source"
        payload = run_runtime_check(python, python_paths)
        payload["mode"] = mode
    except Exception as exc:
        payload = {"status": "failed", "error": str(exc), "mode": "app" if args.app else "source"}

    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("status") != "ok":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
