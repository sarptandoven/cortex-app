"""Push / delivery layer (Phase 8 of the outbound plan).

Cortex is otherwise pull-only (an app asks, Cortex answers). This module lets Cortex proactively
DELIVER a cited context brief OUT to a user-configured target (a webhook — e.g. a Slack incoming
webhook, a local service, an automation). Egress of personal memory is the highest-consent action
in the product, so every send:
  - is scoped (a task + optional sector) and reuses assemble_context's cited-only, sector-isolated,
    identity-omitted guarantees — no uncited or cross-sector leakage;
  - is SSRF-guarded (internal/loopback/link-local/private targets are refused unless explicitly
    allowed for local dev), and requires https for public targets;
  - is gated at the endpoint by the export scope + the allow_agent_exports trust toggle (default
    off) and audited.

Stdlib-only (http.client + socket + ipaddress). The HTTP sender is injectable for deterministic tests.
"""
from __future__ import annotations

import ipaddress
import http.client
import json
import os
import socket
import ssl
from typing import Any, Callable
from urllib.parse import SplitResult, urlsplit

DELIVERY_KINDS = ("brief", "digest", "context")
_DEFAULT_TIMEOUT = 15


def _allow_internal_targets() -> bool:
    return os.environ.get("CORTEX_DELIVERY_ALLOW_INTERNAL", "").strip().lower() in {"1", "true", "on", "yes"}


def _validated_webhook_target(url: str) -> tuple[SplitResult | None, str | None, str]:
    """Validate and resolve once, returning the exact address to connect to."""
    raw = str(url or "").strip()
    parsed = urlsplit(raw)
    if parsed.scheme not in {"http", "https"}:
        return None, None, "URL must be http(s)"
    if parsed.username is not None or parsed.password is not None:
        return None, None, "credentials in URL are not allowed"
    host = parsed.hostname
    if not host:
        return None, None, "URL has no host"
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError:
        return None, None, "URL has an invalid port"
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP)
    except (OSError, ValueError):
        return None, None, "host does not resolve"
    addresses: list[str] = []
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return None, None, "unresolvable address"
        # ``is_private`` does not cover every non-public range (notably
        # 100.64.0.0/10 shared address space). Public delivery accepts only
        # globally routable addresses.
        if not _allow_internal_targets() and not ip.is_global:
            return None, None, "target resolves to an internal address"
        addresses.append(str(ip))
    if not addresses:
        return None, None, "host does not resolve"
    if not _allow_internal_targets() and parsed.scheme != "https":
        return None, None, "public targets must use https"
    return parsed, addresses[0], ""


def is_safe_webhook_url(url: str) -> tuple[bool, str]:
    """Refuse unsafe webhook targets and require public HTTPS."""
    parsed, _address, reason = _validated_webhook_target(url)
    return parsed is not None, reason


class _PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host: str, port: int, pinned_address: str, *, timeout: int) -> None:
        self._pinned_address = pinned_address
        super().__init__(host, port, timeout=timeout)

    def connect(self) -> None:
        self.sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, host: str, port: int, pinned_address: str, *, timeout: int) -> None:
        self._pinned_address = pinned_address
        super().__init__(
            host,
            port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )

    def connect(self) -> None:
        raw_socket = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        # TLS verifies the original hostname even though the TCP connection is
        # pinned to the already-validated address.
        self.sock = self._context.wrap_socket(raw_socket, server_hostname=self.host)


def _post_to_pinned_target(
    parsed: SplitResult,
    address: str,
    body: bytes,
    headers: dict[str, str],
    *,
    timeout: int,
) -> int:
    host = parsed.hostname or ""
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    connection_cls = _PinnedHTTPSConnection if parsed.scheme == "https" else _PinnedHTTPConnection
    connection = connection_cls(host, port, address, timeout=timeout)
    target = parsed.path or "/"
    if parsed.query:
        target += f"?{parsed.query}"
    request_headers = {**headers, "Host": parsed.netloc}
    try:
        connection.request("POST", target, body=body, headers=request_headers)
        response = connection.getresponse()
        response.read(4096)
        return int(response.status)
    finally:
        connection.close()


def build_delivery_payload(
    store: Any,
    user_id: str,
    *,
    task: str,
    sector: str | None = None,
    token_budget: int = 1500,
    kind: str = "brief",
) -> dict[str, Any]:
    """Build the cited, sector-scoped, identity-omitted context brief that would be delivered.
    Reuses assemble_context, so cited-only + no-cross-sector-leak + budget are enforced for us."""
    normalized_kind = kind if kind in DELIVERY_KINDS else "brief"
    pack = store.assemble_context(
        user_id,
        str(task or "").strip(),
        surface="delivery",
        token_budget=max(300, min(6000, int(token_budget))),
        sector=sector,
        include_identity=False,  # never push the user's identity/persona layer out
        format="json",
    )
    return {
        "kind": normalized_kind,
        "task": str(task or "").strip(),
        "sector": sector,
        "pack": pack,
    }


def deliver_webhook(
    url: str,
    payload: dict[str, Any],
    *,
    request_fn: Callable[[str, bytes, dict[str, str]], int] | None = None,
    timeout: int = _DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    """Deliver a payload to a webhook (POST JSON). SSRF-guarded. `request_fn(url, body, headers) ->
    status_code` is injectable for tests. Returns {ok, status, reason}."""
    parsed, pinned_address, reason = _validated_webhook_target(url)
    if parsed is None or pinned_address is None:
        return {"ok": False, "status": 0, "reason": reason}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "Cortex-Delivery/1"}
    try:
        if request_fn is not None:
            status = int(request_fn(url, body, headers))
        else:
            status = _post_to_pinned_target(
                parsed,
                pinned_address,
                body,
                headers,
                timeout=timeout,
            )
    except (OSError, http.client.HTTPException, ssl.SSLError) as exc:
        return {"ok": False, "status": 0, "reason": f"delivery failed: {exc}"}
    return {"ok": 200 <= status < 300, "status": status, "reason": "" if 200 <= status < 300 else f"HTTP {status}"}
