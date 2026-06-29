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

`scripts/check_distribution_site.py` verifies:

- local HTML links and anchors
- the privacy page, update feed, and checksum links
- required landing-page elements
- DMG and ZIP presence
- artifact byte sizes
- artifact SHA-256 hashes

## Local QA

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
2. Run backend tests.
3. Run reliability and battle tests against the packaged app.
4. Package release artifacts.
5. Prepare site downloads.
6. Run static site validation.
7. Run `python3 scripts/ops_readiness_check.py --refresh-site`.
8. Run static site browser QA locally.
9. Deploy `site/`.
10. Download the DMG from the deployed page.
11. Install on a clean Mac profile.
12. Verify first-run onboarding, Today loop, MCP setup, backup, support bundle export, and update feed.

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
Cortex gives ChatGPT, Claude, Cursor, and MCP agents your memory, preferences, decisions, and open loops so they can work with context instead of starting from zero.
```

Positioning:

```text
Cortex is not just AI memory. It is your personal operating model for agents.
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

## Free Beta Checklist

The beta is ready to share with a small group when:

- a user can download and install in under two minutes
- the app opens from Applications
- first-run setup completes without docs
- the Today loop explains the next action
- the user can save one memory
- the user can copy context into ChatGPT or Claude
- the user can see where data is stored
- the user can create a backup
- the user can read the privacy page
- the team can replace the DMG and update `latest.json` repeatably
