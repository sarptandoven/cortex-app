# Cortex Installer and Updates

Cortex is currently a local-first macOS beta. The current direct-download build
referenced by `site/downloads/latest.json` is Developer ID signed and notarized
by Apple. It requires account sign-in, while memory retrieval remains local and
cloud capture sync is optional. The release system also retains an ad-hoc
`local-beta` track for internal QA.

Signing and notarization are complete for the current public beta artifact.
Cortex checks the release feed for newer builds, but installing or rolling back
still uses normal app replacement rather than a background self-updater.

## Current Direct Beta Track

Use the direct beta path only when the landing page, release manifest, operator
handoff, and support runbook all describe the exact artifact consistently:

- The app is packaged as a DMG and ZIP by `macos/package_release.sh`.
- Production direct builds are Developer ID signed and notarized by Apple.
- GitHub Releases hosts the binaries referenced by the HTTPS update feed.
- Cortex can detect a newer build; installation and rollback remain app
  replacement flows.
- The local memory folder is outside `Cortex.app` and must not be deleted during
  install, update, or rollback.
- The static site and `latest.json` provide release metadata and download
  discovery, not automatic background installation.

If any operator, invite, landing page, or handoff copy disagrees with the
manifest about signing, notarization, version, build, or artifact URLs, the
release is a no-go. An internal ad-hoc build must explicitly document the
Control-click > Open path and must never reuse the public notarized-build copy.

## Ready And Not Ready

Ready for the current direct beta track:

- repeatable DMG, ZIP, checksum file, `latest.json`, and `BETA_HANDOFF.md`
  generation
- Developer ID signing, Apple notarization, and GitHub Release hosting
- checksum and manifest validation for generated artifacts
- manual install from DMG on macOS 13 or newer
- manual update by replacing `Cortex.app`
- manual rollback by replacing `Cortex.app` with the previous build
- local memory folder preservation across install, update, and rollback
- static distribution-site validation
- live packaged-app smoke testing after launch
- content-free support bundle generation

Not included in the current direct beta:

- automatic background updates or in-app rollback
- Sparkle appcast or signed update-feed rollout
- cloud backup, billing, teams, production telemetry, or production-grade
  hosted operations
- production incident response for broad external launch

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
  --base-url https://trydoppl.com/downloads \
  --output outputs \
  --note "First-100 local beta DMG with source connection, Review, cited Ask, MCP retrieval, backup, and support bundle checks."
```

For a hosted beta feed:

```bash
./macos/package_release.sh \
  --channel local-beta \
  --base-url https://trydoppl.com/downloads \
  --output outputs
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
- `latest.json` source provenance matching the current git commit, unless `--allow-stale-package` is used for an explicit stale-artifact audit

Do not publish `latest.json` or invite first-100 testers if this gate fails.

## First-100 Go / No-Go Before Invites

Run this gate before sending a build to any first-100 tester. Every step must be
green for the exact build, release directory, and download location that users
will receive.

1. Confirm release track and copy:
   - `latest.json` uses the intended beta channel, normally `local-beta`.
   - Invite copy says the build is an unnotarized local beta when Developer ID
     notarization has not completed.
   - `BETA_HANDOFF.md`, landing-page copy, and support copy do not promise
     automatic background updates, cloud backup, preconfigured broad OAuth
     sync, or production support.
2. Generate or verify the package:

   ```bash
   python3 scripts/ops_readiness_check.py --refresh-site --include-package
   ```

3. Verify the generated release directory:

   ```bash
   RELEASE_DIR="outputs/Cortex-0.1.0-1"
   python3 scripts/validate_update_manifest.py "$RELEASE_DIR/latest.json"
   python3 scripts/ops_readiness_check.py \
     --skip-tests \
     --skip-build \
     --require-package-artifacts \
     --release-dir "$RELEASE_DIR"
   (cd "$RELEASE_DIR" && shasum -a 256 -c "Cortex-0.1.0-1.checksums.txt")
   ```

4. Verify the distribution site:

   ```bash
   python3 scripts/prepare_distribution_site.py
   python3 scripts/check_distribution_site.py
   python3 scripts/ops_readiness_check.py --refresh-site
   ```

5. Test the exact user path on a clean macOS 13 or newer user profile:
   - download the DMG from the planned tester location;
   - verify the checksum;
   - drag `Cortex.app` to Applications;
   - for an unnotarized build, confirm the documented Control-click > Open path
     works;
   - complete first-run setup without source-code instructions;
   - connect MCP tools or Obsidian/local notes and confirm sync health;
   - approve memory in Review and get at least one cited Ask result;
   - create a backup;
   - export a support bundle and confirm it omits raw memory content;
   - update over the previous beta and confirm the memory folder remains intact;
   - roll back to the previous beta and confirm the memory folder remains intact.
6. Run live checks after launching the packaged app:

   ```bash
   python3 scripts/first100_live_smoke.py
   python3 scripts/ops_readiness_check.py \
     --require-live \
     --base-url http://127.0.0.1:8766 \
     --token "$CORTEX_API_KEY"
   ```

