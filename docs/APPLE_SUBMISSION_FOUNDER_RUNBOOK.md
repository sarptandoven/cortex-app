# Doppl — Mac App Store Submission Runbook (founder-only steps)

Everything the automation could NOT do because it needs **your Apple Developer
account, physical device access, or App Store Connect**. Each step is exact:
where to click, what to type, which command to run, and how to verify it worked.

**Known constants for this app** (use these verbatim):
- App name (Mac App Store): **Doppl**
- Bundle ID: **`com.cortex.doppl`**
- Apple Team ID: **`P4H96J3VXY`** (Sarp Doven)
- Repo root: `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei` (called `$REPO` below)
- MAS packaging script: `macos/package_app_store.sh`
- Signed installer output: `outputs/Doppl-AppStore-0.2.0-<build>/Doppl-AppStore-0.2.0-<build>.pkg`
- Review notes to paste into App Review: `docs/APP_REVIEW_NOTES.md`
- Hosted backend: `https://api.signindoppl.com` (deploy via `deploy/push.sh`)

**Certificates already in your keychain** (verified): `3rd Party Mac Developer
Application: Sarp Doven (P4H96J3VXY)` and `3rd Party Mac Developer Installer: Sarp
Doven (P4H96J3VXY)`. The build already signs the app + `.pkg` with these and the
deep signature (incl. the bundled Python.framework) verifies clean. **The only
signing gate left is the provisioning profile (Step 3).**

---

## Step 0 — Prerequisites (one-time)

0.1 **Apple Developer Program membership** must be active ($99/yr). Verify at
<https://developer.apple.com/account> — top of page should show your team
"Sarp Doven (P4H96J3VXY)" with an active membership. If it says "enrollment
pending," you cannot create profiles until it clears.

0.2 **Xcode + command line tools** on this Mac. Verify:
```sh
xcode-select -p        # should print a path, not an error
xcrun --find swiftc    # should print the compiler path
```
If missing: install Xcode from the Mac App Store, then `xcode-select --install`.

