"""Server-rendered web account front-door for the Cortex hosted plane.

The hosted backend already exposes the full ``/v1/auth`` JSON surface (signup,
verify-email, login, refresh, logout, session, password reset, OAuth
start/callback, self-serve token mint/list/revoke, account delete). This module
adds the missing *browser* pages so a real person can sign up, log in, and
manage their account/tokens — the onboarding front-door.

Design constraints (see the task brief and docs/ACCOUNTS_ENCRYPTION_DESIGN.md):

- Dependency-free: minimal HTML + CSS + vanilla JS. No frontend framework, no
  build step, no new Python deps. Pages call the EXISTING ``/v1/auth`` JSON
  endpoints via ``fetch()``.
- Served ONLY when ``settings.auth_enabled`` — every ``/account*`` route answers
  exactly like the ``/v1/auth`` API (404 Not Found) when auth is disabled. This
  is enforced by routing every handler through the same ``runtime_or_404``
  callable main.py uses.
- Visual style reuses site/styles.css tokens (the same palette + Inter font) so
  the pages read as the same product.
- CSP: the global Caddy CSP is strict (``default-src 'none'; form-action
  'self'``). Rather than relax it globally, JavaScript lives in an external
  ``/account/app.js`` route (never inline in markup) and every ``/account*``
  page sends a route-scoped ``Content-Security-Policy`` header permitting
  ``script-src 'self'``, ``style-src 'self' 'unsafe-inline'`` (one small inline
  ``<style>`` block per page), ``connect-src 'self'`` (fetch to /v1/auth) and
  ``form-action 'self'``. Markup stays clean; the global strict CSP is
  unchanged.
- Every interpolated value is HTML-escaped. ``autocomplete`` attributes are set
  correctly (email / current-password / new-password) so password managers
  work. No inline secrets.
- Cloudflare Turnstile (bot/DoS protection on signup) is DORMANT until
  configured. When ``settings.turnstile_enabled`` the signup page (and ONLY the
  signup page) embeds the Turnstile widget — the remote ``api.js`` script + a
  ``<div class="cf-turnstile">`` carrying the site key — and its route-scoped
  CSP is extended to allow ``script-src``/``frame-src``/``connect-src
  https://challenges.cloudflare.com``. Every other page and the global site CSP
  are untouched; with Turnstile off the signup page is byte-identical to today.
"""

from __future__ import annotations

import html
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response

# Route-scoped CSP: relaxed enough for external JS + fetch to the same origin
# and a single inline <style> block, but far tighter than "unsafe-eval"/remote
# origins. The global Caddy CSP (default-src 'none') stays untouched.
_ACCOUNT_CSP = (
    "default-src 'none'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "connect-src 'self'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'"
)

# The Cloudflare origin that serves the Turnstile widget script + challenge
# frame. Only whitelisted on the signup page, only when Turnstile is enabled.
_TURNSTILE_ORIGIN = "https://challenges.cloudflare.com"
_TURNSTILE_API_JS = _TURNSTILE_ORIGIN + "/turnstile/v0/api.js"

# The signup-page CSP with Turnstile allowances folded into the relevant
# directives. Extends (never relaxes) _ACCOUNT_CSP: script + frame + connect to
# the Cloudflare challenge origin. Used ONLY for /account/signup when enabled.
_SIGNUP_CSP_TURNSTILE = (
    "default-src 'none'; "
    f"script-src 'self' {_TURNSTILE_ORIGIN}; "
    "style-src 'self' 'unsafe-inline'; "
    f"connect-src 'self' {_TURNSTILE_ORIGIN}; "
    f"frame-src {_TURNSTILE_ORIGIN}; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'"
)

