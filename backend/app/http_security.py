"""Safe HTTP primitives for requests that carry credentials.

``urllib`` forwards request headers across redirects by default, including
``Authorization``. Connector and OAuth requests must therefore reject any
redirect that changes origin; otherwise an API response could exfiltrate a
user's bearer token or client secret to another host.
"""

from __future__ import annotations

from typing import Any
from urllib.error import HTTPError
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener


def _origin(url: str) -> tuple[str, str, int | None] | None:
    parsed = urlsplit(str(url or "").strip())
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        explicit_port = parsed.port
    except ValueError:
        return None
    port = explicit_port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, parsed.hostname.lower(), port


class SameOriginRedirectHandler(HTTPRedirectHandler):
    """Allow redirects only when scheme, hostname, and effective port match."""

    def redirect_request(  # type: ignore[override]
        self,
        req: Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> Request | None:
        resolved = urljoin(req.full_url, str(newurl or ""))
        if _origin(req.full_url) != _origin(resolved):
            raise HTTPError(
                req.full_url,
                code,
                "cross-origin redirect blocked for credential-bearing request",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, resolved)


def open_same_origin(request: Request, *, timeout: float = 30) -> Any:
    """Open a request while preventing credentials from crossing origins."""

    if _origin(request.full_url) is None:
        raise ValueError("credential-bearing request URL must be absolute HTTP(S) without user info")
    return build_opener(SameOriginRedirectHandler()).open(request, timeout=timeout)
