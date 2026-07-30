# Cortex / Doppl Apple Release

Cortex ships in two distinct Apple builds from one codebase. Which one you build
is decided entirely by `CORTEX_DISTRIBUTION_MODE`, and the two paths never share
a packaging script.

| | Mac App Store (`app-store`) | Direct download / notarized DMG (`direct`) |
|---|---|---|
| Packager | `macos/package_app_store.sh` | `macos/package_release.sh` |
| Sandbox | Yes (App Sandbox entitlement) | No |
| Account / sync | No account | Sign-in required; sync optional |
| Outbound HTTPS connectors | No — TLS stripped from the bundle | Yes |
| MCP setup | Guided **manual** (no config writes) | **Automatic** config install |
| Distribution | App Store Connect review | Signed + notarized + stapled DMG/ZIP |
| Audience | Everyone; safest default | Power users who want the full product |

## Hybrid strategy

The Mac App Store build is **LOCAL-FIRST ONLY**. It is the sandbox-compliant,
review-safe build:

- No cloud account and no sign-in.
- No outbound HTTPS connectors. In `app-store` mode `macos/build.sh` strips
  `_ssl*.so` and `ssl.py` from the bundled Python. `urllib`/`http.client` guard
  `import ssl`, so the backend degrades cleanly to loopback/HTTP only. The local
  pipeline (loopback HTTP, model2vec embeddings, sqlite-vec, MCP stdio) needs no
  TLS.
- No automatic MCP config installation. In `app-store` mode the app never writes
  into other apps' MCP config files; it shows guided **manual** setup steps
  instead.
- All memory stays inside the app's sandbox container.

The notarized **Developer-ID DMG** is the full-featured power-user path: no
sandbox, required account sign-in, optional cloud sync, outbound HTTPS
connectors, and automatic MCP config install. It keeps `_ssl` and is built by
`package_release.sh` — that path is documented in the second half of this file.

Every App Store behavior difference is gated behind `DistributionMode.isAppStore`
(Swift, reads `CortexDistributionMode == "app-store"` from Info.plist) or the
`app-store` branch of `macos/build.sh`. The direct/DMG path is never affected.

---

# Part 1 — Mac App Store submission

## Apple project facts

- App Store Connect name: `Doppl`
- Bundle ID: `com.cortex.doppl` (explicit App ID)
- Team ID: `P4H96J3VXY`
- SKU: `0626`
- Apple ID (app record): `6784247132`

## What the packager does

`macos/package_app_store.sh`:

1. Builds the sandboxed `app-store` bundle via `macos/build.sh` (bundled Python,
   TLS stripped, `AppStore.entitlements` applied, `embedded.provisionprofile`
   embedded when a profile is provided).
2. Signs the `.app` with the **Apple Distribution** identity — it accepts both
   the modern `Apple Distribution:` name and the legacy
   `3rd Party Mac Developer Application:` name.
3. Copies `macos/PrivacyInfo.xcprivacy` into `Contents/Resources/` (or generates
   a local-first, collects-nothing manifest if the source file is absent).
4. Sets `ITSAppUsesNonExemptEncryption = false` in the built bundle's Info.plist
   (the source `Info.plist` and the DMG path are untouched) — the app-store build
   uses no non-exempt encryption because the TLS stack is removed.
5. Re-seals the bundle after those injections, then builds the installer `.pkg`
   with `productbuild`, signed by the **Mac Installer Distribution** identity —
   accepting `Apple Distribution Installer:` or legacy
   `3rd Party Mac Developer Installer:`.
6. Runs an optional upload step (`altool`/`notarytool`, else Transporter) when
   `CORTEX_APPSTORE_UPLOAD=1` and credentials are present; otherwise it prints
   the manual Transporter instruction.
7. Writes `app-store-build-report.json` and, whenever the build is not a finished
   upload, a `FOUNDER_CHECKLIST.txt`.

Without a distribution identity **and** a provisioning profile **and** an
installer identity, the script does a clean **DRY RUN**: it still builds an
ad-hoc-signed `.pkg` for structural inspection, prints exactly what is missing,
prints the founder checklist, and exits 0. A dry run never errors out.

## Environment variables

