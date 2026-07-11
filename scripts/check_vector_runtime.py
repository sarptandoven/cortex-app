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
import os
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

# --- Embeddings demo-integrity probe ---------------------------------------------------------
# Report the ACTIVE embedding provider end-to-end. With CORTEX_EMBEDDING_PROVIDER=model2vec
# (the launcher default we mirror), embed_text_result() returns provider="model2vec" only when
# the real on-device model actually loads — otherwise it silently degrades to the deterministic
# keyword-hash fallback, which is exactly the failure this probe exists to expose. This section
# is report-only; enforcement lives in the parent via --require-model2vec.
from app.embeddings import embed_text_result, embedding_status

_status = embedding_status()
embedding: dict[str, object] = {
    "configured_provider": _status.get("provider"),
    "configured_model": _status.get("model"),
    "model2vec_path": os.environ.get("CORTEX_MODEL2VEC_PATH") or None,
}
try:
    _probe = embed_text_result("cortex embeddings demo-integrity probe")
    embedding["provider"] = _probe.provider
    embedding["model"] = _probe.model
    embedding["dimensions"] = _probe.dimensions
except Exception as exc:
    embedding["provider"] = "error"
    embedding["error"] = f"{type(exc).__name__}: {exc}"
if embedding.get("provider") != "model2vec" and _status.get("provider") == "model2vec":
    # Surface WHY the model failed to load: strict mode re-raises instead of falling back.
    os.environ["CORTEX_EMBEDDING_STRICT"] = "1"
    try:
        embed_text_result("cortex embeddings demo-integrity probe")
    except Exception as exc:
        embedding.setdefault("error", f"{type(exc).__name__}: {exc}")
    finally:
        os.environ.pop("CORTEX_EMBEDDING_STRICT", None)
payload["embedding"] = embedding

print(json.dumps(payload, sort_keys=True))
raise SystemExit(0 if payload["status"] == "ok" else 1)
"""


def app_runtime(app: Path) -> tuple[Path, list[Path]]:
    resources = app / "Contents" / "Resources"
    python = app / "Contents" / "Frameworks" / "Python.framework" / "Versions" / "3.12" / "bin" / "python3"
    if not python.exists():
        raise FileNotFoundError(f"Bundled Python is missing: {python}")
    return python, [resources / "backend", resources / "python"]


def source_runtime() -> tuple[Path, list[Path]]:
    site_paths = {
        Path(str(value))
        for key, value in sysconfig.get_paths().items()
        if key in {"purelib", "platlib"} and value
    }
    return Path(sys.executable), [ROOT / "backend", *sorted(site_paths)]


def run_runtime_check(python: Path, python_paths: list[Path], model2vec_path: Path | None = None) -> dict:
    env = os.environ.copy()
    env["PYTHONPATH"] = os.pathsep.join(str(path) for path in python_paths if path.exists())
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # Embeddings demo-integrity probe environment (setdefault: an explicit caller env wins, so
    # pre-existing behavior/overrides are preserved). Mirror the app launcher: provider=model2vec
    # with the bundled model directory when checking a built .app. HF_HUB_OFFLINE keeps the probe
    # deterministic and network-free — the model must load from the given path or the local cache,
    # never a first-run download.
    env.setdefault("CORTEX_EMBEDDING_PROVIDER", "model2vec")
    env.setdefault("HF_HUB_OFFLINE", "1")
    if model2vec_path is not None and model2vec_path.exists():
        env.setdefault("CORTEX_MODEL2VEC_PATH", str(model2vec_path))
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
    parser.add_argument(
        "--require-model2vec",
        action="store_true",
        help=(
            "Demo-integrity guard: exit nonzero unless the real model2vec embedding model loads "
            "and is the ACTIVE provider (i.e. the keyword-hash fallback would ship). Default "
            "behavior only reports the provider without enforcing it."
        ),
    )
    args = parser.parse_args()

    try:
        model2vec_path: Path | None = None
        if args.app:
            app = args.app.expanduser().resolve()
            python, python_paths = app_runtime(app)
            model2vec_path = app / "Contents" / "Resources" / "model2vec"
            mode = "app"
        else:
            python, python_paths = source_runtime()
            mode = "source"
        payload = run_runtime_check(python, python_paths, model2vec_path=model2vec_path)
        payload["mode"] = mode
    except Exception as exc:
        payload = {"status": "failed", "error": str(exc), "mode": "app" if args.app else "source"}

    print(json.dumps(payload, indent=2, sort_keys=True))
    if payload.get("status") != "ok":
        raise SystemExit(1)
    if args.require_model2vec:
        embedding = payload.get("embedding") or {}
        if embedding.get("provider") != "model2vec":
            print(
                "ERROR: --require-model2vec: active embedding provider is "
                f"{embedding.get('provider')!r} (model2vec_path={embedding.get('model2vec_path')!r}"
                f", error={embedding.get('error')!r}) — the shipped artifact would silently fall "
                "back to keyword-hash embeddings instead of real on-device semantics.",
                file=sys.stderr,
            )
            raise SystemExit(2)


if __name__ == "__main__":
    main()