# Palette + font pulled straight from site/styles.css so the pages match the
# marketing site. Kept inline (one <style> block) to avoid a shared-asset fetch
# and to survive the strict global CSP without a stylesheet origin.
_SHARED_CSS = """
:root {
  color-scheme: light;
  /* "The Archive" palette — matched to the macOS app (CortexDesign.swift): iron-gall ink on
     warm paper, one decisive sealing-wax-red accent, banker's-lamp moss for the local/success
     mark. Keeps the web front-door visually identical to the app users already know. */
  --ink: #2b2620;          /* iron-gall ink — warm near-black */
  --muted: #5c554b;        /* secondary ink */
  --faint: #7a7166;        /* faint ink — mono/disabled */
  --line: #e3dcce;         /* warm hairline */
  --paper: #f7f4ed;        /* warm paper — the desk blotter */
  --paper-strong: #fefdfa; /* index-card white */
  --quiet: #f1ede3;        /* quiet fill — notes, token boxes */
  --accent: #8c3a2b;       /* sealing-wax red — the decisive accent */
  --accent-soft: #f1e2dc;  /* wax red at rest — soft rose fill */
  --moss: #4f6043;         /* banker's-lamp moss — local / success mark */
  --coral: #a8412f;        /* destructive red */
  --gold: #b58121;         /* index-tab gold */
  --shadow: 0 18px 50px rgba(43, 38, 32, 0.13);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  min-height: 100vh;
}
a { color: var(--accent); }
.account-header {
  align-items: center;
  background: rgba(247, 244, 237, 0.92);
  border-bottom: 1px solid rgba(43, 38, 32, 0.08);
  display: flex;
  gap: 12px;
  height: 64px;
  padding: 0 24px;
}
.brand { align-items: center; display: flex; font-weight: 760; gap: 9px; text-decoration: none; }
.brand-mark {
  background: var(--accent);
  border-radius: 8px;
  box-shadow: inset 0 0 0 1px rgba(255, 255, 255, 0.45);
  display: inline-block;
  height: 24px;
  width: 24px;
}
main { margin: 0 auto; max-width: 460px; padding: 48px 24px 72px; }
main.wide { max-width: 720px; }
.card {
  background: var(--paper-strong);
  border: 1px solid var(--line);
  border-radius: 12px;
  box-shadow: var(--shadow);
  padding: 28px;
}
.card + .card { margin-top: 20px; }
h1 { font-size: 30px; line-height: 1.1; margin: 0 0 8px; }
h2 { font-size: 20px; margin: 0 0 12px; }
p, li { color: var(--muted); font-size: 15px; line-height: 1.55; }
label { display: block; font-size: 13px; font-weight: 700; margin: 16px 0 6px; }
input[type=email], input[type=password], input[type=text] {
  border: 1px solid var(--line);
  border-radius: 8px;
  font: inherit;
  padding: 11px 12px;
  width: 100%;
}
input:focus { border-color: var(--accent); outline: none; }
.checkbox-row { align-items: flex-start; display: flex; gap: 9px; margin: 16px 0 4px; }
.checkbox-row input { margin-top: 3px; }
.checkbox-row label { margin: 0; font-weight: 500; color: var(--muted); }
.button {
  border: none;
  border-radius: 8px;
  cursor: pointer;
  display: inline-flex;
  font: inherit;
  font-weight: 760;
  justify-content: center;
  min-height: 46px;
  padding: 12px 16px;
  text-decoration: none;
  width: 100%;
}
.button.primary { background: var(--accent); color: #fffdf6; }
.button.secondary { background: rgba(255, 255, 255, 0.72); border: 1px solid var(--line); color: var(--ink); }
.button.danger { background: var(--coral); color: #fffdf6; }
.button + .button { margin-top: 10px; }
.button[disabled] { cursor: not-allowed; opacity: 0.55; }
.actions { margin-top: 22px; }
.provider-buttons { display: grid; gap: 10px; margin-top: 8px; }
.divider { align-items: center; color: var(--muted); display: flex; font-size: 12px; gap: 12px; margin: 22px 0; text-transform: uppercase; }
.divider::before, .divider::after { background: var(--line); content: ""; flex: 1; height: 1px; }
.meta-links { font-size: 14px; margin-top: 20px; }
.meta-links a { font-weight: 650; }
.note {
  background: var(--quiet);
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--ink);
  font-size: 14px;
  line-height: 1.5;
  margin-top: 16px;
  padding: 12px 14px;
}
.note.warn { background: var(--accent-soft); border-color: #e6ccc1; }
.status-msg { font-size: 14px; font-weight: 650; margin-top: 16px; min-height: 1px; }
.status-msg.error { color: var(--coral); }
.status-msg.ok { color: var(--moss); }
.field-row { display: flex; gap: 8px; }
.field-row input { flex: 1; }
.token-out {
  background: var(--quiet);
  border: 1px solid var(--line);
  border-radius: 8px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 13px;
  margin-top: 10px;
  overflow-wrap: anywhere;
  padding: 12px;
}
.hidden { display: none !important; }
.token-list { list-style: none; margin: 12px 0 0; padding: 0; }
.token-list li {
  align-items: center;
  border: 1px solid var(--line);
  border-radius: 8px;
  display: flex;
  gap: 10px;
  justify-content: space-between;
  margin-bottom: 8px;
  padding: 12px 14px;
}
.token-list .revoke {
  background: none;
  border: 1px solid var(--line);
  border-radius: 6px;
  color: var(--coral);
  cursor: pointer;
  font: inherit;
  font-weight: 700;
  padding: 6px 10px;
  width: auto;
  min-height: 0;
}
.token-list small { color: var(--muted); display: block; }
.row-between { align-items: center; display: flex; gap: 12px; justify-content: space-between; }
.pill { background: var(--quiet); border-radius: 999px; color: var(--moss); font-size: 12px; font-weight: 700; padding: 4px 10px; }
.pill.pill-warn { background: var(--accent-soft); color: var(--accent); }  /* non-active status (e.g. email not verified) */
/* OAuth "finishing sign in" moment — a centered card with a wax-red loading ring, matching the
   app's calm, centered activity states rather than a top-aligned form. */
main.oauth { align-items: center; display: flex; justify-content: center; min-height: calc(100vh - 64px); padding-top: 24px; padding-bottom: 24px; }
main.oauth .card { text-align: center; width: 100%; }
main.oauth h1 { font-size: 26px; }
.spinner {
  animation: cortex-spin 0.85s linear infinite;
  border: 3px solid var(--line);
  border-radius: 50%;
  border-top-color: var(--accent);
  height: 34px;
  margin: 2px auto 18px;
  width: 34px;
}
/* Only the WEIGHT here — no color — so .status-msg.ok / .status-msg.error keep their state color
   (a descendant `color` here would out-specify the single-class state rules and mute them). */
main.oauth .status-msg { font-weight: 600; }
main.oauth .note { text-align: left; }
main.oauth .actions { margin-top: 20px; }
@keyframes cortex-spin { to { transform: rotate(360deg); } }
@media (prefers-reduced-motion: reduce) { .spinner { animation-duration: 0s; } }
/* Public content pages (landing / terms / privacy / download): a comfortable reading column. */
main.doc { max-width: 720px; }
.doc .lede { color: var(--muted); font-size: 17px; margin: 0 0 20px; }
.doc h2 { font-size: 19px; margin: 30px 0 10px; }
.doc ul { margin: 0 0 14px; padding-left: 22px; }
.doc .muted { color: var(--faint); font-size: 13px; }
.doc .cta { margin: 24px 0 8px; }
.doc .cta .button { display: inline-flex; width: auto; padding: 12px 22px; }
.doc .foot-links { border-top: 1px solid var(--line); margin-top: 32px; padding-top: 16px; }
.doc .foot-links a { font-weight: 650; margin-right: 18px; }
""".strip()


def _page(title: str, body: str, script: str, *, head_extra: str = "") -> str:
    """Assemble a full HTML document. ``title`` and ``script`` are baked by us
    (never user-controlled); ``body`` is static markup composed below with all
    interpolated values escaped at the call site. ``head_extra`` is optional
    extra <head> markup (baked by us — e.g. the Turnstile <script> tag)."""
    safe_title = html.escape(title)
    safe_script = html.escape(script)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{safe_title}</title>\n"
        '  <link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        f"  <style>{_SHARED_CSS}</style>\n"
        f"{head_extra}"
        "</head>\n"
        "<body>\n"
        '  <header class="account-header">\n'
        '    <a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span><span>Doppl</span></a>\n'
        "  </header>\n"
        f"  <main{body}\n"
        f'  <script src="/account/{safe_script}"></script>\n'
        "</body>\n"
        "</html>\n"
    )