```bash
# App + installer signing (modern or legacy names both accepted):
export CORTEX_APPSTORE_SIGN_IDENTITY="Apple Distribution: Your Name (P4H96J3VXY)"
export CORTEX_APPSTORE_INSTALLER_IDENTITY="Apple Distribution Installer: Your Name (P4H96J3VXY)"

# Mac App Store provisioning profile (embedded at Contents/embedded.provisionprofile):
export CORTEX_MAS_PROFILE="/path/to/Doppl_Mac_App_Store.provisionprofile"

# Optional upload (App Store Connect). Only runs with CORTEX_APPSTORE_UPLOAD=1:
export CORTEX_APPSTORE_UPLOAD=1
# either an API key ...
export CORTEX_ASC_API_KEY_ID="XXXXXXXXXX"
export CORTEX_ASC_API_ISSUER_ID="xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx"
export CORTEX_ASC_API_KEY_PATH="/path/to/AuthKey_XXXXXXXXXX.p8"
# ... or an Apple ID + app-specific password:
export CORTEX_ASC_APPLE_ID="you@example.com"
export CORTEX_ASC_APP_PASSWORD="abcd-efgh-ijkl-mnop"
```

Discover your installed identities:

```bash
security find-identity -v -p codesigning
```

## End-to-end MAS submission

```bash
# 1. Dry run first (no creds needed) — inspect structure + read the checklist.
./macos/package_app_store.sh

# 2. Once certs + profile are installed, build the upload candidate.
export CORTEX_APPSTORE_SIGN_IDENTITY="Apple Distribution: Your Name (P4H96J3VXY)"
export CORTEX_APPSTORE_INSTALLER_IDENTITY="Apple Distribution Installer: Your Name (P4H96J3VXY)"
export CORTEX_MAS_PROFILE="/path/to/Doppl_Mac_App_Store.provisionprofile"
./macos/package_app_store.sh
```

The report must then show:

```json
{
  "status": "upload-candidate",
  "uploadable": true,
  "bundle_id": "com.cortex.doppl",
  "distribution_mode": "app-store",
  "provisioning_profile": "embedded",
  "privacy_manifest": "present",
  "installer_package": "signed"
}
```

```bash
# 3. Upload — either automatically ...
export CORTEX_APPSTORE_UPLOAD=1
export CORTEX_ASC_API_KEY_ID=...            # or the Apple-ID pair above
export CORTEX_ASC_API_ISSUER_ID=...
export CORTEX_ASC_API_KEY_PATH=/path/AuthKey_XXXX.p8
./macos/package_app_store.sh
```

... or manually: open **Transporter.app** and drag in the produced
`Doppl-AppStore-<version>-<build>.pkg`.

Then in App Store Connect: attach the build to the version, complete metadata,
answer export compliance (**no** non-exempt encryption — pre-declared by the
build), and Submit for Review.

## Founder checklist (account owner only)

Only the Apple Developer account owner can do these, in order. The packager
also writes this list to `FOUNDER_CHECKLIST.txt` next to the `.pkg`.

1. **Enroll / confirm** the Apple Developer Program and that this Mac's Apple
   account has access to team `P4H96J3VXY` (Account Holder or Admin).
2. **Create the App Store Connect app record** for `com.cortex.doppl` (macOS,
   name `Doppl`). Register the explicit App ID first if it doesn't exist.
3. **Generate the certs**: `Apple Distribution` (app) and
   `Mac Installer Distribution` (`.pkg`) — or their legacy `3rd Party Mac
   Developer Application` / `Installer` equivalents. Install both, with private
   keys, into this Mac's login keychain.
4. **Create the Mac App Store provisioning profile** for `com.cortex.doppl` with
   App Sandbox + Keychain-sharing entitlements matching
   `macos/AppStore.entitlements`. Point `CORTEX_MAS_PROFILE` at it.
5. **Export compliance** — already handled in-build: the packager sets
   `ITSAppUsesNonExemptEncryption = false` (TLS is stripped in app-store mode).
   Confirm the "no" answer in App Store Connect.
6. **Re-run the packager** with the identities + profile set; confirm
   `"uploadable": true`.
7. **Upload** the signed `.pkg` (`CORTEX_APPSTORE_UPLOAD=1` with creds, or
   Transporter).
