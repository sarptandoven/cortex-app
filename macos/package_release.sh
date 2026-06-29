#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
PROJECT_ROOT="$(cd "$ROOT/.." && pwd)"
PROJECT_PARENT="$(cd "$PROJECT_ROOT/.." && pwd)"
if [[ "$(basename "$PROJECT_PARENT")" == "work" ]]; then
  WORKSPACE_ROOT="$(cd "$PROJECT_PARENT/.." && pwd)"
else
  WORKSPACE_ROOT="$PROJECT_ROOT"
fi
DEFAULT_OUTPUT="$WORKSPACE_ROOT/outputs"
CHANNEL="local-beta"
BASE_URL=""
OUTPUT_ROOT="$DEFAULT_OUTPUT"
NOTES=()
SIGN_IDENTITY="${CORTEX_CODESIGN_IDENTITY:--}"
NOTARY_PROFILE="${CORTEX_NOTARY_PROFILE:-}"
STRICT_RELEASE="${CORTEX_RELEASE_STRICT:-0}"
BUNDLE_PYTHON="${CORTEX_BUNDLE_PYTHON:-0}"

usage() {
  cat <<'EOF'
Usage: macos/package_release.sh [options]

Options:
  --channel NAME       Release channel. Default: local-beta
  --base-url URL      Public URL prefix for artifacts in latest.json
  --output DIR        Output directory. Default: ../outputs from the workspace root
  --note TEXT         Release note. Can be passed multiple times
  --production        Require HTTPS URLs, Developer ID signing, notarization, and bundled Python
  -h, --help          Show help

Environment:
  CORTEX_CODESIGN_IDENTITY   Optional Developer ID Application identity.
  CORTEX_NOTARY_PROFILE      Optional notarytool keychain profile.
  CORTEX_BUNDLE_PYTHON       Set to 1 for production direct builds.
  CORTEX_RELEASE_STRICT      Set to 1 to enable the same checks as --production.

Creates:
  Cortex-<version>-<build>.dmg
  Cortex-<version>-<build>.app.zip
  Cortex-<version>-<build>.checksums.txt
  latest.json
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --channel)
      CHANNEL="$2"
      shift 2
      ;;
    --base-url)
      BASE_URL="${2%/}"
      shift 2
      ;;
    --output)
      OUTPUT_ROOT="$2"
      shift 2
      ;;
    --note)
      NOTES+=("$2")
      shift 2
      ;;
    --production|--strict)
      STRICT_RELEASE="1"
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ "$CHANNEL" != "local-beta" ]]; then
  STRICT_RELEASE="1"
fi

if [[ "$STRICT_RELEASE" == "1" ]]; then
  if [[ "$BASE_URL" != https://* ]]; then
    echo "Production release packaging requires --base-url with an https:// URL." >&2
    exit 2
  fi
  if [[ "$SIGN_IDENTITY" == "-" ]]; then
    echo "Production release packaging requires CORTEX_CODESIGN_IDENTITY." >&2
    exit 2
  fi
  if [[ -z "$NOTARY_PROFILE" ]]; then
    echo "Production release packaging requires CORTEX_NOTARY_PROFILE for notarization." >&2
    exit 2
  fi
  case "$BUNDLE_PYTHON" in
    1|true|TRUE|yes|YES) ;;
    *)
      echo "Production release packaging requires CORTEX_BUNDLE_PYTHON=1." >&2
      exit 2
      ;;
  esac
fi

mkdir -p "$OUTPUT_ROOT"
OUTPUT_ROOT="$(cd "$OUTPUT_ROOT" && pwd)"

INFO="$ROOT/Info.plist"
VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "$INFO")"
BUILD="$(plutil -extract CFBundleVersion raw -o - "$INFO")"
MIN_MACOS="$(plutil -extract LSMinimumSystemVersion raw -o - "$INFO")"
BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "$INFO")"
STAMP="$(date -u +"%Y-%m-%dT%H:%M:%SZ")"
RELEASE_NAME="Cortex-${VERSION}-${BUILD}"
OUT_DIR="$OUTPUT_ROOT/$RELEASE_NAME"
STAGING="$ROOT/build/release-staging"
APP="$ROOT/build/Cortex.app"
DMG="$OUT_DIR/$RELEASE_NAME.dmg"
ZIP="$OUT_DIR/$RELEASE_NAME.app.zip"
MANIFEST="$OUT_DIR/latest.json"
CHECKSUMS="$OUT_DIR/$RELEASE_NAME.checksums.txt"