def _html_response(
    document: str,
    *,
    csp: str = _ACCOUNT_CSP,
    status_code: int = 200,
) -> HTMLResponse:
    response = HTMLResponse(content=document, status_code=status_code)
    response.headers["Content-Security-Policy"] = csp
    response.headers["Referrer-Policy"] = "same-origin"
    return response


# CSP for the PUBLIC content pages (landing / terms / privacy / download). They are static — no
# fetch(), no JS at all — so this is stricter than _ACCOUNT_CSP: only an inline <style>, images and
# fonts from self. Served by main.py for pages that must exist WITHOUT an account (a user reads the
# terms before signing up), so they can't live behind the auth-gated /account routes.
PUBLIC_PAGE_CSP = (
    "default-src 'none'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "base-uri 'none'; "
    "form-action 'self'"
)

# The brand mark as an inline SVG (wax-red rounded square with a soft inner highlight) — matches the
# .brand-mark chip in the header. Served at /favicon.svg so every page's <link rel=icon> resolves.
FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32" role="img" aria-label="Doppl">'
    '<rect width="32" height="32" rx="8" fill="#8c3a2b"/>'
    '<rect x="1" y="1" width="30" height="30" rx="7" fill="none" stroke="#ffffff" '
    'stroke-opacity="0.35" stroke-width="1"/>'
    '<circle cx="16" cy="16" r="6.5" fill="#f7f4ed"/>'
    "</svg>"
)


def render_public_page(title: str, body_html: str) -> str:
    """A full HTML document for a PUBLIC content page (no JS), in the same Archive shell + palette
    as the account front-door. ``title`` is baked by us (escaped); ``body_html`` is trusted markup
    composed by the caller (no user input). Used by main.py for /, /terms, /privacy, /download."""
    safe_title = html.escape(title)
    return (
        "<!doctype html>\n"
        '<html lang="en">\n'
        "<head>\n"
        '  <meta charset="utf-8">\n'
        '  <meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"  <title>{safe_title}</title>\n"
        '  <link rel="icon" href="/favicon.svg" type="image/svg+xml">\n'
        f"  <style>{_SHARED_CSS}</style>\n"
        "</head>\n"
        "<body>\n"
        '  <header class="account-header">\n'
        '    <a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span><span>Doppl</span></a>\n'
        "  </header>\n"
        f'  <main class="doc">\n{body_html}\n  </main>\n'
        "</body>\n"
        "</html>\n"
    )


# --------------------------------------------------------------- page markup
# Every page is composed as static markup; the only dynamic bit is the login
# page's provider buttons, which are rendered from GET /v1/auth/providers and
# escaped below.

def _login_body(provider_buttons_html: str, *, signup_enabled: bool = True) -> str:
    divider = (
        '    <div class="divider">or</div>\n'
        f'    <div class="provider-buttons">{provider_buttons_html}</div>\n'
        if provider_buttons_html
        else ""
    )
    signup_link = (
        '      <p class="meta-links">New to Doppl? <a href="/account/signup">Create an account</a></p>\n'
        if signup_enabled
        else '      <p class="meta-links">Account creation is temporarily unavailable.</p>\n'
    )
    return (
        ' class="login">\n'
        '    <div class="card">\n'
        "      <h1>Sign in</h1>\n"
        "      <p>Access your Doppl account, tokens, and downloads.</p>\n"
        '      <form id="login-form" method="post" action="/v1/auth/login" autocomplete="on">\n'
        '        <label for="email">Email</label>\n'
        '        <input id="email" name="email" type="email" autocomplete="email" required>\n'
        '        <label for="password">Password</label>\n'
        '        <input id="password" name="password" type="password" autocomplete="current-password" required>\n'
        '        <div class="actions">\n'
        '          <button class="button primary" type="submit">Sign in</button>\n'
        "        </div>\n"
        '        <div id="status" class="status-msg" role="status" aria-live="polite"></div>\n'
        "      </form>\n"
        f"{divider}"
        '      <p class="meta-links"><a href="/account/reset">Forgot your password?</a></p>\n'
        f"{signup_link}"
        "    </div>\n"
        "  </main>"
    )


def _signup_body(turnstile_site_key: str = "", provider_buttons: str = "") -> str:
    """Signup page body. When ``turnstile_site_key`` is set, embeds the
    Cloudflare Turnstile widget div (the widget writes its token into a
    ``cf-turnstile-response`` field the JS reads and forwards to the API);
    otherwise the markup is byte-identical to before Turnstile existed."""
    turnstile_widget = ""
    if turnstile_site_key:
        safe_key = html.escape(turnstile_site_key, quote=True)
        turnstile_widget = (
            f'        <div class="cf-turnstile" data-sitekey="{safe_key}"></div>\n'
        )
    provider_section = (
        '      <div class="divider"><span>or</span></div>\n' + provider_buttons
        if provider_buttons
        else ""
    )
    return (
        ' class="signup">\n'
        '    <div class="card">\n'
        "      <h1>Create your account</h1>\n"
        "      <p>Start building your private personal memory model.</p>\n"
        '      <form id="signup-form" method="post" action="/v1/auth/signup" autocomplete="on">\n'
        '        <label for="email">Email</label>\n'
        '        <input id="email" name="email" type="email" autocomplete="email" required>\n'
        '        <label for="password">Password</label>\n'
        '        <input id="password" name="password" type="password" autocomplete="new-password" minlength="10" required>\n'
        '        <label for="confirm">Confirm password</label>\n'
        '        <input id="confirm" name="confirm" type="password" autocomplete="new-password" minlength="10" required>\n'
        '        <div class="checkbox-row">\n'
        '          <input id="tos" name="tos" type="checkbox" required>\n'
        '          <label for="tos">I am 16+ and agree to the <a href="/terms">Terms</a> and '
        '<a href="/privacy">Privacy Policy</a>.</label>\n'
        "        </div>\n"
        f"{turnstile_widget}"
        '        <div class="actions">\n'
        '          <button class="button primary" type="submit">Create account</button>\n'
        "        </div>\n"
        '        <div id="status" class="status-msg" role="status" aria-live="polite"></div>\n'
        "      </form>\n"
        f"{provider_section}"
        '      <p class="meta-links">Already have an account? <a href="/account/login">Sign in</a></p>\n'
        "    </div>\n"
        "  </main>"
    )


