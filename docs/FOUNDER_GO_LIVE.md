# Founder go-live runbook — required accounts + user data + App Store

Everything in the code is done and verified (auth, all four sign-in providers, admin dashboard,
per-user data/sync/backup, App Store compliance, a CI gate that pins the compliance attributes).
What remains needs **your** external accounts and credentials — I cannot do these. Do them **in
order**; each step says exactly what to set. When they're done, tell me and I do the "After you" steps.

---

## A. Deploy the hosted backend (`api.trydoppl.com`)

The app talks to this over HTTPS for accounts + cloud memory. Without it live, sign-in fails.

1. Deploy `backend/` (FastAPI `backend.app.main:app`) behind TLS at `https://api.trydoppl.com`.
   `deploy/Caddyfile` already maps `api.trydoppl.com → uvicorn:8766`. Run with
   `uvicorn backend.app.main:app` (or gunicorn+uvicorn workers). Python 3.12; `pip install -r
   backend/requirements.txt`.
2. Set these environment variables on the server:
   - `CORTEX_API_KEY` = a long random string. **This is also your admin key** for the dashboard.
   - `CORTEX_AUTH_ENABLED=1`
   - `CORTEX_ACCOUNTS_DB_PATH` = a durable path (e.g. `/data/accounts.sqlite`), or set
     `CORTEX_HOSTED_DATABASE_URL` if you use the Postgres path.
   - `CORTEX_PUBLIC_APP_URL=https://api.trydoppl.com` (used to build OAuth redirect URIs).
   - Email (for verification links): either `CORTEX_AUTH_AUTOVERIFY=1` (beta: accounts activate
     instantly, no email needed) **or** SMTP: `CORTEX_SMTP_HOST/_PORT/_USERNAME/_PASSWORD/_FROM_ADDR`.
   - The OIDC client IDs/secrets from Part B (below).
3. Verify: `GET https://api.trydoppl.com/health` returns ok. Open
   `https://api.trydoppl.com/admin`, enter your `CORTEX_API_KEY` → you should see the user
   dashboard (0 users at first).

> The whole accounts + admin stack is proven working locally (signup → login → tokens; admin
> metrics/accounts with `Authorization: Bearer <CORTEX_API_KEY>`). It only needs to be running
> somewhere reachable.

---

## B. Register the OAuth apps (one per provider)

For each provider you enable, create an app in that provider's console and set the env vars. The
app shows a provider's button once its backend is configured. **Google/GitHub use the browser flow;
Apple uses the native flow.**

### Google  (https://console.cloud.google.com → APIs & Services → Credentials → OAuth client ID, type "Web application")
- Authorized redirect URI: `https://api.trydoppl.com/v1/auth/oauth/google/callback`
- Set: `CORTEX_OIDC_GOOGLE_CLIENT_ID`, `CORTEX_OIDC_GOOGLE_CLIENT_SECRET`

### GitHub  (https://github.com/settings/developers → New OAuth App)
- Authorization callback URL: `https://api.trydoppl.com/v1/auth/oauth/github/callback`
- Set: `CORTEX_OIDC_GITHUB_CLIENT_ID`, `CORTEX_OIDC_GITHUB_CLIENT_SECRET`

### Apple — Sign in with Apple  (https://developer.apple.com → Certificates, Identifiers & Profiles)
- On the **App ID `com.cortex.doppl`**, enable the **"Sign in with Apple"** capability.
- For NATIVE macOS sign-in (what the app uses), the id_token audience is the bundle id, so set:
  `CORTEX_OIDC_APPLE_CLIENT_ID=com.cortex.doppl`  (no client secret needed for the native flow).

---

## C. Apple Developer + App Store Connect (for the App Store build "Doppl")

1. **Sign in with Apple capability** on App ID `com.cortex.doppl` (same as B/Apple). Then
   **regenerate the Mac App Store provisioning profile** — the build now carries the
   `com.apple.developer.applesignin` entitlement, so a profile without that capability will fail to
   sign/validate.
2. **Certificates** (already have the app-signing cert `3rd Party Mac Developer Application: Sarp
   Doven (P4H96J3VXY)`): also create the **Mac Installer Distribution** cert if you don't have it.
3. **App Store Connect → App Privacy** — set the nutrition label to match `PrivacyInfo.xcprivacy`:
   - Email Address → collected, **Linked** to identity, **Not** used for tracking, purpose *App Functionality*.
   - Name → collected, **Linked**, **Not** tracking, *App Functionality*.
   - No other data types; no tracking.
4. **App Store Connect → App Review Information → Notes**: paste `docs/APP_REVIEW_NOTES.md`, and
   fill in the demo account (see step D3).
5. Export compliance stays **"No"** (the app-store build uses only exempt standard HTTPS).

---

## D. Flip the switch, then hand back to me

1. Once A + B are live, create a **demo account** (sign up in the app or via
   `POST https://api.trydoppl.com/v1/auth/signup`) and load it with a bit of sample memory so App
   Review sees a working app.
2. Put the demo email/password into `docs/APP_REVIEW_NOTES.md` (the `<APP REVIEW DEMO …>` placeholders).
3. To make accounts **required**, set `CortexRequireAccount` to `true` in `macos/Info.plist`
   (I can do this in one line once you confirm the backend is live — say the word).

### After you (I do these)
- Flip `CortexRequireAccount=true`, bump the build number.
- Rebuild the MAS bundle + **re-run the full compliance cert** (deterministic + adversarial) and the
  `scripts/appstore_compliance_lint.py` gate.
- Build the signed `.pkg` dry-run report; cut a fresh DMG for the direct build.
- Hand you the exact `package_app_store.sh` invocation with your identities to produce the
  uploadable `.pkg` (or upload via Transporter).

---

## What is already done (no action needed)
- Sign in with Apple (native) + Google + GitHub + email — backend + in-app UI. `/v1/auth/oauth/*`.
- Admin analytics: `GET /v1/admin/metrics`, `GET /v1/admin/accounts`, dashboard at `/admin`
  (auth: `Authorization: Bearer <CORTEX_API_KEY>`).
- Per-user cloud memory: signing in routes all memory ops to the user's hosted store; backups,
  sync cursors/devices, export all wired.
- App Store compliance (2.5.1 / 2.5.2 / 2.1a / 4 / 4.8) + privacy manifest + a CI gate
  (`scripts/appstore_compliance_lint.py`) that fails the build if any attribute drifts.
- The hosted backend now imports/starts (a bug that previously prevented it entirely is fixed).
