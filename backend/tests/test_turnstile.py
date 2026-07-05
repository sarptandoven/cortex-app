"""Cloudflare Turnstile on public signup (H7).

Turnstile is DORMANT until BOTH CORTEX_TURNSTILE_SITEKEY and
CORTEX_TURNSTILE_SECRET are set. These tests cover:

- signup page markup: the widget + remote api.js script appear ONLY when enabled;
  the signup page is byte-identical to today when disabled.
- route-scoped CSP: the signup page's Content-Security-Policy allows
  challenges.cloudflare.com (script/frame/connect) ONLY when enabled; other pages
  keep the strict CSP.
- server-side gate: with Turnstile enabled a valid token passes signup; a missing
  or invalid token is rejected (400/403) BEFORE the account is ever created; with
  Turnstile disabled signup works with no token (no-op).

The siteverify HTTP call is monkeypatched (urllib.request.urlopen) so no network
is touched; the argon2 signup path runs against the real FastAPI app in
auth-enabled hosted mode (same discipline as test_web_account / test_billing).
"""

from __future__ import annotations

import base64
import io
import json
import os
import secrets
import tempfile
import unittest
import urllib.request
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module

SITE_KEY = "0x4AAAAAAA_test_sitekey"
SECRET = "0x4AAAAAAA_test_secret"
PASSWORD = "correct-horse-battery"
CF_ORIGIN = "https://challenges.cloudflare.com"


class _FakeSiteverifyResponse:
    """Context-manager stand-in for the urlopen return value."""

    def __init__(self, payload: dict) -> None:
        self._buf = io.BytesIO(json.dumps(payload).encode("utf-8"))

    def read(self) -> bytes:
        return self._buf.read()

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


class _HostedAppTestCase(unittest.TestCase):
    """Auth-enabled hosted app; subclasses set `extra` settings overrides."""

    extra: dict = {}

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
            shard_mode="user",
            default_user_id="hosted-default",
            require_scoped_api_tokens=True,
            auth_enabled=True,
            accounts_db_path=None,
            auth_email_mode="log",
            auth_rate_limit_per_minute=0,
            auth_access_ttl_seconds=0,
            auth_refresh_idle_ttl_seconds=0,
            auth_refresh_absolute_ttl_seconds=0,
            oidc_google_client_id="",
            oidc_google_client_secret="",
            oidc_github_client_id="",
            oidc_github_client_secret="",
            public_app_url="http://127.0.0.1:8766",
            **self.extra,
        )
        main_module.settings = hosted
        main_module.store = main_module.StoreRegistry.from_settings(hosted)
        main_module.init_auth_runtime()
        self.settings = hosted
        self.runtime = main_module.auth_runtime
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

    def _account_exists(self, email: str) -> bool:
        acct = self.runtime.control_store.get_account_by_email(email.strip().lower())
        return acct is not None


class TurnstileDisabledTests(_HostedAppTestCase):
    """No turnstile env: the page + handler are byte-identical to today."""

    extra = {"turnstile_site_key": "", "turnstile_secret": ""}

    def test_settings_turnstile_disabled(self) -> None:
        self.assertFalse(self.settings.turnstile_enabled)

    def test_signup_page_has_no_widget_or_cf_csp(self) -> None:
        resp = self.client.get("/account/signup")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertNotIn("cf-turnstile", resp.text)
        self.assertNotIn(CF_ORIGIN, resp.text)
        csp = resp.headers.get("content-security-policy", "")
        self.assertNotIn(CF_ORIGIN, csp)
        self.assertIn("script-src 'self'", csp)

    def test_signup_succeeds_with_no_token(self) -> None:
        email = "nobot@example.com"
        resp = self.client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(self._account_exists(email))