_VERIFY_BODY = (
    ' class="verify">\n'
    '    <div class="card">\n'
    "      <h1>Verifying your email</h1>\n"
    '      <div id="status" class="status-msg" role="status" aria-live="polite">One moment…</div>\n'
    '      <div class="actions"><a class="button primary" href="/account/login">Go to sign in</a></div>\n'
    "    </div>\n"
    "  </main>"
)


_RESET_BODY = (
    ' class="reset">\n'
    '    <div class="card" id="request-card">\n'
    "      <h1>Reset your password</h1>\n"
    "      <p>Enter your email and we will send a reset link.</p>\n"
    '      <form id="reset-request-form" method="post" action="/v1/auth/password/reset/request">\n'
    '        <label for="email">Email</label>\n'
    '        <input id="email" name="email" type="email" autocomplete="email" required>\n'
    '        <div class="actions"><button class="button primary" type="submit">Send reset link</button></div>\n'
    '        <div id="status" class="status-msg" role="status" aria-live="polite"></div>\n'
    "      </form>\n"
    '      <p class="meta-links"><a href="/account/login">Back to sign in</a></p>\n'
    "    </div>\n"
    '    <div class="card hidden" id="confirm-card">\n'
    "      <h1>Choose a new password</h1>\n"
    '      <form id="reset-confirm-form" method="post" action="/v1/auth/password/reset/confirm">\n'
    '        <label for="new-password">New password</label>\n'
    '        <input id="new-password" name="new-password" type="password" autocomplete="new-password" minlength="10" required>\n'
    '        <label for="confirm-password">Confirm new password</label>\n'
    '        <input id="confirm-password" name="confirm-password" type="password" autocomplete="new-password" minlength="10" required>\n'
    '        <div class="actions"><button class="button primary" type="submit">Update password</button></div>\n'
    '        <div id="confirm-status" class="status-msg" role="status" aria-live="polite"></div>\n'
    "      </form>\n"
    "    </div>\n"
    "  </main>"
)


_OAUTH_COMPLETE_BODY = (
    ' class="oauth">\n'
    '    <div class="card">\n'
    '      <div class="spinner" id="spinner" aria-hidden="true"></div>\n'
    "      <h1>Finishing sign in</h1>\n"
    '      <div id="status" class="status-msg" role="status" aria-live="polite">One moment…</div>\n'
    '      <div id="link-note" class="note warn hidden">An account with this email already exists. '
    'Sign in with your existing method to link this provider.</div>\n'
    '      <div class="actions hidden" id="login-link"><a class="button primary" href="/account/login">Go to sign in</a></div>\n'
    "    </div>\n"
    "  </main>"
)


_HOME_BODY = (
    ' class="home wide">\n'
    '    <div id="loading" class="card"><p>Loading your account…</p></div>\n'
    '    <div id="dashboard" class="hidden">\n'
    '      <div class="card">\n'
    '        <div class="row-between">\n'
    "          <div>\n"
    "            <h1>Your account</h1>\n"
    '            <p id="account-email"></p>\n'
    "          </div>\n"
    '          <span class="pill" id="account-status"></span>\n'
    "        </div>\n"
    '        <div class="actions"><a class="button secondary" href="/download">Download the Mac app</a></div>\n'
    "      </div>\n"
    '      <div class="card">\n'
    "        <h2>Create a token</h2>\n"
    '        <p>Mint an API token for the REST data plane, or an MCP token for AI tools. The token is shown once.</p>\n'
    '        <label for="token-label">Label (optional)</label>\n'
    '        <input id="token-label" type="text" autocomplete="off" placeholder="e.g. My laptop">\n'
    '        <div class="actions">\n'
    '          <button class="button primary" id="mint-api">Mint API token</button>\n'
    '          <button class="button secondary" id="mint-mcp">Mint MCP token</button>\n'
    "        </div>\n"
    '        <div id="minted" class="note warn hidden">\n'
    "          <strong>Save this now — it will not be shown again.</strong>\n"
    '          <div class="token-out" id="minted-token"></div>\n'
    '          <div class="actions"><button class="button secondary" id="copy-token">Copy token</button></div>\n'
    "        </div>\n"
    '        <div id="mint-status" class="status-msg" role="status" aria-live="polite"></div>\n'
    "      </div>\n"
    '      <div class="card">\n'
    "        <h2>Active tokens</h2>\n"
    '        <ul class="token-list" id="token-list"></ul>\n'
    '        <p id="token-empty" class="hidden">No active tokens yet.</p>\n'
    "      </div>\n"
    '      <div class="card">\n'
    "        <h2>Sign out</h2>\n"
    '        <div class="actions"><button class="button secondary" id="logout">Log out</button></div>\n'
    "      </div>\n"
    '      <div class="card">\n'
    "        <h2>Delete account</h2>\n"
    '        <p>This destroys your connector-credential encryption keys and deletes your hosted data. Type '
    "<strong>DELETE</strong> to confirm.</p>\n"
    '        <label for="delete-confirm">Type DELETE to confirm</label>\n'
    '        <input id="delete-confirm" type="text" autocomplete="off">\n'
    '        <label for="delete-password">Your password</label>\n'
    '        <input id="delete-password" type="password" autocomplete="current-password">\n'
    '        <div class="actions"><button class="button danger" id="delete-account" disabled>Delete my account</button></div>\n'
    '        <div id="delete-status" class="status-msg" role="status" aria-live="polite"></div>\n'
    "      </div>\n"
    "    </div>\n"
    "  </main>"
)