if [[ ${#NOTES[@]} -eq 0 ]]; then
  NOTES+=("Local-first Cortex beta with bundled backend, capture, MCP, and trust controls.")
fi

mkdir -p "$OUT_DIR"

CORTEX_BUNDLE_PYTHON="$BUNDLE_PYTHON" "$ROOT/build.sh"
codesign --verify --deep --strict "$APP"

rm -rf "$STAGING"
mkdir -p "$STAGING"
cp -R "$APP" "$STAGING/Cortex.app"
ln -s /Applications "$STAGING/Applications"
cat > "$STAGING/README.txt" <<EOF
Cortex ${VERSION} (${BUILD})

Install:
1. Drag Cortex.app to Applications.
2. Open Cortex from Applications.
3. If macOS warns because this local beta is not notarized yet, Control-click Cortex.app and choose Open.

Update:
1. Quit Cortex.
2. Replace the old Cortex.app in Applications with this version.
3. Your local vault remains in ~/Library/Application Support/Cortex/Cortex.vault.

Channel: ${CHANNEL}
Released: ${STAMP}
EOF

rm -f "$DMG" "$ZIP" "$CHECKSUMS" "$MANIFEST"
COPYFILE_DISABLE=1 hdiutil create -volname "Cortex ${VERSION}" -srcfolder "$STAGING" -ov -format UDZO "$DMG" >/dev/null
(cd "$ROOT/build" && COPYFILE_DISABLE=1 zip -qry -X "$ZIP" Cortex.app)

if [[ "$SIGN_IDENTITY" != "-" ]]; then
  codesign --force --timestamp --sign "$SIGN_IDENTITY" "$DMG" >/dev/null
  codesign --verify --verbose=2 "$DMG" >/dev/null
fi

if [[ -n "$NOTARY_PROFILE" ]]; then
  if [[ "$SIGN_IDENTITY" == "-" ]]; then
    echo "CORTEX_NOTARY_PROFILE requires CORTEX_CODESIGN_IDENTITY." >&2
    exit 2
  fi
  xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
  xcrun stapler staple "$DMG"
  xcrun stapler validate "$DMG"
fi

if [[ "$STRICT_RELEASE" == "1" ]]; then
  spctl -a -vvv -t open "$DMG"
fi

sha256() {
  shasum -a 256 "$1" | awk '{print $1}'
}

size_bytes() {
  wc -c < "$1" | tr -d ' '
}

url_for() {
  local file_name="$1"
  if [[ -n "$BASE_URL" ]]; then
    printf "%s/%s" "$BASE_URL" "$file_name"
  else
    python3 - "$OUT_DIR/$file_name" <<'PY'
import pathlib
import sys
print(pathlib.Path(sys.argv[1]).resolve().as_uri())
PY
  fi
}

DMG_FILE="$(basename "$DMG")"
ZIP_FILE="$(basename "$ZIP")"
DMG_SHA="$(sha256 "$DMG")"
ZIP_SHA="$(sha256 "$ZIP")"
DMG_SIZE="$(size_bytes "$DMG")"
ZIP_SIZE="$(size_bytes "$ZIP")"
DMG_URL="$(url_for "$DMG_FILE")"
ZIP_URL="$(url_for "$ZIP_FILE")"
NOTES_JSON="$(printf '%s\n' "${NOTES[@]}" | python3 -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin]))')"

{
  echo "$DMG_SHA  $DMG_FILE"
  echo "$ZIP_SHA  $ZIP_FILE"
} > "$CHECKSUMS"

python3 - "$MANIFEST" <<PY
import json
import sys

notes = $NOTES_JSON
payload = {
    "app": "Cortex",
    "bundle_id": "$BUNDLE_ID",
    "channel": "$CHANNEL",
    "version": "$VERSION",
    "build": "$BUILD",
    "minimum_macos": "$MIN_MACOS",
    "released_at": "$STAMP",
    "mandatory": False,
    "release_notes": notes,
    "artifacts": [
        {
            "kind": "dmg",
            "filename": "$DMG_FILE",
            "url": "$DMG_URL",
            "size_bytes": int("$DMG_SIZE"),
            "sha256": "$DMG_SHA",
        },
        {
            "kind": "zip",
            "filename": "$ZIP_FILE",
            "url": "$ZIP_URL",
            "size_bytes": int("$ZIP_SIZE"),
            "sha256": "$ZIP_SHA",
        },
    ],
}
with open(sys.argv[1], "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
    handle.write("\\n")
PY

python3 "$PROJECT_ROOT/scripts/validate_update_manifest.py" "$MANIFEST"

echo "Release packaged:"
echo "  $DMG"
echo "  $ZIP"
echo "  $MANIFEST"
echo "  $CHECKSUMS"
