#!/usr/bin/env bash
# publish_release.sh — publish a notarized Cortex build to GitHub Releases and
# repoint site/downloads/latest.json at the Release asset URLs.
#
# GitHub Releases give the org free binary hosting, so the DMG/ZIP live in a
# Release (not in git). This script is the repeatable bridge from a
# package_release.sh output directory to a published Release + an updated
# download feed.
#
# What it does, given a tag and a package_release output directory:
#   1. Verifies gh is installed + authenticated and the artifacts exist.
#   2. Creates the GitHub Release for the tag if it does not exist, or reuses it.
#   3. Uploads the DMG, .app.zip and checksums.txt as Release assets (clobbering
#      any prior upload of the same asset name so re-runs are safe).
#   4. Rewrites site/downloads/latest.json so every artifact `url` points at
#      https://github.com/<owner>/<repo>/releases/download/<tag>/<filename>,
#      preserving each artifact's filename, size_bytes and sha256.
#
# It is safe to run repeatedly against the same tag: an existing Release is
# updated, assets are re-uploaded with --clobber, and the manifest rewrite is
# deterministic.
#
# This does NOT build, sign or notarize anything — run
# `macos/package_release.sh --production` first (see docs/DISTRIBUTION.md).

set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/publish_release.sh --tag <tag> --release-dir <dir> [options]

Required:
  --tag TAG            Release tag, e.g. v0.1.0-1 (the tag is created on publish
                       if it does not already exist).
  --release-dir DIR    package_release.sh output directory containing latest.json,
                       the DMG, the .app.zip and the .checksums.txt.

Options:
  --repo OWNER/REPO    Target GitHub repo (owner/name). Defaults to the repo gh
                       resolves for the current directory (`gh repo view`). Set
                       this explicitly to the CANONICAL ORG REPO from DECISION 0
                       in docs/REMAINING_LAUNCH_WORK.txt (e.g. doppl-tech/cortex).
  --title TITLE        Release title. Default: "Cortex <tag>".
  --notes-file FILE    Path to a Markdown notes file for the Release body.
  --site-manifest PATH latest.json to rewrite. Default: site/downloads/latest.json.
  --draft              Create/keep the Release as a draft.
  --prerelease         Mark the Release as a prerelease.
  --dry-run            Print the actions (gh commands + manifest URL rewrites)
                       without creating the Release, uploading assets or writing
                       the manifest.
  -h, --help           Show this help.

Environment:
  gh must be installed and authenticated (`gh auth login`, or GH_TOKEN with a
  token that has `contents:write` on the target repo — see docs/DISTRIBUTION.md).

Example:
  macos/package_release.sh --production --channel stable \
    --base-url https://github.com/doppl-tech/cortex/releases/download/v0.1.0-1
  scripts/publish_release.sh \
    --tag v0.1.0-1 \
    --release-dir outputs/Cortex-0.1.0-1 \
    --repo doppl-tech/cortex
EOF
}

