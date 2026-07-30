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
            legal_terms_approved=True,
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
            sync_signing_key="test-signing-key-abc123",
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

    def test_app_login_handoff_opens_account_login_and_threads_app_flow(self) -> None:
        # Regression (was broken in prod): the desktop "Continue with <provider>" handoff must open
        # the REAL web login page (/account/login) — /login 404s — and thread app_flow into each
        # OAuth start URL so the callback completes the flow server-side (else the app never gets
        # signed in and GitHub/Google sign-in silently fails).
        start = self.client.post("/v1/auth/app/start", json={})
        self.assertEqual(start.status_code, 200, start.text)
        body = start.json()
        flow_id = body["flow_id"]
        burl = body["browser_url"]
        self.assertTrue(burl.endswith(f"/account/login?app_flow={flow_id}"), burl)
        self.assertNotIn("8766/login?", burl)  # never the bare /login (404)
        # The login page rendered WITH that app_flow threads it into every provider start URL.
        page = self.client.get(f"/account/login?app_flow={flow_id}").text
        self.assertIn(f"/v1/auth/oauth/github/start?app_flow={flow_id}", page)
        self.assertIn(f"/v1/auth/oauth/google/start?app_flow={flow_id}", page)
        # Normal web sign-in (no app_flow) leaves the buttons unadorned.
        plain = self.client.get("/account/login").text
        self.assertIn('/v1/auth/oauth/github/start"', plain)
        self.assertNotIn("start?app_flow=", plain)

    def test_provider_one_hop_redirects_configured_provider(self) -> None:
        # The desktop app's "Sign in with GitHub" button opens
        # /account/login?app_flow=...&provider=github. A CONFIGURED provider one-hops: a 302 straight
        # to that provider's OAuth start (with app_flow), and the df_af binding cookie is set on the
        # redirect so the flow-fixation guard still passes.
        start = self.client.post("/v1/auth/app/start", json={})
        flow_id = start.json()["flow_id"]
        resp = self.client.get(
            f"/account/login?app_flow={flow_id}&provider=github", follow_redirects=False
        )
        self.assertEqual(resp.status_code, 302, resp.text)
        self.assertEqual(resp.headers["location"], f"/v1/auth/oauth/github/start?app_flow={flow_id}")
        self.assertIn("df_af", resp.headers.get("set-cookie", ""))

    def test_provider_one_hop_ignores_unknown_or_unbound_provider(self) -> None:
        # An UNKNOWN provider (open-redirect guard) or a provider without an app_flow must NOT
        # redirect — the normal login page renders instead.
        start = self.client.post("/v1/auth/app/start", json={})
        flow_id = start.json()["flow_id"]
        bogus = self.client.get(
            f"/account/login?app_flow={flow_id}&provider=evilcorp", follow_redirects=False
        )
        self.assertEqual(bogus.status_code, 200, "unknown provider must not redirect")
        no_flow = self.client.get("/account/login?provider=github", follow_redirects=False)
        self.assertEqual(no_flow.status_code, 200, "provider without app_flow must not redirect")

    def test_app_login_flow_binding_cookie_prevents_login_csrf(self) -> None:
        # SECURITY (login-CSRF / flow fixation): the OAuth start must only attach app_flow to the
        # authenticating account when the caller holds the signed df_af cookie the /account/login page
        # set. Without it, app_flow is dropped so a victim's OAuth completion can't be captured by an
        # attacker-owned poll flow.
        import json
        import re
        from backend.app import main as m

        FLOW = "flw_bindtest01"
        page = self.client.get(f"/account/login?app_flow={FLOW}")
        setc = page.headers.get("set-cookie", "")
        self.assertIn("df_af=", setc)
        self.assertIn("HttpOnly", setc)
        cookie_val = re.search(r"df_af=([^;]+)", setc).group(1)

        def app_flow_id_of(state: str):
            flow = m._auth_runtime_or_404().control_store.get_flow(state)
            self.assertIsNotNone(flow)
            return json.loads(flow.get("payload_json") or "{}").get("app_flow_id")

        # No cookie -> app_flow DROPPED (attacker's silent-link path is defeated).
        no_cookie = TestClient(m.app)
        s1 = no_cookie.get(f"/v1/auth/oauth/github/start?app_flow={FLOW}")
        self.assertEqual(s1.status_code, 200, s1.text)
        self.assertIsNone(app_flow_id_of(s1.json()["state"]))

        # Forged cookie -> also dropped.
        forged = TestClient(m.app)
        forged.cookies.set("df_af", f"{FLOW}.deadbeef")
        s2 = forged.get(f"/v1/auth/oauth/github/start?app_flow={FLOW}")
        self.assertIsNone(app_flow_id_of(s2.json()["state"]))

        # Valid server-issued cookie -> app_flow honored (the legitimate desktop handoff still works).
        good = TestClient(m.app)
        good.cookies.set("df_af", cookie_val)
        s3 = good.get(f"/v1/auth/oauth/github/start?app_flow={FLOW}")
        self.assertEqual(app_flow_id_of(s3.json()["state"]), FLOW)

    def test_app_login_ignores_malformed_app_flow(self) -> None:
        # A non-[A-Za-z0-9_] app_flow (e.g. an injection attempt) is dropped, not interpolated.
        page = self.client.get("/account/login?app_flow=abc%22%3E%3Cscript%3E").text
        self.assertNotIn("<script>", page.lower().split("</head>", 1)[-1])
        self.assertNotIn("start?app_flow=abc", page)

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
        self.assertIn("/v1/auth/oauth/google/start?signup=1", html)
        self.assertIn('class="button secondary oauth-signup"', html)
        js = self.client.get("/account/app.js").text
        self.assertIn("target.searchParams.set('terms_accepted', 'true')", js)
        self.assertIn("target.searchParams.set('age_confirmed', 'true')", js)

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

    # ---- visual identity: match the macOS app ("The Archive") + Doppl brand ----

    def test_pages_carry_route_scoped_csp_that_permits_style_and_script(self) -> None:
        # The whole reason the pages rendered unstyled in prod: a blanket upstream CSP. The APP's
        # response must always carry the relaxed route-scoped CSP so Caddy's set-default defers to it.
        for path in ACCOUNT_PAGES:
            csp = self.client.get(path).headers.get("content-security-policy", "")
            self.assertIn("style-src 'self' 'unsafe-inline'", csp, path)  # inline <style> allowed
            self.assertIn("script-src 'self'", csp, path)                 # /account/app.js allowed
            self.assertNotEqual(csp, "default-src 'none'; form-action 'self'", path)  # not the strict one

    def test_brand_is_doppl_not_cortex(self) -> None:
        # The app + admin dashboard are "Doppl"; the web front-door must match (no stale "Cortex").
        for path in ("/account/login", "/account/oauth/complete"):
            html = self.client.get(path).text
            self.assertIn(">Doppl</span>", html, path)
            self.assertNotIn(">Cortex</span>", html, path)

    def test_shared_css_uses_the_app_archive_palette(self) -> None:
        # The decisive sealing-wax-red accent + warm paper from CortexDesign.swift, so the web reads
        # as the same product as the native app.
        css = self.client.get("/account/login").text
        self.assertIn("#8c3a2b", css.lower())  # wax-red accent
        self.assertIn("#f7f4ed", css.lower())  # warm paper background

    def test_oauth_complete_is_a_centered_moment_with_a_spinner(self) -> None:
        html = self.client.get("/account/oauth/complete").text
        self.assertIn('<main class="oauth"', html)          # centered layout hook
        self.assertIn('class="spinner"', html)              # loading ring
        self.assertIn("main.oauth", html)                   # its centering CSS is present
        self.assertIn("@keyframes cortex-spin", html)       # animation defined (no external asset)

    def test_home_humanizes_account_status_and_flags_non_active(self) -> None:
        # A pending_verification user CAN log in (verification gates token minting, not basic use),
        # so they reach /account/home. Show a readable label + a warning pill, not raw green success.
        js = self.client.get("/account/app.js").text
        self.assertIn("Email not verified", js)         # humanized pending label
        self.assertIn("pill-warn", js)                  # non-active pill variant applied
        css = self.client.get("/account/home").text
        self.assertIn(".pill.pill-warn", css)           # the variant is styled

    def test_home_mint_error_surfaces_server_detail(self) -> None:
        # A pending user clicking "Mint" gets a 403 "verify your email before minting tokens"; the UI
        # must show that reason, not a generic failure.
        js = self.client.get("/account/app.js").text
        self.assertIn("data.detail", js)

    def test_page_init_js_dispatches_on_main_class_not_empty_body(self) -> None:
        # REGRESSION (frozen OAuth spinner): _page() puts the page class on <main>, but <body> has
        # none. The JS must read the class off <main> (pageClass -> querySelector('main')), or every
        # page's init silently no-ops (spinner never stops, home never loads).
        js = self.client.get("/account/app.js").text
        self.assertIn("querySelector('main')", js)
        self.assertIn("pageClass()", js)
        # And the pages actually carry the class on <main> for pageClass to read.
        for path, klass in (
            ("/account/login", "login"),
            ("/account/oauth/complete", "oauth"),
            ("/account/home", "home"),
        ):
            self.assertRegex(self.client.get(path).text, rf'<main class="{klass}[" ]')

    # ---- browser OAuth content-negotiation (the "I can't sign in" fix) ----

    def test_oauth_start_redirects_a_browser_to_the_provider(self) -> None:
        # THE bug: clicking "Continue with Google" showed raw JSON. A browser navigation
        # (Accept: text/html) must be 302-redirected to the provider's authorize URL.
        r = self.client.get(
            "/v1/auth/oauth/google/start",
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302, r.text)
        loc = r.headers["location"]
        self.assertIn("accounts.google.com", loc)
        self.assertIn("client_id=google-client-id", loc)
        self.assertIn("redirect_uri=", loc)

    def test_oauth_start_keeps_json_contract_for_the_app(self) -> None:
        # The desktop app / JSON-API client (Accept: */* or application/json) still gets the
        # {authorize_url, state} body it polls against — the app contract must not change.
        r = self.client.get(
            "/v1/auth/oauth/google/start", headers={"accept": "application/json"}
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("accounts.google.com", body["authorize_url"])
        self.assertIn("state", body)

    def test_oauth_callback_bounces_browser_to_completion_on_error(self) -> None:
        # A provider error (or a bad state) must bounce a browser to /account/oauth/complete
        # (its JS shows a friendly failure), NOT surface a raw JSON 4xx in the address bar.
        r = self.client.get(
            "/v1/auth/oauth/google/callback",
            params={"error": "access_denied"},
            headers={"accept": "text/html"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 302, r.text)
        self.assertEqual(r.headers["location"], "/account/oauth/complete")

    def test_oauth_callback_keeps_json_error_for_the_app(self) -> None:
        # The JSON-API caller keeps its HTTP error code (no redirect) so the app can react.
        r = self.client.get(
            "/v1/auth/oauth/google/callback",
            params={"error": "access_denied"},
            headers={"accept": "application/json"},
            follow_redirects=False,
        )
        self.assertEqual(r.status_code, 400, r.text)
        self.assertIn("detail", r.json())

    def test_oauth_callback_delivers_tokens_to_browser_in_url_fragment(self) -> None:
        # The success path: a browser lands back on /account/oauth/complete with the session
        # pair in the URL FRAGMENT (never the query), so tokens never reach the server log.
        from unittest import mock

        runtime = main_module._auth_runtime_or_404()
        identity = {
            "subject": "sub-123",
            "email": "signin@example.com",
            "email_verified": True,
            "display_name": "Sign In",
        }  # no app_flow_id -> plain web sign-in
        result = {"action": "login", "account": {"account_id": "acc_1", "status": "login"}}
        session = {
            "access": "AT.browser.token",
            "refresh": "RT.browser.token",
            "session_id": "sess_1",
            "account": {"account_id": "acc_1", "status": "login", "primary_email": "signin@example.com"},
        }
        with mock.patch.object(runtime.oidc, "complete", return_value=identity), mock.patch.object(
            runtime.control_store, "get_identity", return_value={"identity_id": "ident_1"}
        ), mock.patch.object(
            runtime.service, "find_or_challenge_identity", return_value=result
        ), mock.patch.object(runtime.service, "mint_session", return_value=session):
            r = self.client.get(
                "/v1/auth/oauth/google/callback",
                params={"code": "auth-code", "state": "state-token"},
                headers={"accept": "text/html"},
                follow_redirects=False,
            )
        self.assertEqual(r.status_code, 302, r.text)
        loc = r.headers["location"]
        self.assertTrue(loc.startswith("/account/oauth/complete#"), loc)
        self.assertIn("access_token=AT.browser.token", loc)
        self.assertIn("refresh_token=RT.browser.token", loc)
        # Tokens are in the FRAGMENT only — never the query string.
        self.assertNotIn("?access_token", loc)
        self.assertNotIn("access_token=AT.browser.token", loc.split("#", 1)[0])

    def test_oauth_complete_js_reads_tokens_from_url_fragment(self) -> None:
        # The success redirect puts tokens in the URL FRAGMENT (never the query), so the
        # completion JS must read location.hash — not window.location.search — for them.
        js = self.client.get("/account/app.js").text
        self.assertIn("window.location.hash", js)
        self.assertIn("access_token", js)
        self.assertIn("refresh_token", js)


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
