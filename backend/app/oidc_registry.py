"""Generic OIDC / OAuth2 provider registry for first-party auth.

Implements docs/ACCOUNTS_ENCRYPTION_DESIGN.md section 1 ("oidc_registry.py")
and build-plan step 5:

- **Google**: standard OIDC entry (hardcoded well-known endpoints, PKCE S256 +
  state + nonce always) with FULL id_token verification — iss/aud/exp and the
  RS256 signature against Google's JWKS (fetched through the injectable
  transport and cached), implemented with the `cryptography` package. Email is
  trusted only when the id_token says ``email_verified: true``.
- **GitHub**: dedicated non-OIDC OAuth2 adapter. No id_token exists; the
  immutable numeric id from ``/user`` is the subject and the email comes from
  ``/user/emails`` filtered to the *verified primary* address. An account with
  no verified primary email is rejected outright — unverified GitHub emails
  never create or link accounts.
- **'openai'**: a DISABLED placeholder row — the pluggable AI-vendor slot from
  the design. It is never enabled by default; when/if a public standard-OIDC
  offering ships, enabling it is a config change, not an architecture change.

Flow state (state + nonce + PKCE verifier + redirect_uri + optional app-login
flow linkage) is persisted in the accounts ControlStore ``auth_flows`` table
(kind='oidc', single-use via ``consume_flow``, ~10 min TTL) so the
unauthenticated callback can be validated by ``state`` alone.

ALL network I/O goes through an injectable ``transport(request) -> response``
callable (default: urllib.request) so tests fake the provider end to end.
Request dict: {"method", "url", "headers", "body", "timeout"}; response dict:
{"status", "headers", "body"(bytes)}.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Callable, Optional

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from .accounts import ControlStore, iso_utc, utc_now

OAUTH_FLOW_KIND = "oidc"  # accounts.FLOW_KINDS row used for OAuth state
DEFAULT_STATE_TTL_SECONDS = 600  # ~10 minutes, single-use
DEFAULT_HTTP_TIMEOUT_SECONDS = 10.0
JWKS_CACHE_TTL_SECONDS = 3600.0

GOOGLE_ACCEPTED_ISSUERS = ("https://accounts.google.com", "accounts.google.com")


class OidcError(Exception):
    """Any OAuth/OIDC flow failure. Callers surface a generic message; the
    specific reason stays server-side (logs/audit), never an oracle."""


class UnknownSigningKeyError(OidcError):
    """The id_token kid is not in the cached JWKS (triggers one forced refetch)."""


@dataclass(frozen=True)
class ProviderDefinition:
    name: str
    kind: str  # 'oidc' | 'github'
    display_name: str
    issuer: str = ""
    authorize_endpoint: str = ""
    token_endpoint: str = ""
    jwks_uri: str = ""
    api_base_url: str = ""  # GitHub adapter: REST base for /user + /user/emails
    scopes: str = ""
    client_id: str = ""
    client_secret: str = ""
    enabled: bool = False
    button_order: int = 100


def definitions_from_settings(settings: Any) -> dict[str, ProviderDefinition]:
    """Seed the provider table from Settings (config.py oidc_* knobs). A
    provider is enabled only when BOTH its client id and secret are configured;
    the 'openai' row is the reserved pluggable slot and stays disabled."""
    google_id = str(getattr(settings, "oidc_google_client_id", "") or "")
    google_secret = str(getattr(settings, "oidc_google_client_secret", "") or "")
    github_id = str(getattr(settings, "oidc_github_client_id", "") or "")
    github_secret = str(getattr(settings, "oidc_github_client_secret", "") or "")
    return {
        "google": ProviderDefinition(
            name="google",
            kind="oidc",
            display_name="Google",
            issuer="https://accounts.google.com",
            authorize_endpoint="https://accounts.google.com/o/oauth2/v2/auth",
            token_endpoint="https://oauth2.googleapis.com/token",
            jwks_uri="https://www.googleapis.com/oauth2/v3/certs",
            scopes="openid email profile",
            client_id=google_id,
            client_secret=google_secret,
            enabled=bool(google_id and google_secret),
            button_order=10,
        ),
        "github": ProviderDefinition(
            name="github",
            kind="github",
            display_name="GitHub",
            authorize_endpoint="https://github.com/login/oauth/authorize",
            token_endpoint="https://github.com/login/oauth/access_token",
            api_base_url="https://api.github.com",
            scopes="read:user user:email",
            client_id=github_id,
            client_secret=github_secret,
            enabled=bool(github_id and github_secret),
            button_order=20,
        ),
        # Reserved AI-vendor slot (design doc section 1): shipped disabled, no
        # endpoints, no credentials. Never enabled by default — activation is a
        # deliberate config decision once a public standard-OIDC offering exists.
        "openai": ProviderDefinition(
            name="openai",
            kind="oidc",
            display_name="OpenAI",
            enabled=False,
            button_order=100,
        ),
    }


# --------------------------------------------------------------------------
# Default transport (urllib) — replaced entirely by tests
# --------------------------------------------------------------------------

def urllib_transport(request: dict[str, Any]) -> dict[str, Any]:
    url = str(request.get("url") or "")
    if not url.lower().startswith("https://") and not url.lower().startswith("http://127.0.0.1"):
        raise OidcError(f"refusing non-HTTPS provider URL: {url!r}")
    req = urllib.request.Request(
        url,
        data=request.get("body"),
        headers=dict(request.get("headers") or {}),
        method=str(request.get("method") or "GET").upper(),
    )
    timeout = float(request.get("timeout") or DEFAULT_HTTP_TIMEOUT_SECONDS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - scheme checked above
            return {
                "status": int(getattr(response, "status", 200) or 200),
                "headers": dict(response.headers.items()),
                "body": response.read(),
            }
    except urllib.error.HTTPError as exc:
        return {
            "status": int(exc.code),
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "body": exc.read() or b"",
        }
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise OidcError(f"provider request failed: {exc}") from exc


# --------------------------------------------------------------------------
# Minimal RS256 JWT verification (cryptography) — no JWT library dependency
# --------------------------------------------------------------------------

def _b64url_decode(segment: str) -> bytes:
    normalized = segment.strip()
    try:
        return base64.urlsafe_b64decode(normalized + "=" * (-len(normalized) % 4))
    except (ValueError, TypeError) as exc:
        raise OidcError("malformed base64url segment in token") from exc


def _b64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8 or 1, "big")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _jwk_public_key(jwk: dict[str, Any]) -> rsa.RSAPublicKey:
    if str(jwk.get("kty") or "") != "RSA":
        raise OidcError("id_token signing key is not an RSA key")
    try:
        n = int.from_bytes(_b64url_decode(str(jwk["n"])), "big")
        e = int.from_bytes(_b64url_decode(str(jwk["e"])), "big")
    except (KeyError, OidcError) as exc:
        raise OidcError("malformed JWK in provider JWKS") from exc
    return rsa.RSAPublicNumbers(e, n).public_key()


def _select_jwk(keys: list[dict[str, Any]], kid: Optional[str]) -> dict[str, Any]:
    if kid:
        for key in keys:
            if str(key.get("kid") or "") == kid:
                return key
        raise UnknownSigningKeyError(f"no JWKS key with kid {kid!r}")
    if len(keys) == 1:
        return keys[0]
    raise OidcError("id_token has no kid and the JWKS is ambiguous")


def verify_id_token(
    id_token: str,
    *,
    keys: list[dict[str, Any]],
    audience: str,
    issuers: tuple[str, ...],
    nonce: Optional[str],
    now_epoch: float,
) -> dict[str, Any]:
    """Full OIDC id_token verification: RS256 signature against the provider
    JWKS, then iss / aud / exp / nonce. Every failure raises OidcError."""
    parts = (id_token or "").split(".")
    if len(parts) != 3:
        raise OidcError("malformed id_token")
    header = _json_object(_b64url_decode(parts[0]), "id_token header")
    if str(header.get("alg") or "") != "RS256":
        raise OidcError(f"unsupported id_token alg {header.get('alg')!r} (RS256 only)")
    jwk = _select_jwk(keys, str(header.get("kid")) if header.get("kid") else None)
    public_key = _jwk_public_key(jwk)
    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    signature = _b64url_decode(parts[2])
    try:
        public_key.verify(signature, signing_input, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise OidcError("id_token signature verification failed") from exc
    claims = _json_object(_b64url_decode(parts[1]), "id_token claims")
    if str(claims.get("iss") or "") not in issuers:
        raise OidcError(f"id_token issuer {claims.get('iss')!r} is not trusted")
    aud = claims.get("aud")
    audiences = [str(item) for item in aud] if isinstance(aud, list) else [str(aud or "")]
    if audience not in audiences:
        raise OidcError("id_token audience mismatch")
    try:
        expires = float(claims.get("exp") or 0)
    except (TypeError, ValueError) as exc:
        raise OidcError("id_token exp claim is malformed") from exc
    if expires <= now_epoch:
        raise OidcError("id_token is expired")
    if nonce is not None and str(claims.get("nonce") or "") != nonce:
        raise OidcError("id_token nonce mismatch")
    if not str(claims.get("sub") or "").strip():
        raise OidcError("id_token has no subject")
    return claims


def _json_object(raw: bytes, what: str) -> dict[str, Any]:
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise OidcError(f"malformed {what}") from exc
    if not isinstance(parsed, dict):
        raise OidcError(f"malformed {what}")
    return parsed


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

class OidcProviderRegistry:
    """Config-driven provider registry + the start/complete OAuth engine.

    - ``start(provider, redirect_uri, app_flow_id=None)`` persists the
      single-use flow row (state = flow_id) and returns the authorize URL.
    - ``complete(provider, state, code, ...)`` consumes the flow, exchanges the
      code (PKCE verifier included), and returns the VERIFIED provider
      assertion: {subject, email, email_verified, display_name, app_flow_id}.
      The caller (main.py wiring) maps that onto accounts via
      AccountsService.find_or_challenge_identity — never here.
    """

    def __init__(
        self,
        store: ControlStore,
        providers: dict[str, ProviderDefinition],
        *,
        transport: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        clock: Callable[[], datetime] = utc_now,
        state_ttl_seconds: int = DEFAULT_STATE_TTL_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.providers = dict(providers)
        self._transport = transport or urllib_transport
        self._clock = clock
        self.state_ttl_seconds = int(state_ttl_seconds)
        self._monotonic = monotonic
        self._jwks_cache: dict[str, tuple[list[dict[str, Any]], float]] = {}

    @classmethod
    def from_settings(
        cls,
        settings: Any,
        store: ControlStore,
        *,
        transport: Optional[Callable[[dict[str, Any]], dict[str, Any]]] = None,
        clock: Callable[[], datetime] = utc_now,
    ) -> "OidcProviderRegistry":
        return cls(store, definitions_from_settings(settings), transport=transport, clock=clock)

    # ------------------------------------------------------------------ public
    def enabled_providers(self) -> list[dict[str, Any]]:
        """Rows for the login-buttons endpoint. NEVER includes secrets (or even
        client ids) — display metadata only."""
        rows = [
            {
                "provider": definition.name,
                "kind": definition.kind,
                "display_name": definition.display_name,
                "button_order": definition.button_order,
            }
            for definition in self.providers.values()
            if definition.enabled
        ]
        return sorted(rows, key=lambda row: (row["button_order"], row["provider"]))

    def start(
        self,
        provider: str,
        redirect_uri: str,
        *,
        app_flow_id: Optional[str] = None,
    ) -> dict[str, Any]:
        definition = self._require_enabled(provider)
        redirect = str(redirect_uri or "").strip()
        if not redirect:
            raise OidcError("redirect_uri is required")
        state = "st" + secrets.token_urlsafe(32).replace("-", "").replace("_", "")
        nonce = secrets.token_urlsafe(24)
        pkce_verifier = secrets.token_urlsafe(64)  # 86 chars of the PKCE charset
        pkce_challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(pkce_verifier.encode("ascii")).digest())
            .decode("ascii")
            .rstrip("=")
        )
        now = self._clock()
        self.store.create_flow(
            flow_id=state,
            kind=OAUTH_FLOW_KIND,
            provider=definition.name,
            payload={
                "provider": definition.name,
                "nonce": nonce,
                "pkce_verifier": pkce_verifier,
                "redirect_uri": redirect,
                "app_flow_id": str(app_flow_id or "") or None,
            },
            secret_hash=None,
            expires_at=iso_utc(now + timedelta(seconds=self.state_ttl_seconds)),
            now=iso_utc(now),
        )
        params = {
            "response_type": "code",
            "client_id": definition.client_id,
            "redirect_uri": redirect,
            "scope": definition.scopes,
            "state": state,
            "code_challenge": pkce_challenge,
            "code_challenge_method": "S256",
        }
        if definition.kind == "oidc":
            params["nonce"] = nonce
        authorize_url = f"{definition.authorize_endpoint}?{urllib.parse.urlencode(params)}"
        return {"authorize_url": authorize_url, "state": state, "provider": definition.name}

    def complete(
        self,
        provider: str,
        *,
        state: str,
        code: str,
        redirect_uri: Optional[str] = None,
    ) -> dict[str, Any]:
        definition = self._require_enabled(provider)
        if not str(code or "").strip():
            raise OidcError("authorization code is required")
        payload = self._consume_state(definition, state)
        if redirect_uri is not None and str(redirect_uri) != str(payload.get("redirect_uri") or ""):
            raise OidcError("redirect_uri does not match the started flow")
        token_response = self._exchange_code(
            definition,
            code=str(code),
            redirect_uri=str(payload.get("redirect_uri") or ""),
            pkce_verifier=str(payload.get("pkce_verifier") or ""),
        )
        if definition.kind == "github":
            identity = self._github_identity(definition, token_response)
        else:
            identity = self._oidc_identity(definition, token_response, nonce=str(payload.get("nonce") or ""))
        identity["provider"] = definition.name
        identity["app_flow_id"] = payload.get("app_flow_id") or None
        return identity

    # -------------------------------------------------------------- internals
    def _require_enabled(self, provider: str) -> ProviderDefinition:
        definition = self.providers.get(str(provider or "").strip().lower())
        if definition is None or not definition.enabled:
            raise OidcError(f"provider {provider!r} is unknown or disabled")
        if not definition.client_id or not definition.client_secret:
            raise OidcError(f"provider {provider!r} is not fully configured")
        return definition

    def _now_iso(self) -> str:
        return iso_utc(self._clock())

    def _consume_state(self, definition: ProviderDefinition, state: str) -> dict[str, Any]:
        """Single-use, TTL'd, tamper-proof state consumption. Any mismatch —
        unknown state, wrong kind, wrong provider, expired, already consumed —
        fails the same way."""
        flow_id = str(state or "").strip()
        if not flow_id:
            raise OidcError("state is required")
        flow = self.store.get_flow(flow_id)
        if flow is None or str(flow.get("kind") or "") != OAUTH_FLOW_KIND:
            raise OidcError("unknown or tampered OAuth state")
        payload = self._flow_payload(flow)
        if str(payload.get("provider") or "") != definition.name:
            raise OidcError("OAuth state does not belong to this provider")
        if str(flow.get("expires_at") or "") <= self._now_iso():
            raise OidcError("OAuth state expired")
        consumed = self.store.consume_flow(flow_id, self._now_iso())
        if consumed is None:
            raise OidcError("OAuth state already used")
        return payload

    @staticmethod
    def _flow_payload(flow: dict[str, Any]) -> dict[str, Any]:
        try:
            payload = json.loads(flow.get("payload_json") or "{}")
        except ValueError:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _exchange_code(
        self,
        definition: ProviderDefinition,
        *,
        code: str,
        redirect_uri: str,
        pkce_verifier: str,
    ) -> dict[str, Any]:
        body = urllib.parse.urlencode(
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": definition.client_id,
                "client_secret": definition.client_secret,
                "code_verifier": pkce_verifier,
            }
        ).encode("utf-8")
        response = self._transport(
            {
                "method": "POST",
                "url": definition.token_endpoint,
                "headers": {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Accept": "application/json",
                },
                "body": body,
                "timeout": DEFAULT_HTTP_TIMEOUT_SECONDS,
            }
        )
        if int(response.get("status") or 0) != 200:
            raise OidcError(
                f"token exchange with {definition.name} failed (HTTP {response.get('status')})"
            )
        data = _json_object(bytes(response.get("body") or b""), "token response")
        if data.get("error"):
            raise OidcError(f"token exchange with {definition.name} failed: {data.get('error')}")
        return data

    # ------------------------------------------------------------ OIDC (Google)
    def _oidc_identity(
        self, definition: ProviderDefinition, token_response: dict[str, Any], *, nonce: str
    ) -> dict[str, Any]:
        id_token = str(token_response.get("id_token") or "")
        if not id_token:
            raise OidcError(f"{definition.name} token response carried no id_token")
        issuers = (
            GOOGLE_ACCEPTED_ISSUERS
            if definition.name == "google"
            else ((definition.issuer,) if definition.issuer else ())
        )
        if not issuers:
            raise OidcError(f"provider {definition.name} has no trusted issuer configured")
        now_epoch = self._clock().timestamp()
        keys = self._jwks_keys(definition)
        try:
            claims = verify_id_token(
                id_token,
                keys=keys,
                audience=definition.client_id,
                issuers=issuers,
                nonce=nonce or None,
                now_epoch=now_epoch,
            )
        except UnknownSigningKeyError:
            # Key rotation: force one JWKS refetch, then verify or fail.
            keys = self._jwks_keys(definition, force=True)
            claims = verify_id_token(
                id_token,
                keys=keys,
                audience=definition.client_id,
                issuers=issuers,
                nonce=nonce or None,
                now_epoch=now_epoch,
            )
        email = str(claims.get("email") or "").strip().lower() or None
        return {
            "subject": str(claims["sub"]),
            "email": email,
            "email_verified": bool(claims.get("email_verified")) and email is not None,
            "display_name": str(claims.get("name") or ""),
        }

    def _jwks_keys(self, definition: ProviderDefinition, *, force: bool = False) -> list[dict[str, Any]]:
        if not definition.jwks_uri:
            raise OidcError(f"provider {definition.name} has no JWKS endpoint configured")
        cached = self._jwks_cache.get(definition.name)
        if not force and cached is not None and cached[1] > self._monotonic():
            return cached[0]
        response = self._transport(
            {
                "method": "GET",
                "url": definition.jwks_uri,
                "headers": {"Accept": "application/json"},
                "body": None,
                "timeout": DEFAULT_HTTP_TIMEOUT_SECONDS,
            }
        )
        if int(response.get("status") or 0) != 200:
            raise OidcError(f"JWKS fetch for {definition.name} failed (HTTP {response.get('status')})")
        document = _json_object(bytes(response.get("body") or b""), "JWKS document")
        keys = document.get("keys")
        if not isinstance(keys, list) or not keys:
            raise OidcError(f"JWKS document for {definition.name} has no keys")
        typed = [key for key in keys if isinstance(key, dict)]
        self._jwks_cache[definition.name] = (typed, self._monotonic() + JWKS_CACHE_TTL_SECONDS)
        return typed

    # ---------------------------------------------------------------- GitHub
    def _github_identity(
        self, definition: ProviderDefinition, token_response: dict[str, Any]
    ) -> dict[str, Any]:
        access_token = str(token_response.get("access_token") or "")
        if not access_token:
            raise OidcError("GitHub token response carried no access_token")
        user = self._github_get(definition, access_token, "/user")
        if not isinstance(user, dict) or user.get("id") is None:
            raise OidcError("GitHub /user response is malformed")
        emails = self._github_get(definition, access_token, "/user/emails")
        if not isinstance(emails, list):
            raise OidcError("GitHub /user/emails response is malformed")
        verified_primary: Optional[str] = None
        for entry in emails:
            if not isinstance(entry, dict):
                continue
            if bool(entry.get("primary")) and bool(entry.get("verified")) and entry.get("email"):
                verified_primary = str(entry["email"]).strip().lower()
                break
        if not verified_primary:
            # Design rule (release gate): unverified GitHub emails never create
            # or link accounts — reject the whole login.
            raise OidcError("GitHub account has no verified primary email")
        return {
            "subject": str(user["id"]),  # immutable numeric id, never the login name
            "email": verified_primary,
            "email_verified": True,
            "display_name": str(user.get("name") or user.get("login") or ""),
        }

    def _github_get(self, definition: ProviderDefinition, access_token: str, path: str) -> Any:
        response = self._transport(
            {
                "method": "GET",
                "url": f"{definition.api_base_url.rstrip('/')}{path}",
                "headers": {
                    "Authorization": f"Bearer {access_token}",
                    "Accept": "application/vnd.github+json",
                    "User-Agent": "cortex-auth",
                },
                "body": None,
                "timeout": DEFAULT_HTTP_TIMEOUT_SECONDS,
            }
        )
        if int(response.get("status") or 0) != 200:
            raise OidcError(f"GitHub {path} request failed (HTTP {response.get('status')})")
        try:
            return json.loads(bytes(response.get("body") or b"").decode("utf-8"))
        except (UnicodeDecodeError, ValueError) as exc:
            raise OidcError(f"GitHub {path} response is not JSON") from exc


def jwk_from_rsa_public_numbers(n: int, e: int, *, kid: str) -> dict[str, Any]:
    """Build a JWKS entry from RSA public numbers (used by tests to publish a
    fake provider JWKS for a locally generated keypair)."""
    return {
        "kty": "RSA",
        "alg": "RS256",
        "use": "sig",
        "kid": kid,
        "n": _b64url_uint(n),
        "e": _b64url_uint(e),
    }


__all__ = [
    "DEFAULT_STATE_TTL_SECONDS",
    "GOOGLE_ACCEPTED_ISSUERS",
    "OAUTH_FLOW_KIND",
    "OidcError",
    "OidcProviderRegistry",
    "ProviderDefinition",
    "UnknownSigningKeyError",
    "definitions_from_settings",
    "jwk_from_rsa_public_numbers",
    "urllib_transport",
    "verify_id_token",
]
