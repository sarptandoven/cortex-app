# Security review — accounts / OAuth / data surface (2026-07-07)

Adversarial review across 6 lenses (auth/session, OAuth+SSRF, multi-tenant isolation, admin/secrets,
injection, macOS sandbox). Every candidate finding was independently verified; false positives
dropped. **5 confirmed** findings. Multi-tenant isolation, admin auth, injection, and the macOS
required-account gate came back clean (no confirmed issues).

## Fixed (this pass)

### HIGH — Login-CSRF / flow fixation on the desktop OAuth handoff → account takeover
`backend/app/main.py` (app-login flow). The desktop "Continue with Google/GitHub" handoff attached
whatever account completed OAuth to a poll flow identified by an attacker-controllable `app_flow`
id, with no proof the OAuth completer owned that poll flow. An attacker could start their own poll
flow, get a victim to complete provider OAuth via a crafted `/v1/auth/oauth/<p>/start?app_flow=<atk>`
link, then poll out a full session for the **victim's** account.
**Fix:** bind the flow to the initiating browser. `/account/login?app_flow=<id>` now sets a signed
(`HMAC-SHA256` over `CORTEX_SYNC_SIGNING_KEY`) **HttpOnly, SameSite=Lax, Secure** cookie `df_af`, and
`/v1/auth/oauth/<provider>/start` only honors `app_flow` when that cookie validates — otherwise it is
dropped and the request is treated as a normal web sign-in (mints a session for that browser only).
Regression tests: `test_app_login_flow_binding_cookie_prevents_login_csrf`, plus the handoff journey
test updated to carry the cookie.

### LOW — `email_verified` string `"false"` treated as verified
`backend/app/oidc_registry.py`. `bool(claims.get("email_verified"))` — but Apple (and some IdPs) send
the claim as the JSON **string** `"false"`, and `bool("false")` is `True`. Now parsed explicitly:
only a real boolean `True` or the strings `"true"`/`"1"` count as verified.

### LOW — macOS opened server-controlled URLs with no scheme check
`macos/Sources/CortexCloudAuth.swift` + `CortexApp.swift`. `browser_url` (app-login) and
`authorization_url` (connector OAuth) come from the server and were passed straight to
`NSWorkspace.shared.open`. A malicious/MITM response could launch a `file://` / custom-scheme / app
URL. Now both require an `http(s)` scheme before opening.

## Deferred (documented; not yet fixed)

### MEDIUM — `CORTEX_AUTH_AUTOVERIFY=1` enables email squatting
Beta profile skips email verification, so an attacker can pre-register a victim's email + password;
when the real owner later tries Google/Apple, the "never silent auto-link" rule returns
`link_required` and effectively locks them out of their own address.
**Fix at public launch:** set `CORTEX_AUTH_AUTOVERIFY=0` and require a real emailed verify token
(the flow already exists) before an account becomes active — i.e. wire real email (Postmark/SES).
Acceptable for a closed beta; **must** be flipped before opening signups broadly.

### LOW — Native `id_token` nonce is client-supplied / skipped when absent
`POST /v1/auth/oauth/{provider}/native` reads the nonce from the request body and only enforces it
when present. Replay is already bounded by the token's audience (our bundle id) + short `exp`.
Hardening (server-issued challenge nonce round-tripped by the native client) is optional; low risk.

## Verified clean
- **Multi-tenant isolation:** per-user shard resolution + scoped-token enforcement (`require_agent_access`)
  on every data path; no IDOR found.
- **Admin:** `admin_auth` is a constant-time compare of `CORTEX_API_KEY`; a user token cannot reach
  `/v1/admin/*`; no secret leaks in responses.
- **Injection:** account/metrics SQL is parameterized (incl. the new `last_active_at` subqueries and
  `active_last_7d`); web pages carry a strict route-scoped CSP with no inline script.
- **macOS gate:** the required-account sign-in wall has no bypass surface (menu-bar panel, status
  menu, onboarding, quick-capture, live-activity, constellation all gated); app-store build ships no
  runnable code and no `_ssl`.
