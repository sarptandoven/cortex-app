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

Stdlib-only (urllib + socket + ipaddress). The HTTP sender is injectable for deterministic tests.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
from typing import Any, Callable
from urllib.parse import urlparse
from urllib.request import Request, urlopen


DELIVERY_KINDS = ("brief", "digest", "context")
_DEFAULT_TIMEOUT = 15


def _allow_internal_targets() -> bool:
    return os.environ.get("CORTEX_DELIVERY_ALLOW_INTERNAL", "").strip().lower() in {"1", "true", "on", "yes"}


def is_safe_webhook_url(url: str) -> tuple[bool, str]:
    """SSRF guard. Returns (ok, reason). Refuses non-http(s), credential-bearing, and — unless
    CORTEX_DELIVERY_ALLOW_INTERNAL is set (local dev) — any target that resolves to a loopback,
    private, link-local, reserved, or multicast address. Public targets must be https."""
    raw = str(url or "").strip()
    parsed = urlparse(raw)
    if parsed.scheme not in {"http", "https"}:
        return False, "URL must be http(s)"
    if "@" in (parsed.netloc or ""):
        return False, "credentials in URL are not allowed"
    host = parsed.hostname
    if not host:
        return False, "URL has no host"
    if _allow_internal_targets():
        return True, ""
    try:
        infos = socket.getaddrinfo(host, parsed.port or (443 if parsed.scheme == "https" else 80), proto=socket.IPPROTO_TCP)
    except OSError:
        return False, "host does not resolve"
    for info in infos:
        try:
            ip = ipaddress.ip_address(info[4][0])
        except ValueError:
            return False, "unresolvable address"
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_multicast or ip.is_unspecified:
            return False, "target resolves to an internal address"
    if parsed.scheme != "https":
        return False, "public targets must use https"
    return True, ""


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
    safe, reason = is_safe_webhook_url(url)
    if not safe:
        return {"ok": False, "status": 0, "reason": reason}
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "Cortex-Delivery/1"}
    try:
        if request_fn is not None:
            status = int(request_fn(url, body, headers))
        else:
            req = Request(url, data=body, headers=headers, method="POST")  # noqa: S310 — SSRF-guarded above
            with urlopen(req, timeout=timeout) as response:  # noqa: S310
                status = int(getattr(response, "status", 0) or 0)
    except OSError as exc:
        return {"ok": False, "status": 0, "reason": f"delivery failed: {exc}"}
    return {"ok": 200 <= status < 300, "status": status, "reason": "" if 200 <= status < 300 else f"HTTP {status}"}
