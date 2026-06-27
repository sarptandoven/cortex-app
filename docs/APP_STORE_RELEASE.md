# Doppl Mac App Store Release

This is the App Store/TestFlight build path for Doppl.

## Apple Project

- App Store Connect name: `Doppl`
- Bundle ID: `com.cortex.doppl`
- Team ID: `P4H96J3VXY`
- SKU: `0626`
- Apple ID: `6784247132`

## Current App Store Build Mode

The App Store build mode differs from the direct-download build:

- `CFBundleDisplayName` is set to `Doppl`.
- `CortexDistributionMode` is set to `app-store`.
- The app is signed with `macos/AppStore.entitlements`.
- Direct writes into other apps' MCP config files are disabled.
- Custom vault-folder selection is disabled; the vault stays inside the sandbox container.
- The package output is a `.pkg` suitable for the App Store upload pipeline after real Apple signing assets are installed.

## Required Local Signing Assets

Install these on the build Mac:

- Apple Distribution certificate for Team ID `P4H96J3VXY`
- Mac App Store provisioning profile for `com.cortex.doppl`
- Optional installer-signing identity accepted by `productbuild`

Verify identities:

```bash
security find-identity -v -p codesigning
```

Expected app signing identity shape:

```text
Apple Distribution: Your Name or Company (P4H96J3VXY)
```

## Dry-Run Package

Without signing assets, the script still creates a local package and report:

```bash
./macos/package_app_store.sh
```

This is useful for checking:

- bundle id
- display name
- App Store distribution mode
- embedded entitlements
- package structure

It is not uploadable until signing and provisioning are present.

## Upload-Candidate Package

After installing signing assets:

```bash
export CORTEX_APPSTORE_SIGN_IDENTITY="Apple Distribution: Your Name or Company (P4H96J3VXY)"
export CORTEX_APPSTORE_PROVISIONING_PROFILE="/path/to/Doppl_Mac_App_Store.provisionprofile"
export CORTEX_APPSTORE_INSTALLER_IDENTITY="3rd Party Mac Developer Installer: Your Name or Company (P4H96J3VXY)"
./macos/package_app_store.sh
```

The report must show:

```json
{
  "status": "upload-candidate",
  "uploadable": true,
  "bundle_id": "com.cortex.doppl",
  "distribution_mode": "app-store"
}
```

## Remaining Review Risks

Before broad App Store submission, resolve or document these:

- the bundled backend currently launches Python, so the App Store build needs a bundled/signed runtime or a native backend path
- MCP setup in App Store mode is copy/manual instead of direct config editing
- sandboxed persistent access to user-selected external vault folders is intentionally disabled until security-scoped bookmarks are implemented
- payments should use StoreKit/In-App Purchase for digital feature unlocks in the App Store version
- push, iCloud, Apple Pay, Wi-Fi, and associated-domain entitlements should only be embedded once the matching product features are implemented
