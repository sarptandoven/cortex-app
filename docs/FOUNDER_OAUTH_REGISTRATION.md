# Founder OAuth registration runbook

**Who this is for:** the founder (you). The agent cannot do these steps: they need YOUR Google,
Microsoft, and Notion accounts, and access to the Hetzner box. Once you finish, every "Sign in with
X" source flips from **"Available soon"** to true one-click login.

**What already works (don't touch):** the browser sign-in flow is built end-to-end (app opens the
system browser → PKCE → loopback callback on the local server → token exchange → refresh →
auto-sync). Every connector also keeps a **token-paste fallback**, and the App Store (sandbox) build
behaves as before. You are only supplying the missing OAuth **client IDs** (and, for Notion only, a
server-side secret).

**Local-first invariant (do not break):** synced tokens and all user content stay on the user's Mac.
Google and Microsoft are **public PKCE desktop clients** with **no secret on the device**. Notion is
the one confidential provider, and its secret lives **only on the server**, which may proxy the
Notion code→token exchange but **must not persist tokens**.

---

## Shared facts (every provider)

- **Flow:** OAuth 2.0 Authorization Code + PKCE (S256), system browser, loopback redirect to the
  bundled local server on port **8766**.
- **Redirect / callback URIs to register (exact, copy-paste):**
  - Managed (Microsoft, Notion, and most others): `http://127.0.0.1:8766/v1/connectors/oauth/callback`
  - Google-specific: `http://127.0.0.1:8766/v1/connectors/google/oauth/callback`
  - Register the **exact host + path + port `:8766`**. Prefer `127.0.0.1` over `localhost`.
- **Where a client ID goes (Google, Microsoft):** either the `macos/Info.plist` key for that provider
  (then rebuild the app) **or** the matching `CORTEX_<PROVIDER>_OAUTH_CLIENT_ID` env var read at
  launch. Info.plist is the shipping path; the env var is for local testing without a rebuild.
- **Server (Notion broker only):** api.signindoppl.com (Hetzner CPX11, 5.78.188.95). Env file is
  `/etc/cortex/cortex.env`; the service is `cortex-api` (uvicorn on `127.0.0.1:8766`) plus
  `cortex-worker`.

---

## 1. Google (Gmail + Google Drive, one shared client)

One "Connect Google" grants read-only Gmail **and** Drive in a single consent. Register **one**
desktop client; the app ships **no Google secret** (public client, PKCE proves the exchange).

### Console steps

1. Open the Google Cloud Console: <https://console.cloud.google.com/>
2. Create or select a project (top bar project picker → **New project**, name it e.g. `Cortex`).
3. **OAuth consent screen** → <https://console.cloud.google.com/apis/credentials/consent>
   - User type: **External** → **Create**.
   - App name: `Cortex`. User support email: your email. Developer contact: your email. **Save and continue**.
   - **Scopes** → **Add or remove scopes**, add exactly:
     - `openid`
     - `.../auth/userinfo.email` (the "email" scope)
     - `https://www.googleapis.com/auth/drive.readonly`
     - `https://www.googleapis.com/auth/gmail.readonly`
   - **Save and continue**. Add yourself under **Test users** while the app is unverified.
   - Note: `gmail.readonly` and `drive.readonly` are **restricted/sensitive** scopes. Testing works
     immediately for test users; broad public launch needs Google's OAuth **verification** (brand
     review + possibly a CASA security assessment). Start in Testing mode, submit for verification
     before wide distribution.
4. **Credentials** → <https://console.cloud.google.com/apis/credentials> → **Create credentials** →
   **OAuth client ID**.
   - Application type: **Desktop app**. Name: `Cortex Desktop`. **Create**.
   - Copy the **Client ID** (looks like `NNN-xxxx.apps.googleusercontent.com`).
   - Google desktop clients also issue a non-confidential "installed app" secret. **You do not need
     it**, Cortex uses PKCE with no secret on the device. Ignore/discard it.
5. **Enable the APIs** the scopes touch: <https://console.cloud.google.com/apis/library> → enable
   **Gmail API** and **Google Drive API**.

### Where the value goes

- Ship it: put the Client ID in `macos/Info.plist` under `CortexGoogleOAuthClientID`, then rebuild.
- Or test without a rebuild: export `CORTEX_GOOGLE_OAUTH_CLIENT_ID=<the client id>` before launch.
- **No secret goes anywhere on the device.**

### Verify

