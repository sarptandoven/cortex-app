# Cortex Landing Page and Distribution

This document defines the controlled distribution paths for the local-first
macOS beta. The current download referenced by
`site/downloads/latest.json` is a Developer ID signed, Apple-notarized direct
DMG hosted in `doppl-tech/releases`. An ad-hoc-signed `local-beta` path remains
available for internal QA, but it is not the build described by the public
download page. The direct build requires account sign-in; local retrieval
continues to run on-device and cloud capture sync is optional.

## Goal

Ship a clean beta experience:

1. User lands on the Cortex page.
2. User understands the promise in under 10 seconds.
3. User downloads the DMG.
4. User installs the app.
5. User completes the local setup loop.
6. User can verify privacy, checksums, and update metadata.

## Current Distribution Status

The current direct beta is ready for controlled tester distribution when the
package, static site, clean-profile install, update, rollback, and
support-bundle checks pass for the exact build being shared.

For the build named by `site/downloads/latest.json`:

- The app is Developer ID signed and notarized by Apple.
- The normal install path is a double-click launch after copying the app to
  Applications.
- Cortex checks the HTTPS update feed and directs users to the current DMG.
- Installation and rollback still use app replacement; there is no background
  self-updater.
- Rollback is manual app replacement with the local memory folder left in place.
- GitHub Releases hosts the binaries; the site hosts release metadata,
  checksums, and download links.

This signing state does not make Cortex a broad production service. Public copy
must still identify it as a local-first beta, describe the app-replacement
update path, link privacy and checksum information, and avoid promises about
background updates, cloud backup, production-grade hosted operations, or
production support.

Internal ad-hoc builds must use a separate handoff that explicitly documents
Control-click > Open. Never use that internal signing language for the current
notarized public DMG.

## Static Site

The site lives in:

```text
site/
```

Key files:

- `site/index.html`: landing page, install flow, download CTA
- `site/privacy.html`: local beta privacy summary
- `site/styles.css`: responsive visual system
- `site/app.js`: animated hero scene and release metadata hydration
- `site/downloads/latest.json`: website copy of the release feed
- `site/downloads/distribution.json`: generated release summary

The hero uses a canvas memory map instead of stock photography. This keeps the page visually specific to Cortex without adding external assets or trackers.

## Prepare Downloads

For an internal ad-hoc QA build:

```bash
./macos/package_release.sh \
  --channel local-beta \
  --base-url https://trydoppl.com/downloads \
  --output outputs \
  --note "First-100 local beta DMG with source connection, Review, cited Ask, MCP retrieval, backup, and support bundle checks."
RELEASE_DIR="outputs/Cortex-0.1.0-1"
python3 scripts/ops_readiness_check.py \
  --skip-tests \
  --skip-build \
  --require-package-artifacts \
  --release-dir "$RELEASE_DIR"
python3 scripts/prepare_distribution_site.py \
  --artifact-base-url https://trydoppl.com/downloads
python3 scripts/check_distribution_site.py
python3 scripts/ops_readiness_check.py --skip-build
```

For a hosted beta:

```bash
python3 scripts/prepare_distribution_site.py \
  --artifact-base-url https://your-domain.example/downloads
```

This copies the DMG, ZIP, checksums, and update feed into `site/downloads/`.

This path can host internal QA artifacts, but HTTPS hosting does not change
their signing state or make them automatic-update ready.

For a Gatekeeper-ready public direct download release, use the fail-closed
packaging path instead:

```bash
CORTEX_BUNDLE_PYTHON=1 \
CORTEX_CODESIGN_IDENTITY="Developer ID Application: Example, Inc. (TEAMID)" \
CORTEX_NOTARY_PROFILE=cortex-notary \
./macos/package_release.sh \
  --production \
  --channel stable \
  --base-url https://your-domain.example/downloads
```

