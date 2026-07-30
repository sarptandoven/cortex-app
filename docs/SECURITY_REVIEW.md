# Security review — accounts / OAuth / data surface (2026-07-07)

> Follow-up review: 2026-07-30. The original review below is retained for
> history; its earlier "no IDOR found" conclusion did not cover server-local
> path inputs and was incomplete. The follow-up closed the discovered
> boundaries and added regression tests.

## Follow-up fixed (2026-07-30)

### CRITICAL — hosted path inputs could read API-server files

Hosted import analysis/import, Obsidian sync/write-back, coding-agent session
harvesting, and calendar file inputs accepted paths on the API server. In a
sharded deployment, that confused a user's local-machine boundary with the
server's filesystem and could expose cross-tenant or deployment data readable
by the service account.

**Fix:** all path-based operations now fail closed unless
`CORTEX_SHARD_MODE=local`. The check exists at the FastAPI boundary and again
inside sharded `CortexStore` instances so scheduled jobs and MCP/tool calls
cannot bypass it. Hosted imports must use uploaded content rather than server
paths. Regression coverage:
`backend/tests/test_hosted_trust_boundaries.py`.

### HIGH — connector credentials could be sent to arbitrary origins

Credential-bearing connector requests accepted caller-controlled
`api_base_url`, `api_url`, Jira `site_url`, calendar feeds, and OAuth token
endpoints. That created SSRF and credential-exfiltration paths.

**Fix:** hosted connector calls now allow only each provider's canonical HTTPS
API origin. Hosted Jira is restricted to HTTPS `*.atlassian.net`; local-only
Zotero, calendar feed/file, Obsidian, and agent-session operations are blocked
on sharded stores. Custom OAuth token endpoints are rejected by hosted HTTP
routes. The storage-level policy also covers saved scheduled connector jobs and
MCP/tool calls. Local mode retains explicit test/development overrides.

### HIGH — backups contained plaintext memory and production secrets

The server backup copied `/etc/cortex/cortex.env` and produced an unencrypted
archive. **Fix:** the environment file is excluded, backup directories and
files use `0700`/`0600` permissions under `umask 077`, and the script refuses
to run without an `age` public recipient. Bootstrap generates a dedicated age
identity whose private half must be escrowed separately; offsite copies contain
only encrypted archives.

### HIGH — public deployment defaults auto-verified email

Both hosted environment templates now default to
`CORTEX_AUTH_AUTOVERIFY=0` and SMTP mode. Existing installations must migrate
their persisted environment file; changing the example does not rewrite an
already-provisioned deployment.

### MEDIUM — query-string capture could leak tokens and content to logs

`GET /capture` accepted a token and captured content in the URL. Hosted mode
now rejects query-string capture and requires `POST /capture`; the local
compatibility surface remains available for loopback clients.

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

### MEDIUM — `CORTEX_AUTH_AUTOVERIFY=1` enables email squatting (deployment default fixed 2026-07-30)
Beta profile skips email verification, so an attacker can pre-register a victim's email + password;
when the real owner later tries Google/Apple, the "never silent auto-link" rule returns
`link_required` and effectively locks them out of their own address.
**Required operator action:** set `CORTEX_AUTH_AUTOVERIFY=0` and require a real emailed verify token
(the flow already exists) before an account becomes active — i.e. wire real email (Postmark/SES).
Acceptable for a closed beta; **must** be flipped before opening signups broadly.

### LOW — Native `id_token` nonce is client-supplied / skipped when absent
`POST /v1/auth/oauth/{provider}/native` reads the nonce from the request body and only enforces it
when present. Replay is already bounded by the token's audience (our bundle id) + short `exp`.
Hardening (server-issued challenge nonce round-tripped by the native client) is optional; low risk.

## Verified clean
- **Multi-tenant identity routing:** per-user shard resolution + scoped-token enforcement
  (`require_agent_access`) were clean in the original scope. The later
  server-filesystem issue above was outside that scope and is now regression-tested.
- **Admin:** `admin_auth` is a constant-time compare of `CORTEX_API_KEY`; a user token cannot reach
  `/v1/admin/*`; no secret leaks in responses.
- **Injection:** account/metrics SQL is parameterized (incl. the new `last_active_at` subqueries and
  `active_last_7d`); web pages carry a strict route-scoped CSP with no inline script.
- **macOS gate:** the required-account sign-in wall has no bypass surface (menu-bar panel, status
  menu, onboarding, quick-capture, live-activity, constellation all gated); app-store build ships no
  runnable code and no `_ssl`.
