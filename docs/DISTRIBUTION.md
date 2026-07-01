# Cortex Landing Page and Distribution

This document defines the first public-facing distribution path for the local-first macOS beta.

## Goal

Ship a clean beta experience:

1. User lands on the Cortex page.
2. User understands the promise in under 10 seconds.
3. User downloads the DMG.
4. User installs the app.
5. User completes the local setup loop.
6. User can verify privacy, checksums, and update metadata.

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

After packaging the app:

```bash
./macos/package_release.sh
RELEASE_DIR="outputs/Cortex-0.1.0-1"
python3 scripts/ops_readiness_check.py \
  --skip-tests \
  --skip-build \
  --require-package-artifacts \
  --release-dir "$RELEASE_DIR"
python3 scripts/prepare_distribution_site.py
python3 scripts/check_distribution_site.py
python3 scripts/ops_readiness_check.py --skip-build
```

For a hosted beta:

```bash
python3 scripts/prepare_distribution_site.py \
  --artifact-base-url https://your-domain.example/downloads
```

This copies the DMG, ZIP, checksums, and update feed into `site/downloads/`.

For a public direct download release, use the fail-closed packaging path:

```bash
CORTEX_BUNDLE_PYTHON=1 \
CORTEX_CODESIGN_IDENTITY="Developer ID Application: Example, Inc. (TEAMID)" \
CORTEX_NOTARY_PROFILE=cortex-notary \
./macos/package_release.sh \
  --production \
  --channel stable \
  --base-url https://your-domain.example/downloads
```

`--production` refuses to package unless artifacts use HTTPS URLs, the app has a Developer ID signing identity, notarization is configured, and a Python runtime is bundled. Local beta artifacts are useful for QA, but they are not a Gatekeeper-ready public release.

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

## Public Distribution Blockers

Before broad public distribution:

- Apple Developer ID Application certificate
- hardened runtime signing
- notarized app and DMG
- stapled notarization tickets
- HTTPS-hosted downloads
- signed update feed or Sparkle appcast
- formal privacy policy review
- crash/error reporting decision
- support email or feedback form
- rollback plan for broken releases

The local static site is enough for a small free beta. The public site should not promise automatic updates, hosted sync, team accounts, or background capture until those systems exist.

## Known First-100 Limitations

Do not describe these as available beta capabilities:

- automatic app updates or in-app rollback
- hosted accounts, cloud sync, or cloud backup
- live OAuth/API sync for third-party services
- remote MCP/OAuth
- billing, teams, enterprise policy, or hosted analytics
- production incident response or telemetry
- notarized external distribution unless the current build completed Developer ID signing and notarization

## Free Beta Checklist

The beta is ready to share with a small group when:

- generated artifacts pass checksum, manifest, and package-readiness verification
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
