# Cortex OAuth — exact setup steps per provider

> The founder's single source of truth for registering every OAuth provider Cortex connects to.
> Do these steps in the recommended order (§2). Preserve every literal value exactly as written.

---

## 1. How Cortex's OAuth flow works (read this first)

Cortex is a **local-first macOS app** with a bundled local server on **`http://127.0.0.1:8766`**. Sign-in uses **OAuth 2.0 Authorization Code + PKCE (S256)** with a **loopback redirect** back to that local server. Read-only import of the user's own content is the goal, so **minimize scopes** everywhere.

### The two exact redirect / callback URIs

There are exactly two callback paths. Register the correct one per provider — byte-for-byte, including scheme, host, port, and path. No trailing slash, no wildcards, no fragments.

| Callback | Exact string | Used by |
|---|---|---|
| **Google-specific** | `http://127.0.0.1:8766/v1/connectors/google/oauth/callback` | Google only (Calendar, Drive, Docs, Gmail) |
| **Generic ("Others")** | `http://127.0.0.1:8766/v1/connectors/oauth/callback` | Notion, Microsoft, Slack, GitHub |

Plain **`http://`** is correct and accepted for both — `127.0.0.1`/`localhost` are **loopback** addresses (per RFC 8252 and every provider's docs) and are exempt from the usual HTTPS-only redirect rules. Do not "upgrade" these to `https`.

### Hosted account-login callbacks are separate

The table above is for **source connectors** that redirect back to the local bundled server. The first-run **account login** page is hosted on the public API and uses different HTTPS callbacks. If GitHub shows "The redirect_uri is not associated with this application", the GitHub OAuth App was registered with the wrong account-login callback.

Register these exact hosted account-login callbacks for the social sign-in buttons:

| Provider | Exact hosted account callback |
|---|---|
| GitHub | `https://api.signindoppl.com/v1/auth/oauth/github/callback` |
| Google | `https://api.signindoppl.com/v1/auth/oauth/google/callback` |

These are also covered in `docs/FOUNDER_GO_LIVE.md`, but keep them here so connector OAuth and account OAuth do not get mixed up.

### Where the client IDs go (the app reads these)

- **`CortexGoogleOAuthClientID`** — Info.plist key; Google Web-application client ID (public, PKCE).
- **`CortexMicrosoftOAuthClientID`** — Info.plist key; Microsoft Entra Application (client) ID (public, PKCE).
- Slack and GitHub client IDs are also **public** and ship the same way (public identifiers, safe to embed). Add Info.plist keys following the same `Cortex<Provider>OAuthClientID` convention.

### The broker (confidential providers only)

**Notion** and **GitHub** are **confidential clients** — their token exchange *requires a client secret* (Notion always; GitHub even when using PKCE). Those secrets must live **only** in the hosted **Cortex Cloud broker** and never in the macOS bundle. The broker holds the secret and performs the `code → token` exchange; the **loopback redirect on `127.0.0.1:8766` is still used** to hand the authorization code back. Broker secrets are supplied via env vars, e.g.:

- **`CORTEX_BROKER_NOTION_CLIENT_ID`** / **`CORTEX_BROKER_NOTION_CLIENT_SECRET`**
- **`CORTEX_BROKER_GITHUB_CLIENT_ID`** / **`CORTEX_BROKER_GITHUB_CLIENT_SECRET`**

**Google, Microsoft, Slack are PUBLIC clients (PKCE, no secret) and do NOT touch the broker.** GitHub has a broker-free alternative — the **Device Flow** (client ID only) — which is the recommended path for a local-first app (see §3.6).

---

## 2. Recommended order (and why)

Do them in this order to front-load the fast wins and defer the slow, gated, or costly ones:

1. **Notion** — zero verification/review gate; public integration works immediately once you fill the org fields. Fastest win.
2. **Google Calendar** (`calendar.readonly`) — *sensitive* scope only, no CASA. Get the Google client created and one non-restricted scope flowing first.
3. **Google Docs + Drive via `drive.file`** — keep Drive on the **non-sensitive** `drive.file` scope (only files the user picks) to **avoid the restricted tier and CASA** entirely. Docs (`documents.readonly`) is sensitive-only.
4. **Microsoft** — no Google-style scope review; only optional (but practically required) *publisher verification*, which is free and fast once you're an enrolled partner. Front-load the partner enrollment because that part can take days.
5. **Slack** — no review gate, but **PKCE is a one-way, irreversible toggle** and the `127.0.0.1` vs `localhost` redirect acceptance must be tested empirically before you commit. Do it deliberately, not first.
6. **GitHub** — no verification gate, but it's a confidential client; decide Device Flow vs broker. Straightforward but needs the broker or Device-Flow decision.
7. **Gmail (`gmail.readonly`) — LAST.** This is a **restricted** scope that forces a paid **CASA security assessment** (annual) plus full OAuth verification. Ship everything else first; add Gmail only when you're ready to pay for and pass CASA (or decide Gmail isn't needed at launch).

