# Setting up "Sign in with…" for the OAuth connectors

Token connectors (Slack, GitHub, Linear, Jira, Readwise, Raindrop) need **no setup here** —
the user pastes a personal token and it works. This guide is only for the connectors that use
real OAuth "Sign in" buttons, which are **dormant until you register an app with each provider**
and give Cortex the client id/secret:

| Connector(s) | Provider app you register | Cortex env vars |
|---|---|---|
| Gmail, Google Drive, (Google) Calendar | Google Cloud OAuth client | `CORTEX_GOOGLE_OAUTH_CLIENT_ID`, `CORTEX_GOOGLE_OAUTH_CLIENT_SECRET` |
| Notion | Notion public integration | `CORTEX_NOTION_OAUTH_CLIENT_ID`, `CORTEX_NOTION_OAUTH_CLIENT_SECRET` |
| Outlook / Microsoft 365 | Microsoft Entra (Azure) app | `CORTEX_OUTLOOK_OAUTH_CLIENT_ID`, `CORTEX_OUTLOOK_OAUTH_CLIENT_SECRET` |

There's a **separate** pair for "Sign in with Google/GitHub" to your **Cortex account** (not the
same as reading your data): `CORTEX_OIDC_GOOGLE_CLIENT_ID/SECRET`, `CORTEX_OIDC_GITHUB_CLIENT_ID/SECRET`.

## The one thing people get wrong: redirect URIs
Register **both** the local-app and hosted redirect URIs so it works either way. Use your real
public API origin for the hosted one (`https://api.signindoppl.com`).

| Purpose | Redirect URI(s) to register |
|---|---|
| Google **connectors** (Gmail/Drive) | `http://127.0.0.1:8766/v1/connectors/google/oauth/callback` **and** `https://api.signindoppl.com/v1/connectors/google/oauth/callback` |
| Notion / Outlook **connectors** (managed) | `http://127.0.0.1:8766/v1/connectors/oauth/callback` **and** `https://api.signindoppl.com/v1/connectors/oauth/callback` |
| Google/GitHub **account login** (OIDC) | `https://api.signindoppl.com/v1/auth/oauth/google/callback` and `.../github/callback` |

---

## 1. Google (Gmail + Google Drive + Calendar) — ~10 min
1. Go to <https://console.cloud.google.com> → create a project (e.g. "Cortex").
2. **APIs & Services → Enabled APIs** → enable **Gmail API** and **Google Drive API** (and **Google Calendar API** if you want calendar).
3. **APIs & Services → OAuth consent screen** → External → fill app name/support email → add scopes:
   `.../auth/gmail.readonly`, `.../auth/drive.readonly`, (optional) `.../auth/calendar.readonly`.
   Add yourself as a **Test user** (you can publish later for public launch).
4. **APIs & Services → Credentials → Create credentials → OAuth client ID → Web application.**
   Under **Authorized redirect URIs**, add the two Google connector URIs from the table above.
5. Copy the **Client ID** and **Client secret** →
   `CORTEX_GOOGLE_OAUTH_CLIENT_ID` / `CORTEX_GOOGLE_OAUTH_CLIENT_SECRET`.

## 2. Notion — ~5 min
1. Go to <https://www.notion.so/my-integrations> → **New integration** → **Public integration**
   (public is what enables the OAuth "Sign in" flow; internal integrations use a pasted token instead).
2. Set the **Redirect URI(s)** to the two Notion/Outlook managed URIs from the table above.
3. Capabilities: **Read content** is enough.
4. Copy **OAuth client ID** + **OAuth client secret** →
   `CORTEX_NOTION_OAUTH_CLIENT_ID` / `CORTEX_NOTION_OAUTH_CLIENT_SECRET`.
   (Users still "share" the pages they want with the integration after connecting.)

## 3. Microsoft (Outlook / Microsoft 365) — ~10 min
1. Go to <https://entra.microsoft.com> → **App registrations → New registration**.
2. **Supported account types:** personal + work/school (multitenant) if you want both.
3. **Redirect URI:** platform **Web**, add the two Notion/Outlook managed URIs from the table.
4. **API permissions → Microsoft Graph → Delegated:** `Mail.Read`, `User.Read`, `offline_access`.
5. **Certificates & secrets → New client secret** → copy the **Value** (not the ID).
6. Copy **Application (client) ID** + the secret →
   `CORTEX_OUTLOOK_OAUTH_CLIENT_ID` / `CORTEX_OUTLOOK_OAUTH_CLIENT_SECRET`.

## 4. Account login — Google + GitHub (optional; separate from data)
- **GitHub** (2 min, no review): <https://github.com/settings/developers> → New OAuth App →
  Homepage `https://trydoppl.com`, callback `https://api.signindoppl.com/v1/auth/oauth/github/callback` →
  `CORTEX_OIDC_GITHUB_CLIENT_ID/SECRET`.
- **Google login:** reuse the project from §1; add an OAuth client (or reuse) with redirect
  `https://api.signindoppl.com/v1/auth/oauth/google/callback` → `CORTEX_OIDC_GOOGLE_CLIENT_ID/SECRET`.
  (Publishing the consent screen is required for non-test users — a Lane-C launch item.)

---

## Where to put the env vars
- **Hosted backend (mini/cloud):** add the vars to `~/CortexServer/cortex.env` (mini) or the
  deploy env, then restart the API. That's what makes the buttons live for account users.
- **Local bundled app:** the macOS app passes OAuth client ids/secrets it has been given into the
  bundled backend at launch (Settings → advanced connector config). For a local user, paste the
  same client id/secret there.

## Verify
After setting a provider's vars and restarting: the connector's **"Sign in with …"** button in
Cortex should open the provider's consent page, and after approving, the connection flips to
Connected and starts syncing. If the button still shows only instructions, the client id/secret
weren't picked up (wrong env location, or the API wasn't restarted).

## Reality check
- **Slack/GitHub/Linear/Jira/Readwise/Raindrop:** no app registration — user pastes a personal
  token. Already works.
- **ChatGPT/Claude/Gemini/etc.:** no OAuth exists — export file only (Cortex auto-detects the
  export in Downloads/`~/CortexImports`).
- Until §1–§3 are done, those "Sign in" buttons in the app show the setup instructions but can't
  complete a sign-in — that's expected, not a bug.
