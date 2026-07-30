#!/usr/bin/env python3
"""Idempotently seed synthetic data into a loopback-only Cortex dev server."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


FIXTURE = Path(__file__).with_name("fixtures") / "demo_captures.json"
LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        return None


def _validated_base_url(raw: str) -> str:
    value = raw.strip().rstrip("/")
    parsed = urlsplit(value)
    try:
        _ = parsed.port
    except ValueError as exc:
        raise ValueError(f"invalid CORTEX_BASE_URL port: {exc}") from exc
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "seed_demo.py only writes to an absolute loopback Cortex origin "
            "(for example http://127.0.0.1:8766)"
        )
    return value


def main() -> int:
    try:
        base_url = _validated_base_url(
            os.environ.get("CORTEX_BASE_URL", "http://127.0.0.1:8766")
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    token = os.environ.get("CORTEX_API_KEY", "dev-local-key").strip()
    if token != "dev-local-key" and os.environ.get("CORTEX_ALLOW_DEMO_SEED") != "1":
        print(
            "error: seed_demo.py requires the canonical dev-local-key; "
            "set CORTEX_ALLOW_DEMO_SEED=1 only for an intentional isolated test server",
            file=sys.stderr,
        )
        return 2
    captures = json.loads(FIXTURE.read_text(encoding="utf-8"))
    opener = build_opener(_NoRedirect())

    saved = 0
    for capture in captures:
        request = Request(
            f"{base_url}/v1/captures",
            data=json.dumps(capture).encode("utf-8"),
            method="POST",
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with opener.open(request, timeout=10) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")
            print(f"error: Cortex returned HTTP {exc.code}: {detail}", file=sys.stderr)
            return 1
        except URLError as exc:
            print(f"error: could not reach Cortex at {base_url}: {exc.reason}", file=sys.stderr)
            return 1
        saved += 1
        print(f"seeded {payload.get('capture_id', capture['capture_id_override'])}: {capture['title']}")

    print(f"done: {saved} deterministic synthetic captures are ready")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
