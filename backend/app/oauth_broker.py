"""Cortex Cloud OAuth token-exchange broker.

Some providers (Notion, GitHub) require a CLIENT SECRET at token exchange and offer no PKCE
public-client path, so the secret cannot be safely embedded in the distributed desktop app. This
broker — which runs ONLY in the hosted Cortex Cloud backend (never on a user's local server) — holds
those secrets in its environment and performs the authorize-URL construction and the code→token
exchange on the app's behalf.

Trust model / what stays local: the broker only ever touches the short-lived OAuth authorization
CODE and mints the provider tokens, which it returns to the caller and does NOT persist. The user's
actual content sync still runs entirely on their Mac using those tokens. This is the "the auth
handshake transits our server, the data does not" posture.

Design:
- Provider config is env-driven: CORTEX_BROKER_<PROVIDER>_CLIENT_ID / _CLIENT_SECRET (+ optional
  _AUTHORIZE_URL / _TOKEN_URL / _SCOPES overrides). A provider with no CLIENT_ID configured is simply
  absent — the connector shows "Coming soon" until the founder sets its env vars and redeploys. NO
  app rebuild is needed to add or rotate a provider.
- Redirect URIs are ALLOWLISTED to the Cortex local-loopback callback paths, so the broker can never
  be abused as an open token-minting oracle that redirects tokens to an attacker.
- Stdlib-only HTTP (urllib) to match the backend; no new dependencies.
"""

from __future__ import annotations

import base64
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request as URLLibRequest, urlopen
from urllib.error import HTTPError, URLError

from .config import APP_BRAND

# The route handlers below annotate `request: "Request"`. Because `from __future__ import
# annotations` makes every annotation a STRING, FastAPI resolves "Request" against THIS module's
# globals when it builds the routes — so module-global "Request" MUST be FastAPI's Request, not
# urllib's (imported above as URLLibRequest for the token exchange). Without this, FastAPI tried to
# build a Pydantic body field from urllib.request.Request and the entire hosted app failed to
# import at startup. Guarded so the broker's own fastapi-free logic still imports without fastapi.
try:  # pragma: no cover - fastapi is a hosted-plane dependency
    from fastapi import Request
except ImportError:  # pragma: no cover
    Request = Any  # type: ignore[assignment,misc]


# --- Provider blueprints ----------------------------------------------------

@dataclass(frozen=True)
class BrokerProviderSpec:
    """Static, non-secret shape of a broker provider. Secrets are read from env at request time."""
    key: str
    authorize_url: str
    token_url: str
    default_scopes: str
    # "basic" → HTTP Basic base64(client_id:client_secret) (Notion); "post" → creds in the form body (GitHub).
    token_auth_style: str
    # Extra static params to include on the authorize URL (e.g. Notion's owner=user, response_type=code).
    authorize_extra: dict[str, str] = field(default_factory=dict)
    # GitHub returns application/x-www-form-urlencoded unless asked for JSON.
    token_accept_json: bool = True
    supports_pkce: bool = False


