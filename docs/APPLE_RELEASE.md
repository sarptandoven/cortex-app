# Cortex Apple Developer Release

This is the launch path for distributing Cortex as a macOS app outside the Mac App Store.

For TestFlight and Mac App Store distribution, use `docs/APP_STORE_RELEASE.md`.

## Recommended First Launch Path

Use Developer ID distribution:

- download from the Cortex landing page
- signed with your Apple Developer account
- notarized by Apple
- stapled DMG
- manual updates through the static `latest.json` feed

The Mac App Store path is possible later, but it adds sandboxing, review, and entitlement constraints that are not ideal for this local-first MCP/vault beta.

## What I Need From You

Apple Developer account:

- Apple Developer Program membership
- Team ID: `P4H96J3VXY`
- legal entity/developer name as it appears on certificates
- App Store Connect app name/identifier: `Doppl`
- App bundle ID: `com.cortex.doppl`
- SKU: `0626`
- Apple ID: `6784247132`

Registered App ID:

- App ID Prefix: `P4H96J3VXY`
- Bundle ID: `com.cortex.doppl` explicit

Recommended App ID capabilities for the full product:

- Push Notifications
- Time Sensitive Notifications
- Associated Domains
- iCloud with CloudKit support
- App Groups
- Keychain Sharing
- Sign in with Apple
- In-App Purchase
- Apple Pay
- Access Wi-Fi Information, only if location/network-aware memory becomes an explicit opt-in feature

Initial identifiers to create when those capabilities are enabled:

- iCloud container: `iCloud.com.cortex.doppl`
- App Group: `group.com.cortex.doppl`
- Merchant ID: `merchant.com.cortex.doppl`
- Associated domain entry: `applinks:YOUR_DOMAIN`

Signing access on this Mac:

- Developer ID Application certificate installed in Keychain
- certificate private key available on this machine
- exact signing identity from:

```bash
security find-identity -v -p codesigning
```

Notarization access:

- Apple ID with App Store Connect access, or App Store Connect API key
- notarytool keychain profile name created with:

```bash
xcrun notarytool store-credentials cortex-notary \
  --apple-id "YOUR_APPLE_ID" \
  --team-id "P4H96J3VXY" \
  --password "APP_SPECIFIC_PASSWORD"
```

or an API-key based notary profile if you prefer App Store Connect API keys.

Launch metadata:

- final app name: `Cortex` or a different public name
- support email
- marketing URL
- privacy policy URL
- update/download URL
- whether the first public build should remain `0.1.0 (1)` or move to `0.1.0 (2)`

## Signed Build

Set your signing identity. It should include Team ID `P4H96J3VXY`:

```bash
export CORTEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (P4H96J3VXY)"
```

Build:

```bash
./macos/build.sh
codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app
```

Inspect:

```bash
codesign -dv --verbose=4 macos/build/Cortex.app
```

Expected:

- authority is your Developer ID Application certificate
- hardened runtime is enabled
- bundle id is `com.cortex.doppl`
- minimum macOS is `13.0`

## Notarized Package

Set both variables:

```bash
export CORTEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (P4H96J3VXY)"
export CORTEX_NOTARY_PROFILE="cortex-notary"
```

Package:

```bash
./macos/package_release.sh \
  --channel public-beta \
  --base-url https://YOUR_DOMAIN/downloads \
  --note "Public beta with local vault, Cortex memory, MCP integrations, and support bundle export."
```

The script will:

1. Build and sign `Cortex.app` with hardened runtime.
2. Verify the app signature.
3. Create the DMG and ZIP.
4. Sign the DMG.
5. Submit the DMG to Apple notarization.
6. Staple the notarization ticket.
7. Validate the stapled DMG.
8. Generate checksums and `latest.json`.

## Distribution Site

After packaging:

```bash
python3 scripts/prepare_distribution_site.py \
  --artifact-base-url https://YOUR_DOMAIN/downloads
python3 scripts/check_distribution_site.py
python3 scripts/ops_readiness_check.py --refresh-site
```

Upload `site/` to the public static host.

Upload order:

1. DMG
2. ZIP
3. checksums
4. `distribution.json`
5. `latest.json`
6. `index.html`, `privacy.html`, CSS, JS, favicon

Publish `latest.json` after artifacts are already reachable.

## Final Acceptance Checks

On a clean Mac user profile:

```bash
spctl -a -vvv -t open /path/to/Cortex-0.1.0-1.dmg
hdiutil attach /path/to/Cortex-0.1.0-1.dmg
```

Then:

- drag Cortex to Applications
- launch from Applications
- complete first-run setup
- save one memory
- create one backup
- export one support bundle
- copy one context pack into ChatGPT or Claude
- connect one MCP client
- quit and relaunch
- verify the vault remains intact

## Current Public Release Blockers

The app is technically close to public beta, but do not publish widely until these are done:

- Developer ID signing
- notarization
- hosted HTTPS download domain
- public privacy policy URL
- support email
- clean rollback archive for the previous DMG
- final app icon if the current generated mark is not enough
- final copy review on landing page and privacy page
- test on a clean Mac that has never run the dev build
