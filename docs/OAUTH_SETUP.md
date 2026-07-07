# Cortex "Sign in with…" — OAuth provisioning checklist

The browser sign-in flow is **already built** end-to-end (app opens the system browser → PKCE →
loopback callback on the local server → token exchange → refresh → auto-sync). A connector's tile
flips from **"Coming soon"** to a working **"Sign in"** the moment a real **OAuth client ID** for
that provider is present in the build. This doc is the exact, per-provider work to register those
OAuth apps and light them up.

> Sourced from a verified deep-research pass (RFC 8252 + each provider's 2026 primary docs). Provider
> consoles change — re-verify against the linked docs before shipping. Google's verification detail
> (below) is the one area to re-confirm live.

## How Cortex's flow works (shared for every provider)

- **Flow:** OAuth 2.0 Authorization Code + **PKCE** (S256), system browser, **loopback redirect** to
  the bundled local server.
- **Redirect / callback URL to register (exact):**
  - Google: `http://127.0.0.1:8766/v1/connectors/google/oauth/callback`
  - Everyone else (managed): `http://127.0.0.1:8766/v1/connectors/oauth/callback`
  - Register the **exact host + path**; also register the concrete port `:8766`. RFC 8252 says
    servers MUST allow any loopback port, but **not all providers honor it** — so pin `:8766`.
  - Prefer `127.0.0.1` over `localhost`. A few providers only document `localhost` in examples;
    confirm each accepts `127.0.0.1` specifically (Slack/Microsoft docs show `localhost`).
- **Where the client ID goes:** the app reads `CORTEX_<PROVIDER>_OAUTH_CLIENT_ID` (and, only where a
  secret is unavoidable, `CORTEX_<PROVIDER>_OAUTH_CLIENT_SECRET`) from env / Info.plist keys
  (`CortexGoogleOAuthClientID`, `CortexNotionOAuthClientID`, `CortexMicrosoftOAuthClientID`, …).
  Give me the client ID and I wire it in; the tile then works.

## Client-secret reality (this drives the architecture)

| Provider | Secretless public client? | What Cortex needs |
|---|---|---|
| **Google** (Calendar/Drive/Docs/Gmail) | PKCE + loopback; Desktop client ships a **non-confidential installed-app secret** (Google's accepted model) | Register Desktop client → ship client ID (+ the installed "secret", which is fine to ship). **No broker.** |
| **Slack** | **Yes** — PKCE GA 2026-03-30, `oauth.v2.access` with no secret | Client ID only. **No broker.** (user scopes only; PKCE refresh token expires 30 days) |
| **Microsoft / Outlook (Graph)** | **Yes** — public client, "Allow public client flows"=Yes, no secret at exchange | Client ID only. **No broker.** (loopback `http://127.0.0.1` must be added via app **manifest** `replyUrlsWithType`, not the portal textbox) |
| **Notion** | **No** — token exchange requires HTTP Basic `base64(client_id:client_secret)`; **no PKCE** for the connector API | Either **ship the secret** (extractable — not recommended) or a **hosted token-exchange broker**. |
| **GitHub** | **No** — secret required at token exchange (PKCE is additive only) | Use the **device flow** (client_id only, secretless) **or** a broker. |

**Decision needed from you:** for Notion/GitHub, do we (a) stand up a tiny **Cortex Cloud
token-exchange broker** (holds the secrets; the auth handshake transits our server, ongoing data
sync stays local), or (b) skip them for now, or (c) GitHub-only via device flow? My recommendation:
build the broker (it unlocks Notion + GitHub cleanly and is a small, well-contained service).

---

## Per-provider checklists

### 1. Notion — fastest to a *working* sign-in (no verification gate), but needs a secret
- **Register:** https://www.notion.so/my-integrations → New integration → **Public** integration.
- **Redirect URI:** `http://127.0.0.1:8766/v1/connectors/oauth/callback`
- **Auth:** confidential — token exchange needs `base64(client_id:client_secret)`. **No PKCE.**
- **Scopes:** Notion grants page/database access at the consent screen (user picks what to share);
  read is implied by the integration's capabilities (set read-only).
- **Verification:** **none** — register → works immediately.
- **Cortex needs:** client ID **+** the broker (or shipped secret).
- **Why first:** highest-demand personal KB with zero review gate; the only blocker is the secret →
  the broker decision.

### 2. Google Calendar — high demand, *sensitive* scope (verification, but no CASA)
- **Register:** https://console.cloud.google.com → APIs & Services → Credentials → Create OAuth
  client ID → **Desktop app**. Enable the Google Calendar API.
- **Redirect URI:** `http://127.0.0.1:8766/v1/connectors/google/oauth/callback`
- **Auth:** PKCE + loopback; ship the Desktop client ID (+ installed secret — fine to ship).
- **Scope (read-only):** `https://www.googleapis.com/auth/calendar.readonly`
- **Verification:** **sensitive** → OAuth consent-screen verification (brand review, ~days–weeks).
  No CASA. Until verified: **Testing** mode = up to **100 test users**.
- **Why early:** ubiquitous, high "about-me" signal, and the *least* painful Google to verify.

### 3. Google Drive / Docs — high demand; Docs = sensitive, full Drive = restricted
- **Register:** same Desktop client as Calendar (one Google OAuth client can hold all Google scopes).
  Enable Drive API / Docs API.
- **Redirect URI:** `http://127.0.0.1:8766/v1/connectors/google/oauth/callback`
- **Scopes (read-only, minimize!):**
  - `documents.readonly` (Google Docs) → **sensitive**
  - `drive.metadata.readonly` → sensitive; `drive.readonly` (full file read) → **RESTRICTED**
  - Prefer `drive.file` (only files the user opens/picks) to **avoid the restricted tier** entirely.
- **Verification:** restricted scopes (`drive.readonly`, Gmail) require the **CASA annual third-party
  security assessment** (App Defense Alliance) — weeks-to-months + assessor fees. Sensitive scopes
  need consent verification only.
- **Recommendation:** ship Docs + `drive.file` first (sensitive), defer full `drive.readonly`.

### 4. Gmail — highest desire, slowest (RESTRICTED → CASA). Start the process early, ship last.
- **Register:** same Google Desktop client; enable Gmail API.
- **Scope:** `https://www.googleapis.com/auth/gmail.readonly` → **RESTRICTED**.
- **Verification:** brand verification **+ CASA** third-party security assessment (annual, weeks-to-
  months, assessor fees). Until then: Testing mode ≤100 users only.
- **Reality:** do not promise public Gmail sign-in on a near-term timeline; kick off CASA now if you
  want it, and ship the other connectors meanwhile.

### 5. Slack — truly secretless, register → works (secondary demand for a personal memory app)
- **Register:** https://api.slack.com/apps → Create New App → OAuth & Permissions → enable **PKCE**.
- **Redirect URI:** register your loopback (`http://127.0.0.1:8766/...`); Slack docs show `localhost`
  — confirm `127.0.0.1` is accepted or fall back to `localhost`.
- **Auth:** PKCE, `oauth.v2.access` with **no secret**. **User scopes only** on desktop redirects.
- **Scopes (user, read-only):** `channels:history`, `channels:read`, `files:read`, `users:read`.
- **Verification:** none for your own workspace / distribution; **PKCE refresh tokens expire in 30
  days** → needs a silent/again re-auth UX.

### 6. GitHub — needs a secret; use the device flow to stay secretless (dev-audience)
- **Register:** https://github.com/settings/developers → New OAuth App (or GitHub App).
- **Auth:** Authorization-Code needs a client secret → **use the OAuth device flow** (client_id only)
  to stay secretless on a distributed app, or route through the broker.
- **Scopes (read-only):** `read:user`, `repo` (or fine-grained read perms).
- **Verification:** none.

### 7. Microsoft / Outlook (Graph) — secretless public client (mind the manifest gotcha)
- **Register:** https://entra.microsoft.com → App registrations → New → Authentication → Add platform
  → **Mobile and desktop applications** → Advanced → **Allow public client flows = Yes**.
- **Redirect URI:** `http://127.0.0.1:8766/v1/connectors/oauth/callback` — **must be added via the
  app manifest `replyUrlsWithType`** (the portal textbox rejects `http://127.0.0.1`). Register the
  exact port for `127.0.0.1` (port-agnostic matching is only documented for `localhost`).
- **Auth:** public client, no secret at exchange.
- **Scopes (delegated, read-only):** `Mail.Read`, `Files.Read`, `Calendars.Read`, `User.Read`.
- **Verification:** **publisher verification is free** and fast (minutes if prereqs met), but an
  unverified multitenant app shows an "unverified publisher" consent warning; verify to remove it.

---

## Recommended sequence (likelihood × feasibility)

1. **Notion** — register (5 min, no review) + stand up the broker → first real one-tap sign-in.
2. **Google Calendar** (`calendar.readonly`, sensitive) — register Desktop client, submit consent
   verification. Ubiquitous, least-painful Google.
3. **Google Docs + `drive.file`** (sensitive) — same client; avoids the restricted tier.
4. **Microsoft / Outlook** — secretless, fast publisher verification. Good work coverage.
5. **Slack** — secretless; add if the work-chat audience matters.
6. **GitHub** — device flow; dev audience.
7. **Gmail + full Drive** (restricted, CASA) — start CASA now if wanted; ship last.

Meanwhile, the zero-friction sources that need **no** OAuth stay the primary path for most users:
**ChatGPT/Claude/Gemini exports** and the **local notes folder**.

## The token-exchange broker (option A) — built; here's how to turn it on

For the confidential providers (**Notion**, **GitHub**) the app can't hold a secret, so the exchange
runs on Cortex Cloud. The broker is **built, secured, and unit-tested** (`backend/app/oauth_broker.py`,
routes registered on the hosted FastAPI app; 10/10 tests). It's a **no-op 503 surface until you set
its env vars and deploy** — no secret ever ships in the app.

**Endpoints (hosted):** `POST /oauth/broker/{start,exchange,refresh}`, `GET /oauth/broker/providers`.
It owns each provider's `client_id` + `client_secret`, builds authorize URLs, exchanges codes, and
returns tokens **without storing them**. `redirect_uri` is allowlisted to the app's loopback callback
so it can't be abused as an open token oracle.

### What YOU do (per confidential provider)
1. **Register the OAuth app** (per the per-provider sections above) and note its **client ID + secret**.
2. **Set the broker env vars** on the hosted Cortex Cloud host (see `deploy/cortex.env.example`):
   ```
   CORTEX_BROKER_NOTION_CLIENT_ID=...
   CORTEX_BROKER_NOTION_CLIENT_SECRET=...
   # optional overrides: CORTEX_BROKER_NOTION_AUTHORIZE_URL / _TOKEN_URL / _SCOPES
   CORTEX_BROKER_GITHUB_CLIENT_ID=...
   CORTEX_BROKER_GITHUB_CLIENT_SECRET=...
   ```
3. **Register the redirect URI** with each provider: `http://127.0.0.1:8766/v1/connectors/oauth/callback`
   (if Notion rejects a loopback `http://127.0.0.1` redirect and demands HTTPS, tell me — I'll switch
   that provider to the broker-hosted relay callback instead; the broker already centralizes this).
4. **Deploy** the hosted backend (`deploy/` — Caddy + systemd on the Mac Mini) so `/oauth/broker/*`
   is reachable over HTTPS at your Cortex Cloud domain.
5. **Tell me the broker's public base URL** (e.g. `https://trydoppl.com`). I set the app's
   `CORTEX_OAUTH_BROKER_URL` to it.

### What I do next (fast, once the broker is live)
- Wire the local backend's `start_managed_oauth` / `complete_managed_oauth` to route Notion (and
  GitHub) through `/oauth/broker/start` + `/oauth/broker/exchange` when `CORTEX_OAUTH_BROKER_URL` is
  set (the seam is already there — `request_token` is injectable, authorize URL is centralized).
- Mark Notion as a managed-OAuth "Sign in" connector in the catalog.
- Flip the Connections setup sheet to **sign-in-first** (token entry demoted to "Advanced").
- Verify the real end-to-end round-trip against the live broker, then ship the DMG.

For **Google** (Calendar/Docs/Drive) and **Microsoft/Slack** there's **no broker** — those are
public-client PKCE loopback flows. You register the app, give me the **client ID**, I add it to the
build's Info.plist, and the tile flips to "Sign in." (Google also needs its verification submitted.)
