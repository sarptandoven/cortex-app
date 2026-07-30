"""Dependency-light client for the Cortex local memory server.

Wraps Cortex's local HTTP surface (default ``http://127.0.0.1:8766``) using only the
Python standard library — no third-party runtime dependencies. This matches the
project's stdlib-only ethos and keeps the SDK trivial to vendor into any project.

The same tool catalog Cortex exposes over MCP is reachable here over plain HTTP, so a
function-calling app (OpenAI, Anthropic, or a custom agent) can drive Cortex from one
definition. See ``CortexClient.openai_tools`` / ``anthropic_tools`` for wiring the tool
schema into a provider's ``tools=`` array and ``call_tool`` for routing calls back.
"""

from __future__ import annotations

import json
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

__all__ = ["CortexClient", "CortexError"]

DEFAULT_BASE_URL = "http://127.0.0.1:8766"
DEFAULT_TIMEOUT = 30.0


def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    return parsed.scheme, parsed.hostname.lower(), port or (443 if parsed.scheme == "https" else 80)


class _SameOriginRedirectHandler(HTTPRedirectHandler):
    """Never forward a Cortex bearer token to another origin."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
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


def _safe_urlopen(request: Request, *, timeout: float):
    if _origin(request.full_url) is None:
        raise CortexError(
            0,
            "Invalid Cortex base URL: expected an absolute HTTP(S) URL "
            "without user info, a query, or a fragment",
        )
    return build_opener(_SameOriginRedirectHandler()).open(request, timeout=timeout)


class CortexError(Exception):
    """Raised when the Cortex server returns a non-2xx response or is unreachable.

    Attributes:
        status: The HTTP status code (0 when the request never reached the server,
            e.g. a connection error).
        detail: The parsed ``detail`` field from the error body when present, otherwise
            the raw response text or the underlying transport error message. The server
            returns errors as ``{"detail": <str or object>}``.
    """

    def __init__(self, status: int, detail: Any) -> None:
        self.status = status
        self.detail = detail
        super().__init__(f"Cortex request failed (HTTP {status}): {detail}")


class CortexClient:
    """Client for the Cortex local memory HTTP API.

    Args:
        base_url: Base URL of the Cortex server. Defaults to the local loopback address
            the macOS app serves on.
        token: Bearer token sent as ``Authorization: Bearer <token>``. An empty token is
            only accepted by a server running in unauthenticated local dev mode.
        user: Optional user id sent as the ``X-Cortex-User`` header to select a specific
            user (multi-user / sharded deployments).
        timeout: Per-request socket timeout in seconds.

    Example:
        >>> client = CortexClient(token="cxa_...")
        >>> answer = client.ask("What database do we use?")
        >>> hits = client.search("release checklist", top_k=5)
    """

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        token: str = "",
        user: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        normalized_base_url = base_url.strip().rstrip("/")
        try:
            parsed_base_url = urlsplit(normalized_base_url)
        except ValueError:
            parsed_base_url = None
        if (
            _origin(normalized_base_url) is None
            or parsed_base_url is None
            or bool(parsed_base_url.query)
            or bool(parsed_base_url.fragment)
        ):
            raise CortexError(
                0,
                "Invalid Cortex base URL: expected an absolute HTTP(S) URL "
                "without user info, a query, or a fragment",
            )
        self.base_url = normalized_base_url
        self.token = token
        self.user = user
        self.timeout = timeout

    # -- transport ---------------------------------------------------------------------

    def _headers(self, *, json_body: bool) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if self.user:
            headers["X-Cortex-User"] = self.user
        if json_body:
            headers["Content-Type"] = "application/json"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict[str, Any]] = None,
        body: Optional[dict[str, Any]] = None,
    ) -> Any:
        """Issue an HTTP request and return the parsed JSON response.

        Raises:
            CortexError: On any non-2xx response or transport failure.
        """
        url = self.base_url + path
        if params:
            # Drop None values so unset optional filters are simply omitted.
            filtered = {k: v for k, v in params.items() if v is not None}
            if filtered:
                url = f"{url}?{urlencode(filtered)}"

        data: Optional[bytes] = None
        if body is not None:
            data = json.dumps(body).encode("utf-8")

        request = Request(
            url,
            data=data,
            method=method,
            headers=self._headers(json_body=body is not None),
        )
        try:
            with _safe_urlopen(request, timeout=self.timeout) as response:
                raw = response.read()
        except HTTPError as exc:
            raise self._error_from_http(exc) from exc
        except URLError as exc:
            raise CortexError(0, f"Could not reach Cortex at {self.base_url}: {exc.reason}") from exc

        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: bytes) -> Any:
        if not raw:
            return None
        text = raw.decode("utf-8")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Some endpoints (context-pack, markdown formats) return text, not JSON.
            return text

    @staticmethod
    def _error_from_http(exc: HTTPError) -> CortexError:
        detail: Any
        try:
            raw = exc.read()
        except Exception:  # pragma: no cover - defensive
            raw = b""
        text = raw.decode("utf-8", "replace") if raw else ""
        try:
            parsed = json.loads(text) if text else None
        except json.JSONDecodeError:
            parsed = None
        if isinstance(parsed, dict) and "detail" in parsed:
            detail = parsed["detail"]
        else:
            detail = parsed if parsed is not None else (text or exc.reason)
        return CortexError(exc.code, detail)

    # -- tool catalog ------------------------------------------------------------------

    def tools_schema(self, fmt: str = "openai") -> Any:
        """Fetch the tool catalog projected to a function-calling format.

        Args:
            fmt: One of ``"openai"``, ``"anthropic"``, ``"openapi"``, or ``"mcp"``.

        Returns:
            The schema as returned by the server (a list for openai/anthropic/mcp, a
            dict for openapi). The server wraps it as ``{"schema": ...}``; this method
            unwraps it for you.
        """
        response = self._request("GET", "/v1/tools/schema", params={"format": fmt})
        if isinstance(response, dict) and "schema" in response:
            return response["schema"]
        return response

    def openai_tools(self) -> list[dict[str, Any]]:
        """Return the OpenAI function-calling ``tools`` array (convenience).

        Drop the result straight into an OpenAI ``chat.completions.create(tools=...)``
        or Responses call, then route any tool calls back through :meth:`call_tool`.
        """
        return self.tools_schema("openai")

    def anthropic_tools(self) -> list[dict[str, Any]]:
        """Return the Anthropic Messages API ``tools`` array (convenience)."""
        return self.tools_schema("anthropic")

    def call_tool(self, name: str, arguments: Optional[dict[str, Any]] = None) -> Any:
        """Invoke a Cortex tool by name and return its result.

        Uses the generic ``POST /v1/tools/call`` endpoint. The server responds with
        ``{"tool": name, "result": value}``; this method returns ``value`` directly.

        Args:
            name: Tool name, e.g. ``"search_memory"`` or ``"get_context"``.
            arguments: Tool arguments matching the tool's input schema.

        Returns:
            The tool's ``result`` payload.
        """
        response = self._request(
            "POST",
            "/v1/tools/call",
            body={"name": name, "arguments": arguments or {}},
        )
        if isinstance(response, dict) and "result" in response:
            return response["result"]
        return response

    # -- high-value endpoints ----------------------------------------------------------

    def context(
        self,
        task: str,
        intent: Optional[str] = None,
        token_budget: int = 2000,
        surface: str = "agent",
    ) -> Any:
        """Build a token-budgeted, cited working-context pack for a task.

        Calls ``POST /v1/context``. Call this first before doing work for the user.

        Args:
            task: What you are about to do for the user.
            intent: Optional intent hint (``answer``, ``act``, ``draft``, ``plan``,
                ``recall``).
            token_budget: Approximate token budget for the assembled pack.
            surface: Which tool/agent you are (e.g. ``cursor``, ``claude``, ``agent``).

        Returns:
            The assembled context pack (JSON object).
        """
        return self._request(
            "POST",
            "/v1/context",
            body={
                "task": task,
                "intent": intent,
                "token_budget": token_budget,
                "surface": surface,
            },
        )

    def search(self, query: str, top_k: int = 8) -> Any:
        """Search Cortex memory and return results with retrieval diagnostics.

        Calls ``GET /v1/search``. ``top_k`` maps to the server's ``limit`` parameter.
        """
        return self._request(
            "GET",
            "/v1/search",
            params={"query": query, "limit": top_k},
        )

    def ask(self, query: str, top_k: int = 8) -> Any:
        """Ask a question against memory and get a cited answer (or an abstention).

        Calls ``GET /v1/ask``. ``top_k`` maps to the server's ``limit`` parameter.
        Never returns an uncited guess — the server abstains explicitly when unsure.
        """
        return self._request(
            "GET",
            "/v1/ask",
            params={"query": query, "limit": top_k},
        )
