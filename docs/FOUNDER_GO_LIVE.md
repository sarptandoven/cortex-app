# Founder go-live — exact, step-by-step

The code is done and verified. To turn on accounts + cloud data for real users, do the steps
below **in order**. Everything is copy-paste; where you must paste a value, it says so in **bold**.
Total hands-on time ≈ 30–45 min (plus DNS/OAuth propagation waits).

Legend:
- `On your Mac:` run in Terminal from the repo folder (`.../taipei`).
- `On the server:` run after `ssh root@<VM-IP>`.
- **<...>** = a value you fill in.

---

## STEP 1 — Put the backend on a server (≈15 min, one command)

**1a. Create a server.** Go to https://console.hetzner.cloud → New project → Add Server:
- Image: **Ubuntu 24.04**
- Type: **CX32** (4 vCPU / 8 GB) — CX22 is fine for a small beta
- Add your SSH key (so you can log in without a password)
- Turn on "Backups"
- Create it, then **copy the server's IP address** (looks like `203.0.113.7`).

**1b. Point your domain at it.** In your DNS provider for `trydoppl.com`, add ONE record:
- Type: **A**, Name: **api**, Value: **<the server IP>**, TTL: default.
- This makes `api.trydoppl.com` point at the server. (Doesn't touch your website.)
- Wait ~5 min, then confirm: `On your Mac:` `dig +short api.trydoppl.com` should print the IP.

**1c. Deploy — one command.** `On your Mac:`
```bash
deploy/push.sh root@<the server IP> api.trydoppl.com
```
This uploads the code and sets up EVERYTHING on the server automatically: HTTPS certificate,
Python, the API service, background worker, firewall, nightly backups, and it **generates your
secrets**. At the very end it prints an **admin token** and a **KEK** — **copy BOTH into your
password manager immediately** (they are shown only once). The admin token = your `CORTEX_API_KEY`.

**1d. Verify it's live.** `On your Mac:`
```bash
curl https://api.trydoppl.com/health      # -> {"status":"ok", ...}
```
Then open **https://api.trydoppl.com/admin** in a browser, paste your **admin token** → you'll see
the user dashboard (0 users so far). ✅ Accounts backend + admin are now running.

> Email is off by default (`CORTEX_AUTH_AUTOVERIFY=1`): people sign up and are active instantly, no
> email server needed. Fine for beta. (For public launch, add Postmark later — see the env file.)

---

## STEP 2 — Turn on the sign-in providers you want

Email/password already works with zero setup. For the social buttons, do each provider you want.
After editing the server's env file, apply it with:
`On the server:` `nano /etc/cortex/cortex.env`  (edit) → then  `systemctl restart cortex-api`

### 2a. GitHub (2 minutes, no review)
1. https://github.com/settings/developers → **New OAuth App**.
2. Application name: `Doppl`. Homepage URL: `https://trydoppl.com`.
   **Authorization callback URL:** `https://api.trydoppl.com/v1/auth/oauth/github/callback`
3. Create it → copy the **Client ID**; click "Generate a new client secret" → copy the **secret**.
4. `On the server:` in `/etc/cortex/cortex.env` set:
   ```
   CORTEX_OIDC_GITHUB_CLIENT_ID=<paste client id>
   CORTEX_OIDC_GITHUB_CLIENT_SECRET=<paste secret>
   ```
   then `systemctl restart cortex-api`. "Continue with GitHub" now works.

### 2b. Google (works, but Google review takes 1–2 weeks for >100 users)
1. https://console.cloud.google.com → create a project → APIs & Services → **OAuth consent screen**
   (External; add app name `Doppl`, your email, the domain `trydoppl.com`).
2. Credentials → **Create credentials → OAuth client ID → Web application**.
   **Authorized redirect URI:** `https://api.trydoppl.com/v1/auth/oauth/google/callback`
3. Copy the **Client ID** and **Client secret** → set on the server:
   ```
   CORTEX_OIDC_GOOGLE_CLIENT_ID=<...>
   CORTEX_OIDC_GOOGLE_CLIENT_SECRET=<...>
   ```
   then `systemctl restart cortex-api`.

### 2c. Sign in with Apple (needed for the App Store — see Step 3)
1. https://developer.apple.com/account → Certificates, Identifiers & Profiles → **Identifiers** →
   click your App ID **`com.cortex.doppl`** → check **"Sign in with Apple"** → Save.
2. `On the server:` set `CORTEX_OIDC_APPLE_CLIENT_ID=com.cortex.doppl` → `systemctl restart cortex-api`.
   (No secret needed — the Mac app uses the native flow.)

---

## STEP 3 — App Store prep (only for submitting "Doppl" to the Mac App Store)

1. **Apple Developer portal** (developer.apple.com → Certificates, Identifiers & Profiles):
   - App ID `com.cortex.doppl`: enable **Sign in with Apple** (done in 2c).
   - **Profiles** → find your Mac App Store distribution profile for `com.cortex.doppl` → **Edit →
     Generate/Download again** (it must now include the Sign in with Apple capability), or create a
     new "Mac App Store" distribution profile. Download the `.provisionprofile`.
   - If you don't have it: **Certificates** → create a **"Mac Installer Distribution"** cert.
2. **App Store Connect** (appstoreconnect.apple.com → your app → App Privacy):
   - Add data type **Email Address**: "Yes, we collect", **Linked to the user**, **Not used for
     tracking**, purpose **App Functionality**.
   - Add data type **Name**: same settings.
   - Nothing else; answer **No** to tracking.
3. **App Store Connect → App Review Information → Notes:** paste the contents of
   `docs/APP_REVIEW_NOTES.md`, and fill in the demo login from Step 4.

---

## STEP 4 — Create a demo account for Apple's reviewer
1. In the Doppl app (or via the API), sign up an account, e.g. `review@trydoppl.com` /
   a strong password. Add a couple of notes so the reviewer sees a working app.
   (API way, `On your Mac:`
   `curl -X POST https://api.trydoppl.com/v1/auth/signup -H 'content-type: application/json' -d '{"email":"review@trydoppl.com","password":"<a strong password>"}'`)
2. Put that email + password into `docs/APP_REVIEW_NOTES.md` where it says
   `<APP REVIEW DEMO EMAIL>` / `<APP REVIEW DEMO PASSWORD>`.

---

## STEP 5 — Tell me "the backend is live", and I finish it
Once Steps 1–4 are done, reply and I will (no action from you):
1. Set `CortexRequireAccount=true` in `macos/Info.plist` and bump the build number.
2. Rebuild the Mac App Store bundle and re-run the full compliance certification + the CI gate.
3. Give you the exact `macos/package_app_store.sh` command (with your signing identity + the new
   provisioning profile) that produces the uploadable `.pkg`, and the Transporter upload steps.

---

## To update the backend later (after any new build)
`On your Mac:` `deploy/push.sh root@<the server IP> --update`

## Where to see your users
Anytime: open **https://api.trydoppl.com/admin**, paste your admin token (`CORTEX_API_KEY`).
Shows total/active/pending users, signups per day, which provider they used, and a searchable list.
JSON if you prefer: `curl -H "Authorization: Bearer <admin token>" https://api.trydoppl.com/v1/admin/metrics`

## Already done (nothing for you)
Sign in with Apple/Google/GitHub/email (backend + app UI), per-user cloud memory + backups + sync,
the admin dashboard, App Store compliance + a CI gate that blocks drift, and the backend-import bug
that previously stopped the server from starting at all.
