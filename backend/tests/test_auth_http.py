"""HTTP-level accounts/auth surface tests (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §3).

Boots the real FastAPI app in auth-enabled hosted mode (temp shard root, temp
accounts.sqlite, random KEK) by swapping main_module.settings/store and calling
init_auth_runtime() — the same monkeypatch discipline as the other hosted
suites. Covers:

- full email+password journey: signup -> verify (token captured from the
  log-mode delivery) -> login -> GET session -> refresh -> logout
- enumeration-resistant response shapes
- cxs_ works on data routes but is REJECTED by /v1/admin/* and /mcp
- activation provisions the user (registry row + usable shard, NO auto tokens)
- OAuth callback journeys against a faked transport: consented signup, login, and
  the never-silent-auto-link link_required challenge
- app start/poll handoff: poll_secret single-use, no token in any URL
- self-serve cxa_ mint (account_id linkage) that then authenticates
- invite -> claim binding a login to an EXISTING provisioned user_id
- DELETE account with a KEK configured: credential blobs become unreadable
  (ShreddedKeyError) and the session dies
- auth-disabled boot: the /v1/auth endpoints are absent (404) and existing
  behavior is unchanged
"""

from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.keyring import ShreddedKeyError
from backend.app.oidc_registry import OidcProviderRegistry, definitions_from_settings, jwk_from_rsa_public_numbers

GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_JWKS_URL = "https://www.googleapis.com/oauth2/v3/certs"

ADMIN = {"Authorization": "Bearer test-token"}
PASSWORD = "correct-horse-battery"


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _make_jwt(claims: dict[str, Any], private_key: rsa.RSAPrivateKey, kid: str = "kid-1") -> str:
    header = _b64url(json.dumps({"alg": "RS256", "kid": kid, "typ": "JWT"}).encode())
    body = _b64url(json.dumps(claims).encode())
    signing_input = f"{header}.{body}".encode("ascii")
    signature = private_key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    return f"{header}.{body}.{_b64url(signature)}"