def _provider_buttons_html(
    providers: list[dict[str, Any]],
    app_flow: str = "",
    *,
    signup: bool = False,
) -> str:
    """Render a 'Continue with X' button per configured provider. Only the
    provider name/display_name are interpolated, both escaped. When an app-login
    flow id is present (the desktop 'Continue with <provider>' handoff opened
    /account/login?app_flow=...), it is threaded into each OAuth start URL so the
    callback can complete that flow server-side and the app polls the tokens out.
    ``app_flow`` is pre-sanitized by the caller to [A-Za-z0-9_] so it is URL-safe."""
    query = []
    if app_flow:
        query.append(f"app_flow={app_flow}")
    if signup:
        query.append("signup=1")
    suffix = f"?{'&'.join(query)}" if query else ""
    css_class = "button secondary oauth-signup" if signup else "button secondary"
    parts: list[str] = []
    for row in providers:
        name = str(row.get("provider") or "")
        if not name:
            continue
        display = str(row.get("display_name") or name.title())
        safe_name = html.escape(name, quote=True)
        safe_display = html.escape(display)
        parts.append(
            f'<a class="{css_class}" href="/v1/auth/oauth/{safe_name}/start{suffix}">'
            f"Continue with {safe_display}</a>"
        )
    return "".join(parts)


# ------------------------------------------------------------ external JS
# Served from /account/app.js so the markup carries no inline script and the
# route-scoped CSP can stay at script-src 'self'. Each page includes only the
# same one file and dispatches on document.body.className.

