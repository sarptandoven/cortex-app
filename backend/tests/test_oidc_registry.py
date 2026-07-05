"""OIDC/OAuth2 provider registry tests (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §1/§3).

Release-gate coverage against a fully faked transport (no network):
- state is single-use and tamper-rejected; expired state rejected
- the PKCE verifier persisted at start is sent on the code exchange and the
  authorize URL carries its S256 challenge
- nonce mismatch rejected
- Google id_token verification with a real RS256 keypair + JWKS: bad iss, bad
  aud, expired, and bad signature EACH rejected; happy path verifies
- GitHub adapter: verified primary email accepted; unverified/non-primary
  rejected outright
- disabled providers (including the reserved 'openai' slot) rejected
- enabled_providers() output carries no secrets
"""

from __future__ import annotations

import base64
import hashlib
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from backend.app.accounts import SQLiteControlStore
from backend.app.oidc_registry import (
    OidcError,
    OidcProviderRegistry,
    definitions_from_settings,
    jwk_from_rsa_public_numbers,
)

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"
GITHUB_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_USER_URL = "https://api.github.com/user"
GITHUB_EMAILS_URL = "https://api.github.com/user/emails"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def make_jwt(
    claims: dict[str, Any],
    private_key: rsa.RSAPrivateKey,
    *,
    kid: str = "kid-1",
    alg: str = "RS256",
    tamper_signature: bool = False,
) -> str:
    header = _b64url(json.dumps({"alg": alg, "kid": kid, "typ": "JWT"}).encode())
    body = _b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    if tamper_signature:
        signature = bytes([signature[0] ^ 0xFF]) + signature[1:]
    return f"{header}.{body}.{_b64url(signature)}"


def _json_response(payload: Any, status: int = 200) -> dict[str, Any]:
    return {"status": status, "headers": {}, "body": json.dumps(payload).encode("utf-8")}