_PROVIDER_SPECS: dict[str, BrokerProviderSpec] = {
    "notion": BrokerProviderSpec(
        key="notion",
        authorize_url="https://api.notion.com/v1/oauth/authorize",
        token_url="https://api.notion.com/v1/oauth/token",
        default_scopes="",  # Notion grants page/db access via the consent screen, not scope strings.
        token_auth_style="basic",
        authorize_extra={"response_type": "code", "owner": "user"},
        token_accept_json=True,
        supports_pkce=False,
    ),
    "github": BrokerProviderSpec(
        key="github",
        authorize_url="https://github.com/login/oauth/authorize",
        token_url="https://github.com/login/oauth/access_token",
        default_scopes="read:user repo",
        token_auth_style="post",
        authorize_extra={},
        token_accept_json=True,
        supports_pkce=True,
    ),
    # One "Connect Google" grants read-only Drive + Gmail in a single consent (a user rarely wants
    # one without the other). access_type=offline + prompt=consent guarantee a refresh_token so the
    # local sync keeps working after the access token expires. Google is a confidential web client,
    # so the secret lives here in the broker — the founder registers ONE Google OAuth client and
    # every user gets one-click, no per-user credential paste. Override scopes per deployment with
    # CORTEX_BROKER_GOOGLE_SCOPES.
    "google": BrokerProviderSpec(
        key="google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        default_scopes=(
            "openid email "
            "https://www.googleapis.com/auth/drive.readonly "
            "https://www.googleapis.com/auth/gmail.readonly"
        ),
        token_auth_style="post",
        authorize_extra={"response_type": "code", "access_type": "offline", "prompt": "consent"},
        token_accept_json=True,
        supports_pkce=True,
    ),
    # Microsoft (Outlook / Microsoft 365). Like Google, Microsoft's web app is a confidential client:
    # the token exchange requires the client secret, so it lives here in the broker rather than in the
    # distributed app. offline_access earns a refresh_token; User.Read + Mail.Read is the least-
    # privilege read set the Outlook connector needs. Microsoft supports PKCE, so the app still sends a
    # code_challenge and the code_verifier never leaves the Mac. Override scopes with
    # CORTEX_BROKER_MICROSOFT_SCOPES.
    "microsoft": BrokerProviderSpec(
        key="microsoft",
        authorize_url="https://login.microsoftonline.com/common/oauth2/v2.0/authorize",
        token_url="https://login.microsoftonline.com/common/oauth2/v2.0/token",
        default_scopes="offline_access User.Read Mail.Read",
        token_auth_style="post",
        authorize_extra={"response_type": "code", "response_mode": "query"},
        token_accept_json=True,
        supports_pkce=True,
    ),
}


# --- Redirect allowlist ------------------------------------------------------

# Only the app's own local-loopback callback paths are ever permitted, on 127.0.0.1/localhost, any
# port. This is the load-bearing guard: it stops the broker (which holds the secret) from being used
# to mint tokens toward an attacker-controlled redirect.
_ALLOWED_CALLBACK_PATHS = frozenset({
    "/v1/connectors/oauth/callback",
    "/v1/connectors/google/oauth/callback",
})
_ALLOWED_REDIRECT_HOSTS = frozenset({"127.0.0.1", "localhost", "[::1]", "::1"})


def _redirect_uri_is_allowed(redirect_uri: str) -> bool:
    from urllib.parse import urlsplit
    try:
        parts = urlsplit(redirect_uri.strip())
    except ValueError:
        return False
    if parts.scheme != "http":  # loopback callbacks are http; never allow https/custom schemes here
        return False
    if (parts.hostname or "") not in _ALLOWED_REDIRECT_HOSTS:
        return False
    return parts.path in _ALLOWED_CALLBACK_PATHS


# --- Broker errors -----------------------------------------------------------

class BrokerError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.message = message
        self.status = status


# --- Registry ----------------------------------------------------------------