0.3 **Accept any pending agreements** in App Store Connect
(<https://appstoreconnect.apple.com> → Business / Agreements, Tax, and Banking).
A new app cannot be submitted while the **Paid Apps** or free-app agreement is
unsigned. Doppl is free, so you at minimum need the free-app agreement active.

---

## Step 1 — Certificates (you likely already have what you need)

You already have the two "3rd Party Mac Developer" certs, which are valid for Mac
App Store submission. Apple's current portal offers **Apple Distribution** or
**Mac App Distribution** + **Mac Installer Distribution** certs; the installed
legacy pair also works. If App Store Connect later
complains about the cert type, create the newer ones:

1.1 Go to <https://developer.apple.com/account/resources/certificates/list>.
1.2 Click the **+** (Create a Certificate).
1.3 Under **Software**, choose **Apple Distribution** (unified) or **Mac App
Distribution** → Continue.
1.4 Create a CSR on this Mac: open **Keychain Access** → menu **Keychain Access ▸
Certificate Assistant ▸ Request a Certificate From a Certificate Authority** →
enter your email, Common Name = "Sarp Doven", choose **Saved to disk** → Continue
→ save the `.certSigningRequest`.
1.5 Back in the browser, upload that CSR → Continue → **Download** the `.cer` →
double-click it to add to your keychain.
1.6 Repeat 1.2–1.5 choosing **Mac Installer Distribution** for the installer cert.
1.7 Verify both now appear:
```sh
security find-identity -v | grep -Ei "Apple Distribution|Mac App Distribution|Mac Developer"
```

> You do NOT need this if the current 3rd Party Mac Developer certs are accepted at
> upload. Try the existing ones first (Step 5) and only create these if rejected.

---

## Step 2 — App ID with Sign in with Apple

The 4.8 code fix is done; it needs the App ID to actually carry the capability.

2.1 Go to <https://developer.apple.com/account/resources/identifiers/list>.
2.2 If `com.cortex.doppl` already exists, click it; otherwise click **+** →
**App IDs** → **App** → Continue, and set:
- Description: `Doppl`
- Bundle ID: **Explicit** → `com.cortex.doppl`
2.3 In the **Capabilities** list, tick **Sign in with Apple** (leave it "Enable as
a primary App ID") and **Keychain Sharing**. The latter is required because the
shipping entitlements use the app's own keychain access group to protect session
credentials. **App Sandbox** is a local signing entitlement rather than a portal
toggle. Do NOT enable other capabilities the app does not use.
2.4 Click **Save** (or **Register**). The App ID's enabled capabilities must match
`macos/AppStore.entitlements` — that file declares `com.apple.developer.applesignin`,
the app's keychain access group, `com.apple.security.app-sandbox`, network
client/server, and file bookmarks, which is exactly this configuration.

Verify: the App ID detail page shows **Sign in with Apple = Enabled** and
**Keychain Sharing = Enabled**.

---

## Step 3 — Mac App Store provisioning profile (THE signing gate)

This is the one artifact the automation cannot produce; it needs your account.

3.1 Go to <https://developer.apple.com/account/resources/profiles/list>.
3.2 Click **+** → under **Distribution** choose **Mac App Store Connect** (older
UIs call it "Mac App Store") → Continue.
3.3 App ID: select **`com.cortex.doppl`** → Continue.
3.4 Certificate: select your distribution cert (the **Apple Distribution** or
**Mac App Distribution** one if you made it in Step 1, otherwise **3rd Party Mac
Developer Application**) → Continue.
3.5 Name it `Doppl Mac App Store` → **Generate** → **Download**. You get a file like
`Doppl_Mac_App_Store.provisionprofile`. Put it somewhere stable, e.g.
`~/Doppl_Mac_App_Store.provisionprofile`.

The packaging script embeds it at `Contents/embedded.provisionprofile` before it
signs — you just point `CORTEX_MAS_PROFILE` at it in Step 5.

---

## Step 4 — Backend configuration (so Sign in with Apple actually authenticates)

The app's SIWA button is wired and entitled, but the **token exchange** happens on
your hosted backend, which needs Apple's client id set. Without this, tapping
"Sign in with Apple" opens the Apple sheet and then returns a clean error.

4.1 Creating a **Services ID** in the Apple Developer portal is NOT required for
the native macOS SIWA flow — the native flow uses the **App ID / bundle id**
(`com.cortex.doppl`) as its client_id. So on the backend set:
```sh
CORTEX_OIDC_APPLE_CLIENT_ID=com.cortex.doppl
```
4.2 SSH to the hosted server and set it as a server-side env var, then restart.
The env lives with the other provider secrets (per the deployment). On the box:
```sh
ssh root@5.78.188.95
# edit /etc/cortex/cortex.env, add:
#   CORTEX_OIDC_APPLE_CLIENT_ID=com.cortex.doppl
# then restart:
systemctl restart cortex-api
sudo grep -Fx 'CORTEX_OIDC_APPLE_CLIENT_ID=com.cortex.doppl' /etc/cortex/cortex.env
systemctl is-active cortex-api
curl -sS -o /dev/null -w '%{http_code}\n' \
  https://api.signindoppl.com/v1/auth/oauth/apple/native \
  -X POST -d '{}' -H 'content-type: application/json'
#   grep must echo the exact setting; systemctl must print "active"; curl must print 401
```
The empty-token probe intentionally returns the same generic `401` whether the
client id is absent or present, so **the exact `grep` plus active service are the
configuration checks**; the curl only confirms that the deployed native endpoint
is reachable. Do not expect that public error body to reveal server configuration.
4.3 (Only if you also want Apple as a *web* button, which we deliberately do not
render) you'd additionally need an Apple **Services ID** + key; skip it — native
SIWA on macOS does not need it.

> The macOS code change already made the SIWA button render whenever the build
> carries the entitlement (it no longer depends on the web-provider list). So once
> Steps 2–4 are done, a reviewer sees Sign in with Apple alongside Google/GitHub.

---

## Step 5 — Produce the uploadable, signed, provisioned `.pkg` (one command)

On this Mac, in `$REPO`:
```sh
cd /Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei

CORTEX_MAS_PROFILE="$HOME/Doppl_Mac_App_Store.provisionprofile" \
  ./macos/package_app_store.sh
```

If you created newer distribution certs in Step 1, also export their
exact names first (copy them from `security find-identity -v`):
```sh
export CORTEX_APPSTORE_SIGN_IDENTITY="Apple Distribution: Sarp Doven (P4H96J3VXY)"
export CORTEX_APPSTORE_INSTALLER_IDENTITY="Mac Installer Distribution: Sarp Doven (P4H96J3VXY)"
```

**Verify success** — the script writes a JSON report; it must now say:
```sh
cat outputs/Doppl-AppStore-0.2.0-38/app-store-build-report.json | grep -E 'status|uploadable|provisioning'
#   "status": "upload-candidate"
#   "uploadable": true
#   "provisioning_profile": "embedded"   (NOT "missing")
#   "provisioning_profile_validation": "validated"
```
And confirm the deep signature still verifies:
```sh
codesign --verify --deep --strict outputs/Doppl-AppStore-0.2.0-38/payload/Doppl.app && echo "signature OK"
```

---

## Step 6 — Create the app record in App Store Connect

6.1 Go to <https://appstoreconnect.apple.com/apps> → **+** → **New App**.
6.2 Fill in:
- Platforms: **macOS**
- Name: **Doppl** (must be globally unique on the App Store; if taken, pick a
  variant like "Doppl — Memory")
- Primary language: English (U.S.)
- Bundle ID: select **`com.cortex.doppl`** (it appears because of Step 2)
- SKU: any stable string, e.g. `doppl-macos-001`
- User Access: Full Access
6.3 Click **Create**. On the new app record, create/select the macOS version
**`0.2.0`** so it exactly matches this binary's `CFBundleShortVersionString`.
Do not leave the submission page at a default `1.0` unless you first change the
binary's marketing version and rebuild it.

---

## Step 7 — App metadata, privacy, export compliance, review account

On the app's **0.2.0 (Prepare for Submission)** page:

7.1 **Screenshots** (required). Use the App-Store-ready Doppl screenshots produced
in `outputs/screenshots-build38/`. macOS requires **1280×800
or 1440×900 (or 2560×1600 / 2880×1800 retina)**. If the provided PNGs are a
different size, resize/pad to one of those in Preview (Tools ▸ Adjust Size) or:
```sh
sips -z 1600 2560 outputs/screenshots-build38/2-home.png --out ~/Desktop/doppl-home-2560x1600.png
```
Upload 4–5 (Home, Review, Ask, Connections, and optionally the sign-in screen).

7.2 **Description / keywords / support URL / marketing URL** — write the product
copy. Support URL can be your site (e.g. `https://trydoppl.com` or the GitHub).

7.3 **App Privacy** (the "nutrition label"). Click **Get Started** / **Edit** and
declare — this matches `macos/PrivacyInfo.xcprivacy` and `docs/APP_REVIEW_NOTES.md`:
- **Data collected: YES.**
- **Contact Info → Email Address**: Linked to the user, **not** used for tracking,
  purpose **App Functionality** (account sign-in).
- **Contact Info → Name**: Linked to user, not tracking, App Functionality.
- **User Content → Other User Content** (the memory/notes): Linked to user, not
  tracking, App Functionality.
- Everything else: **not collected**. Do NOT check "Used to Track You".

7.4 **Export compliance / encryption.** When asked "Does your app use encryption?"
answer **NO / uses only exempt encryption**. The Mac App Store build strips
`_ssl`/OpenSSL, and `ITSAppUsesNonExemptEncryption=false` is baked into the bundle,
so it uses no non-exempt encryption. (If ASC asks again at upload, same answer.)

7.5 **App Review Information → Sign-In required.** The app requires an account, so
provide a reviewer path (pick ONE):
- **Preferred (no account needed):** in the **Notes** box, paste the contents of
  `docs/APP_REVIEW_NOTES.md`, which tells the reviewer to click **"Explore with
  sample notes, no account needed"** on the sign-in screen to exercise the whole
  app offline. Tick "Sign-in required? = NO" is NOT accurate (it does require an
  account for the full path), so instead tick **Sign-in required = YES** and ALSO
  give a demo account below, OR rely on the offline path and explain it clearly in
  Notes. Safest is to also supply a demo account:
- **Demo account:** create a real account in the app (Continue with Google/GitHub
  or email), then enter that email + password in **App Review Information → Sign-In
  Information → User name / Password**. Use a throwaway you are willing to share.

7.6 **Age rating** — fill the questionnaire (Doppl has no objectionable content →
4+).

7.7 **Category** — Productivity (primary).

---

## Step 8 — Upload the build

Two ways; **Transporter is simplest**.

8.1 **Transporter app:** install "Transporter" from the Mac App Store, open it,
sign in with your Apple ID, click **+ / Add App**, and drag in:
```
outputs/Doppl-AppStore-0.2.0-<build>/Doppl-AppStore-0.2.0-<build>.pkg
```
Click **Deliver**. Wait for "Delivered successfully."

8.2 **Command line (alternative):** re-run the packaging script with upload creds.
Create an App Store Connect **API key** first at
<https://appstoreconnect.apple.com/access/integrations/api> (Keys → +, role
"App Manager") → download the `.p8`, note the **Key ID** and **Issuer ID**. Then:
```sh
CORTEX_MAS_PROFILE="$HOME/Doppl_Mac_App_Store.provisionprofile" \
CORTEX_APPSTORE_UPLOAD=1 \
CORTEX_ASC_API_KEY_ID=XXXXXXXXXX \
CORTEX_ASC_API_ISSUER_ID=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
CORTEX_ASC_API_KEY_PATH="$HOME/AuthKey_XXXXXXXXXX.p8" \
  ./macos/package_app_store.sh
```

After upload, the build appears under the app's **TestFlight / Build** section in
~15–60 min (it goes through Apple processing). You may get an email if processing
finds an issue (e.g. missing icon size) — fix and re-upload with a bumped build
number.

---

## Step 9 — Attach build + Submit for Review

9.1 On the app's **0.2.0** page, in the **Build** section, click **+** / **Add Build**
and select the processed build (0.2.0 / build number you uploaded).
9.2 Re-confirm the export-compliance answer if prompted (NO non-exempt encryption).
9.3 Click **Add for Review** → **Submit to App Review**.
9.4 Watch status: **Waiting for Review → In Review → (Approved | Rejected)**.
Typical first review is 24–48h.

---

## Step 10 — If Apple rejects (how to respond)

Everything reviewer-reproducible in the *code* is closed (2.5.1 private APIs, 2.5.2
executable code, 2.1 completeness/offline path, G4 brand, 4.8 SIWA — all verified).
The remaining risk is subjective. If you get a rejection:
- Read the exact guideline number they cite in **Resolution Center**.
- If it's about the reviewer not being able to sign in → make sure the demo account
  (7.5) works, or point them again at the "Explore with sample notes" button.
- If it's about screenshots/metadata → adjust and re-submit (no new build needed).
- If it's a genuine new code issue → send it to me with the exact text and I'll fix
  it, bump the build, and you re-upload (Step 8) + re-submit (Step 9).
- Reply in Resolution Center; you do not always need a new binary for metadata
  issues.

---

## Step 11 — (Optional, separate) The direct-download DMG channel

The versioned `Cortex-<version>.dmg` on your website is a DIFFERENT distribution channel (not the App
Store) and is currently unsigned. To sign + notarize it you need a **Developer ID
Application** certificate, which is NOT in your keychain yet:

11.1 Create it: <https://developer.apple.com/account/resources/certificates/list>
→ **+** → **Developer ID Application** → CSR (as in Step 1) → download → install.
11.2 Create an app-specific password for notarization at
<https://account.apple.com> → Sign-In and Security → App-Specific Passwords → **+**.
11.3 Then follow `docs/FOUNDER_GO_LIVE.md` / `docs/OAUTH_SETUP_STEPS.md` to build,
sign with Developer ID, and `xcrun notarytool submit ... --wait` + `stapler staple`.
This is independent of and not required for the Mac App Store submission.

---

## Step 12 — (Optional, post-submission) Prepare true one-click imports

The App Store build filters the OAuth account-source tiles and shows **"Sign-in
sources unavailable in this build"**. This
is optional and not needed for App Store approval. Registering provider apps and
setting broker secrets prepares the hosted half, but **does not by itself activate
one-click sign-in in build 38**.

12.1 Follow `docs/OAUTH_SETUP_STEPS.md` to register each provider's OAuth app
(redirect URI = the loopback callback the doc specifies).
12.2 On the hosted server set, per provider:
```sh
CORTEX_BROKER_NOTION_CLIENT_ID=...     CORTEX_BROKER_NOTION_CLIENT_SECRET=...
CORTEX_BROKER_GOOGLE_CLIENT_ID=...     CORTEX_BROKER_GOOGLE_CLIENT_SECRET=...
# (repeat for the providers you want)
```
then `systemctl restart cortex-api`. Verify:
```sh
curl -s https://api.signindoppl.com/oauth/broker/providers   # should now list them
```
That endpoint confirms only that the hosted broker has credentials. Build 38 reads
the list for future use, but deliberately does **not** flip those tiles: the local
backend does not yet route the callback code through the hosted
`/oauth/broker/exchange` endpoint. Enabling a tile now would open consent and then
dead-end.

12.3 After the provider credentials are ready, hand the configured provider list
back to engineering. The remaining product work is to wire the local OAuth
start/completion path to hosted `/oauth/broker/start` and
`/oauth/broker/exchange`, test each real provider end to end, then ship a **new
signed build**. Do not put provider client secrets in `Info.plist` or the app
binary as a shortcut.

Note: ChatGPT / Claude / Gemini have no supported data-import API — those stay
guided export; this step only affects supported OAuth sources.

---

### One-glance summary of what only YOU can do
1. Apple Developer membership + agreements active (Step 0).
2. App ID with Sign in with Apple + Keychain Sharing (Step 2).
3. **Mac App Store provisioning profile** (Step 3) ← the one true signing blocker.
4. `CORTEX_OIDC_APPLE_CLIENT_ID=com.cortex.doppl` on the backend (Step 4).
5. One command to build the signed `.pkg` (Step 5).
6. Create the app record at version `0.2.0` + metadata + screenshots + privacy +
   export compliance + demo account in App Store Connect (Steps 6–7).
7. Upload via Transporter (Step 8) and Submit (Step 9).

Everything else (all code, signing config, entitlements, packaging, the offline
reviewer path, screenshots, and the compliance fixes) is already done and verified.

### Official Apple references (re-checked July 12, 2026)

- Certificate types: <https://developer.apple.com/help/account/certificates/certificates-overview/>
- Mac App Store Connect provisioning profile: <https://developer.apple.com/help/account/provisioning-profiles/create-an-app-store-provisioning-profile/>
- Mac screenshot sizes and count: <https://developer.apple.com/help/app-store-connect/reference/app-information/screenshot-specifications>
- Uploading with Transporter or `altool`: <https://developer.apple.com/help/app-store-connect/manage-builds/upload-builds/>
