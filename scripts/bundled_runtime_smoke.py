#!/usr/bin/env python3
"""Boot and exercise the Python runtime embedded in a built Cortex.app."""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import tempfile
import time
from pathlib import Path
from urllib import error, parse, request


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _json_request(
    base_url: str,
    path: str,
    *,
    token: str,
    method: str = "GET",
    body: dict | None = None,
    query: dict[str, str] | None = None,
    timeout_seconds: float = 10,
) -> dict:
    url = f"{base_url}{path}"
    if query:
        url = f"{url}?{parse.urlencode(query)}"
    payload = json.dumps(body).encode("utf-8") if body is not None else None
    headers = {"Authorization": f"Bearer {token}"}
    if payload is not None:
        headers["Content-Type"] = "application/json"
    with request.urlopen(
        request.Request(url, data=payload, headers=headers, method=method),
        timeout=timeout_seconds,
    ) as response:
        return json.loads(response.read().decode("utf-8"))


def _server_log_tail(path: Path, *, limit: int = 4000) -> str:
    try:
        output = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"<unable to read server log: {exc}>"
    return output[-limit:] or "<no server output>"


def _wait_for_json_endpoint(
    process: subprocess.Popen,
    base_url: str,
    path: str,
    *,
    token: str,
    deadline: float,
    server_log_path: Path,
) -> dict:
    while True:
        if process.poll() is not None:
            raise RuntimeError(
                f"bundled runtime exited with {process.returncode}; server output:\n"
                f"{_server_log_tail(server_log_path)}"
            )
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(
                f"bundled runtime endpoint {path} was not ready before the deadline; "
                f"server output:\n{_server_log_tail(server_log_path)}"
            )
        try:
            return _json_request(
                base_url,
                path,
                token=token,
                timeout_seconds=min(2, max(0.1, remaining)),
            )
        except (error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(0.2)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--app", type=Path, default=Path("macos/build/Cortex.app"))
    parser.add_argument(
        "--startup-timeout-seconds",
        type=float,
        default=90,
        help="Maximum time to wait for a cold bundled runtime to become healthy.",
    )
    args = parser.parse_args()
    if args.startup_timeout_seconds <= 0:
        parser.error("--startup-timeout-seconds must be greater than zero")

    app = args.app.expanduser().resolve()
    resources = app / "Contents" / "Resources"
    python = (
        app
        / "Contents"
        / "Frameworks"
        / "Python.framework"
        / "Versions"
        / "3.12"
        / "bin"
        / "python3"
    )
    required = [
        python,
        resources / "backend" / "app" / "standalone_server.pyc",
        resources / "python",
        resources / "model2vec",
    ]
    missing = [str(path) for path in required if not path.exists()]
    if missing:
        raise SystemExit(f"bundled runtime is incomplete: {missing}")

    token = "cortex-bundled-runtime-smoke-token"
    port = _free_port()
    base_url = f"http://127.0.0.1:{port}"
    with tempfile.TemporaryDirectory(prefix="cortex-bundled-runtime-") as tmp:
        root = Path(tmp)
        server_log_path = root / "server.log"
        env = os.environ.copy()
        env.update(
            {
                "CORTEX_API_KEY": token,
                "CORTEX_AUTO_APPROVE_CAPTURES": "1",
                "CORTEX_DB_PATH": str(root / "Cortex.vault" / "index.sqlite"),
                "CORTEX_VAULT_PATH": str(root / "Cortex.vault"),
                "CORTEX_EMBEDDING_PROVIDER": "model2vec",
                "CORTEX_EMBEDDING_STRICT": "1",
                "CORTEX_MODEL2VEC_PATH": str(resources / "model2vec"),
                "CORTEX_PUBLIC_BASE_URL": base_url,
                "HF_HUB_OFFLINE": "1",
                "PYTHONNOUSERSITE": "1",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONPATH": os.pathsep.join(
                    [str(resources / "backend"), str(resources / "python")]
                ),
            }
        )
        with server_log_path.open("w", encoding="utf-8") as server_log:
            process = subprocess.Popen(
                [
                    str(python),
                    "-S",
                    "-m",
                    "app.standalone_server",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(port),
                ],
                cwd=resources,
                env=env,
                stdout=server_log,
                stderr=subprocess.STDOUT,
                text=True,
            )
            try:
                deadline = time.monotonic() + args.startup_timeout_seconds
                health = _wait_for_json_endpoint(
                    process,
                    base_url,
                    "/health",
                    token=token,
                    deadline=deadline,
                    server_log_path=server_log_path,
                )

                encoded_health = json.dumps(health)
                assert health["status"] == "ok", health
                assert "db_path" not in encoded_health and "vault_path" not in encoded_health
                ready = _wait_for_json_endpoint(
                    process,
                    base_url,
                    "/ready",
                    token=token,
                    deadline=deadline,
                    server_log_path=server_log_path,
                )
                assert ready["status"] == "ok", ready

                _json_request(
                    base_url,
                    "/v1/captures",
                    token=token,
                    method="POST",
                    body={
                        "content": "Bundled runtime smoke launches Friday after verification.",
                        "source": "bundled-runtime-smoke",
                        "source_url": "cortex-smoke://bundled-runtime",
                    },
                )
                answer = _json_request(
                    base_url,
                    "/v1/ask",
                    token=token,
                    query={"query": "When does the bundled runtime smoke launch?"},
                )
                assert answer.get("status") == "cited", answer
                assert answer.get("citations"), answer
            except Exception as exc:
                if "server output:" in str(exc):
                    raise
                raise RuntimeError(
                    f"bundled runtime smoke failed: {exc}; server output:\n"
                    f"{_server_log_tail(server_log_path)}"
                ) from exc
            finally:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)

    print("bundled runtime smoke: health, readiness, capture, and cited Ask passed")


if __name__ == "__main__":
    main()