_APP_JS = r"""
'use strict';
(function () {
  var TOKENS = 'cortex_tokens';

  function store(pair) {
    try {
      localStorage.setItem(TOKENS, JSON.stringify({
        access_token: pair.access_token,
        refresh_token: pair.refresh_token
      }));
    } catch (e) {}
  }
  function loadTokens() {
    try { return JSON.parse(localStorage.getItem(TOKENS) || 'null'); }
    catch (e) { return null; }
  }
  function clearTokens() {
    try { localStorage.removeItem(TOKENS); } catch (e) {}
  }
  function setStatus(el, msg, kind) {
    if (!el) return;
    el.textContent = msg || '';
    el.className = 'status-msg' + (kind ? ' ' + kind : '');
  }
  function postJSON(path, body, headers) {
    var h = { 'Content-Type': 'application/json', 'X-Cortex-Client': 'web' };
    if (headers) { for (var k in headers) { h[k] = headers[k]; } }
    return fetch(path, { method: 'POST', headers: h, body: JSON.stringify(body || {}) });
  }
  function authHeaders() {
    var t = loadTokens();
    return t && t.access_token ? { 'Authorization': 'Bearer ' + t.access_token } : {};
  }
  // Which page are we on? _page() puts the page class on <main> (the CSS keys off main.oauth /
  // main.wide), so dispatch reads it there — NOT document.body (which carries no class, which would
  // silently skip every page's init, e.g. leaving the OAuth spinner spinning forever).
  function pageClass() {
    var el = document.querySelector('main');
    return (el && el.className) || document.body.className || '';
  }

  // ------------------------------------------------------------- login
  function initLogin() {
    var form = document.getElementById('login-form');
    if (!form) return;
    var status = document.getElementById('status');
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      setStatus(status, 'Signing in…', '');
      postJSON('/v1/auth/login', {
        email: document.getElementById('email').value,
        password: document.getElementById('password').value
      }).then(function (r) {
        if (r.ok) {
          return r.json().then(function (data) {
            store(data);
            window.location.href = '/account/home';
          });
        }
        setStatus(status, 'Sign in failed. Check your email and password.', 'error');
      }).catch(function () {
        setStatus(status, 'Network error. Try again.', 'error');
      });
    });
  }

  // ------------------------------------------------------------ signup
  function initSignup() {
    var form = document.getElementById('signup-form');
    if (!form) return;
    var status = document.getElementById('status');
    Array.prototype.forEach.call(document.querySelectorAll('.oauth-signup'), function (link) {
      link.addEventListener('click', function (ev) {
        ev.preventDefault();
        if (!document.getElementById('tos').checked) {
          setStatus(status, 'You must agree to the Terms to continue.', 'error');
          return;
        }
        var target = new URL(link.href, window.location.origin);
        target.searchParams.set('terms_accepted', 'true');
        target.searchParams.set('age_confirmed', 'true');
        window.location.href = target.toString();
      });
    });
    form.addEventListener('submit', function (ev) {
      ev.preventDefault();
      var email = document.getElementById('email').value;
      var password = document.getElementById('password').value;
      if (password !== document.getElementById('confirm').value) {
        setStatus(status, 'Passwords do not match.', 'error');
        return;
      }
      if (!document.getElementById('tos').checked) {
        setStatus(status, 'You must agree to the Terms to continue.', 'error');
        return;
      }
      // Cloudflare Turnstile (only present when the operator enabled it): the
      // widget writes its token into a hidden 'cf-turnstile-response' field.
      // Forward it so the server can verify before doing any argon2 work. When
      // Turnstile is off there is no such field and this is a no-op.
      var body = {
        email: email,
        password: password,
        terms_accepted: true,
        age_confirmed: true
      };
      var tsField = form.querySelector('[name="cf-turnstile-response"]');
      if (tsField) {
        if (!tsField.value) {
          setStatus(status, 'Please complete the verification challenge.', 'error');
          return;
        }
        body.turnstile_token = tsField.value;
      }
      setStatus(status, 'Creating your account…', '');
      postJSON('/v1/auth/signup', body).then(function (r) {
        if (!r.ok) {
          return r.json().then(function (data) {
            setStatus(status, (data && data.detail) || 'Could not create the account.', 'error');
          }, function () { setStatus(status, 'Could not create the account.', 'error'); });
        }
        // If the server auto-verified (beta), an immediate login succeeds and
        // we land home. Otherwise show the verify-your-email note.
        postJSON('/v1/auth/login', { email: email, password: password }).then(function (lr) {
          if (lr.ok) {
            return lr.json().then(function (data) {
              store(data);
              window.location.href = '/account/home';
            });
          }
          setStatus(status,
            'Account created. Check your email to verify your address. ' +
            '(If your operator runs Doppl without email, verification is handled by your operator.)',
            'ok');
        }).catch(function () {
          setStatus(status, 'Account created. Check your email to verify your address.', 'ok');
        });
      }).catch(function () {
        setStatus(status, 'Network error. Try again.', 'error');
      });
    });
  }

  // ------------------------------------------------------------ verify
  function initVerify() {
    var status = document.getElementById('status');
    if (!status || pageClass().indexOf('verify') === -1) return;
    var token = new URLSearchParams(window.location.search).get('token') || '';
    if (!token) {
      setStatus(status, 'Missing verification token.', 'error');
      return;
    }
    postJSON('/v1/auth/verify-email', { token: token }).then(function (r) {
      if (r.ok) {
        setStatus(status, 'Your email is verified. You can sign in now.', 'ok');
      } else {
        setStatus(status, 'That verification link is invalid or expired.', 'error');
      }
    }).catch(function () {
      setStatus(status, 'Network error. Try again.', 'error');
    });
  }

  // ------------------------------------------------------------- reset
  function initReset() {
    if (pageClass().indexOf('reset') === -1) return;
    var token = new URLSearchParams(window.location.search).get('token') || '';
    var requestCard = document.getElementById('request-card');
    var confirmCard = document.getElementById('confirm-card');
    var requestForm = document.getElementById('reset-request-form');
    var confirmForm = document.getElementById('reset-confirm-form');

    if (token) {
      requestCard.classList.add('hidden');
      confirmCard.classList.remove('hidden');
    }

    if (requestForm) {
      var status = document.getElementById('status');
      requestForm.addEventListener('submit', function (ev) {
        ev.preventDefault();
        setStatus(status, 'Sending…', '');
        postJSON('/v1/auth/password/reset/request', {
          email: document.getElementById('email').value
        }).then(function () {
          // Always the same generic message (no enumeration oracle).
          setStatus(status, 'If that email exists, we sent a link.', 'ok');
        }).catch(function () {
          setStatus(status, 'If that email exists, we sent a link.', 'ok');
        });
      });
    }
    if (confirmForm) {
      var cstatus = document.getElementById('confirm-status');
      confirmForm.addEventListener('submit', function (ev) {
        ev.preventDefault();
        var pw = document.getElementById('new-password').value;
        if (pw !== document.getElementById('confirm-password').value) {
          setStatus(cstatus, 'Passwords do not match.', 'error');
          return;
        }
        setStatus(cstatus, 'Updating…', '');
        postJSON('/v1/auth/password/reset/confirm', { token: token, new_password: pw }).then(function (r) {
          if (r.ok) {
            setStatus(cstatus, 'Password updated. You can sign in now.', 'ok');
          } else {
            setStatus(cstatus, 'That reset link is invalid or expired.', 'error');
          }
        }).catch(function () {
          setStatus(cstatus, 'Network error. Try again.', 'error');
        });
      });
    }
  }

  // ---------------------------------------------------- oauth complete
  function initOauthComplete() {
    if (pageClass().indexOf('oauth') === -1) return;
    var status = document.getElementById('status');
    // The loading ring spins only while we're mid-handoff; hide it once we reach a terminal state
    // (link needed / failed). On the success path we keep it spinning through the redirect.
    function stopSpinner() { var sp = document.getElementById('spinner'); if (sp) sp.classList.add('hidden'); }
    var query = new URLSearchParams(window.location.search);
    // Tokens ride the URL FRAGMENT (never the query) so they never reach the server access log or
    // a Referer header; the link_required marker is a plain query flag (no secret).
    var hash = new URLSearchParams((window.location.hash || '').replace(/^#/, ''));
    var access = hash.get('access_token');
    var refresh = hash.get('refresh_token');
    if (query.get('action') === 'link_required' || query.get('link_required')) {
      stopSpinner();
      setStatus(status, 'Almost there', 'ok');
      document.getElementById('link-note').classList.remove('hidden');
      document.getElementById('login-link').classList.remove('hidden');
      return;
    }
    if (access && refresh) {
      setStatus(status, 'Signed in — taking you to your account…', 'ok');
      store({ access_token: access, refresh_token: refresh });
      // Scrub the tokens out of the address bar / history before navigating on.
      try { history.replaceState(null, '', window.location.pathname); } catch (e) {}
      window.location.href = '/account/home';
      return;
    }
    stopSpinner();
    setStatus(status, 'Could not complete sign in. Please try again.', 'error');
    document.getElementById('login-link').classList.remove('hidden');
  }

  // -------------------------------------------------------------- home
  function fetchSession() {
    return fetch('/v1/auth/session', { headers: authHeaders() });
  }
  function tryRefresh() {
    var t = loadTokens();
    if (!t || !t.refresh_token) return Promise.resolve(false);
    return postJSON('/v1/auth/refresh', { refresh_token: t.refresh_token }).then(function (r) {
      if (!r.ok) return false;
      return r.json().then(function (data) { store(data); return true; });
    });
  }
  function renderTokens() {
    var list = document.getElementById('token-list');
    var empty = document.getElementById('token-empty');
    fetch('/v1/auth/tokens', { headers: authHeaders() }).then(function (r) {
      return r.ok ? r.json() : { results: [] };
    }).then(function (data) {
      var rows = (data && data.results) || [];
      list.innerHTML = '';
      if (!rows.length) { empty.classList.remove('hidden'); return; }
      empty.classList.add('hidden');
      rows.forEach(function (row) {
        var li = document.createElement('li');
        var info = document.createElement('div');
        var label = document.createElement('strong');
        label.textContent = row.label || row.token_id || 'token';
        var meta = document.createElement('small');
        meta.textContent = (row.audience || '') + ' · ' + (row.token_id || '');
        info.appendChild(label);
        info.appendChild(meta);
        var btn = document.createElement('button');
        btn.className = 'revoke';
        btn.textContent = 'Revoke';
        btn.addEventListener('click', function () {
          fetch('/v1/auth/tokens/' + encodeURIComponent(row.token_id), {
            method: 'DELETE',
            headers: Object.assign({ 'X-Cortex-Client': 'web' }, authHeaders())
          }).then(function () { renderTokens(); });
        });
        li.appendChild(info);
        li.appendChild(btn);
        list.appendChild(li);
      });
    });
  }
  function mintToken(audience) {
    var status = document.getElementById('mint-status');
    var label = document.getElementById('token-label').value;
    setStatus(status, 'Minting…', '');
    postJSON('/v1/auth/tokens', { audience: audience, label: label }, authHeaders()).then(function (r) {
      if (!r.ok) {
        // Surface the server's reason (e.g. "verify your email before minting tokens" on a
        // pending account) instead of a generic failure the user can't act on.
        return r.json().then(function (data) {
          setStatus(status, (data && data.detail) ? data.detail : 'Could not mint the token.', 'error');
        }, function () { setStatus(status, 'Could not mint the token.', 'error'); });
      }
      return r.json().then(function (data) {
        setStatus(status, '', '');
        var box = document.getElementById('minted');
        document.getElementById('minted-token').textContent = data.token;
        box.classList.remove('hidden');
        renderTokens();
      });
    }).catch(function () {
      setStatus(status, 'Network error. Try again.', 'error');
    });
  }
  function initHome() {
    if (pageClass().indexOf('home') === -1) return;
    function gotoLogin() { window.location.href = '/account/login'; }

    function boot(session) {
      document.getElementById('loading').classList.add('hidden');
      document.getElementById('dashboard').classList.remove('hidden');
      var acct = (session && session.account) || {};
      document.getElementById('account-email').textContent = acct.email || '';
      // Humanize the status and only paint the "success" pill when the account is actually active —
      // a raw green "pending_verification" reads as if everything's fine when the email isn't verified.
      var statusEl = document.getElementById('account-status');
      var rawStatus = acct.status || 'active';
      var STATUS_LABELS = {
        active: 'Active',
        pending_verification: 'Email not verified',
        suspended: 'Suspended',
        deleted: 'Deleted'
      };
      statusEl.textContent = STATUS_LABELS[rawStatus] || rawStatus.replace(/_/g, ' ');
      statusEl.className = 'pill' + (rawStatus === 'active' ? '' : ' pill-warn');
      renderTokens();

      document.getElementById('mint-api').addEventListener('click', function () { mintToken('api'); });
      document.getElementById('mint-mcp').addEventListener('click', function () { mintToken('mcp'); });
      var copyBtn = document.getElementById('copy-token');
      if (copyBtn) {
        copyBtn.addEventListener('click', function () {
          var text = document.getElementById('minted-token').textContent;
          if (navigator.clipboard) { navigator.clipboard.writeText(text); }
          copyBtn.textContent = 'Copied';
        });
      }
      document.getElementById('logout').addEventListener('click', function () {
        postJSON('/v1/auth/logout', {}, authHeaders()).then(function () {
          clearTokens();
          gotoLogin();
        }).catch(function () { clearTokens(); gotoLogin(); });
      });
      var confirmInput = document.getElementById('delete-confirm');
      var deleteBtn = document.getElementById('delete-account');
      confirmInput.addEventListener('input', function () {
        deleteBtn.disabled = confirmInput.value.trim() !== 'DELETE';
      });
      deleteBtn.addEventListener('click', function () {
        var dstatus = document.getElementById('delete-status');
        if (confirmInput.value.trim() !== 'DELETE') { return; }
        setStatus(dstatus, 'Deleting…', '');
        fetch('/v1/auth/account', {
          method: 'DELETE',
          headers: Object.assign(
            { 'Content-Type': 'application/json', 'X-Cortex-Client': 'web' },
            authHeaders()
          ),
          body: JSON.stringify({ password: document.getElementById('delete-password').value })
        }).then(function (r) {
          if (r.ok) {
            clearTokens();
            setStatus(dstatus, 'Account deleted.', 'ok');
            gotoLogin();
          } else {
            setStatus(dstatus, 'Could not delete. Check your password.', 'error');
          }
        }).catch(function () {
          setStatus(dstatus, 'Network error. Try again.', 'error');
        });
      });
    }

    fetchSession().then(function (r) {
      if (r.ok) { return r.json().then(boot); }
      return tryRefresh().then(function (ok) {
        if (!ok) { gotoLogin(); return; }
        return fetchSession().then(function (r2) {
          if (r2.ok) { return r2.json().then(boot); }
          gotoLogin();
        });
      });
    }).catch(gotoLogin);
  }

  var cls = pageClass();
  if (cls.indexOf('login') !== -1) initLogin();
  if (cls.indexOf('signup') !== -1) initSignup();
  if (cls.indexOf('verify') !== -1) initVerify();
  if (cls.indexOf('reset') !== -1) initReset();
  if (cls.indexOf('oauth') !== -1) initOauthComplete();
  if (cls.indexOf('home') !== -1) initHome();
})();
"""