`--production` refuses to package unless artifacts use HTTPS URLs, the app has a
Developer ID signing identity, notarization is configured, and a Python runtime
is bundled. Local beta artifacts are useful for QA and controlled first-100
distribution, but they are not a Gatekeeper-ready public release.

## Developer ID Hardened-Runtime Entitlements

Developer ID (non-App-Store) builds sign the bundled CPython framework, the
native `.so`/`.dylib` wheels, and the outer `Cortex.app` under the hardened
runtime (`--options runtime`). A bundled interpreter under the hardened runtime
does not launch reliably, and can bounce on the first notarization submission,
without a small set of entitlements. `macos/build.sh` applies
`macos/DeveloperID.entitlements` to make the first submission succeed.

This is the notarization-safe configuration. It is deliberately **not** the Mac
App Store sandbox model: App Sandbox is intentionally omitted because the
Developer ID build writes cross-application MCP config and needs broad user file
access, both of which the sandbox would break. That is a documented
MAS-vs-Developer-ID trade-off. The App Store path keeps using
`AppStore.entitlements` / `AppStoreChild.entitlements` unchanged.

`macos/DeveloperID.entitlements` contains exactly these hardened-runtime keys,
each present for a specific reason:

- `com.apple.security.cs.allow-jit` — CPython maps executable pages at runtime;
  without this the hardened runtime kills the interpreter on launch.
- `com.apple.security.cs.allow-unsigned-executable-memory` — the interpreter and
  some native wheels allocate writable-then-executable memory.
- `com.apple.security.cs.disable-library-validation` — the app loads the bundled
  Python framework and native wheels signed with our own Developer ID identity
  (not the App Store child-cert model); library validation would reject them.
- `com.apple.security.cs.allow-dyld-environment-variables` — the app sets
  `PYTHON*` environment variables so the bundled interpreter finds the bundled
  stdlib and wheels; the hardened runtime strips these variables otherwise.

### When the entitlements are applied

`build.sh` applies `DeveloperID.entitlements` only on the real Developer ID
hardened-runtime path — that is, when **all** of the following hold:

- distribution mode is not `app-store`, and
- a real signing identity is set (`CORTEX_CODESIGN_IDENTITY` is not the ad-hoc
  `-`).

Ad-hoc/local dev builds (the default `-` identity) and the App Store path are
left exactly as they were: the default local build stays ad-hoc-signed with no
entitlements. Both the nested Mach-O files and the outer `.app` receive the
entitlements on the Developer ID path, because notarization checks the whole
bundle. The path is overridable via `CORTEX_DEVID_ENTITLEMENTS` (defaults to
`macos/DeveloperID.entitlements`); an explicit `CORTEX_ENTITLEMENTS` still wins
for the outer app.

`macos/package_release.sh --production` needs no extra flags for this: it passes
`CORTEX_CODESIGN_IDENTITY` through to `build.sh`, which selects the entitlements
automatically.

## Generated Artifact Verification

Every first-100 beta package must ship with a release directory containing:

- `Cortex-<version>-<build>.dmg`
- `Cortex-<version>-<build>.app.zip`
- `Cortex-<version>-<build>.checksums.txt`
- `latest.json`
- `BETA_HANDOFF.md`

Verify the generated artifacts before publishing the static site:

```bash
(cd "$RELEASE_DIR" && shasum -a 256 -c "Cortex-<version>-<build>.checksums.txt")
python3 scripts/validate_update_manifest.py "$RELEASE_DIR/latest.json"
python3 scripts/ops_readiness_check.py \
  --skip-tests \
  --skip-build \
  --require-package-artifacts \
  --release-dir "$RELEASE_DIR"
```

The readiness check fails the package gate if the DMG/ZIP are missing or
hash-mismatched, the checksum file disagrees with the manifest, `BETA_HANDOFF.md`
is missing required handoff sections, or `latest.json` lacks beta-readiness
metadata for install, update, rollback, known limitations, manual QA, and
generated artifact verification.