> Cost/gate summary driving the order: Notion/Slack/GitHub/Microsoft-delegated = **no review**. Google Calendar/Docs + `drive.file` = **sensitive verification, no CASA**. Gmail `gmail.readonly` (and `drive.readonly` if ever used) = **restricted → CASA, paid, annual**.

---

## 3. Per-provider checklists

### 3.1 Notion — do this first

- **Console:** https://www.notion.so/my-integrations
- **Client type:** Notion "Public integration" — a **confidential** OAuth 2.0 Authorization Code client. **No PKCE, no app-type picker.** Every integration starts internal; you promote it to Public via the Distribution tab. Notion issues **both** a client ID **and** a client secret and requires **HTTP Basic auth** (`base64(client_id:client_secret)`) at token exchange → **must go through the Cortex Cloud broker.**
- **Redirect (generic loopback):** `http://127.0.0.1:8766/v1/connectors/oauth/callback`
- **Secret handling:** Client secret lives **only** in the broker (`CORTEX_BROKER_NOTION_CLIENT_SECRET`). The broker does `POST https://api.notion.com/v1/oauth/token`. Access tokens **do not expire** and there is **no refresh token** — the broker stores the long-lived `access_token`. Never build refresh logic for Notion.

**Steps:**
1. Sign in with the **workspace-admin** account, go to https://www.notion.so/my-integrations.
2. Click **"+ New integration"** (top-right).
3. **Name:** `Cortex` (shown on the consent screen).
4. **Associated workspace:** your own workspace (only the dev/owner workspace; end users authorize their own later).
5. Leave type as the default internal integration; click **Save / Submit** to create it.
6. Open the **Configuration** tab → **Capabilities (Content Capabilities):** check **"Read content"** only. Leave "Update content" and "Insert content" **unchecked**.
7. **Comment Capabilities:** leave both **off** (only check "Read comments" if importing comments).
8. **User Capabilities:** select **"No user information"** (or "User information without email addresses" if you need name/avatar; avoid the email option).
9. Click **Save changes**.
10. Open the **Distribution** tab → turn **ON** the toggle **"Do you want to make this integration public?"**. This converts it to a Public (OAuth) integration and reveals the OAuth fields.
11. In the revealed **Organization Information** section, fill all required fields: **Company name**, **Website or homepage** (reachable https), **Tagline**, **Privacy policy** URL, **Terms of use** URL, **Support email** (monitored inbox). All required to save.
12. In **OAuth Domain & URIs → Redirect URIs**, click **"+ Add URI"** and paste exactly:
    `http://127.0.0.1:8766/v1/connectors/oauth/callback`
13. Leave **"Notion URL for optional template"** blank (unless shipping a duplicable template).
14. Click **Submit / Save** at the bottom of the Distribution tab.
15. Back on **Configuration** (Secrets/OAuth area), copy the **OAuth client ID** (→ broker `CORTEX_BROKER_NOTION_CLIENT_ID`) and reveal + copy the **OAuth client secret** (→ broker `CORTEX_BROKER_NOTION_CLIENT_SECRET`, broker-only). Note the prefilled **Authorization URL**.
16. Authorize URL the app opens (`owner=user` is **required**; add random `state`):
    `https://api.notion.com/v1/oauth/authorize?client_id=<CLIENT_ID>&response_type=code&owner=user&redirect_uri=http%3A%2F%2F127.0.0.1%3A8766%2Fv1%2Fconnectors%2Foauth%2Fcallback&state=<RANDOM>`
17. Consent screen: user clicks **"Select pages"** and picks the specific pages/databases (or a parent, which grants children). API only sees what's selected.
18. Broker exchanges the code: `POST https://api.notion.com/v1/oauth/token` with `Authorization: Basic base64("<client_id>:<client_secret>")`, `Notion-Version` header, JSON body `{"grant_type":"authorization_code","code":"<code>","redirect_uri":"http://127.0.0.1:8766/v1/connectors/oauth/callback"}`. Returns `access_token` (non-expiring), `workspace_id`, `workspace_name`, `bot_id`, `owner`, `duplicated_template_id`.

**Scopes (capabilities):** Read content; No user information (or "…without email addresses"); Read comments (optional).

**Verification:** **None.** No review, no test-user allowlist, no user cap, no cost, no waiting. Filling the Distribution toggle + Organization Information is sufficient for any Notion user to authorize immediately.

**Gotchas:**
- No PKCE — always confidential, always a secret → **must use the broker.**
- "Can't be a public IP address" does **not** mean "must be https" — `127.0.0.1`/`localhost` are loopback and allowed over `http`.
- Redirect must match **exactly** (scheme+host+port+path). `127.0.0.1` ≠ `localhost` as strings.
- If you register multiple redirect URIs, you **must** pass `redirect_uri` at token exchange (and match the authorize value).
- OAuth fields **don't exist** until the Distribution public toggle is on.
- `owner=user` is **required** in the authorize URL.
- Tokens don't expire; no refresh token.
- Page picker scopes access to only what the user selected — expect partial imports.
- Always send the `Notion-Version` header (e.g. `2022-06-28` or current) on token + API calls.