7. Keep rollback artifacts available:
   - current DMG, ZIP, checksum file, manifest, and `BETA_HANDOFF.md`;
   - previous beta DMG, ZIP, checksum file, and manifest;
   - operator notes for known limitations and support escalation.

Go only if every automated check passes, clean-profile install succeeds,
Gatekeeper handling is understood for the exact signing state, update and
rollback preserve the memory folder, and support can explain backup, export,
delete, and support-bundle behavior.

No-go if any package check fails, the clean-profile app cannot open, Gatekeeper
blocks the unnotarized path beyond the documented Control-click > Open flow,
manual update or rollback risks the memory folder, checksums or manifests do not
match the hosted artifacts, support bundle redaction fails, or current copy
overstates notarization, automatic updates, hosted sync, or production support.

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
  "source_provenance": {
    "git_commit": "abc123",
    "git_branch": "mass-scale-app-redesign",
    "git_dirty": false,
    "built_at": "2026-06-25T00:00:00Z"
  },
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
6. Confirm Connections & Privacy shows local service health and the expected memory folder path.
7. Create a fresh backup after the new build opens.

The default memory folder remains at:

```text
~/Library/Application Support/Cortex/Cortex.vault
```

Rollback is manual too:

1. Keep the local memory folder unchanged.
2. Download or retain the previous beta DMG and ZIP.
3. Quit Cortex.
4. Replace `Cortex.app` in `/Applications` with the previous build.
5. Reopen Cortex.
6. Run the reliability report and create a fresh backup.

The download host should keep at least one previous beta DMG, ZIP, checksum
file, and manifest until the next build has passed package verification and
manual QA.

## Why Manual Updates First

Manual updates are acceptable for the current beta because:

- the shipped account service does not need control of the app bundle
- internal QA builds may still be ad-hoc signed
- background installation requires a dedicated update framework and separate
  update-signing policy
- users must retain confidence that their local memory folder is not touched by app replacement

## Production Upgrade Path

Completed for the current direct beta:

- create an Apple Developer ID Application certificate
- sign the app with hardened runtime
- notarize the app and DMG
- staple notarization tickets
- host `latest.json` and artifacts over HTTPS

Remaining before automatic background updates:

- rotate update feed keys separately from backend API keys
- add release rollback policy
- decide whether to adopt Sparkle for automatic updates

Sparkle is the likely production path for background update download/install. The current manifest is intentionally simple and can either remain as a channel feed or be converted into a Sparkle appcast later.

## Release Checklist

- Increment `CFBundleShortVersionString` or `CFBundleVersion`.
- Run `python3 -m pytest backend/tests -q`.
- Run `python3 scripts/retrieval_eval.py`.
- Run `python3 scripts/adaptation_eval.py`.
- Run `./macos/build.sh`.
- Run `codesign --verify --deep --strict --verbose=2 macos/build/Cortex.app`.
- Launch the app and verify the bundled local service starts.
- Run `python3 scripts/first100_live_smoke.py` after launching the packaged app. It reads the local API token from the macOS Keychain first, falls back to the legacy `~/Library/Application Support/Cortex/credentials.json` file and defaults for older local builds, uses an isolated smoke user, and cleans up after itself.
- Keep `python3 scripts/battle_test_http.py --base-url http://127.0.0.1:8766 --token "$CORTEX_API_KEY"` for deeper service lifecycle QA because it writes broader test data into the target memory folder.
- Run `./macos/package_release.sh`.
- Confirm the generated release directory includes `BETA_HANDOFF.md`.
- Run `(cd <release> && shasum -a 256 -c Cortex-<version>-<build>.checksums.txt)`.
- Run `python3 scripts/prepare_distribution_site.py`.
- Run `python3 scripts/check_distribution_site.py`.
- Run `python3 scripts/ops_readiness_check.py --refresh-site`.
- Run `python3 scripts/ops_readiness_check.py --skip-tests --skip-build --require-package-artifacts --release-dir <release>`.
  Use `--allow-stale-package` only when intentionally inspecting an old artifact that must not be shipped.
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
- connect MCP AI tools or Obsidian/local notes from Connections & Privacy and confirm sync health
- approve at least one useful memory and archive obvious noise in Review
- ask a question that returns cited memory from the approved source
- confirm Connections & Privacy shows memory folder path, local service health, backup, export, support bundle, and update feed controls
- create a backup and confirm the support bundle does not include raw memory content
- update over a previous beta and confirm the memory folder remains intact
- roll back to the previous beta and confirm the memory folder remains intact

## Current Boundaries

This system does not yet:

- install privileged helpers
- perform automatic replacement of the running app
- run delta updates
- verify update signatures beyond SHA-256 in the feed
- provide rollback from inside the app
- provide cloud backup, preconfigured managed Google/Microsoft/Notion OAuth,
  remote MCP/OAuth, billing, teams, or production telemetry

Those are appropriate for the public-beta release track, not the local-first beta package.