Open the Gmail (or Google Drive) source in the app → tile now reads **"Sign in"** (not "Available
soon"). Click it → system browser opens a `accounts.google.com` consent for Gmail + Drive read-only →
after allow, the browser lands on a local success page and the tile shows **Connected** and begins a
first sync.

---

## 2. Microsoft (Outlook)

Public client, PKCE, **no secret on the device**.

### Console steps

1. Open Microsoft Entra ID (Azure): <https://entra.microsoft.com/> → **Identity** → **Applications**
   → **App registrations** → **New registration**.
   (Direct: <https://portal.azure.com/#view/Microsoft_AAD_RegisteredApps/ApplicationsListBlade>)
2. Name: `Cortex`. Supported account types: **Accounts in any organizational directory and personal
   Microsoft accounts** (multi-tenant + personal). Leave Redirect URI blank here → **Register**.
3. Copy the **Application (client) ID** from the Overview page.
4. **Authentication** → **Add a platform** → **Mobile and desktop applications**.
   - Add custom redirect URI: `http://127.0.0.1:8766/v1/connectors/oauth/callback`
   - Under **Advanced settings**, set **Allow public client flows** = **Yes**. **Save**.
   - (If the loopback URI is rejected in the textbox, add it via **Manifest** →
     `replyUrlsWithType` with type `PublicClient`.)
5. **API permissions** → **Add a permission** → **Microsoft Graph** → **Delegated permissions**, add
   exactly: `offline_access`, `User.Read`, `Mail.Read`. **Add permissions**. (Admin consent is not
   required for these delegated scopes on personal accounts.)

### Where the value goes

- Ship it: put the Application (client) ID in `macos/Info.plist` under `CortexMicrosoftOAuthClientID`,
  then rebuild.
- Or test without a rebuild: export `CORTEX_MICROSOFT_OAUTH_CLIENT_ID=<the client id>` before launch.
- Leave `CortexMicrosoftOAuthClientSecret` **empty**. No secret on the device.

### Verify

Open the Outlook source → tile reads **"Sign in"** → click → `login.microsoftonline.com` consent for
`User.Read` + `Mail.Read` → after allow, local success page, tile shows **Connected** and mail sync
starts.

---

## 3. Notion (confidential: secret stays on the SERVER only)

Notion's token exchange requires HTTP Basic `base64(client_id:client_secret)` and offers no
public-client PKCE, so the secret **cannot** ship in the app. The app sends the OAuth code to the
hosted broker; the broker holds the secret, does the code→token exchange, and returns the token
**without persisting it**. Content sync still runs on the user's Mac.

### Console steps

1. Open <https://www.notion.so/my-integrations> → **New integration**.
2. Choose **Public** integration (a public OAuth integration, not internal). Name: `Cortex`. Fill the
   company name, logo, and required URLs (a website + a privacy policy URL are required for public).
3. Under **OAuth Domain & URIs**, set the **Redirect URI** to:
   `http://127.0.0.1:8766/v1/connectors/oauth/callback`
4. Capabilities: **Read content** is enough for import. Submit/save.
5. Copy the **OAuth client ID** and **OAuth client secret** from the integration's **Secrets** tab.

### Where the values go (SERVER env, NOT the app)

SSH to the box and set them in the deploy env, then restart. Do **not** put these in Info.plist.

```bash
ssh root@5.78.188.95   # api.signindoppl.com

# Append the two broker vars (edit in place; keep existing lines):
printf 'CORTEX_BROKER_NOTION_CLIENT_ID=%s\n'     'PASTE_NOTION_CLIENT_ID'     >> /etc/cortex/cortex.env
printf 'CORTEX_BROKER_NOTION_CLIENT_SECRET=%s\n' 'PASTE_NOTION_CLIENT_SECRET' >> /etc/cortex/cortex.env

# Lock down the env file (contains a secret) and restart the service:
chmod 600 /etc/cortex/cortex.env
systemctl restart cortex-api cortex-worker

# Confirm they loaded (values are masked in this check):
systemctl show cortex-api -p EnvironmentFiles
grep -c '^CORTEX_BROKER_NOTION_' /etc/cortex/cortex.env   # expect 2
```

> The broker allowlists only the loopback callback paths, so it can never be turned into an open
> token-minting oracle. Rotating the secret later = edit the same two lines and
> `systemctl restart cortex-api cortex-worker`. No app rebuild is ever needed for Notion.

### Verify

- On the box: `curl -fsS http://127.0.0.1:8766/v1/connectors/oauth/broker/providers` should list
  `notion` as configured (an unconfigured provider is simply absent).
- In the app: open the Notion source → tile reads **"Sign in"** → click → `notion.so` authorize +
  page-picker → after allow, local success page, tile shows **Connected**. The Mac holds the token;
  the server kept nothing.

---

## 4. GitHub (device flow, already wired)

Nothing to register. The GitHub OAuth app and its client ID (`Ov23liwB0zkC2Qac88ig`) are already
embedded (`macos/Info.plist` → `CortexGitHubOAuthClientID`). You only flip one switch.

### Console step

1. Open <https://github.com/settings/developers> → **OAuth Apps** → open the Cortex app (client ID
   `Ov23liwB0zkC2Qac88ig`).
2. Check **Enable Device Flow** → **Update application**.

### Verify

Open the GitHub source → **"Sign in"** → the app shows a short user code and opens
`https://github.com/login/device` → enter the code, approve → tile shows **Connected**. (Device flow
is secretless; no redirect URI needed.)

---

## No-API providers (honest note)

**ChatGPT, Claude, and Gemini have no consumer data API**, for anyone, not just Cortex. There is
**nothing to register** for these. They stay on the **guided-export → auto-import** path: the app
walks the user through requesting their data export from the provider, then auto-imports the file
when it arrives. Leave these as-is.

---

## One-glance summary

| Provider | Where the value lives | Secret on device? | Registration needed |
|---|---|---|---|
| Google (Gmail + Drive) | Info.plist `CortexGoogleOAuthClientID` / env `CORTEX_GOOGLE_OAUTH_CLIENT_ID` | No | Desktop OAuth client + consent screen |
| Microsoft (Outlook) | Info.plist `CortexMicrosoftOAuthClientID` / env `CORTEX_MICROSOFT_OAUTH_CLIENT_ID` | No | Entra app registration (public client) |
| Notion | Server env on the box: `CORTEX_BROKER_NOTION_CLIENT_ID` + `_CLIENT_SECRET` | No (server-side only) | Public OAuth integration |
| GitHub | Already embedded (`Ov23liwB0zkC2Qac88ig`) | No (device flow) | Flip "Enable Device Flow" |
| ChatGPT / Claude / Gemini | n/a | n/a | None guided export → auto-import |