**Sources:** developers.notion.com/guides/get-started/authorization · /docs/authorization · /reference/capabilities · /reference/create-a-token · norahsakal.com/blog/create-public-notion-integration · unified.to/blog/how_to_set_up_and_configure_notion

---

### 3.2 – 3.4 & 3.7 Google (Calendar, Docs, Drive, Gmail) — one client, staged scopes

- **Console:** https://console.cloud.google.com/ — create/select a project. You live in two areas: **APIs & Services > Library** (https://console.cloud.google.com/apis/library) to enable APIs, and **Google Auth Platform** (https://console.cloud.google.com/auth/overview) which now hosts **Branding / Audience / Data Access / Clients** (this replaced the old "OAuth consent screen" + "Credentials" UI).
- **Client type — DEFINITIVE:** create a **"Web application"** OAuth client, **NOT "Desktop app."** A Desktop client has **no "Authorized redirect URIs" field** and cannot pin the specific path `/v1/connectors/google/oauth/callback` (it only accepts bare host+port loopback). Cortex routes on that exact path, so **only a Web client works.** A Web client with a loopback URI is fully supported; loopback isn't deprecated for Web clients.
- **Redirect (Google-specific loopback):** `http://127.0.0.1:8766/v1/connectors/google/oauth/callback`
- **Secret handling:** **PUBLIC client, PKCE (S256).** Ship **only the client ID** (→ Info.plist `CortexGoogleOAuthClientID`). A Web client is technically issued a secret, but you **leave the secret out** of the PKCE token exchange (`client_id` + `code_verifier`, no secret). Cortex's local server exchanges directly at `https://oauth2.googleapis.com/token`. **Google does NOT use the broker.**