class TurnstileEnabledTests(_HostedAppTestCase):
    """Turnstile configured: page carries the widget + widened CSP, and the
    signup handler verifies the token server-side before hashing."""

    extra = {"turnstile_site_key": SITE_KEY, "turnstile_secret": SECRET}

    def setUp(self) -> None:
        super().setUp()
        self._orig_urlopen = urllib.request.urlopen
        self._siteverify_calls: list = []

    def tearDown(self) -> None:
        urllib.request.urlopen = self._orig_urlopen
        super().tearDown()

    def _patch_siteverify(self, *, success: bool) -> None:
        calls = self._siteverify_calls

        def fake_urlopen(req, *args, **kwargs):
            # Capture the posted secret/response for assertions.
            body = req.data.decode("ascii") if getattr(req, "data", None) else ""
            calls.append({"url": req.full_url, "body": body})
            payload = {"success": success}
            if not success:
                payload["error-codes"] = ["invalid-input-response"]
            return _FakeSiteverifyResponse(payload)

        urllib.request.urlopen = fake_urlopen

    # ----------------------------------------------------------- page markup
    def test_settings_turnstile_enabled(self) -> None:
        self.assertTrue(self.settings.turnstile_enabled)

    def test_signup_page_has_widget_and_cf_csp(self) -> None:
        resp = self.client.get("/account/signup")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("cf-turnstile", resp.text)
        self.assertIn(f'data-sitekey="{SITE_KEY}"', resp.text)
        self.assertIn(CF_ORIGIN + "/turnstile/v0/api.js", resp.text)
        csp = resp.headers.get("content-security-policy", "")
        self.assertIn(f"script-src 'self' {CF_ORIGIN}", csp)
        self.assertIn(f"frame-src {CF_ORIGIN}", csp)
        self.assertIn(f"connect-src 'self' {CF_ORIGIN}", csp)

    def test_only_signup_page_gets_the_widened_csp(self) -> None:
        # Login (and every other /account page) keep the strict CSP.
        for path in ("/account/login", "/account/reset", "/account/home"):
            csp = self.client.get(path).headers.get("content-security-policy", "")
            self.assertNotIn(CF_ORIGIN, csp, path)

    # -------------------------------------------------------- server-side gate
    def test_valid_token_passes_signup(self) -> None:
        self._patch_siteverify(success=True)
        email = "human@example.com"
        resp = self.client.post(
            "/v1/auth/signup",
            json={"email": email, "password": PASSWORD, "turnstile_token": "tok-good"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(self._account_exists(email))
        # siteverify was actually called, with our secret + the token.
        self.assertEqual(len(self._siteverify_calls), 1)
        self.assertIn("challenges.cloudflare.com/turnstile/v0/siteverify", self._siteverify_calls[0]["url"])
        self.assertIn("response=tok-good", self._siteverify_calls[0]["body"])
        self.assertIn("secret=" + SECRET, self._siteverify_calls[0]["body"])

    def test_missing_token_rejected_before_signup(self) -> None:
        self._patch_siteverify(success=True)  # would pass if reached
        email = "notoken@example.com"
        resp = self.client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
        self.assertEqual(resp.status_code, 400, resp.text)
        # No account created, and siteverify never called (rejected before hash).
        self.assertFalse(self._account_exists(email))
        self.assertEqual(self._siteverify_calls, [])

    def test_invalid_token_rejected_before_signup(self) -> None:
        self._patch_siteverify(success=False)
        email = "badtoken@example.com"
        resp = self.client.post(
            "/v1/auth/signup",
            json={"email": email, "password": PASSWORD, "turnstile_token": "tok-bad"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertFalse(self._account_exists(email))
        # siteverify was consulted (1 call) and the account was NOT created.
        self.assertEqual(len(self._siteverify_calls), 1)

    def test_siteverify_network_error_fails_closed(self) -> None:
        def boom(req, *a, **k):
            raise OSError("network down")

        urllib.request.urlopen = boom
        email = "offline@example.com"
        resp = self.client.post(
            "/v1/auth/signup",
            json={"email": email, "password": PASSWORD, "turnstile_token": "tok"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertFalse(self._account_exists(email))


if __name__ == "__main__":
    unittest.main()