TAG=""
RELEASE_DIR=""
REPO=""
TITLE=""
NOTES_FILE=""
SITE_MANIFEST=""
DRAFT=0
PRERELEASE=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --tag)
      [[ $# -ge 2 ]] || { echo "Error: --tag requires a value." >&2; exit 2; }
      TAG="$2"; shift 2 ;;
    --release-dir)
      [[ $# -ge 2 ]] || { echo "Error: --release-dir requires a value." >&2; exit 2; }
      RELEASE_DIR="$2"; shift 2 ;;
    --repo)
      [[ $# -ge 2 ]] || { echo "Error: --repo requires a value." >&2; exit 2; }
      REPO="$2"; shift 2 ;;
    --title)
      [[ $# -ge 2 ]] || { echo "Error: --title requires a value." >&2; exit 2; }
      TITLE="$2"; shift 2 ;;
    --notes-file)
      [[ $# -ge 2 ]] || { echo "Error: --notes-file requires a value." >&2; exit 2; }
      NOTES_FILE="$2"; shift 2 ;;
    --site-manifest)
      [[ $# -ge 2 ]] || { echo "Error: --site-manifest requires a value." >&2; exit 2; }
      SITE_MANIFEST="$2"; shift 2 ;;
    --draft)
      DRAFT=1; shift ;;
    --prerelease)
      PRERELEASE=1; shift ;;
    --dry-run)
      DRY_RUN=1; shift ;;
    -h|--help)
      usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2 ;;
  esac
done

if [[ -z "$TAG" || -z "$RELEASE_DIR" ]]; then
  echo "Error: --tag and --release-dir are required." >&2
  usage >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
if [[ -z "$SITE_MANIFEST" ]]; then
  SITE_MANIFEST="$REPO_ROOT/site/downloads/latest.json"
fi
if [[ -z "$TITLE" ]]; then
  TITLE="Cortex $TAG"
fi

# --- Guards ------------------------------------------------------------------

if ! command -v gh >/dev/null 2>&1; then
  echo "Error: the GitHub CLI (gh) is not installed. Install it and run 'gh auth login'." >&2
  exit 1
fi

if ! gh auth status >/dev/null 2>&1; then
  echo "Error: gh is not authenticated. Run 'gh auth login', or set GH_TOKEN to a" >&2
  echo "       token with 'contents:write' on the target repo (see docs/DISTRIBUTION.md)." >&2
  exit 1
fi

if [[ ! -d "$RELEASE_DIR" ]]; then
  echo "Error: release directory not found: $RELEASE_DIR" >&2
  exit 1
fi

MANIFEST_SRC="$RELEASE_DIR/latest.json"
if [[ ! -f "$MANIFEST_SRC" ]]; then
  echo "Error: $MANIFEST_SRC not found. Run macos/package_release.sh first." >&2
  exit 1
fi

if [[ ! -f "$SITE_MANIFEST" ]]; then
  echo "Error: site manifest not found: $SITE_MANIFEST" >&2
  exit 1
fi

# Resolve the target repo (owner/name).
if [[ -z "$REPO" ]]; then
  if ! REPO="$(gh repo view --json nameWithOwner --jq .nameWithOwner 2>/dev/null)"; then
    echo "Error: could not resolve the target repo. Pass --repo OWNER/REPO" >&2
    echo "       (the canonical org repo from DECISION 0)." >&2
    exit 1
  fi
fi
if [[ "$REPO" != */* ]]; then
  echo "Error: --repo must be in OWNER/REPO form, got: $REPO" >&2
  exit 2
fi

# Collect the artifact filenames from the source manifest, then confirm the
# files (plus the checksums file) exist in the release directory. Read into an
# array without `mapfile` so this works on the stock macOS bash 3.2.
ARTIFACT_FILES=()
while IFS= read -r artifact_name; do
  [[ -n "$artifact_name" ]] && ARTIFACT_FILES+=("$artifact_name")
done < <(python3 -c '
import json, sys
with open(sys.argv[1], encoding="utf-8") as handle:
    manifest = json.load(handle)
for artifact in manifest.get("artifacts", []):
    name = artifact.get("filename")
    if name:
        print(name)
' "$MANIFEST_SRC")

if [[ ${#ARTIFACT_FILES[@]} -eq 0 ]]; then
  echo "Error: no artifacts listed in $MANIFEST_SRC" >&2
  exit 1
fi

UPLOADS=()
MISSING=()
for name in "${ARTIFACT_FILES[@]}"; do
  path="$RELEASE_DIR/$name"
  if [[ -f "$path" ]]; then
    UPLOADS+=("$path")
  else
    MISSING+=("$name")
  fi
done

# Include the checksums file as a Release asset if present.
for checksums in "$RELEASE_DIR"/*.checksums.txt; do
  [[ -e "$checksums" ]] && UPLOADS+=("$checksums")
done

if [[ ${#MISSING[@]} -gt 0 ]]; then
  echo "Error: artifacts listed in the manifest are missing from $RELEASE_DIR:" >&2
  for name in "${MISSING[@]}"; do
    echo "  - $name" >&2
  done
  echo "Build them with macos/package_release.sh --production before publishing." >&2
  exit 1
fi

# --- Publish -----------------------------------------------------------------

echo "Target repo:   $REPO"
echo "Tag:           $TAG"
echo "Release dir:   $RELEASE_DIR"
echo "Assets:        ${#UPLOADS[@]}"
for path in "${UPLOADS[@]}"; do
  echo "  - $(basename "$path")"
done

RELEASE_EXISTS=0
if gh release view "$TAG" --repo "$REPO" >/dev/null 2>&1; then
  RELEASE_EXISTS=1
fi

CREATE_ARGS=(release create "$TAG" --repo "$REPO" --title "$TITLE")
if [[ -n "$NOTES_FILE" ]]; then
  if [[ ! -f "$NOTES_FILE" ]]; then
    echo "Error: notes file not found: $NOTES_FILE" >&2
    exit 1
  fi
  CREATE_ARGS+=(--notes-file "$NOTES_FILE")
else
  CREATE_ARGS+=(--generate-notes)
fi
[[ "$DRAFT" == "1" ]] && CREATE_ARGS+=(--draft)
[[ "$PRERELEASE" == "1" ]] && CREATE_ARGS+=(--prerelease)

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "[dry-run] would run:"
  if [[ "$RELEASE_EXISTS" == "1" ]]; then
    echo "  gh release view $TAG --repo $REPO   # release exists; reuse it"
  else
    printf '  gh'; printf ' %q' "${CREATE_ARGS[@]}"; echo
  fi
  for path in "${UPLOADS[@]}"; do
    echo "  gh release upload $TAG $(basename "$path") --repo $REPO --clobber"
  done
  echo "  # then rewrite $SITE_MANIFEST urls -> https://github.com/$REPO/releases/download/$TAG/<file>"
else
  if [[ "$RELEASE_EXISTS" == "1" ]]; then
    echo "Release $TAG already exists on $REPO; reusing it."
  else
    echo "Creating release $TAG on $REPO..."
    gh "${CREATE_ARGS[@]}"
  fi
  echo "Uploading assets..."
  gh release upload "$TAG" "${UPLOADS[@]}" --repo "$REPO" --clobber
fi

# --- Repoint the site download feed at the Release asset URLs -----------------

BASE_URL="https://github.com/$REPO/releases/download/$TAG"

python3 - "$SITE_MANIFEST" "$BASE_URL" "$DRY_RUN" <<'PY'
import json
import sys

manifest_path, base_url, dry_run = sys.argv[1], sys.argv[2], sys.argv[3] == "1"

with open(manifest_path, encoding="utf-8") as handle:
    manifest = json.load(handle)

changes = []
for artifact in manifest.get("artifacts", []):
    name = artifact.get("filename")
    if not name:
        continue
    new_url = f"{base_url}/{name}"
    old_url = artifact.get("url", "")
    if old_url != new_url:
        changes.append((name, old_url, new_url))
    # filename / size_bytes / sha256 are preserved; only url flips.
    artifact["url"] = new_url

if dry_run:
    print()
    print("[dry-run] manifest url rewrites:")
    if not changes:
        print("  (no changes; urls already point at this Release)")
    for name, old, new in changes:
        print(f"  {name}:\n    {old or '(none)'} -> {new}")
    sys.exit(0)

with open(manifest_path, "w", encoding="utf-8") as handle:
    json.dump(manifest, handle, indent=2, sort_keys=True)
    handle.write("\n")

print(f"Rewrote {len(changes)} artifact url(s) in {manifest_path} -> {base_url}/<file>")
PY

if [[ "$DRY_RUN" == "1" ]]; then
  echo
  echo "[dry-run] no Release created, no assets uploaded, no manifest written."
  exit 0
fi

echo
echo "Published. Verify the download feed with:"
echo "  python3 scripts/check_distribution_site.py"
echo "The site now links to the Release at $BASE_URL/"