class FakeTransport:
    """Route-by-URL-prefix transport double; records every request."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.routes: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {}

    def __call__(self, request: dict[str, Any]) -> dict[str, Any]:
        self.requests.append(request)
        for prefix, handler in self.routes.items():
            if str(request.get("url") or "").startswith(prefix):
                return handler(request)
        raise AssertionError(f"unexpected provider URL: {request.get('url')!r}")

    def exchange_bodies(self, token_url: str) -> list[dict[str, str]]:
        from urllib.parse import parse_qs

        bodies = []
        for request in self.requests:
            if str(request.get("url") or "").startswith(token_url):
                parsed = parse_qs((request.get("body") or b"").decode("utf-8"))
                bodies.append({key: values[0] for key, values in parsed.items()})
        return bodies


def _settings(**overrides: str) -> SimpleNamespace:
    base = {
        "oidc_google_client_id": "",
        "oidc_google_client_secret": "",
        "oidc_github_client_id": "",
        "oidc_github_client_secret": "",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


class OidcRegistryTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.store = SQLiteControlStore(Path(self._tmp.name) / "accounts.sqlite")
        self.transport = FakeTransport()
        self.now = datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _clock(self) -> datetime:
        return self.now

    def _registry(self, settings: SimpleNamespace) -> OidcProviderRegistry:
        return OidcProviderRegistry(
            self.store,
            definitions_from_settings(settings),
            transport=self.transport,
            clock=self._clock,
        )


class GoogleOidcTests(OidcRegistryTestBase):
    CLIENT_ID = "google-client-id"

    def setUp(self) -> None:
        super().setUp()
        self.private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = self.private_key.public_key().public_numbers()
        self.jwks = {"keys": [jwk_from_rsa_public_numbers(numbers.n, numbers.e, kid="kid-1")]}
        self.registry = self._registry(
            _settings(
                oidc_google_client_id=self.CLIENT_ID,
                oidc_google_client_secret="google-secret",
            )
        )
        self.id_token_claims: dict[str, Any] = {}
        self.tamper_signature = False
        self.sign_with_key = self.private_key
        self.transport.routes[GOOGLE_JWKS_URL] = lambda request: _json_response(self.jwks)
        self.transport.routes[GOOGLE_TOKEN_URL] = lambda request: _json_response(
            {
                "access_token": "google-access",
                "id_token": make_jwt(
                    self.id_token_claims,
                    self.sign_with_key,
                    tamper_signature=self.tamper_signature,
                ),
            }
        )

    def _start(self) -> tuple[dict[str, Any], dict[str, Any]]:
        started = self.registry.start("google", "https://app.example/callback")
        flow = self.store.get_flow(started["state"])
        assert flow is not None
        return started, json.loads(flow["payload_json"])

    def _claims(self, payload: dict[str, Any], **overrides: Any) -> dict[str, Any]:
        claims = {
            "iss": "https://accounts.google.com",
            "aud": self.CLIENT_ID,
            "sub": "google-sub-1",
            "email": "person@example.com",
            "email_verified": True,
            "name": "Person Example",
            "exp": int(self.now.timestamp()) + 600,
            "iat": int(self.now.timestamp()),
            "nonce": payload["nonce"],
        }
        claims.update(overrides)
        return claims

    def test_happy_path_verifies_and_sends_pkce_verifier(self) -> None:
        started, payload = self._start()
        # Authorize URL carries state + the S256 challenge of the stored verifier + nonce.
        expected_challenge = _b64url(
            hashlib.sha256(payload["pkce_verifier"].encode("ascii")).digest()
        )
        self.assertIn(f"state={started['state']}", started["authorize_url"])
        self.assertIn(f"code_challenge={expected_challenge}", started["authorize_url"])
        self.assertIn("code_challenge_method=S256", started["authorize_url"])
        self.assertIn(f"nonce={payload['nonce']}", started["authorize_url"])
        self.assertNotIn("google-secret", started["authorize_url"])

        self.id_token_claims = self._claims(payload)
        identity = self.registry.complete("google", state=started["state"], code="auth-code")
        self.assertEqual(identity["subject"], "google-sub-1")
        self.assertEqual(identity["email"], "person@example.com")
        self.assertTrue(identity["email_verified"])
        self.assertEqual(identity["provider"], "google")
        self.assertIsNone(identity["app_flow_id"])

        exchanges = self.transport.exchange_bodies(GOOGLE_TOKEN_URL)
        self.assertEqual(len(exchanges), 1)
        self.assertEqual(exchanges[0]["code_verifier"], payload["pkce_verifier"])
        self.assertEqual(exchanges[0]["code"], "auth-code")
        self.assertEqual(exchanges[0]["redirect_uri"], "https://app.example/callback")

    def test_state_is_single_use(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload)
        self.registry.complete("google", state=started["state"], code="auth-code")
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_tampered_or_unknown_state_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload)
        with self.assertRaises(OidcError):
            self.registry.complete("google", state="totally-forged-state", code="auth-code")
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"] + "x", code="auth-code")
        # The genuine state still works after the tamper attempts (they never consumed it).
        self.registry.complete("google", state=started["state"], code="auth-code")

    def test_state_from_other_provider_rejected(self) -> None:
        registry = self._registry(
            _settings(
                oidc_google_client_id=self.CLIENT_ID,
                oidc_google_client_secret="google-secret",
                oidc_github_client_id="github-client-id",
                oidc_github_client_secret="github-secret",
            )
        )
        self.transport.routes[GOOGLE_JWKS_URL] = lambda request: _json_response(self.jwks)
        github_started = registry.start("github", "https://app.example/callback")
        with self.assertRaises(OidcError):
            registry.complete("google", state=github_started["state"], code="auth-code")

    def test_expired_state_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload, exp=int(self.now.timestamp()) + 4000)
        self.now = self.now + timedelta(minutes=11)  # past the ~10 min state TTL
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_nonce_mismatch_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload, nonce="attacker-chosen-nonce")
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_bad_issuer_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload, iss="https://evil.example")
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_bad_audience_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload, aud="some-other-client")
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_expired_id_token_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload, exp=int(self.now.timestamp()) - 1)
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_bad_signature_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload)
        self.tamper_signature = True
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_signature_from_wrong_key_rejected(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload)
        # Signed by a different keypair while claiming the same kid.
        self.sign_with_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        with self.assertRaises(OidcError):
            self.registry.complete("google", state=started["state"], code="auth-code")

    def test_unverified_google_email_is_not_trusted(self) -> None:
        started, payload = self._start()
        self.id_token_claims = self._claims(payload, email_verified=False)
        identity = self.registry.complete("google", state=started["state"], code="auth-code")
        self.assertFalse(identity["email_verified"])
        self.assertEqual(identity["email"], "person@example.com")


class GitHubAdapterTests(OidcRegistryTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.registry = self._registry(
            _settings(
                oidc_github_client_id="github-client-id",
                oidc_github_client_secret="github-secret",
            )
        )
        self.emails: list[dict[str, Any]] = []
        self.transport.routes[GITHUB_TOKEN_URL] = lambda request: _json_response(
            {"access_token": "gh-access", "token_type": "bearer"}
        )
        self.transport.routes[GITHUB_EMAILS_URL] = lambda request: _json_response(self.emails)
        self.transport.routes[GITHUB_USER_URL] = lambda request: _json_response(
            {"id": 4242, "login": "octocat", "name": "Octo Cat"}
        )

    def _complete(self) -> dict[str, Any]:
        started = self.registry.start("github", "https://app.example/callback")
        return self.registry.complete("github", state=started["state"], code="gh-code")

    def test_verified_primary_email_accepted(self) -> None:
        self.emails = [
            {"email": "SECONDARY@example.com", "primary": False, "verified": True},
            {"email": "Primary@Example.com", "primary": True, "verified": True},
        ]
        identity = self._complete()
        self.assertEqual(identity["subject"], "4242")  # immutable numeric id, not the login
        self.assertEqual(identity["email"], "primary@example.com")
        self.assertTrue(identity["email_verified"])
        self.assertEqual(identity["display_name"], "Octo Cat")
        # PKCE verifier rides the GitHub exchange too.
        exchange = self.transport.exchange_bodies(GITHUB_TOKEN_URL)[0]
        self.assertTrue(exchange["code_verifier"])

    def test_unverified_primary_email_rejected(self) -> None:
        self.emails = [{"email": "primary@example.com", "primary": True, "verified": False}]
        with self.assertRaises(OidcError):
            self._complete()

    def test_verified_but_non_primary_only_rejected(self) -> None:
        self.emails = [{"email": "side@example.com", "primary": False, "verified": True}]
        with self.assertRaises(OidcError):
            self._complete()

    def test_no_emails_rejected(self) -> None:
        self.emails = []
        with self.assertRaises(OidcError):
            self._complete()


class RegistryPolicyTests(OidcRegistryTestBase):
    def test_openai_placeholder_is_disabled(self) -> None:
        registry = self._registry(
            _settings(
                oidc_google_client_id="google-client-id",
                oidc_google_client_secret="google-secret",
            )
        )
        # The reserved AI-vendor slot exists in the table but can never start a flow.
        self.assertIn("openai", registry.providers)
        self.assertFalse(registry.providers["openai"].enabled)
        with self.assertRaises(OidcError):
            registry.start("openai", "https://app.example/callback")
        with self.assertRaises(OidcError):
            registry.complete("openai", state="anything", code="anything")

    def test_unconfigured_and_unknown_providers_rejected(self) -> None:
        registry = self._registry(_settings())  # nothing configured
        for provider in ("google", "github", "gitlab", ""):
            with self.assertRaises(OidcError):
                registry.start(provider, "https://app.example/callback")

    def test_enabled_requires_both_id_and_secret(self) -> None:
        registry = self._registry(_settings(oidc_google_client_id="id-but-no-secret"))
        self.assertEqual(registry.enabled_providers(), [])
        with self.assertRaises(OidcError):
            registry.start("google", "https://app.example/callback")

    def test_enabled_providers_carries_no_secrets(self) -> None:
        registry = self._registry(
            _settings(
                oidc_google_client_id="google-client-id",
                oidc_google_client_secret="google-secret-value",
                oidc_github_client_id="github-client-id",
                oidc_github_client_secret="github-secret-value",
            )
        )
        rows = registry.enabled_providers()
        self.assertEqual([row["provider"] for row in rows], ["google", "github"])
        serialized = json.dumps(rows)
        self.assertNotIn("secret", serialized)
        self.assertNotIn("google-secret-value", serialized)
        self.assertNotIn("github-secret-value", serialized)
        self.assertNotIn("client_id", serialized)
        for row in rows:
            self.assertEqual(
                sorted(row.keys()), ["button_order", "display_name", "kind", "provider"]
            )


if __name__ == "__main__":
    unittest.main()