def register_web_account_routes(
    app: FastAPI,
    *,
    runtime_or_404: Callable[[], Any],
    list_providers: Callable[[], list[dict[str, Any]]],
    turnstile_site_key: Callable[[], str] | None = None,
    app_flow_cookie: Callable[[str], str] | None = None,
    signup_enabled: Callable[[], bool] | None = None,
) -> None:
    """Register the /account* browser pages on ``app``.

    ``runtime_or_404`` is main.py's ``_auth_runtime_or_404`` — calling it makes
    every page 404 when ``settings.auth_enabled`` is false, exactly like the
    ``/v1/auth`` JSON API. ``list_providers`` returns the enabled OIDC provider
    rows (from GET /v1/auth/providers) so the login page renders the right
    "Continue with X" buttons.

    ``turnstile_site_key`` (optional) is a callable returning the current
    Cloudflare Turnstile site key, or "" when Turnstile is disabled. It is read
    at request time (so tests/reboots that swap settings take effect). When it
    returns a key, the signup page embeds the widget and sends the widened
    signup CSP; when "" (the default), the signup page is byte-identical to
    before Turnstile existed.
    """

    def _turnstile_key() -> str:
        if turnstile_site_key is None:
            return ""
        try:
            return (turnstile_site_key() or "").strip()
        except Exception:
            return ""

    def _signup_enabled() -> bool:
        if signup_enabled is None:
            return True
        try:
            return bool(signup_enabled())
        except Exception:
            return False

    @app.get("/account", response_class=HTMLResponse)
    @app.get("/account/login", response_class=HTMLResponse)
    def account_login(request: Request) -> Response:
        runtime_or_404()
        try:
            providers = list_providers()
        except Exception:
            providers = []
        # Desktop app-login handoff: /account/login?app_flow=flw_... The id must ride the OAuth
        # start URL so the callback completes the flow. Sanitize to [A-Za-z0-9_] (the flow-id
        # alphabet) before interpolating into the href.
        raw_flow = (request.query_params.get("app_flow") or "")[:120]
        app_flow = raw_flow if raw_flow and raw_flow.replace("_", "").isalnum() else ""
        # One-hop provider handoff: the desktop app's "Sign in with GitHub" / "Continue with Google"
        # button opens /account/login?app_flow=...&provider=<p>. When <p> is a CONFIGURED provider,
        # bind this browser to the flow (df_af cookie) and redirect straight to that provider's OAuth,
        # so the user lands on GitHub/Google directly instead of a second button page. Only providers
        # present in the configured list are honored, so this can never be an open redirect.
        raw_provider = (request.query_params.get("provider") or "")[:40].lower()
        provider_hint = raw_provider if raw_provider and raw_provider.replace("_", "").replace("-", "").isalnum() else ""
        configured_provider_names = {str(p.get("provider") or "").lower() for p in providers}
        if app_flow and provider_hint and provider_hint in configured_provider_names:
            redirect = RedirectResponse(
                f"/v1/auth/oauth/{provider_hint}/start?app_flow={app_flow}", status_code=302
            )
            if app_flow_cookie is not None:
                redirect.set_cookie(
                    "df_af", app_flow_cookie(app_flow),
                    max_age=900, httponly=True, samesite="lax", secure=True, path="/",
                )
            return redirect
        buttons = _provider_buttons_html(providers, app_flow=app_flow)
        resp = _html_response(
            _page(
                "Sign in · Doppl",
                _login_body(buttons, signup_enabled=_signup_enabled()),
                "app.js",
            )
        )
        # Bind THIS browser to the desktop app-login poll flow: set a signed cookie that the OAuth
        # start requires before it will attach an authenticated account to app_flow (main.py
        # _app_flow_cookie / auth_oauth_start). Prevents a login-CSRF / flow-fixation takeover where a
        # victim's OAuth completion is captured by an attacker-owned poll flow. Cookie name must match
        # main.APP_FLOW_COOKIE_NAME ("df_af").
        if app_flow and app_flow_cookie is not None:
            resp.set_cookie(
                "df_af", app_flow_cookie(app_flow),
                max_age=900, httponly=True, samesite="lax", secure=True, path="/",
            )
        return resp

    @app.get("/account/signup", response_class=HTMLResponse)
    def account_signup() -> Response:
        runtime = runtime_or_404()
        if not _signup_enabled():
            return _html_response(
                _page(
                    "Account creation unavailable · Doppl",
                    ' class="signup">\n'
                    '    <div class="card">\n'
                    "      <h1>Account creation is unavailable</h1>\n"
                    "      <p>The service operator has not yet published approved Terms and "
                    "Privacy text. Existing users can still sign in.</p>\n"
                    '      <p class="meta-links"><a href="/account/login">Back to sign in</a></p>\n'
                    "    </div>\n"
                    "  </main>",
                    "app.js",
                ),
                status_code=503,
            )
        try:
            providers = runtime.oidc.enabled_providers()
        except Exception:
            providers = []
        provider_buttons = _provider_buttons_html(providers, signup=True)
        site_key = _turnstile_key()
        if site_key:
            head_extra = f'  <script src="{_TURNSTILE_API_JS}" async defer></script>\n'
            document = _page(
                "Create your account · Doppl",
                _signup_body(site_key, provider_buttons),
                "app.js",
                head_extra=head_extra,
            )
            return _html_response(document, csp=_SIGNUP_CSP_TURNSTILE)
        return _html_response(
            _page(
                "Create your account · Doppl",
                _signup_body(provider_buttons=provider_buttons),
                "app.js",
            )
        )

    @app.get("/account/verify", response_class=HTMLResponse)
    def account_verify() -> Response:
        runtime_or_404()
        return _html_response(_page("Verify email · Doppl", _VERIFY_BODY, "app.js"))

    @app.get("/account/reset", response_class=HTMLResponse)
    def account_reset() -> Response:
        runtime_or_404()
        return _html_response(_page("Reset password · Doppl", _RESET_BODY, "app.js"))

    @app.get("/account/home", response_class=HTMLResponse)
    def account_home() -> Response:
        runtime_or_404()
        return _html_response(_page("Your account · Doppl", _HOME_BODY, "app.js"))

    @app.get("/account/oauth/complete", response_class=HTMLResponse)
    def account_oauth_complete() -> Response:
        runtime_or_404()
        return _html_response(_page("Finishing sign in · Doppl", _OAUTH_COMPLETE_BODY, "app.js"))

    @app.get("/account/app.js")
    def account_app_js(request: Request) -> Response:
        runtime_or_404()
        response = PlainTextResponse(content=_APP_JS, media_type="application/javascript")
        response.headers["Content-Security-Policy"] = _ACCOUNT_CSP
        response.headers["Cache-Control"] = "no-store"
        return response