**Steps:**
1. https://console.cloud.google.com/ → project picker → **New Project** → Name **`Cortex`** → Create → select it.
2. **APIs & Services > Library** → enable all four: **Gmail API**, **Google Drive API**, **Google Docs API**, **Google Calendar API** (accept per-API Terms if prompted). *(Enable Gmail only when you commit to CASA — see §2/Verification. You can defer enabling it.)*
3. Open https://console.cloud.google.com/auth/overview → **Get started** (fresh project) → **Branding:** set **App name = Cortex**, **User support email = sarptan.doven1@gmail.com**.
4. **Audience = External** (any Google account can sign in, not just a Workspace org).
5. Set **Developer contact = sarptan.doven1@gmail.com**, accept the User Data Policy, click **Create**.
6. Finish **Branding** for verification: **App logo** (120×120 PNG; optional in Testing, required to publish), **App home page** URL, **Privacy policy** link, **Terms of Service** link. Under **Authorized domains** add your bare product domain (e.g. `getcortex.app`) — this domain must be **verified in Google Search Console** before publishing.
7. **Auth Platform > Clients** (https://console.cloud.google.com/auth/clients) → **Create client** → **Application type: Web application** → **Name:** `Cortex local loopback`.
8. Under **Authorized redirect URIs** → **"+ ADD URI"** → paste `http://127.0.0.1:8766/v1/connectors/google/oauth/callback`. Leave **Authorized JavaScript origins EMPTY**. Click **Create**.
9. In the "OAuth client created" dialog copy the **Client ID** → app (`CortexGoogleOAuthClientID`, public). Ignore/store the secret securely but **do NOT embed it** in the app.
10. **Auth Platform > Data Access** → **Add or remove scopes** → add the read-only minimum (staged — see below). Save.
11. **Auth Platform > Audience > Test users > Add users** — add every tester's Google account (max 100). In Testing they click past the "unverified app" warning; refresh tokens/grants expire after **7 days**.
12. When ready for real users: **Audience > Publish app** (Testing → In production). Sensitive/restricted scopes trigger OAuth verification (brand verification + demo video + per-scope justification; restricted scopes add **CASA**). Submit from the Data Access / verification prompt.

**Scopes (staged, least-privilege):**
- Sign-in identity: `openid`, `email`, `profile`
- **Calendar (§3.2):** `https://www.googleapis.com/auth/calendar.readonly` — *sensitive*
- **Docs (§3.3):** `https://www.googleapis.com/auth/documents.readonly` — *sensitive*
- **Drive (§3.3, recommended):** `https://www.googleapis.com/auth/drive.file` — **non-sensitive** (only files the user picks) → **avoids CASA.** Only use `https://www.googleapis.com/auth/drive.readonly` (*restricted*) if you truly need whole-Drive read (forces CASA).
- **Gmail (§3.7, LAST):** `https://www.googleapis.com/auth/gmail.readonly` — **restricted** (forces CASA). No non-restricted read-only equivalent exists.

**Verification (the whole story):**
- **Tiers:** *Restricted* = `gmail.readonly`, `drive.readonly`. *Sensitive* = `documents.readonly`, `calendar.readonly`. `drive.file` is *non-sensitive*.
- **Testing (unverified):** fine for dev — up to 100 test users, "unverified app" warning, grants/refresh tokens expire every **7 days**.
- **Go live:** Publish (status In production) + pass OAuth App Verification:
  1. **Brand verification** — own your Authorized Domain (verify in Search Console), host privacy policy on that domain, provide app name/logo/home page. Typically **2–3 business days**; sensitive-scope review overall up to **~10 business days**.
  2. **CASA** (only if requesting restricted scopes like `gmail.readonly`/`drive.readonly`) — via an App Defense Alliance-approved assessor, practically **CASA Tier 2**, **paid** (roughly a few hundred up to ~**$3,000/yr**; Google has a discounted TAC Security rate), **re-done every 12 months**.
  - Verification needs a **demo video** of the full consent + read-only data-use flow and a **written per-scope justification**.
- **Cost-cutting (solo founder):** dropping restricted scopes removes CASA entirely (only sensitive verification remains). Use **`drive.file`** instead of `drive.readonly`. **Defer Gmail** until CASA is worth it. Until CASA passes, keep restricted usage to the 100 test users.

**Gotchas:**
- **#1 trap:** use **Web application**, not Desktop — Desktop can't pin the path.
- Redirect must match **exactly** (`127.0.0.1` ≠ `localhost`, right port, exact path, no stray trailing slash) or `redirect_uri_mismatch`.
- Plain `http` works only because loopback is exempt.
- `gmail.readonly`/`drive.readonly` are **restricted → CASA (paid, annual)**; `documents.readonly`/`calendar.readonly` are only sensitive. "readonly" ≠ low-risk.
- Testing: 7-day refresh-token death + hard 100-user cap — can't beta past that without publishing + verifying.
- Console moved to "Google Auth Platform" (Branding/Audience/Data Access/Clients); old-guide "Credentials > Create OAuth client ID" is now under **Auth Platform > Clients**.
- Publishing needs logo + homepage + privacy policy on a **Search-Console-verified** Authorized Domain — line these up first.
- Leave **Authorized JavaScript origins empty**.
- Public PKCE client — ship only the client ID, never the secret; **no broker**.
- **Enable each API before use** — un-enabled API returns `403 accessNotConfigured` even with a valid token.

**Sources:** support.google.com/cloud/answer/15549257 · developers.google.com/identity/protocols/oauth2/native-app · /web-server · /resources/loopback-migration · /workspace/guides/configure-oauth-consent · /production-readiness/restricted-scope-verification · /sensitive-scope-verification · /workspace/gmail/api/auth/scopes · /workspace/drive/api/guides/api-specific-auth · /workspace/docs/api/auth · /workspace/calendar/api/auth · appdefensealliance.dev/casa

---

### 3.5 Microsoft / Outlook (Microsoft Graph)

- **Console:** https://entra.microsoft.com → **Entra ID > App registrations > New registration** (identical blades exist under portal.azure.com > "Microsoft Entra ID" > "App registrations").
- **Client type:** **Public client / native desktop app**, one registration. Auth Code + PKCE, **no secret**. In Entra: (a) add the **"Mobile and desktop applications"** platform, and (b) set **"Allow public client flows" = Yes** (`allowPublicClient=true`). Do **not** create a "Web" platform or a client secret. **Supported account types = multitenant + personal** (`AzureADandPersonalMicrosoftAccount`) so any work/school **and** personal Outlook.com user can import their own data.
- **Redirect (generic loopback):** `http://127.0.0.1:8766/v1/connectors/oauth/callback`
- **Secret handling:** **PUBLIC client, no secret.** Ship only the **Application (client) ID** (→ `CortexMicrosoftOAuthClientID`). **No broker.** Keep "Allow public client flows = Yes."

**PORTAL QUIRK — the redirect must be added via the Manifest:** The Authentication blade's "Redirect URIs" text box **rejects any `http://` URI whose host is the `127.0.0.1` literal** (only `http://localhost` is accepted in the UI; Microsoft documents this and shows the error dialog). Because Cortex uses `http://127.0.0.1:8766/...`, you must add it in **Manage > Manifest**.

**Steps:**
1. Sign in to https://entra.microsoft.com (need at least **Application Developer** role). Use the gear icon to pick the right tenant.
2. **Entra ID > App registrations** → **New registration**.
3. **Name:** `Cortex` (shown on consent).
4. **Supported account types:** **"Accounts in any organizational directory (Any Microsoft Entra ID tenant – Multitenant) and personal Microsoft accounts (e.g. Skype, Xbox)."**
5. Leave the top **Redirect URI** field **BLANK** (portal rejects http+127.0.0.1 here). Click **Register**.
6. On **Overview**, copy **Application (client) ID** → app (`CortexMicrosoftOAuthClientID`). A public multitenant app uses the `/common` (or `/consumers`) authority.
7. **Manage > Authentication** → **Add a platform** → **"Mobile and desktop applications"**.
8. In that pane, tick the suggested **`https://login.microsoftonline.com/common/oauth2/nativeclient`** (or type `http://localhost`) just to **create the native platform**, → **Configure**.
9. Still in Authentication → **Advanced settings > Allow public client flows = Yes** (`allowPublicClient=true`) → **Save**.
10. **Manage > Manifest** → find **`replyUrlsWithType`** → add the loopback with type **`InstalledClient`** → **Save**:
    ```json
    "replyUrlsWithType": [
        {
            "url": "http://127.0.0.1:8766/v1/connectors/oauth/callback",
            "type": "InstalledClient"
        }
    ]
    ```
11. Confirm **`"allowPublicClient": true`** is present in the manifest; if `false`/`null`, set it and Save.
12. **Manage > API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions** → check the four scopes → **Add permissions**.
13. You do **not** need "Grant admin consent" for a multitenant public app — each end user consents at first sign-in.
14. **Manage > Branding & properties** → set a real **Publisher domain** (DNS-verifiable, **not** `*.onmicrosoft.com`), **Home page URL**, **Terms of service URL**, **Privacy statement URL**.
15. **Publisher verification:** enroll in the **Microsoft AI Cloud Partner Program** (partner.microsoft.com/membership) and complete Partner Center verification to get a verified **Partner One ID** (PGA). Then Branding & properties → **"Add Partner ID to verify publisher"** → enter Partner One ID → **Verify and save** (signed in with MFA as App/Cloud App Admin + Partner Center CPP/Account Admin).
16. After a few minutes a blue **"verified"** badge appears next to the publisher name. Test with `prompt=consent` and confirm the badge shows and the "unverified publisher" warning is gone.

**Scopes (delegated, read-only):**
- `User.Read` — sign-in + basic profile (id `a42657d6-7f20-40e3-b6f0-cee03008a662`)
- `Mail.Read` — read mailbox (id `570282fd-fa5c-430d-a7fd-fc8dc98a9dca`)
- `Files.Read` — read OneDrive files (id `01d4889c-1287-42c6-ac1f-5d1e02578e6d`)
- `Calendars.Read` — read calendars (id `465a38f9-76ea-45b9-9f34-9e8b0d4b0b42`)
- Microsoft Graph `resourceAppId` (for manifest `requiredResourceAccess`): `00000003-0000-0000-c000-000000000000`
- Optional: `offline_access` (refresh token for silent re-import); `openid`/`profile` implied.

**Verification (two separate things):**
1. **No Google-style scope review** for these delegated read scopes — users consent and import immediately after registration.
2. **Publisher verification** — optional but **effectively required in practice** for a multitenant app (registered after 8 Nov 2020) requesting more than basic sign-in: with a tenant's risk-based step-up consent enabled, cross-tenant users are **blocked** (not just warned) until you're verified. **Cost: free.** **Time: minutes** once prerequisites are met (partner enrollment itself can take days). Not supported in national clouds; badge implies no quality/compliance certification. No test-user cap.

**Gotchas:**
- Text box blocks http+127.0.0.1 → add via **Manifest `replyUrlsWithType`**, type **`InstalledClient`** (never `Web`/`Spa` — `Web` makes it confidential + expects a secret).
- **Port is ignored** for loopback matching, so `:8766` needs nothing special; don't register multiple 127.0.0.1 URIs differing only by port. **Path is case-sensitive**: `/v1/connectors/oauth/callback`.
- **Allow public client flows = Yes** or the desktop PKCE flow can fail (Entra falls back to confidential). Verify `allowPublicClient=true` after saving.
- Multitenant **+ personal** audience required; personal accounts have a 100 (vs 256) redirect cap and disallow query params — Cortex's callback has none, so fine.
- No secret, **no broker** — public client.
- Unverified-publisher **blocking** is real; plan CPP/Partner Center enrollment early.
- Publisher domain can't be `*.onmicrosoft.com`; app must be registered by a **work/school** account (not personal MS account).
- Manifest editor uses `replyUrlsWithType` (legacy `replyUrls` is not editable). Edit fields individually; don't upload old downloaded manifests (schema errors).
- Redirect URIs on the **application object only**, never the service principal.
- Manifest `requiredResourceAccess` for the four scopes uses resourceAppId `00000003-0000-0000-c000-000000000000`, type `Scope`, with the four ids above.

**Sources:** learn.microsoft.com/entra/identity-platform/quickstart-register-app · /reply-url · /reference-app-manifest · /mark-app-as-publisher-verified · /publisher-verification-overview · learn.microsoft.com/graph/permissions-reference · graphpermissions.merill.net/permission/Mail.Read

---

### 3.6 Slack

- **Console:** https://api.slack.com/apps
- **Client type:** No client-type dropdown. Create one app **"From scratch,"** then make it a **public client** by enabling **PKCE** in **OAuth & Permissions**. Enabling PKCE marks it public (no secret at token exchange) and allows a localhost/loopback HTTP desktop redirect. **⚠️ Enabling PKCE is a ONE-WAY, IRREVERSIBLE operation** (undo requires Slack support) — flip it only on the app you'll actually ship.
- **Redirect (generic loopback):** `http://127.0.0.1:8766/v1/connectors/oauth/callback` — **but test acceptance first** (see quirk below).
- **Secret handling:** **PUBLIC client, PKCE, no secret.** Send `client_id` + `code` + `code_verifier` (S256) to `oauth.v2.access`; **never** send `client_secret`. The Client Secret on Basic Information is unused. **No broker.** This is a **user-token-only** flow (desktop/PKCE forbids bot scopes) — exactly what Cortex wants. **Caveat:** PKCE apps get **refresh tokens that expire after 30 days** with rotation — Cortex must persist and rotate refresh tokens; a user idle >30 days must re-authorize.

**⚠️ `127.0.0.1` vs `localhost` quirk:** Slack's PKCE docs whitelist only the literal host **`localhost`** (their example `http://localhost:8080/auth`) as an HTTP desktop redirect; they **never mention `127.0.0.1`**, and general OAuth docs still say redirect URLs "must use HTTPS." There's real risk the console rejects `http://127.0.0.1:8766/...` or treats it as a server redirect demanding HTTPS. **Test empirically:** try to save the `127.0.0.1` form with PKCE ON. If rejected, register `http://localhost:8766/v1/connectors/oauth/callback` instead **and** make Cortex's local server answer on host `localhost` and send that exact string (Slack does exact-string matching; a `127.0.0.1` vs `localhost` mismatch fails). No URL fragment (`#`) allowed.

**Steps:**
1. Sign in to Slack in your dev workspace, go to https://api.slack.com/apps.
2. **Create New App** (green, top-right) → **From scratch**.
3. **App Name:** `Cortex`; pick a dev workspace → **Create App**.
4. On **Basic Information → App Credentials**, copy the **Client ID** (this ships; you will NOT use the Client Secret).
5. Left sidebar **Features → OAuth & Permissions**.
6. **Enable PKCE** (the public-client toggle). **⚠️ One-way/irreversible.** Required for the localhost desktop redirect and no-secret exchange.
7. **Redirect URLs → Add New Redirect URL** → paste `http://127.0.0.1:8766/v1/connectors/oauth/callback` → **Add** → **Save URLs**. If rejected/HTTPS-forced, use the `localhost` form (see quirk).
8. **Scopes:** add ONLY under **User Token Scopes** (no Bot Token Scopes — desktop/PKCE rejects them). Click **Add an OAuth Scope**.
9. Add User Token Scopes: **`channels:read`**, **`channels:history`**, **`users:read`**, **`files:read`**. No bot scopes.
10. Authorize URL Cortex opens (note **`user_scope`**, comma-separated, not `scope`):
    `https://slack.com/oauth/v2/authorize?client_id=<CLIENT_ID>&user_scope=channels:read,channels:history,users:read,files:read&redirect_uri=http://127.0.0.1:8766/v1/connectors/oauth/callback&state=<RANDOM>&code_challenge=<S256(verifier)>&code_challenge_method=S256`
11. Exchange at callback **without** client_secret: `POST https://slack.com/api/oauth.v2.access` with `client_id`, `code`, `code_verifier`, `redirect_uri` (`grant_type=authorization_code`). **User token is under `authed_user.access_token`** (not top-level). Store `authed_user.refresh_token` and rotate (30-day expiry).
12. **Manage Distribution → Activate Public Distribution** (self-serve checklist: no hard-coded secrets, valid redirect) so users outside your dev workspace can authorize.

**Scopes (User Token Scopes):** `channels:read`, `channels:history`, `users:read`, `files:read`.

**Verification:** **No review gate**, no cost, no human review — just the self-serve **Activate Public Distribution** checklist. Marketplace/App Directory listing is a separate, heavier review Cortex does **not** need. Practical caveat: 30-day refresh-token expiry → plan rotation + silent re-auth.

**Gotchas:**
- **PKCE toggle is one-way/irreversible** — flip only on the shipping app.
- `127.0.0.1` **not documented** as accepted — test; fall back to `localhost` and byte-match exactly.
- HTTP allowed **only because PKCE is on**; a non-PKCE localhost redirect is held to HTTPS.
- **Bot scopes forbidden** on desktop/PKCE — User Token Scopes only.
- Use **`user_scope=`** (not `scope=`) in authorize; comma-separated.
- At exchange send **`code_verifier`, not `client_secret`**.
- User token under **`authed_user.access_token`** / `authed_user.refresh_token`.
- **30-day refresh expiry** — implement rotation + re-auth-after-inactivity.
- `code_challenge_method` must be **S256**; `code_challenge` URL-safe base64.
- No `#` fragment in redirect; click **Save URLs** after Add.
- Client ID is under **Basic Information → App Credentials** (not the OAuth page).

**Sources:** docs.slack.dev/authentication/using-pkce · docs.slack.dev/changelog/2026/03/30/pkce · /installing-with-oauth · /reference/methods/oauth.v2.access · scope docs channels.read / channels.history / users.read / files.read · api.slack.com/apps

---

### 3.7 GitHub (OAuth App)

- **Console:** https://github.com/settings/developers (Settings → Developer settings → OAuth Apps)
- **Client type:** Classic **"OAuth App"** (only one kind — no public/native vs web split). **CRITICAL:** GitHub does **not** distinguish public vs confidential clients — the token exchange **always requires the client secret, even with PKCE** (confirmed by GitHub staff, community discussion #15752). So GitHub is a **confidential** provider. Two architectures:
  - **(A) RECOMMENDED — Device Authorization Flow:** needs **only the client ID** (no secret, no loopback redirect). Ship just the client ID; do the whole exchange locally. **No broker.** Cleanest local-first fit.
  - **(B) Authorization Code + PKCE + loopback:** the `code → token` POST needs the secret, so it **must** run in the **Cortex Cloud broker**.
- **Redirect (generic loopback):** `http://127.0.0.1:8766/v1/connectors/oauth/callback` — one "Authorization callback URL" field only (OAuth Apps allow exactly one). Required at creation even for Device Flow (used only in path B); put the loopback there as a placeholder.
- **Secret handling:** Device Flow (A) — client ID only, `POST https://github.com/login/oauth/access_token` takes `client_id + device_code + grant_type`, **no `client_secret`**. Auth-code (B) — secret lives **only** in the broker (`CORTEX_BROKER_GITHUB_CLIENT_SECRET`); never in the app binary.

**Steps:**
1. Sign in to GitHub → profile picture (upper-right) → **Settings**.
2. Left sidebar bottom → **Developer settings** (https://github.com/settings/developers).
3. **OAuth Apps** → **New OAuth App** ("Register a new application" if first).
4. **Application name:** `Cortex`.
5. **Homepage URL:** `https://cortex.<your-domain>` (your real site URL).
6. (Optional) **Application description** — treat as public.
7. **Authorization callback URL:** `http://127.0.0.1:8766/v1/connectors/oauth/callback` (required even for Device Flow).
8. Check **"Enable Device Flow"** (required for Device Flow — endpoints return HTTP 400 otherwise; harmless if you later choose path B).
9. Click **Register application**.
10. Copy the **Client ID** (public). This is the only credential needed for Device Flow → app (`CORTEX_BROKER_GITHUB_CLIENT_ID` if using the broker, or ship as public client ID for Device Flow).
11. **Only for path B (auth-code + broker):** click **Generate a new client secret**, copy it **immediately** (shown once), store in the **broker** (`CORTEX_BROKER_GITHUB_CLIENT_SECRET`) — never in the app. Skip entirely for Device Flow.
12. To edit later (toggle Device Flow / change callback): OAuth Apps → select app → edit → **Update application**.
13. **Device Flow runtime — request codes:** `POST https://github.com/login/device/code` body `client_id=<CLIENT_ID>&scope=read:user%20public_repo` (`Accept: application/json`). Returns `device_code`, `user_code`, `verification_uri` (https://github.com/login/device), `interval`.
14. Show `user_code`: "Go to https://github.com/login/device and enter code: `<user_code>`".
15. **Poll for token (no secret):** `POST https://github.com/login/oauth/access_token` body `client_id=<CLIENT_ID>&device_code=<device_code>&grant_type=urn:ietf:params:oauth:grant-type:device_code` (poll at `interval`; handle `authorization_pending` / `slow_down`).
16. **Auth-code alternative (needs broker):** open browser to `https://github.com/login/oauth/authorize?client_id=<CLIENT_ID>&redirect_uri=http://127.0.0.1:8766/v1/connectors/oauth/callback&scope=read:user%20public_repo&state=<state>&code_challenge=<S256(verifier)>&code_challenge_method=S256`
17. **Auth-code alternative — broker exchanges the code** (S256 only): `POST https://github.com/login/oauth/access_token` body `client_id=<CLIENT_ID>&client_secret=<SECRET in broker>&code=<code>&redirect_uri=http://127.0.0.1:8766/v1/connectors/oauth/callback&code_verifier=<verifier>`

**Scopes:** `read:user` (profile, read-only) · `user:email` (only if you need email; implied by `user`, don't request both) · `public_repo` (public repos only — closest read-only-ish option) · `read:org` (only if importing org context).

**Verification:** **None.** Works for any GitHub user immediately — no submission, no test-user allowlist, no cap. Only gates are the per-user consent screen and, for org private data, an org's OAuth App access policy. "Enable Device Flow" is a self-serve checkbox, not a review.

**Gotchas:**
- **PKCE does NOT make GitHub public** — `client_secret` still required at the auth-code exchange (community #15752). Only **S256** accepted.
- **Device Flow** is the only client-ID-only flow → best fit; avoids the broker for GitHub. Device-flow token POST needs no secret.
- Must **check "Enable Device Flow"** before using it, or endpoints return HTTP 400.
- No read-only `repo` variant: `repo` grants full read+write. For read-only use `read:user` + `public_repo`. For read-only **private** repo content, an OAuth App can't express that — use a **GitHub App** (fine-grained "Contents: Read-only") or fine-grained PAT.
- OAuth Apps allow **only one** callback URL — GitHub gets the generic `/v1/connectors/oauth/callback`.
- Client secret shown **once** — copy immediately into broker; if lost, regenerate + rotate.
- Tokens historically don't expire unless you enabled token expiration (then you get a `refresh_token` — handle refresh).
- Scopes normalize: requesting `user,gist,user:email` yields `user+gist` (email implied). Request minimal **leaf** scopes (`read:user`, not `user`).

**Sources:** docs.github.com/apps/oauth-apps/building-oauth-apps/creating-an-oauth-app · /authorizing-oauth-apps · /scopes-for-oauth-apps · /maintaining-oauth-apps/modifying-an-oauth-app · github.blog/changelog/2025-07-14-pkce-support · github.com/orgs/community/discussions/15752 · github.blog/changelog/2022-03-16-enable-oauth-device-authentication-flow · /2020-07-27-oauth-2-0-device-authorization-flow

---

## 4. What to hand back to Cortex

Give Cortex exactly these per provider:

| Provider | Give to the app (public) | Give to the broker (secret) | Broker URL note |
|---|---|---|---|
| **Notion** | — (nothing ships in app) | `CORTEX_BROKER_NOTION_CLIENT_ID` = OAuth client ID; `CORTEX_BROKER_NOTION_CLIENT_SECRET` = OAuth client secret | Broker does `POST https://api.notion.com/v1/oauth/token` (HTTP Basic). Store long-lived `access_token`. |
| **Google** | Info.plist `CortexGoogleOAuthClientID` = Web-app Client ID | — (secret kept out of everything; app exchanges via PKCE) | No broker. |
| **Microsoft** | Info.plist `CortexMicrosoftOAuthClientID` = Application (client) ID | — | No broker; public PKCE. |
| **Slack** | Slack Client ID (ship as public client ID, e.g. `CortexSlackOAuthClientID`) | — | No broker; PKCE. Rotate 30-day refresh token in app. |
| **GitHub** | Client ID (Device Flow → ship as public client ID) | `CORTEX_BROKER_GITHUB_CLIENT_ID` + `CORTEX_BROKER_GITHUB_CLIENT_SECRET` **only if using auth-code path B** | Device Flow (recommended) = no broker. Auth-code path = broker does the exchange. |

**Broker URL:** hand me the hosted Cortex Cloud broker base URL for the confidential exchanges (Notion always; GitHub only if you choose auth-code path B). If any provider ever forbids loopback and forces a hosted HTTPS callback the broker owns, flag it — **none of the five require that today** (all accept the loopback).

## Comparison table

| Provider | Client type | Secret needed? | Broker? | Verification/review | Time-to-live |
|---|---|---|---|---|---|
| **Notion** | Confidential (Public integration; no PKCE) | **Yes** (client secret) | **Yes** | None (fill org fields only) | **Immediate** |
| **Google — Calendar / Docs / `drive.file`** | Public (Web app + PKCE) | No | No | Sensitive-scope verification (brand + video + justification); no CASA | ~2–3 days brand, up to ~10 days overall |
| **Google — Gmail `gmail.readonly` / Drive `drive.readonly`** | Public (Web app + PKCE) | No | No | Restricted → **CASA** (paid ~ up to $3k/yr, annual) + full verification | Days-to-weeks + CASA cycle |
| **Microsoft** | Public (native + PKCE) | No | No | No scope review; **publisher verification** (free, minutes) practically required for multitenant | Minutes (once partner-enrolled) |
| **Slack** | Public (PKCE, one-way) | No | No | No review; self-serve "Activate Public Distribution" | **Immediate** (test loopback first) |
| **GitHub (Device Flow)** | Confidential app, but Device Flow uses client ID only | No (Device Flow) | No (Device Flow) | None | **Immediate** |
| **GitHub (auth-code)** | Confidential | **Yes** (secret) | **Yes** | None | **Immediate** |