`scripts/check_distribution_site.py` verifies:

- local HTML links and anchors
- the privacy page, update feed, and checksum links
- required landing-page elements
- DMG and ZIP presence
- artifact byte sizes
- artifact SHA-256 hashes

The DMG/ZIP presence, size, and SHA-256 checks apply to artifacts served from
the site itself (a relative `url` like `downloads/Cortex-<v>-<b>.dmg`, or an
absolute `url` whose file is still copied into `site/downloads/`). When an
artifact `url` is an absolute `https://` URL **and** the file is not on disk —
the GitHub Releases layout below, where the binary lives in a Release rather than
in git — the checker skips the on-disk existence/size/hash checks for that
artifact but still validates the manifest structure and requires the
`*.checksums.txt` file to be present. Run `python3
scripts/check_distribution_site.py --self-test` to exercise both branches.

## GitHub Releases Distribution (free binary hosting in the org)

Committing the DMG/ZIP into `site/downloads/` bloats git. The current public
path attaches notarized binaries to
[`doppl-tech/releases`](https://github.com/doppl-tech/releases/releases) and
points `latest.json` at the Release asset URLs, so the site links to the Release
and git stays small.

The canonical source repository is
[`trace-cortex/cortex-app`](https://github.com/trace-cortex/cortex-app); binary
artifacts currently use the separate `doppl-tech/releases` repository. Pass the
binary repository as `--repo OWNER/REPO`.

Procedure:

1. **Build notarized artifacts** (founder task F10, needs the Developer ID cert +
   notary profile from F2):

   ```bash
   CORTEX_CODESIGN_IDENTITY="Developer ID Application: NAME (TEAMID)" \
   CORTEX_NOTARY_PROFILE=cortex-notary \
   CORTEX_BUNDLE_PYTHON=1 \
   ./macos/package_release.sh --production --channel stable \
     --base-url https://github.com/doppl-tech/releases/releases/download/v0.2.0-52
   ```

   The `--base-url` can point at the Release download prefix so the release
   directory's own `latest.json` already carries Release URLs; `publish_release.sh`
   also rewrites the site copy regardless.

2. **Tag the build** (matching the tag you will publish, e.g. `v0.1.0-1`):

   ```bash
   git tag v0.1.0-1
   git push origin v0.1.0-1   # push to the canonical remote per DECISION 0
   ```

3. **Publish the Release and repoint the site feed:**

   ```bash
   scripts/publish_release.sh \
     --tag v0.2.0-52 \
     --release-dir outputs/Cortex-0.2.0-52 \
     --repo doppl-tech/releases
   ```

   `scripts/publish_release.sh`:

   - requires `gh` to be installed and authenticated (fails clearly otherwise),
     and fails clearly if the release directory or any manifest-listed artifact
     is missing;
   - creates the GitHub Release for the tag, or reuses it if it already exists
     (safe to re-run);
   - uploads the DMG, `.app.zip`, and `*.checksums.txt` as Release assets
     (`--clobber`, so re-runs replace prior uploads);
   - rewrites `site/downloads/latest.json` so each artifact `url` becomes
     `https://github.com/<owner>/<repo>/releases/download/<tag>/<filename>`,
     preserving each `filename`, `size_bytes`, and `sha256`.

   Use `--dry-run` first to preview the `gh` commands and the exact URL rewrites
   without creating a Release, uploading assets, or writing the manifest.

4. **Verify and deploy the site:**

   ```bash
   python3 scripts/check_distribution_site.py
   ```

   The site now links to the Release. `latest.json` and `*.checksums.txt` stay in
   git (small); the binaries live in the Release.

### CI-driven variant and required token

`publish_release.sh` authenticates through the `gh` CLI. For a CI-driven release
(triggered on a version tag), the founder must add a repo/org secret exposed to
the job as `GH_TOKEN` (or `GITHUB_TOKEN`) with **`contents: write`** permission
on the canonical org repo, so the workflow can create the Release and upload
assets. This is founder task F4d. In a GitHub Actions workflow, grant
`permissions: contents: write` to the job and export the token as `GH_TOKEN` so
`gh` picks it up.

### Keeping binaries out of git

The first notarized releases have been published and the current
`site/downloads/latest.json` uses GitHub Release URLs. Large DMG and ZIP
artifacts therefore stay out of git.

For future releases, keep the binaries ignored and verify that the manifest
points at the newly published Release:

```bash
python3 scripts/check_distribution_site.py   # still green: URLs are Release-hosted
```

Keep `site/downloads/latest.json`, `distribution.json`, and the
`*.checksums.txt` in git. Update `site/index.html` and the manifest together so
the visible buttons, checksums, and machine-readable feed identify the same
build.

## Manual Site QA

```bash
cd site
python3 -m http.server 8780
```

Then verify:

- hero loads and animates
- Download DMG points to `site/downloads/Cortex-<version>-<build>.dmg`
- Download ZIP points to `site/downloads/Cortex-<version>-<build>.app.zip`
- Update feed opens as JSON
- Privacy page is reachable
- Mobile width has no overlapping text
- Header links scroll to the correct sections
- browser console has no JavaScript errors

This is a manual QA pass. Do not use it as a substitute for testing a downloaded
DMG on a clean macOS user profile.

## Hosting Options

Good first options:

- GitHub Pages for a private/manual beta page
- Cloudflare Pages for a public static site with easy HTTPS
- Vercel static deployment if the team wants preview URLs
- S3 plus CloudFront for direct control over downloads and cache headers

Recommended beta structure:

```text
/
  index.html
  privacy.html
  styles.css
  app.js
/downloads/
  latest.json
  Cortex-0.1.0-1.dmg
  Cortex-0.1.0-1.app.zip
  Cortex-0.1.0-1.checksums.txt
  distribution.json
```

## Invite Go / No-Go

Before inviting users, evaluate the exact release directory and the exact hosted
site users will reach.

Go when these commands pass:

```bash
python3 scripts/ops_readiness_check.py --refresh-site --include-package
python3 scripts/validate_update_manifest.py "$RELEASE_DIR/latest.json"
python3 scripts/ops_readiness_check.py \
  --skip-tests \
  --skip-build \
  --require-package-artifacts \
  --release-dir "$RELEASE_DIR"
(cd "$RELEASE_DIR" && shasum -a 256 -c "Cortex-<version>-<build>.checksums.txt")
python3 scripts/prepare_distribution_site.py
python3 scripts/check_distribution_site.py
```

Go when all of these manual checks are also true:

- The DMG downloaded from the planned tester URL matches the checksum and opens
  on a clean macOS 13 or newer user profile.
- For an unnotarized local-beta build, the documented Control-click > Open path
  works on that clean profile.
- First-run setup completes without source-code instructions.
- MCP tools or Obsidian/local notes can connect, sync, produce Review
  candidates, and return cited Ask results.
- Backup and support-bundle export work, and the bundle omits raw memory
  content.
- Manual update over the previous beta preserves the memory folder.
- Manual rollback to the previous beta preserves the memory folder.
- The current page, invite text, and `BETA_HANDOFF.md` identify the build as an
  unnotarized local beta unless the exact build completed notarization.

No-go when any of these are true:

- Any automated package, manifest, checksum, site, or readiness check fails.
- The hosted download differs from the verified release directory.
- The app cannot open from Applications on a clean profile.
- Gatekeeper blocks the unnotarized build beyond the documented Control-click >
  Open flow.
- Update, rollback, backup, export, or support-bundle behavior risks user data.
- The previous beta artifacts needed for rollback are unavailable.
- The known-limitations list is stale for the build.
- Any copy says or implies the current local-beta build is notarized,
  auto-updating, cloud-backed, broadly public, or production-supported when it is
  not.

## Release Flow

1. Update version/build in `macos/Info.plist` when appropriate.
2. Run backend tests and retrieval eval.
3. Run reliability and battle tests against the packaged app.
4. Package release artifacts.
5. Verify checksums, `latest.json`, DMG/ZIP presence, and generated handoff sections.
6. Review the generated `BETA_HANDOFF.md` in the release directory.
7. Prepare site downloads.
8. Run static site validation.
9. Run `python3 scripts/ops_readiness_check.py --refresh-site`.
10. Run manual static-site QA locally.
11. Deploy `site/`.
12. Download the DMG from the deployed page.
13. Install on a clean Mac profile.
14. Verify first-run onboarding, Home/Review/Ask flow, Connections & Privacy, source readiness, MCP or Obsidian sync, cited Ask results, backup, support bundle export, and update feed.
15. Update over the previous beta and confirm the memory folder remains intact.
16. Roll back to the previous beta and confirm the memory folder remains intact.

## Update And Rollback Notes

For first-100 testers, updates are manual:

1. Download the new DMG.
2. Verify the DMG checksum.
3. Quit Cortex.
4. Replace `Cortex.app` in Applications.
5. Reopen Cortex and confirm Connections & Privacy shows local service health and the expected memory folder path.
6. Create a fresh backup.

Rollback is also manual:

1. Keep the memory folder unchanged.
2. Quit Cortex.
3. Replace `Cortex.app` with the previous beta build.
4. Reopen Cortex, run the reliability report, and create a fresh backup.

Keep at least one previous beta DMG, ZIP, checksum file, and manifest available
until the new beta has passed manual QA.

## Current Beta Copy

Primary headline:

```text
Cortex
```

Primary slogan:

```text
You, but AI.
```

Short description:

```text
Cortex connects MCP tools and Obsidian/local notes, syncs useful context on your Mac, and lets you ask questions that return reviewed memory with citations.
```

Positioning:

```text
Cortex is not just AI memory. It is your personal operating model for the AI tools you already use.
```

## Broad-Launch Status

Completed for the current direct macOS beta:

- Apple Developer ID Application certificate
- hardened runtime signing
- notarized app and DMG
- stapled notarization tickets
- HTTPS-hosted downloads
- rollback artifacts for the current release

Remaining before a broad production launch:

- signed update feed or Sparkle appcast
- formal privacy policy review
- crash/error reporting decision
- formal support and incident-response ownership

The local static site is enough for a small free beta. The public site should not promise automatic updates, hosted sync, team accounts, or background capture until those systems exist.

## Known First-100 Limitations

Do not describe these as available beta capabilities:

- automatic app updates or in-app rollback
- cloud backup or production-grade hosted operations
- preconfigured managed Google, Microsoft, or Notion OAuth in the distributed
  build (their client IDs currently ship empty)
- remote MCP/OAuth
- billing, teams, enterprise policy, or hosted analytics
- production incident response or telemetry

## Free Beta Checklist

The beta is ready to share with a small group when:

- generated artifacts pass checksum, manifest, and package-readiness verification
- the exact hosted DMG matches the verified release directory
- public copy matches the manifest's version, build, signing state, and artifact
  URLs
- normal double-click launch is tested for the notarized public DMG; the
  Control-click > Open path is tested only for explicitly internal ad-hoc builds
- a user can download and install in under two minutes
- the app opens from Applications
- first-run setup completes without docs
- Home explains readiness and the next useful action
- Connections & Privacy can connect MCP or Obsidian/local notes and show sync health
- Review can approve or archive pending memory
- Ask returns cited memory from a connected source
- fallback import/export tools stay available in Advanced for migration and support
- the user can see where data is stored
- the user can create a backup
- the user can read the privacy page
- the team can replace the DMG and update `latest.json` repeatably
- the team can roll back to the previous DMG without touching the user's memory folder
- the previous beta DMG, ZIP, checksum file, and manifest remain available
