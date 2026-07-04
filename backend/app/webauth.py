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
"""

from __future__ import annotations

import html
from typing import Any, Callable

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, Response

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

# Palette + font pulled straight from site/styles.css so the pages match the
# marketing site. Kept inline (one <style> block) to avoid a shared-asset fetch
# and to survive the strict global CSP without a stylesheet origin.
_SHARED_CSS = """
:root {
  color-scheme: light;
  --ink: #18211f;
  --muted: #5c6965;
  --line: #d8ded9;
  --paper: #f8f7f1;
  --paper-strong: #ffffff;
  --green: #1f7a5c;
  --blue: #365d8c;
  --coral: #bd5d45;
  --gold: #a77722;
  --shadow: 0 18px 50px rgba(24, 33, 31, 0.14);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
  min-height: 100vh;
}
a { color: var(--green); }
.account-header {
  align-items: center;
  background: rgba(248, 247, 241, 0.92);
  border-bottom: 1px solid rgba(24, 33, 31, 0.08);
  display: flex;
  gap: 12px;
  height: 64px;
  padding: 0 24px;
}
.brand { align-items: center; display: flex; font-weight: 760; gap: 9px; text-decoration: none; }
.brand-mark {
  background: var(--green);
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
input:focus { border-color: var(--green); outline: none; }
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
.button.primary { background: var(--ink); color: #fffdf6; }
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
  background: #eef4ed;
  border: 1px solid var(--line);
  border-radius: 8px;
  color: var(--ink);
  font-size: 14px;
  line-height: 1.5;
  margin-top: 16px;
  padding: 12px 14px;
}
.note.warn { background: #fdf0ec; border-color: #eac8bd; }
.status-msg { font-size: 14px; font-weight: 650; margin-top: 16px; min-height: 1px; }
.status-msg.error { color: var(--coral); }
.status-msg.ok { color: var(--green); }
.field-row { display: flex; gap: 8px; }
.field-row input { flex: 1; }
.token-out {
  background: #f4f3ec;
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
.pill { background: #eef4ed; border-radius: 999px; color: var(--green); font-size: 12px; font-weight: 700; padding: 4px 10px; }
""".strip()


def _page(title: str, body: str, script: str) -> str:
    """Assemble a full HTML document. ``title`` and ``script`` are baked by us
    (never user-controlled); ``body`` is static markup composed below with all
    interpolated values escaped at the call site."""
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
        "</head>\n"
        "<body>\n"
        '  <header class="account-header">\n'
        '    <a class="brand" href="/"><span class="brand-mark" aria-hidden="true"></span><span>Cortex</span></a>\n'
        "  </header>\n"
        f"  <main{body}\n"
        f'  <script src="/account/{safe_script}"></script>\n'
        "</body>\n"
        "</html>\n"
    )


def _html_response(document: str) -> HTMLResponse:
    response = HTMLResponse(content=document)
    response.headers["Content-Security-Policy"] = _ACCOUNT_CSP
    response.headers["Referrer-Policy"] = "same-origin"
    return response


# --------------------------------------------------------------- page markup
# Every page is composed as static markup; the only dynamic bit is the login
# page's provider buttons, which are rendered from GET /v1/auth/providers and
# escaped below.

def _login_body(provider_buttons_html: str) -> str:
    divider = (
        '    <div class="divider">or</div>\n'
        f'    <div class="provider-buttons">{provider_buttons_html}</div>\n'
        if provider_buttons_html
        else ""
    )
    return (
        ' class="login">\n'
        '    <div class="card">\n'
        "      <h1>Sign in</h1>\n"
        "      <p>Access your Cortex account, tokens, and downloads.</p>\n"
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
        '      <p class="meta-links">New to Cortex? <a href="/account/signup">Create an account</a></p>\n'
        "    </div>\n"
        "  </main>"
    )


_SIGNUP_BODY = (
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
    '        <div class="actions">\n'
    '          <button class="button primary" type="submit">Create account</button>\n'
    "        </div>\n"
    '        <div id="status" class="status-msg" role="status" aria-live="polite"></div>\n'
    "      </form>\n"
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
    '        <p>This permanently crypto-shreds your keys and deletes your data. Type '
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


def _provider_buttons_html(providers: list[dict[str, Any]]) -> str:
    """Render a 'Continue with X' button per configured provider. Only the
    provider name/display_name are interpolated, both escaped."""
    parts: list[str] = []
    for row in providers:
        name = str(row.get("provider") or "")
        if not name:
            continue
        display = str(row.get("display_name") or name.title())
        safe_name = html.escape(name, quote=True)
        safe_display = html.escape(display)
        parts.append(
            f'<a class="button secondary" href="/v1/auth/oauth/{safe_name}/start">'
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
      setStatus(status, 'Creating your account…', '');
      postJSON('/v1/auth/signup', { email: email, password: password }).then(function (r) {
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
            '(If your operator runs Cortex without email, verification is handled by your operator.)',
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
    if (!status || document.body.className.indexOf('verify') === -1) return;
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
    if (document.body.className.indexOf('reset') === -1) return;
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
    if (document.body.className.indexOf('oauth') === -1) return;
    var status = document.getElementById('status');
    var params = new URLSearchParams(window.location.search);
    var access = params.get('access_token');
    var refresh = params.get('refresh_token');
    if (params.get('action') === 'link_required' || params.get('link_required')) {
      setStatus(status, '', '');
      document.getElementById('link-note').classList.remove('hidden');
      document.getElementById('login-link').classList.remove('hidden');
      return;
    }
    if (access && refresh) {
      store({ access_token: access, refresh_token: refresh });
      window.location.href = '/account/home';
      return;
    }
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
        setStatus(status, 'Could not mint the token.', 'error');
        return;
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
    if (document.body.className.indexOf('home') === -1) return;
    function gotoLogin() { window.location.href = '/account/login'; }

    function boot(session) {
      document.getElementById('loading').classList.add('hidden');
      document.getElementById('dashboard').classList.remove('hidden');
      var acct = (session && session.account) || {};
      document.getElementById('account-email').textContent = acct.email || '';
      document.getElementById('account-status').textContent = acct.status || 'active';
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

  var cls = document.body.className || '';
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
) -> None:
    """Register the /account* browser pages on ``app``.

    ``runtime_or_404`` is main.py's ``_auth_runtime_or_404`` — calling it makes
    every page 404 when ``settings.auth_enabled`` is false, exactly like the
    ``/v1/auth`` JSON API. ``list_providers`` returns the enabled OIDC provider
    rows (from GET /v1/auth/providers) so the login page renders the right
    "Continue with X" buttons.
    """

    @app.get("/account", response_class=HTMLResponse)
    @app.get("/account/login", response_class=HTMLResponse)
    def account_login() -> Response:
        runtime_or_404()
        try:
            providers = list_providers()
        except Exception:
            providers = []
        buttons = _provider_buttons_html(providers)
        return _html_response(_page("Sign in · Cortex", _login_body(buttons), "app.js"))

    @app.get("/account/signup", response_class=HTMLResponse)
    def account_signup() -> Response:
        runtime_or_404()
        return _html_response(_page("Create your account · Cortex", _SIGNUP_BODY, "app.js"))

    @app.get("/account/verify", response_class=HTMLResponse)
    def account_verify() -> Response:
        runtime_or_404()
        return _html_response(_page("Verify email · Cortex", _VERIFY_BODY, "app.js"))

    @app.get("/account/reset", response_class=HTMLResponse)
    def account_reset() -> Response:
        runtime_or_404()
        return _html_response(_page("Reset password · Cortex", _RESET_BODY, "app.js"))

    @app.get("/account/home", response_class=HTMLResponse)
    def account_home() -> Response:
        runtime_or_404()
        return _html_response(_page("Your account · Cortex", _HOME_BODY, "app.js"))

    @app.get("/account/oauth/complete", response_class=HTMLResponse)
    def account_oauth_complete() -> Response:
        runtime_or_404()
        return _html_response(_page("Finishing sign in · Cortex", _OAUTH_COMPLETE_BODY, "app.js"))

    @app.get("/account/app.js")
    def account_app_js(request: Request) -> Response:
        runtime_or_404()
        response = PlainTextResponse(content=_APP_JS, media_type="application/javascript")
        response.headers["Content-Security-Policy"] = _ACCOUNT_CSP
        response.headers["Cache-Control"] = "no-store"
        return response