8. **Answer the export-compliance prompt** (pre-answered by step 5).
9. **Submit for review**: attach the build, complete metadata/screenshots/privacy
   nutrition label (this build collects no data), then submit.

## Known product limitation under the sandbox

The Mac App Store build is intentionally reduced (note this in any review
communication):

- **No auto-MCP-connect** — MCP is set up via guided **manual** steps; the app
  never writes into other apps' config files.
- **No cloud** — no account, no sign-in.
- **No HTTPS connectors** — the TLS stack (`_ssl`, `ssl.py`) is removed from the
  bundle, so outbound HTTPS connectors are disabled.
- Persistent access to external, user-selected vault folders is disabled until
  security-scoped bookmarks land; the vault stays inside the sandbox container.

The full/auto experience (cloud, HTTPS connectors, automatic MCP config install)
is the notarized Developer-ID DMG below.

---

# Part 2 — Direct download / notarized DMG (power-user path)

This is the launch path for distributing Cortex as a macOS app **outside** the
Mac App Store, built by `macos/package_release.sh`. It is the full-featured build
and is unchanged by the App Store work above.

## Recommended first launch path

Use Developer ID distribution:

- download from the Cortex landing page
- signed with your Apple Developer account
- notarized by Apple
- stapled DMG
- direct updates through the static `latest.json` feed

## What I need from you

Apple Developer account:

- Apple Developer Program membership
- Team ID: `P4H96J3VXY`
- legal entity/developer name as it appears on certificates
- App Store Connect app name/identifier: `Doppl`
- App bundle ID: `com.cortex.doppl`
- SKU: `0626`
- Apple ID: `6784247132`

Signing access on this Mac:

- Developer ID Application certificate installed in Keychain
- certificate private key available on this machine
- exact signing identity from:

```bash
security find-identity -v -p codesigning
```

Notarization access:

- Apple ID with App Store Connect access, or App Store Connect API key
- notarytool keychain profile created with:

```bash
xcrun notarytool store-credentials cortex-notary \
  --apple-id "YOUR_APPLE_ID" \
  --team-id "P4H96J3VXY" \
  --password "APP_SPECIFIC_PASSWORD"
```

## Signed build

Set your signing identity (must include Team ID `P4H96J3VXY`):

```bash
export CORTEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (P4H96J3VXY)"
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
codesign -dv --verbose=4 macos/build/Cortex.app
```

Expected: authority is your Developer ID Application certificate, hardened
runtime enabled, bundle id `com.cortex.doppl`, minimum macOS `13.0`.

## Notarized package

```bash
export CORTEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (P4H96J3VXY)"
export CORTEX_NOTARY_PROFILE="cortex-notary"

./macos/package_release.sh \
  --channel public-beta \
  --base-url https://YOUR_DOMAIN/downloads \
  --note "Public beta with MCP/Obsidian sync, Review, cited Ask, and support bundle export."
```

The script builds + signs the app, verifies the signature, creates and signs the
DMG/ZIP, submits to Apple notarization, staples, validates the stapled DMG, and
generates checksums + `latest.json`.

## Distribution site

```bash
python3 scripts/prepare_distribution_site.py \
  --artifact-base-url https://YOUR_DOMAIN/downloads
python3 scripts/check_distribution_site.py
python3 scripts/ops_readiness_check.py --refresh-site
```

Upload `site/` to the public static host. Upload order: DMG, ZIP, checksums,
`distribution.json`, `latest.json`, then `index.html`/`privacy.html`/CSS/JS/
favicon. Publish `latest.json` only after the artifacts are reachable.

## Final acceptance checks

On a clean Mac user profile:

```bash
spctl -a -vvv -t open /path/to/Cortex-0.1.0-1.dmg
hdiutil attach /path/to/Cortex-0.1.0-1.dmg
```

Then: drag Cortex to Applications, launch, complete first-run setup, connect MCP
or Obsidian/local notes and complete one sync, approve one memory, ask Cortex and
verify a cited memory appears, create one backup, export one support bundle,
quit + relaunch, and verify the memory folder remains intact.

## Current public release blockers (DMG path)

- Developer ID signing
- notarization
- hosted HTTPS download domain
- public privacy policy URL and support email
- clean rollback archive for the previous DMG
- final app icon and final landing/privacy copy review
- test on a clean Mac that has never run the dev build
