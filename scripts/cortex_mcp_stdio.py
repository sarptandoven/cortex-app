from __future__ import annotations

import json
import os
import sys
import urllib.request


BASE_URL = os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766").rstrip("/")
API_KEY = os.environ.get("CORTEX_API_KEY", "dev-local-key")


def proxy_to_cortex(message: dict) -> dict:
    data = json.dumps(message).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + "/mcp",
        data=data,
        headers={
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def error_response(message: dict, exc: Exception) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": message.get("id"),
        "error": {
            "code": -32000,
            "message": f"Cortex MCP proxy error: {exc}",
        },
    }


def main() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
            if "id" not in message:
                continue
            response = proxy_to_cortex(message)
        except Exception as exc:
            response = error_response(message if "message" in locals() and isinstance(message, dict) else {}, exc)
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
