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
bundle commands, update/rollback notes, known limitations, and the required
manual QA checklist.

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

## First-100 Ship Gate

For a beta candidate that will go to testers, generate and verify a package in
one gate:

```bash
python3 scripts/ops_readiness_check.py --refresh-site --include-package
```

To verify an already generated release directory without rebuilding:

```bash
RELEASE_DIR="outputs/Cortex-0.1.0-1"
python3 scripts/ops_readiness_check.py \
  --skip-tests \
  --skip-build \
  --require-package-artifacts \
  --release-dir "$RELEASE_DIR"
```

The package-artifact gate validates:

- `latest.json` with `scripts/validate_update_manifest.py`
- DMG and ZIP presence, byte sizes, and SHA-256 hashes
- checksum file entries matching the manifest
- `BETA_HANDOFF.md` presence and required handoff sections
- `latest.json` beta-readiness metadata for install, update, rollback, known limitations, manual QA, and artifact verification

Do not publish `latest.json` or invite first-100 testers if this gate fails.

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

The generated manifest also includes `beta_readiness` metadata. That field is
intended for operators and release automation, not the in-app updater. It marks
manual QA as required and carries the install steps, update steps, rollback
steps, known limitations, manual QA checklist, and generated artifact
verification commands for the packaged beta.

Validate a feed:

```bash
python3 scripts/validate_update_manifest.py outputs/Cortex-0.1.0-1/latest.json
```

The validator confirms required keys, artifact existence, byte size, and SHA-256.

## In-App Update Check

The macOS app keeps update controls under Connections & Privacy.

Users can:

- see current app version and build
- enter an update feed URL
- save the feed
- check for updates
- open the preferred update artifact
- use the bundled example feed for local QA

The static landing page and download directory are covered in `docs/DISTRIBUTION.md`.

For the local beta, updates are manual:

1. Download the DMG from the release site.
2. Verify the DMG against `Cortex-<version>-<build>.checksums.txt`.
3. Quit Cortex.
4. Replace `Cortex.app` in `/Applications`.
5. Reopen Cortex.
6. Confirm Trust shows backend health and the expected vault path.
7. Create a fresh backup after the new build opens.

The user vault remains at:

```text
~/Library/Application Support/Cortex/Cortex.vault
```

Rollback is manual too:

1. Keep the local vault folder unchanged.
2. Download or retain the previous beta DMG and ZIP.
3. Quit Cortex.
4. Replace `Cortex.app` in `/Applications` with the previous build.
5. Reopen Cortex.
6. Run the reliability report and create a fresh backup.

The download host should keep at least one previous beta DMG, ZIP, checksum
file, and manifest until the next build has passed package verification and
manual QA.

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
- Run `python3 scripts/retrieval_eval.py`.
- Run `python3 scripts/adaptation_eval.py`.
- Run `./macos/build.sh`.
- Run `codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app`.
- Launch the app and verify the bundled backend starts.
- Run `python3 scripts/first100_live_smoke.py` after launching the packaged app. It reads the local API token from macOS defaults, uses an isolated smoke user, and cleans up after itself.
- Keep `python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"` for deeper backend lifecycle QA because it writes broader test data into the target vault.
- Run `./macos/package_release.sh`.
- Confirm the generated release directory includes `BETA_HANDOFF.md`.
- Run `(cd <release> && shasum -a 256 -c Cortex-<version>-<build>.checksums.txt)`.
- Run `python3 scripts/prepare_distribution_site.py`.
- Run `python3 scripts/check_distribution_site.py`.
- Run `python3 scripts/ops_readiness_check.py --refresh-site`.
- Run `python3 scripts/ops_readiness_check.py --skip-tests --skip-build --require-package-artifacts --release-dir <release>`.
- Run `python3 scripts/validate_update_manifest.py <release>/latest.json`.
- Test the landing page download buttons against `site/downloads/latest.json`.
- Test the DMG by opening it and launching a copied app.
- Test the Connections & Privacy update controls with the generated `latest.json`.
- Export a support bundle with `python3 scripts/export_support_bundle.py --mode live` after launch.

## Required Manual QA

Before first-100 distribution, complete the manual QA loop on a clean macOS 13
or newer user profile:

- install from the DMG and launch from Applications
- complete first-run setup without source-code instructions
- connect MCP AI tools or an Obsidian vault from Connections & Privacy
- approve at least one useful memory and archive obvious noise in Review
- ask a question that returns cited memory from the approved source
- confirm Connections & Privacy shows vault path, backend health, backup, export, support bundle, and update feed controls
- create a backup and confirm the support bundle does not include raw memory content
- update over a previous beta and confirm the vault remains intact
- roll back to the previous beta and confirm the vault remains intact

## Current Boundaries

This system does not yet:

- notarize the app
- install privileged helpers
- perform automatic replacement of the running app
- run delta updates
- verify update signatures beyond SHA-256 in the feed
- provide rollback from inside the app
- provide hosted accounts, cloud backup, live OAuth/API sync, remote MCP/OAuth, billing, teams, or production telemetry

Those are appropriate for the public-beta release track, not the local-first MVP package.