class OAuthBrokerRegistry:
    """Reads provider secrets from the environment and performs authorize/exchange/refresh.

    `token_request` is injectable so tests can exercise the full flow without real network calls.
    """

    def __init__(
        self,
        env: dict[str, str] | None = None,
        token_request: Callable[[str, dict[str, str], dict[str, str]], dict[str, Any]] | None = None,
    ):
        self._env = env if env is not None else dict(os.environ)
        self._token_request = token_request or _http_token_request

    def _client_id(self, provider: str) -> str:
        return (self._env.get(f"CORTEX_BROKER_{provider.upper()}_CLIENT_ID") or "").strip()

    def _client_secret(self, provider: str) -> str:
        return (self._env.get(f"CORTEX_BROKER_{provider.upper()}_CLIENT_SECRET") or "").strip()

    def _spec(self, provider: str) -> BrokerProviderSpec:
        spec = _PROVIDER_SPECS.get((provider or "").strip().lower())
        if spec is None:
            raise BrokerError(f"Unknown broker provider: {provider!r}", status=404)
        return spec

    def configured_providers(self) -> list[str]:
        return [key for key in _PROVIDER_SPECS if self._client_id(key)]

    def is_configured(self, provider: str) -> bool:
        return bool(self._client_id((provider or "").strip().lower()))

    def _require_configured(self, provider: str) -> tuple[BrokerProviderSpec, str, str]:
        spec = self._spec(provider)
        client_id = self._client_id(spec.key)
        if not client_id:
            raise BrokerError(f"{spec.key} sign-in is not configured on this server yet.", status=503)
        client_secret = self._client_secret(spec.key)
        if not client_secret:
            raise BrokerError(f"{spec.key} broker is missing its client secret.", status=503)
        return spec, client_id, client_secret

    def _scopes(self, spec: BrokerProviderSpec) -> str:
        override = (self._env.get(f"CORTEX_BROKER_{spec.key.upper()}_SCOPES") or "").strip()
        return override if override else spec.default_scopes

    # --- endpoints ---

    def authorization_url(self, provider: str, redirect_uri: str, state: str, code_challenge: str | None = None) -> str:
        spec, client_id, _ = self._require_configured(provider)
        if not state.strip():
            raise BrokerError("state is required.")
        if not _redirect_uri_is_allowed(redirect_uri):
            raise BrokerError(f"redirect_uri is not an allowed {APP_BRAND} loopback callback.", status=400)
        params: dict[str, str] = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "state": state,
        }
        params.update(spec.authorize_extra)
        if "response_type" not in params:
            params["response_type"] = "code"
        scopes = self._scopes(spec)
        if scopes:
            params["scope"] = scopes
        if code_challenge and spec.supports_pkce:
            params["code_challenge"] = code_challenge
            params["code_challenge_method"] = "S256"
        return f"{spec.authorize_url}?{urlencode(params)}"

    def exchange(self, provider: str, code: str, redirect_uri: str, code_verifier: str | None = None) -> dict[str, Any]:
        spec, client_id, client_secret = self._require_configured(provider)
        if not code.strip():
            raise BrokerError("code is required.")
        if not _redirect_uri_is_allowed(redirect_uri):
            raise BrokerError(f"redirect_uri is not an allowed {APP_BRAND} loopback callback.", status=400)
        form: dict[str, str] = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
        }
        if code_verifier and spec.supports_pkce:
            form["code_verifier"] = code_verifier
        return self._perform(spec, client_id, client_secret, form)

    def refresh(self, provider: str, refresh_token: str) -> dict[str, Any]:
        spec, client_id, client_secret = self._require_configured(provider)
        if not refresh_token.strip():
            raise BrokerError("refresh_token is required.")
        form = {"grant_type": "refresh_token", "refresh_token": refresh_token}
        return self._perform(spec, client_id, client_secret, form)

    def _perform(self, spec: BrokerProviderSpec, client_id: str, client_secret: str, form: dict[str, str]) -> dict[str, Any]:
        headers = {"Accept": "application/json"} if spec.token_accept_json else {}
        if spec.token_auth_style == "basic":
            token = base64.b64encode(f"{client_id}:{client_secret}".encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {token}"
        else:  # "post" — creds go in the body
            form = {**form, "client_id": client_id, "client_secret": client_secret}
        try:
            payload = self._token_request(spec.token_url, form, headers)
        except BrokerError:
            raise
        except Exception as exc:  # noqa: BLE001 — normalize any transport error into a safe 502
            raise BrokerError("Token exchange with the provider failed.", status=502) from exc
        if not isinstance(payload, dict) or "access_token" not in payload:
            # Providers signal auth-code errors in-body with 200 sometimes (esp. GitHub form mode).
            detail = ""
            if isinstance(payload, dict):
                detail = str(payload.get("error_description") or payload.get("error") or "")
            raise BrokerError(f"Provider did not return an access token. {detail}".strip(), status=502)
        return _normalize_token_payload(payload)


def _normalize_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Return a stable subset the local backend expects, computing an absolute expiry if given a TTL."""
    out: dict[str, Any] = {
        "access_token": payload.get("access_token"),
        "token_type": payload.get("token_type") or "bearer",
    }
    if payload.get("refresh_token"):
        out["refresh_token"] = payload["refresh_token"]
    if payload.get("scope"):
        out["scope"] = payload["scope"]
    expires_in = payload.get("expires_in")
    if isinstance(expires_in, (int, float)) and expires_in > 0:
        out["expires_in"] = int(expires_in)
        out["access_token_expires_at"] = int(time.time()) + int(expires_in)
    # Pass through a few common provider identity hints without storing anything.
    for key in ("workspace_id", "workspace_name", "bot_id", "owner", "workspace_icon"):
        if key in payload:
            out[key] = payload[key]
    return out


def _http_token_request(token_url: str, form: dict[str, str], headers: dict[str, str]) -> dict[str, Any]:
    data = urlencode(form).encode("utf-8")
    req_headers = {"Content-Type": "application/x-www-form-urlencoded", **headers}
    request = URLLibRequest(token_url, data=data, headers=req_headers, method="POST")
    try:
        with urlopen(request, timeout=30) as response:  # noqa: S310 — provider token endpoints only
            raw = response.read().decode("utf-8")
    except HTTPError as exc:
        try:
            raw = exc.read().decode("utf-8")
            return json.loads(raw)  # let _perform surface the provider's error body
        except Exception:
            raise BrokerError("Provider rejected the token request.", status=502) from exc
    except URLError as exc:
        raise BrokerError("Could not reach the provider token endpoint.", status=502) from exc
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise BrokerError("Provider returned a non-JSON token response.", status=502) from exc


# --- FastAPI wiring ----------------------------------------------------------

def register_oauth_broker_routes(app: Any, registry: OAuthBrokerRegistry | None = None) -> OAuthBrokerRegistry:
    """Attach /oauth/broker/* routes to the hosted FastAPI app. Safe to call always: with no providers
    configured the endpoints simply return 503 'not configured'."""
    from fastapi import HTTPException, Request
    from fastapi.responses import JSONResponse

    broker = registry or OAuthBrokerRegistry()

    async def _json_body(request: "Request") -> dict[str, Any]:
        try:
            body = await request.json()
        except Exception:
            body = None
        return body if isinstance(body, dict) else {}

    def _guard(fn: Callable[[], dict[str, Any]]) -> JSONResponse:
        try:
            return JSONResponse(fn())
        except BrokerError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.message)

    @app.get("/oauth/broker/providers")
    async def broker_providers() -> JSONResponse:  # noqa: ANN202
        return JSONResponse({"providers": broker.configured_providers()})

    @app.post("/oauth/broker/start")
    async def broker_start(request: "Request") -> JSONResponse:  # noqa: ANN202
        body = await _json_body(request)
        return _guard(lambda: {"authorization_url": broker.authorization_url(
            str(body.get("provider") or ""),
            str(body.get("redirect_uri") or ""),
            str(body.get("state") or ""),
            (str(body["code_challenge"]) if body.get("code_challenge") else None),
        )})

    @app.post("/oauth/broker/exchange")
    async def broker_exchange(request: "Request") -> JSONResponse:  # noqa: ANN202
        body = await _json_body(request)
        return _guard(lambda: broker.exchange(
            str(body.get("provider") or ""),
            str(body.get("code") or ""),
            str(body.get("redirect_uri") or ""),
            (str(body["code_verifier"]) if body.get("code_verifier") else None),
        ))

    @app.post("/oauth/broker/refresh")
    async def broker_refresh(request: "Request") -> JSONResponse:  # noqa: ANN202
        body = await _json_body(request)
        return _guard(lambda: broker.refresh(
            str(body.get("provider") or ""),
            str(body.get("refresh_token") or ""),
        ))

    return broker