class AuthEnabledTestCase(unittest.TestCase):
    """Base harness: auth-enabled hosted settings against a throwaway tree."""

    shard_mode = "user"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._original_settings = main_module.settings
        self._original_store = main_module.store
        self._original_runtime = main_module.auth_runtime
        self._kek_prev = os.environ.get("CORTEX_KEK")
        os.environ["CORTEX_KEK"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        hosted = replace(
            self._original_settings,
            db_path=root / "hosted.sqlite",
            vault_path=root / "hosted.vault",
            shard_root=root / "shards",
            shard_mode=self.shard_mode,
            default_user_id="hosted-default",
            require_scoped_api_tokens=True,
            legal_terms_approved=True,
            auth_enabled=True,
            accounts_db_path=None,
            auth_email_mode="log",
            auth_rate_limit_per_minute=0,  # keep test volleys un-throttled
            auth_access_ttl_seconds=0,
            auth_refresh_idle_ttl_seconds=0,
            auth_refresh_absolute_ttl_seconds=0,
            sync_signing_key="test-signing-key-abc123",
            oidc_google_client_id="google-client-id",
            oidc_google_client_secret="google-secret",
            oidc_github_client_id="github-client-id",
            oidc_github_client_secret="github-secret",
            public_app_url="http://127.0.0.1:8766",
        )
        main_module.settings = hosted
        main_module.store = main_module.StoreRegistry.from_settings(hosted)
        main_module.init_auth_runtime()
        self.settings = hosted
        self.runtime = main_module.auth_runtime
        assert self.runtime is not None
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        main_module.settings = self._original_settings
        main_module.store = self._original_store
        main_module.auth_runtime = self._original_runtime
        if self._kek_prev is None:
            os.environ.pop("CORTEX_KEK", None)
        else:
            os.environ["CORTEX_KEK"] = self._kek_prev
        self._tmp.cleanup()

    # ------------------------------------------------------------- helpers
    def _last_flow_token(self, kind: str) -> str:
        for item in reversed(self.runtime.outbox):
            if item["kind"] == kind:
                return item["token"]
        raise AssertionError(f"no {kind} delivery found in the log-mode outbox")

    def _signup_and_verify(self, email: str = "user@example.com") -> dict[str, Any]:
        response = self.client.post(
            "/v1/auth/signup",
            json={
                "email": email,
                "password": PASSWORD,
                "terms_accepted": True,
                "age_confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True, "next": "verify_email"})
        verify = self.client.post(
            "/v1/auth/verify-email", json={"token": self._last_flow_token("email_verify")}
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        account = verify.json()["account"]
        self.assertEqual(account["status"], "active")
        self.assertTrue(account["email_verified"])
        return account

    def _login(self, email: str = "user@example.com", password: str = PASSWORD) -> dict[str, Any]:
        response = self.client.post("/v1/auth/login", json={"email": email, "password": password})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_hosted_signup_requires_explicit_legal_and_age_consent(self) -> None:
        response = self.client.post(
            "/v1/auth/signup",
            json={"email": "no-consent@example.com", "password": PASSWORD},
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIsNone(
            self.runtime.control_store.get_account_by_email("no-consent@example.com")
        )

    def test_hosted_signup_is_disabled_until_legal_text_is_approved(self) -> None:
        original = main_module.settings
        main_module.settings = replace(original, legal_terms_approved=False)
        try:
            page = self.client.get("/account/signup")
            self.assertEqual(page.status_code, 503)
            response = self.client.post(
                "/v1/auth/signup",
                json={
                    "email": "legal-block@example.com",
                    "password": PASSWORD,
                    "terms_accepted": True,
                    "age_confirmed": True,
                },
            )
            self.assertEqual(response.status_code, 503, response.text)
        finally:
            main_module.settings = original

    def _bearer(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _install_fake_google(self) -> dict[str, Any]:
        """Swap the runtime's OIDC registry for one whose transport is fully
        faked; the fake signs id_tokens for whatever self.google_claims holds."""
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        numbers = private_key.public_key().public_numbers()
        jwks = {"keys": [jwk_from_rsa_public_numbers(numbers.n, numbers.e, kid="kid-1")]}
        state: dict[str, Any] = {"claims": {}}

        def transport(request: dict[str, Any]) -> dict[str, Any]:
            url = str(request.get("url") or "")
            if url.startswith(GOOGLE_JWKS_URL):
                return {"status": 200, "headers": {}, "body": json.dumps(jwks).encode()}
            if url.startswith(GOOGLE_TOKEN_URL):
                payload = {
                    "access_token": "google-access",
                    "id_token": _make_jwt(state["claims"], private_key),
                }
                return {"status": 200, "headers": {}, "body": json.dumps(payload).encode()}
            raise AssertionError(f"unexpected provider URL {url!r}")

        self.runtime.oidc = OidcProviderRegistry(
            self.runtime.control_store,
            definitions_from_settings(self.settings),
            transport=transport,
        )
        return state

    def _google_claims(self, nonce: str, **overrides: Any) -> dict[str, Any]:
        import time as _time

        claims = {
            "iss": "https://accounts.google.com",
            "aud": "google-client-id",
            "sub": "google-sub-1",
            "email": "oauth.person@example.com",
            "email_verified": True,
            "name": "OAuth Person",
            "exp": int(_time.time()) + 600,
            "nonce": nonce,
        }
        claims.update(overrides)
        return claims

    def _oauth_start(
        self,
        app_flow: str | None = None,
        *,
        signup: bool = False,
    ) -> tuple[str, str]:
        params = {"app_flow": app_flow} if app_flow else {}
        if signup:
            params.update(
                {
                    "signup": "true",
                    "terms_accepted": "true",
                    "age_confirmed": "true",
                }
            )
        started = self.client.get("/v1/auth/oauth/google/start", params=params)
        self.assertEqual(started.status_code, 200, started.text)
        state = started.json()["state"]
        flow = self.runtime.control_store.get_flow(state)
        assert flow is not None
        nonce = json.loads(flow["payload_json"])["nonce"]
        self.assertNotIn("cxs_", started.json()["authorize_url"])
        return state, nonce


class EmailPasswordJourneyTests(AuthEnabledTestCase):
    def test_full_journey_signup_verify_login_session_refresh_logout(self) -> None:
        account = self._signup_and_verify()
        user_id = account["user_id"]

        # Activation provisioned the user WITHOUT auto-minting tokens.
        registry_user = main_module.store.get_user(user_id)
        self.assertIsNotNone(registry_user)
        self.assertEqual(registry_user["status"], "active")
        self.assertEqual(main_module.store.list_tokens(user_id), [])
        # Shard is materialized and usable.
        shard = main_module.store.assignment_for(user_id)
        self.assertTrue(shard.db_path.exists())

        pair = self._login()
        self.assertTrue(pair["access_token"].startswith("cxs_"))
        self.assertTrue(pair["refresh_token"].startswith("cxr_"))
        self.assertEqual(pair["account"]["account_id"], account["account_id"])

        whoami = self.client.get("/v1/auth/session", headers=self._bearer(pair["access_token"]))
        self.assertEqual(whoami.status_code, 200, whoami.text)
        self.assertEqual(whoami.json()["user_id"], user_id)
        self.assertEqual(whoami.json()["account"]["email"], "user@example.com")

        refreshed = self.client.post(
            "/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]}
        )
        self.assertEqual(refreshed.status_code, 200, refreshed.text)
        new_pair = refreshed.json()
        self.assertNotEqual(new_pair["access_token"], pair["access_token"])
        self.assertNotEqual(new_pair["refresh_token"], pair["refresh_token"])

        # Reusing the rotated refresh token revokes the whole family.
        reuse = self.client.post("/v1/auth/refresh", json={"refresh_token": pair["refresh_token"]})
        self.assertEqual(reuse.status_code, 401)
        self.assertEqual(
            self.client.get(
                "/v1/auth/session", headers=self._bearer(new_pair["access_token"])
            ).status_code,
            401,
        )

        # Fresh login, then logout kills the session.
        pair2 = self._login()
        logout = self.client.post("/v1/auth/logout", headers=self._bearer(pair2["access_token"]))
        self.assertEqual(logout.status_code, 200)
        self.assertEqual(
            self.client.get(
                "/v1/auth/session", headers=self._bearer(pair2["access_token"])
            ).status_code,
            401,
        )

    def test_autoverify_beta_mode_activates_at_signup(self) -> None:
        # deploy/ beta profile: CORTEX_AUTH_AUTOVERIFY=1 activates + provisions at signup
        # with no email server, while keeping the generic signup response shape unchanged.
        self._original_autoverify = main_module.settings.auth_autoverify
        main_module.settings = replace(main_module.settings, auth_autoverify=True)
        try:
            response = self.client.post(
                "/v1/auth/signup",
                json={
                    "email": "beta@example.com",
                    "password": PASSWORD,
                    "terms_accepted": True,
                    "age_confirmed": True,
                },
            )
            self.assertEqual(response.status_code, 200, response.text)
            # Identical generic shape to the non-autoverify path — no enumeration signal.
            self.assertEqual(response.json(), {"ok": True, "next": "verify_email"})

            # Login works immediately with no verify step, and the account is active.
            pair = self._login(email="beta@example.com")
            self.assertEqual(pair["account"]["status"], "active")
            user_id = pair["account"]["user_id"]

            # Activation provisioned the shard (so captures/ask work right away).
            registry_user = main_module.store.get_user(user_id)
            self.assertIsNotNone(registry_user)
            self.assertTrue(main_module.store.assignment_for(user_id).db_path.exists())

            whoami = self.client.get("/v1/auth/session", headers=self._bearer(pair["access_token"]))
            self.assertEqual(whoami.status_code, 200, whoami.text)
        finally:
            main_module.settings = replace(main_module.settings, auth_autoverify=self._original_autoverify)

    def test_autoverify_off_by_default_keeps_pending(self) -> None:
        # Default (public) profile: signup stays pending_verification; the safety default.
        self.assertFalse(main_module.settings.auth_autoverify)
        response = self.client.post(
            "/v1/auth/signup",
            json={
                "email": "pending@example.com",
                "password": PASSWORD,
                "terms_accepted": True,
                "age_confirmed": True,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        account = self.runtime.control_store.get_account_by_email("pending@example.com")
        self.assertEqual(account["status"], "pending_verification")

    def test_enumeration_resistant_shapes(self) -> None:
        first = self.client.post(
            "/v1/auth/signup",
            json={
                "email": "dupe@example.com",
                "password": PASSWORD,
                "terms_accepted": True,
                "age_confirmed": True,
            },
        )
        second = self.client.post(
            "/v1/auth/signup",
            json={
                "email": "dupe@example.com",
                "password": PASSWORD,
                "terms_accepted": True,
                "age_confirmed": True,
            },
        )
        self.assertEqual(first.status_code, second.status_code)
        self.assertEqual(first.json(), second.json())

        unknown = self.client.post(
            "/v1/auth/login", json={"email": "nobody@example.com", "password": PASSWORD}
        )
        wrong = self.client.post(
            "/v1/auth/login", json={"email": "dupe@example.com", "password": "wrong-password"}
        )
        self.assertEqual(unknown.status_code, 401)
        self.assertEqual(wrong.status_code, 401)
        self.assertEqual(unknown.json(), wrong.json())

        known_reset = self.client.post(
            "/v1/auth/password/reset/request", json={"email": "dupe@example.com"}
        )
        unknown_reset = self.client.post(
            "/v1/auth/password/reset/request", json={"email": "nobody@example.com"}
        )
        self.assertEqual(known_reset.status_code, unknown_reset.status_code)
        self.assertEqual(known_reset.json(), unknown_reset.json())

    def test_session_token_works_on_data_routes_but_never_admin_or_mcp(self) -> None:
        self._signup_and_verify()
        pair = self._login()
        session_headers = self._bearer(pair["access_token"])

        stats = self.client.get("/v1/stats", headers=session_headers)
        self.assertEqual(stats.status_code, 200, stats.text)

        admin_route = self.client.get("/v1/admin/users", headers=session_headers)
        self.assertIn(admin_route.status_code, (401, 403), admin_route.text)

        mcp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=session_headers,
        )
        self.assertEqual(mcp.status_code, 401, mcp.text)

    def test_password_reset_confirm_revokes_sessions(self) -> None:
        self._signup_and_verify(email="resetme@example.com")
        pair = self._login(email="resetme@example.com")
        request = self.client.post(
            "/v1/auth/password/reset/request", json={"email": "resetme@example.com"}
        )
        self.assertEqual(request.status_code, 200)
        token = self._last_flow_token("password_reset")
        confirm = self.client.post(
            "/v1/auth/password/reset/confirm",
            json={"token": token, "new_password": "brand-new-password"},
        )
        self.assertEqual(confirm.status_code, 200, confirm.text)
        # Old sessions dead; old password dead; new password works.
        self.assertEqual(
            self.client.get(
                "/v1/auth/session", headers=self._bearer(pair["access_token"])
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/v1/auth/login", json={"email": "resetme@example.com", "password": PASSWORD}
            ).status_code,
            401,
        )
        self._login(email="resetme@example.com", password="brand-new-password")
        # The reset token is single-use.
        replay = self.client.post(
            "/v1/auth/password/reset/confirm",
            json={"token": token, "new_password": "another-password"},
        )
        self.assertEqual(replay.status_code, 401)


class OAuthJourneyTests(AuthEnabledTestCase):
    def test_github_oauth_start_uses_public_app_callback(self) -> None:
        started = self.client.get("/v1/auth/oauth/github/start")
        self.assertEqual(started.status_code, 200, started.text)

        authorize_url = started.json()["authorize_url"]
        parsed = urlparse(authorize_url)
        params = parse_qs(parsed.query)
        self.assertEqual(parsed.netloc, "github.com")
        self.assertEqual(params.get("client_id"), ["github-client-id"])
        self.assertEqual(
            params.get("redirect_uri"),
            ["http://127.0.0.1:8766/v1/auth/oauth/github/callback"],
        )

    def test_login_oauth_cannot_silently_create_an_unknown_account(self) -> None:
        fake = self._install_fake_google()
        state, nonce = self._oauth_start()
        fake["claims"] = self._google_claims(
            nonce,
            sub="google-no-consent",
            email="no-oauth-consent@example.com",
        )
        callback = self.client.get(
            "/v1/auth/oauth/google/callback",
            params={"code": "auth-code", "state": state},
        )
        self.assertEqual(callback.status_code, 409, callback.text)
        self.assertIsNone(
            self.runtime.control_store.get_account_by_email(
                "no-oauth-consent@example.com"
            )
        )

    def test_consented_signup_then_login_via_google(self) -> None:
        fake = self._install_fake_google()
        providers = self.client.get("/v1/auth/providers")
        self.assertEqual(providers.status_code, 200)
        names = [row["provider"] for row in providers.json()["results"]]
        self.assertIn("google", names)
        self.assertNotIn("openai", names)

        # Unknown identity with a provider-verified email and bound consent: signup, ACTIVE.
        state, nonce = self._oauth_start(signup=True)
        fake["claims"] = self._google_claims(nonce)
        callback = self.client.get(
            "/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
        )
        self.assertEqual(callback.status_code, 200, callback.text)
        body = callback.json()
        self.assertEqual(body["action"], "signup")
        self.assertEqual(body["account"]["status"], "active")
        self.assertTrue(body["access_token"].startswith("cxs_"))
        user_id = body["account"]["user_id"]
        self.assertIsNotNone(main_module.store.get_user(user_id))

        # Same subject again: plain login onto the same account.
        state2, nonce2 = self._oauth_start()
        fake["claims"] = self._google_claims(nonce2)
        again = self.client.get(
            "/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state2}
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["action"], "login")
        self.assertEqual(again.json()["account"]["account_id"], body["account"]["account_id"])

        # State is single-use: replaying the consumed state fails generically.
        replay = self.client.get(
            "/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state2}
        )
        self.assertEqual(replay.status_code, 401)

    def test_email_collision_returns_link_required_then_links(self) -> None:
        fake = self._install_fake_google()
        account = self._signup_and_verify(email="linkme@example.com")

        state, nonce = self._oauth_start()
        fake["claims"] = self._google_claims(
            nonce, sub="google-sub-linker", email="linkme@example.com"
        )
        callback = self.client.get(
            "/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
        )
        # Never-silent-auto-link: even a verified email match only yields a challenge.
        self.assertEqual(callback.status_code, 409, callback.text)
        challenge_body = callback.json()
        self.assertEqual(challenge_body["action"], "link_required")
        self.assertNotIn("access_token", challenge_body)

        # Authenticate with the existing method, then complete the link.
        pair = self._login(email="linkme@example.com")
        linked = self.client.post(
            "/v1/auth/oauth/google/link",
            json={"challenge": challenge_body["challenge"]},
            headers=self._bearer(pair["access_token"]),
        )
        self.assertEqual(linked.status_code, 200, linked.text)
        self.assertEqual(linked.json()["identity"]["provider"], "google")

        # The linked identity now logs straight into the SAME account.
        state2, nonce2 = self._oauth_start()
        fake["claims"] = self._google_claims(
            nonce2, sub="google-sub-linker", email="linkme@example.com"
        )
        again = self.client.get(
            "/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state2}
        )
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["action"], "login")
        self.assertEqual(again.json()["account"]["account_id"], account["account_id"])

        # Unlink is allowed because a password credential remains.
        unlink = self.client.delete(
            "/v1/auth/oauth/google/unlink",
            headers=self._bearer(again.json()["access_token"]),
        )
        self.assertEqual(unlink.status_code, 200, unlink.text)

    def test_app_start_poll_handoff(self) -> None:
        fake = self._install_fake_google()
        started = self.client.post("/v1/auth/app/start")
        self.assertEqual(started.status_code, 200, started.text)
        handoff = started.json()
        flow_id, poll_secret = handoff["flow_id"], handoff["poll_secret"]
        # No token (and no poll secret) ever rides a URL.
        self.assertNotIn(poll_secret, handoff["browser_url"])
        self.assertNotIn("cxs_", handoff["browser_url"])

        pending = self.client.post(
            "/v1/auth/app/poll", json={"flow_id": flow_id, "poll_secret": poll_secret}
        )
        self.assertEqual(pending.status_code, 200)
        self.assertEqual(pending.json(), {"status": "pending"})

        # A real browser loads /account/login?app_flow=<flow_id> first, which sets the signed df_af
        # binding cookie the OAuth start now requires before it will attach app_flow (login-CSRF
        # guard). The TestClient runs over http and won't echo a Secure cookie, so set the same signed
        # value directly to simulate the browser holding it.
        from backend.app import main as _main
        self.client.cookies.set("df_af", _main._app_flow_cookie(flow_id))
        state, nonce = self._oauth_start(app_flow=flow_id, signup=True)
        authorize_url = self.runtime.control_store.get_flow(state)
        assert authorize_url is not None
        fake["claims"] = self._google_claims(nonce, sub="google-sub-app")
        callback = self.client.get(
            "/v1/auth/oauth/google/callback", params={"code": "auth-code", "state": state}
        )
        self.assertEqual(callback.status_code, 200, callback.text)
        # Callback response completes in-app: it must NOT carry tokens.
        self.assertEqual(callback.json().get("status"), "complete_in_app")
        self.assertNotIn("access_token", callback.json())

        wrong = self.client.post(
            "/v1/auth/app/poll", json={"flow_id": flow_id, "poll_secret": "guessed-secret"}
        )
        self.assertEqual(wrong.status_code, 401)

        done = self.client.post(
            "/v1/auth/app/poll", json={"flow_id": flow_id, "poll_secret": poll_secret}
        )
        self.assertEqual(done.status_code, 200, done.text)
        body = done.json()
        self.assertEqual(body["status"], "complete")
        self.assertTrue(body["access_token"].startswith("cxs_"))
        whoami = self.client.get("/v1/auth/session", headers=self._bearer(body["access_token"]))
        self.assertEqual(whoami.status_code, 200)

        # poll_secret is single-use: the pair cannot be polled out twice.
        replay = self.client.post(
            "/v1/auth/app/poll", json={"flow_id": flow_id, "poll_secret": poll_secret}
        )
        self.assertEqual(replay.status_code, 401)


class SelfServeTokenTests(AuthEnabledTestCase):
    def test_mint_list_use_and_revoke_api_token(self) -> None:
        account = self._signup_and_verify(email="minter@example.com")
        pair = self._login(email="minter@example.com")
        session_headers = self._bearer(pair["access_token"])

        minted = self.client.post(
            "/v1/auth/tokens",
            json={"audience": "api", "label": "My laptop"},
            headers=session_headers,
        )
        self.assertEqual(minted.status_code, 201, minted.text)
        token_body = minted.json()
        self.assertTrue(token_body["token"].startswith("cxa_"))
        self.assertEqual(token_body["account_id"], account["account_id"])

        # The freshly minted cxa_ authenticates on the data plane.
        stats = self.client.get("/v1/stats", headers=self._bearer(token_body["token"]))
        self.assertEqual(stats.status_code, 200, stats.text)

        # Control index row carries the account linkage.
        index_path = Path(self.settings.shard_root) / "control" / "token_index.sqlite"
        with sqlite3.connect(index_path) as conn:
            row = conn.execute(
                "SELECT account_id FROM scoped_token_index WHERE token_id = ?",
                (token_body["token_id"],),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], account["account_id"])

        listed = self.client.get("/v1/auth/tokens", headers=session_headers)
        self.assertEqual(listed.status_code, 200)
        self.assertIn(token_body["token_id"], [item["token_id"] for item in listed.json()["results"]])

        revoked = self.client.delete(
            f"/v1/auth/tokens/{token_body['token_id']}", headers=session_headers
        )
        self.assertEqual(revoked.status_code, 200, revoked.text)
        self.assertEqual(
            self.client.get("/v1/stats", headers=self._bearer(token_body["token"])).status_code,
            401,
        )

    def test_mcp_token_mint(self) -> None:
        self._signup_and_verify(email="mcp-minter@example.com")
        pair = self._login(email="mcp-minter@example.com")
        minted = self.client.post(
            "/v1/auth/tokens",
            json={"audience": "mcp"},
            headers=self._bearer(pair["access_token"]),
        )
        self.assertEqual(minted.status_code, 201, minted.text)
        token = minted.json()["token"]
        self.assertTrue(token.startswith("cxm_"))
        mcp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            headers=self._bearer(token),
        )
        self.assertEqual(mcp.status_code, 200, mcp.text)
        self.assertIn("result", mcp.json())


class InviteClaimTests(AuthEnabledTestCase):
    def test_invite_claim_binds_to_existing_user_id(self) -> None:
        provisioned = self.client.post(
            "/v1/admin/users",
            json={"user_id": "legacy-1", "api_scopes": ["read", "write"], "mcp_scopes": ["read"]},
            headers=ADMIN,
        )
        self.assertEqual(provisioned.status_code, 201, provisioned.text)
        legacy_api_token = provisioned.json()["api_token"]["token"]

        invite = self.client.post("/v1/admin/users/legacy-1/invite", headers=ADMIN)
        self.assertEqual(invite.status_code, 201, invite.text)
        claim_code = invite.json()["claim_code"]

        claim = self.client.post(
            "/v1/auth/claim",
            json={"code": claim_code, "email": "legacy@example.com", "password": PASSWORD},
        )
        self.assertEqual(claim.status_code, 200, claim.text)
        self.assertEqual(claim.json()["account"]["user_id"], "legacy-1")

        # Claim codes are single-use.
        replay = self.client.post(
            "/v1/auth/claim",
            json={"code": claim_code, "email": "other@example.com", "password": PASSWORD},
        )
        self.assertEqual(replay.status_code, 401)

        verify = self.client.post(
            "/v1/auth/verify-email", json={"token": self._last_flow_token("email_verify")}
        )
        self.assertEqual(verify.status_code, 200, verify.text)
        pair = self._login(email="legacy@example.com")
        self.assertEqual(pair["account"]["user_id"], "legacy-1")

        # Session lands on the SAME shard/user; legacy operator token still works.
        whoami = self.client.get("/v1/auth/session", headers=self._bearer(pair["access_token"]))
        self.assertEqual(whoami.json()["user_id"], "legacy-1")
        legacy_stats = self.client.get("/v1/stats", headers=self._bearer(legacy_api_token))
        self.assertEqual(legacy_stats.status_code, 200, legacy_stats.text)

        # A user that already has an account cannot be re-invited.
        again = self.client.post("/v1/admin/users/legacy-1/invite", headers=ADMIN)
        self.assertEqual(again.status_code, 409)


class AccountDeletionTests(AuthEnabledTestCase):
    def test_delete_account_crypto_shreds_credentials_and_kills_session(self) -> None:
        self._signup_and_verify(email="doomed@example.com")
        pair = self._login(email="doomed@example.com")
        user_id = pair["account"]["user_id"]
        keyring = self.runtime.keyring
        assert keyring is not None and keyring.available

        # Store a connector credential; hosted mode encrypts it at rest.
        vault = main_module.store.store_for_user(user_id).vault
        vault.write_source_credential(
            user_id=user_id,
            source_account_id="acct-1",
            source="github",
            payload={"token": "super-secret-connector-token"},
        )
        raw = json.loads(vault.credentials_path.read_text(encoding="utf-8"))
        record = raw["users"][user_id]["acct-1"]
        self.assertIn("payload_cxe1", record)
        self.assertNotIn("payload", record)
        blob = bytes.fromhex(record["payload_cxe1"])
        self.assertEqual(
            json.loads(keyring.decrypt_blob(user_id, "credentials", blob))["token"],
            "super-secret-connector-token",
        )

        # Step-up is enforced: no/wrong password is rejected, nothing deleted.
        refused = self.client.request(
            "DELETE", "/v1/auth/account", headers=self._bearer(pair["access_token"])
        )
        self.assertEqual(refused.status_code, 401)
        wrong = self.client.request(
            "DELETE",
            "/v1/auth/account",
            json={"password": "not-the-password"},
            headers=self._bearer(pair["access_token"]),
        )
        self.assertEqual(wrong.status_code, 401)

        deleted = self.client.request(
            "DELETE",
            "/v1/auth/account",
            json={"password": PASSWORD},
            headers=self._bearer(pair["access_token"]),
        )
        self.assertEqual(deleted.status_code, 200, deleted.text)
        report = deleted.json()
        self.assertTrue(report["deleted"])
        self.assertIsNotNone(report["crypto_shred"])

        # The credential blob is permanently unreadable (crypto-shredded).
        with self.assertRaises(ShreddedKeyError):
            keyring.decrypt_blob(user_id, "credentials", blob)

        # Session invalid; login impossible; control-plane user gone.
        self.assertEqual(
            self.client.get(
                "/v1/auth/session", headers=self._bearer(pair["access_token"])
            ).status_code,
            401,
        )
        self.assertEqual(
            self.client.post(
                "/v1/auth/login", json={"email": "doomed@example.com", "password": PASSWORD}
            ).status_code,
            401,
        )
        self.assertIsNone(main_module.store.get_user(user_id))


class EncryptionBackfillTests(AuthEnabledTestCase):
    def test_backfill_ensures_deks_and_migrates_plaintext(self) -> None:
        account = self._signup_and_verify(email="backfill@example.com")
        user_id = account["user_id"]
        vault = main_module.store.store_for_user(user_id).vault

        # Simulate a legacy plaintext credential written before encryption.
        cipher = vault.cipher
        vault.cipher = None
        try:
            vault.write_source_credential(
                user_id=user_id,
                source_account_id="legacy-acct",
                source="notion",
                payload={"token": "legacy-plaintext"},
            )
        finally:
            vault.cipher = cipher
        raw = json.loads(vault.credentials_path.read_text(encoding="utf-8"))
        self.assertIn("payload", raw["users"][user_id]["legacy-acct"])
        before = main_module._credential_encryption_evidence()
        self.assertEqual(before["remaining_plaintext"], 1)
        self.assertTrue(before["scan_complete"])

        response = self.client.post("/v1/admin/encryption/backfill", headers=ADMIN)
        self.assertEqual(response.status_code, 200, response.text)
        report = response.json()
        self.assertEqual(report["remaining_plaintext"], 0)
        self.assertGreaterEqual(report["migrated"], 1)
        self.assertGreaterEqual(report["deks_ensured"], 1)

        raw_after = json.loads(vault.credentials_path.read_text(encoding="utf-8"))
        record = raw_after["users"][user_id]["legacy-acct"]
        self.assertIn("payload_cxe1", record)
        self.assertNotIn("payload", record)
        after = main_module._credential_encryption_evidence()
        self.assertEqual(after["remaining_plaintext"], 0)
        self.assertTrue(after["scan_complete"])
        read_back = vault.read_source_credential(user_id=user_id, source_account_id="legacy-acct")
        self.assertEqual(read_back["payload"]["token"], "legacy-plaintext")

    def test_readiness_counts_plaintext_even_beside_an_envelope(self) -> None:
        account = self._signup_and_verify(email="partial-migration@example.com")
        user_id = account["user_id"]
        vault = main_module.store.store_for_user(user_id).vault
        cipher = vault.cipher
        vault.cipher = None
        try:
            vault.write_source_credential(
                user_id=user_id,
                source_account_id="partial-acct",
                source="notion",
                payload={"token": "still-plaintext"},
            )
        finally:
            vault.cipher = cipher
        raw = json.loads(vault.credentials_path.read_text(encoding="utf-8"))
        raw["users"][user_id]["partial-acct"]["payload_cxe1"] = "00"
        vault.credentials_path.write_text(json.dumps(raw), encoding="utf-8")

        evidence = main_module._credential_encryption_evidence()

        self.assertEqual(evidence["remaining_plaintext"], 1)
        self.assertGreaterEqual(evidence["invalid_records"], 1)

    def test_readiness_evidence_fails_closed_when_scan_is_truncated(self) -> None:
        account = self._signup_and_verify(email="scan-limit@example.com")
        vault = main_module.store.store_for_user(account["user_id"]).vault
        vault.write_source_credential(
            user_id=account["user_id"],
            source_account_id="encrypted-acct",
            source="notion",
            payload={"token": "encrypted"},
        )

        evidence = main_module._credential_encryption_evidence(max_files=0)

        self.assertFalse(evidence["scan_complete"])
        self.assertEqual(evidence["files_scanned"], 0)


class AuthDisabledTests(unittest.TestCase):
    """Default boot (no auth env): the auth surface must be absent and the
    existing behavior untouched."""

    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def test_auth_endpoints_absent_when_disabled(self) -> None:
        self.assertIsNone(main_module.auth_runtime)
        for method, path in (
            ("POST", "/v1/auth/signup"),
            ("POST", "/v1/auth/verify-email"),
            ("POST", "/v1/auth/login"),
            ("POST", "/v1/auth/refresh"),
            ("POST", "/v1/auth/logout"),
            ("GET", "/v1/auth/session"),
            ("GET", "/v1/auth/providers"),
            ("GET", "/v1/auth/oauth/google/start"),
            ("GET", "/v1/auth/oauth/google/callback"),
            ("POST", "/v1/auth/app/start"),
            ("POST", "/v1/auth/app/poll"),
            ("GET", "/v1/auth/tokens"),
            ("POST", "/v1/auth/claim"),
            ("DELETE", "/v1/auth/account"),
        ):
            response = self.client.request(method, path, json={})
            self.assertEqual(response.status_code, 404, f"{method} {path}: {response.text}")
            self.assertEqual(response.json(), {"detail": "Not Found"}, f"{method} {path}")
        # Admin-side auth endpoints are equally absent, even with the admin key.
        invite = self.client.post("/v1/admin/users/someone/invite", headers=ADMIN)
        self.assertEqual(invite.status_code, 404)
        backfill = self.client.post("/v1/admin/encryption/backfill", headers=ADMIN)
        self.assertEqual(backfill.status_code, 404)

    def test_existing_surface_unchanged(self) -> None:
        health = self.client.get("/health")
        self.assertEqual(health.status_code, 200)
        stats = self.client.get("/v1/stats", headers=ADMIN)
        self.assertEqual(stats.status_code, 200)
        # cxs_-prefixed bearers still fail exactly like any unknown token.
        bogus = self.client.get("/v1/stats", headers={"Authorization": "Bearer cxs_bogus"})
        self.assertEqual(bogus.status_code, 401)


if __name__ == "__main__":
    unittest.main()
