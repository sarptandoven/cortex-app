# Cortex Installer and Updates

Cortex is currently a local-first macOS beta. The release system should make it easy to create a repeatable app package today while leaving a clean path to signed, notarized, automatic updates later.

## Current Release Artifacts

`macos/package_release.sh` creates a full local beta release:

- `Cortex-<version>-<build>.dmg`
- `Cortex-<version>-<build>.app.zip`
- `Cortex-<version>-<build>.checksums.txt`
- `latest.json`
- `BETA_HANDOFF.md`

The DMG is the user-facing installer. It contains:

- `Cortex.app`
- `/Applications` symlink
- `README.txt` with install/update instructions

The ZIP is useful for direct download, testing, CI artifacts, and update tooling.

`BETA_HANDOFF.md` is the tester-facing local beta artifact. It gives one
repeatable source build path, package install path, checksum check, manifest
validation command, readiness gate, live-backend verification commands, support
bundle commands, and the five-step manual first-user loop.

## Build A Release

```bash
./macos/package_release.sh \
  --channel local-beta \
  --note "Local-first Cortex beta with installer, update manifest, capture, MCP, and trust controls."
```

For a hosted beta feed:

```bash
./macos/package_release.sh \
  --channel public-beta \
  --base-url https://download.example.com/cortex/public-beta
```

For Apple Developer ID signing and notarization, see `docs/APPLE_RELEASE.md`.

The script reads version metadata from `macos/Info.plist`:

- `CFBundleShortVersionString`
- `CFBundleVersion`
- `LSMinimumSystemVersion`
- `CortexReleaseChannel`

## Update Manifest

`latest.json` is the update feed the app can check.

Required shape:

```json
{
  "app": "Cortex",
  "bundle_id": "com.cortex.doppl",
  "channel": "local-beta",
  "version": "0.1.0",
  "build": "1",
  "minimum_macos": "13.0",
  "released_at": "2026-06-25T00:00:00Z",
  "mandatory": false,
  "release_notes": ["Release note"],
  "artifacts": [
    {
      "kind": "dmg",
      "filename": "Cortex-0.1.0-1.dmg",
      "url": "file:///.../Cortex-0.1.0-1.dmg",
      "size_bytes": 123,
      "sha256": "..."
    }
  ]
}
```

Validate a feed:

```bash
python3 scripts/validate_update_manifest.py outputs/Cortex-0.1.0-1/latest.json
```

The validator confirms required keys, artifact existence, byte size, and SHA-256.

## In-App Update Check

The macOS app keeps update controls under `Trust > Advanced > Installer and updates`.

Users can:

- see current app version and build
- enter an update feed URL
- save the feed
- check for updates
- open the preferred update artifact
- use the bundled example feed for local QA

The static landing page and download directory are covered in `docs/DISTRIBUTION.md`.

For the local beta, updates are manual:

1. Download/open the DMG.
2. Quit Cortex.
3. Replace `Cortex.app` in `/Applications`.
4. Reopen Cortex.

The user vault remains at:

```text
~/Library/Application Support/Cortex/Cortex.vault
```

## Why Manual Updates First

Manual updates are acceptable for early local beta because:

- there is no hosted backend yet
- the app is ad-hoc signed in local builds
- automatic updates require a signing/notarization/key-management decision
- users must retain confidence that their local vault is not touched by app replacement

## Production Upgrade Path

Before public distribution:

- create an Apple Developer ID Application certificate
- sign the app with hardened runtime
- notarize the app and DMG
- staple notarization tickets
- host `latest.json` and artifacts over HTTPS
- rotate update feed keys separately from backend API keys
- add release rollback policy
- decide whether to adopt Sparkle for automatic updates

Sparkle is the likely production path for background update download/install. The current manifest is intentionally simple and can either remain as a channel feed or be converted into a Sparkle appcast later.

## Release Checklist

- Increment `CFBundleShortVersionString` or `CFBundleVersion`.
- Run `python3 -m unittest discover backend/tests`.
- Run `./macos/build.sh`.
- Run `codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app`.
- Launch the app and verify the bundled backend starts.
- Run `python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"` using the local API token from `Trust > Advanced`.
- Run `./macos/package_release.sh`.
- Confirm the generated release directory includes `BETA_HANDOFF.md`.
- Run `python3 scripts/prepare_distribution_site.py`.
- Run `python3 scripts/check_distribution_site.py`.
- Run `python3 scripts/ops_readiness_check.py --refresh-site`.
- Run `python3 scripts/validate_update_manifest.py <release>/latest.json`.
- Test the landing page download buttons against `site/downloads/latest.json`.
- Test the DMG by opening it and launching a copied app.
- Test `Trust > Advanced > Installer and updates` with the generated `latest.json`.
- Export a support bundle with `python3 scripts/export_support_bundle.py --mode live` after launch.

## Current Boundaries

This system does not yet:

- notarize the app
- install privileged helpers
- perform automatic replacement of the running app
- run delta updates
- verify update signatures beyond SHA-256 in the feed
- provide rollback from inside the app

Those are appropriate for the public-beta release track, not the local-first MVP package.
