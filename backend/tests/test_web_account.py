"""Server-rendered web account front-door tests (backend/app/webauth.py).

Boots the real FastAPI app in auth-enabled hosted mode using the same
monkeypatch discipline as test_auth_http.AuthEnabledTestCase (swap
main_module.settings/store + init_auth_runtime, random KEK, throwaway tree).

Covers the served MARKUP and route gating only — there is no JS engine here, so
browser behavior is out of scope:

- every /account* page returns 200 text/html when auth_enabled, 404 when not
- the login page renders a "Continue with X" button only for providers that
  GET /v1/auth/providers reports as configured
- the pages reference the correct /v1/auth endpoint paths
- the signup page includes the required ToS/age checkbox
- the external JS is served at /account/app.js with a JS content type
- no server-side 500s on any route
"""

from __future__ import annotations

import base64
import os
import secrets
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module

ACCOUNT_PAGES = (
    "/account",
    "/account/login",
    "/account/signup",
    "/account/verify",
    "/account/reset",
    "/account/home",
    "/account/oauth/complete",
)


class WebAccountAuthEnabledTests(unittest.TestCase):
    """Auth-enabled hosted settings against a throwaway tree (copied setUp
    pattern from test_auth_http.AuthEnabledTestCase)."""

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

    def test_every_account_page_returns_200_html(self) -> None:
        for path in ACCOUNT_PAGES:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200, f"{path}: {response.text[:200]}")
            self.assertTrue(
                response.headers["content-type"].startswith("text/html"),
                f"{path}: {response.headers['content-type']}",
            )
            self.assertIn("<!doctype html>", response.text.lower())
            # Route-scoped CSP is present and permits same-origin script + fetch.
            csp = response.headers.get("content-security-policy", "")
            self.assertIn("script-src 'self'", csp)
            self.assertIn("connect-src 'self'", csp)

    def test_app_js_served_as_javascript(self) -> None:
        response = self.client.get("/account/app.js")
        self.assertEqual(response.status_code, 200)
        self.assertIn("javascript", response.headers["content-type"])
        # The JS drives the real endpoints via fetch().
        for path in (
            "/v1/auth/login",
            "/v1/auth/signup",
            "/v1/auth/verify-email",
            "/v1/auth/password/reset/request",
            "/v1/auth/password/reset/confirm",
            "/v1/auth/session",
            "/v1/auth/refresh",
            "/v1/auth/logout",
            "/v1/auth/tokens",
            "/v1/auth/account",
        ):
            self.assertIn(path, response.text, path)
        # Pages carry no inline <script> — the JS lives here, so the strict
        # global CSP can stay put.
        for page in ACCOUNT_PAGES:
            self.assertNotIn("<script>", self.client.get(page).text.lower())

    def test_login_page_points_at_login_endpoint_and_signup_link(self) -> None:
        html = self.client.get("/account/login").text
        self.assertIn('action="/v1/auth/login"', html)
        self.assertIn('autocomplete="email"', html)
        self.assertIn('autocomplete="current-password"', html)
        self.assertIn("/account/signup", html)
        self.assertIn("/account/reset", html)

    def test_login_renders_provider_buttons_only_when_configured(self) -> None:
        # Both providers are configured in setUp -> both buttons appear and link
        # to the provider's /start endpoint.
        html = self.client.get("/account/login").text
        self.assertIn("/v1/auth/oauth/google/start", html)
        self.assertIn("/v1/auth/oauth/github/start", html)
        self.assertIn("Continue with Google", html)
        self.assertIn("Continue with GitHub", html)
        # OpenAI is a disabled registry row -> never rendered.
        self.assertNotIn("/v1/auth/oauth/openai/start", html)

    def test_login_omits_button_for_unconfigured_provider(self) -> None:
        # Reboot with Google unconfigured: its button must disappear while
        # GitHub's remains.
        main_module.settings = replace(
            self.settings, oidc_google_client_id="", oidc_google_client_secret=""
        )
        main_module.store = main_module.StoreRegistry.from_settings(main_module.settings)
        main_module.init_auth_runtime()
        html = self.client.get("/account/login").text
        self.assertNotIn("/v1/auth/oauth/google/start", html)
        self.assertNotIn("Continue with Google", html)
        self.assertIn("/v1/auth/oauth/github/start", html)

    def test_signup_page_requires_tos_age_checkbox(self) -> None:
        html = self.client.get("/account/signup").text
        self.assertIn('action="/v1/auth/signup"', html)
        # The age/ToS checkbox exists and is required.
        self.assertIn('id="tos"', html)
        self.assertIn('type="checkbox"', html)
        # The checkbox input carries the HTML `required` attribute.
        checkbox = html[html.index('id="tos"') - 40 : html.index('id="tos"') + 60]
        self.assertIn("required", checkbox)
        self.assertIn("16+", html)
        self.assertIn("/terms", html)
        self.assertIn("/privacy", html)
        self.assertIn('autocomplete="new-password"', html)

    def test_reset_page_points_at_reset_endpoints(self) -> None:
        html = self.client.get("/account/reset").text
        self.assertIn('action="/v1/auth/password/reset/request"', html)
        self.assertIn('action="/v1/auth/password/reset/confirm"', html)

    def test_home_page_references_token_and_delete_endpoints(self) -> None:
        html = self.client.get("/account/home").text
        # The dashboard scaffold references token minting/listing + delete +
        # download + logout affordances.
        self.assertIn("mint-api", html)
        self.assertIn("mint-mcp", html)
        self.assertIn("delete-account", html)
        self.assertIn("/download", html)
        self.assertIn("save this now", html.lower())

    def test_no_server_side_500s(self) -> None:
        for path in ACCOUNT_PAGES + ("/account/app.js",):
            self.assertLess(self.client.get(path).status_code, 500, path)


class WebAccountAuthDisabledTests(unittest.TestCase):
    """Default boot (no auth env): the /account* front-door must be absent
    (404), exactly like the /v1/auth JSON API."""

    def setUp(self) -> None:
        self.client = TestClient(main_module.app)

    def test_account_pages_absent_when_disabled(self) -> None:
        self.assertIsNone(main_module.auth_runtime)
        for path in ACCOUNT_PAGES + ("/account/app.js",):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 404, f"{path}: {response.text[:120]}")
            self.assertEqual(response.json(), {"detail": "Not Found"}, path)


if __name__ == "__main__":
    unittest.main()
