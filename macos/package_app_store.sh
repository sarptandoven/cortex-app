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

TEAM_ID="${CORTEX_TEAM_ID:-P4H96J3VXY}"
APP_NAME="${CORTEX_APPSTORE_APP_NAME:-Doppl}"
DISPLAY_NAME="${CORTEX_APPSTORE_DISPLAY_NAME:-Doppl}"
BUNDLE_ID="com.cortex.doppl"
INFO="$ROOT/Info.plist"
VERSION="$(plutil -extract CFBundleShortVersionString raw -o - "$INFO")"
BUILD="$(plutil -extract CFBundleVersion raw -o - "$INFO")"
RELEASE_NAME="${APP_NAME}-AppStore-${VERSION}-${BUILD}"
OUTPUT_ROOT="${CORTEX_APPSTORE_OUTPUT:-$WORKSPACE_ROOT/outputs}"
OUT_DIR="$OUTPUT_ROOT/$RELEASE_NAME"
APP="$ROOT/build/$APP_NAME.app"
PKG_STAGE="$OUT_DIR/payload"
APP_FOR_PACKAGE="$PKG_STAGE/$APP_NAME.app"
PKG="$OUT_DIR/$RELEASE_NAME.pkg"
REPORT="$OUT_DIR/app-store-build-report.json"
ENTITLEMENTS_OUT="$OUT_DIR/$RELEASE_NAME.entitlements.plist"
SIGN_IDENTITY="${CORTEX_APPSTORE_SIGN_IDENTITY:-}"
INSTALLER_IDENTITY="${CORTEX_APPSTORE_INSTALLER_IDENTITY:-}"
PROFILE="${CORTEX_APPSTORE_PROVISIONING_PROFILE:-${CORTEX_PROVISIONING_PROFILE:-}}"
ENTITLEMENTS="$ROOT/AppStore.entitlements"

usage() {
  cat <<'EOF'
Usage: macos/package_app_store.sh

Environment:
  CORTEX_APPSTORE_SIGN_IDENTITY       Apple Distribution identity for the app.
  CORTEX_APPSTORE_INSTALLER_IDENTITY  Optional installer identity for productbuild.
  CORTEX_APPSTORE_PROVISIONING_PROFILE Path to Mac App Store provisioning profile.
  CORTEX_TEAM_ID                      Apple Team ID. Default: P4H96J3VXY.
  CORTEX_APPSTORE_OUTPUT              Output root. Default: ../outputs from workspace root.

The script always creates the closest local package possible. Without an Apple
Distribution identity and Mac App Store provisioning profile, the package is a
dry run and is not uploadable to App Store Connect.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

find_identity() {
  local pattern="$1"
  security find-identity -v -p codesigning | awk -v pattern="$pattern" '
    index($0, pattern) { gsub(/^.*"/, ""); gsub(/".*$/, ""); print; exit }
  '
}

if [[ -z "$SIGN_IDENTITY" ]]; then
  SIGN_IDENTITY="$(find_identity "Apple Distribution:")"
fi
if [[ -z "$SIGN_IDENTITY" ]]; then
  SIGN_IDENTITY="-"
fi

SIGNING_STATUS="dry-run"
if [[ "$SIGN_IDENTITY" != "-" && -n "$PROFILE" && -f "$PROFILE" ]]; then
  SIGNING_STATUS="upload-candidate"
fi

mkdir -p "$OUT_DIR"

CORTEX_APP_BUNDLE_NAME="$APP_NAME" \
CORTEX_DISPLAY_NAME="$DISPLAY_NAME" \
CORTEX_PRODUCT_NAME="$DISPLAY_NAME" \
CORTEX_DISTRIBUTION_MODE="app-store" \
CORTEX_ENTITLEMENTS="$ENTITLEMENTS" \
CORTEX_CODESIGN_IDENTITY="$SIGN_IDENTITY" \
CORTEX_CODESIGN_TIMESTAMP="${CORTEX_APPSTORE_CODESIGN_TIMESTAMP:-0}" \
CORTEX_PROVISIONING_PROFILE="$PROFILE" \
"$ROOT/build.sh"

APP_BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "$APP/Contents/Info.plist")"
APP_MODE="$(plutil -extract CortexDistributionMode raw -o - "$APP/Contents/Info.plist")"
if [[ "$APP_BUNDLE_ID" != "$BUNDLE_ID" ]]; then
  echo "Expected bundle id $BUNDLE_ID, got $APP_BUNDLE_ID" >&2
  exit 2
fi
if [[ "$APP_MODE" != "app-store" ]]; then
  echo "Expected CortexDistributionMode app-store, got $APP_MODE" >&2
  exit 2
fi

codesign --verify --deep --strict "$APP"
codesign -d --entitlements :- "$APP" > "$ENTITLEMENTS_OUT" 2>/dev/null || true

rm -rf "$PKG_STAGE"
mkdir -p "$PKG_STAGE"
DITTONORSRC=1 ditto --norsrc "$APP" "$APP_FOR_PACKAGE"
codesign --verify --deep --strict "$APP_FOR_PACKAGE"

rm -f "$PKG"
if [[ -n "$INSTALLER_IDENTITY" ]]; then
  COPYFILE_DISABLE=1 productbuild --component "$APP_FOR_PACKAGE" /Applications --sign "$INSTALLER_IDENTITY" "$PKG" >/dev/null
else
  COPYFILE_DISABLE=1 productbuild --component "$APP_FOR_PACKAGE" /Applications "$PKG" >/dev/null
fi

PROFILE_STATUS="missing"
if [[ -n "$PROFILE" && -f "$PROFILE" ]]; then
  PROFILE_STATUS="embedded"
fi

INSTALLER_STATUS="unsigned"
if [[ -n "$INSTALLER_IDENTITY" ]]; then
  INSTALLER_STATUS="signed"
fi

PKG_SHA="$(shasum -a 256 "$PKG" | awk '{print $1}')"
PKG_SIZE="$(wc -c < "$PKG" | tr -d ' ')"
UPLOADABLE=false
if [[ "$SIGNING_STATUS" == "upload-candidate" && "$INSTALLER_STATUS" == "signed" ]]; then
  UPLOADABLE=true
fi

cat > "$REPORT" <<EOF
{
  "app": "$APP_NAME",
  "bundle_id": "$APP_BUNDLE_ID",
  "team_id": "$TEAM_ID",
  "version": "$VERSION",
  "build": "$BUILD",
  "distribution_mode": "$APP_MODE",
  "status": "$SIGNING_STATUS",
  "uploadable": $UPLOADABLE,
  "app_signing_identity": "$SIGN_IDENTITY",
  "provisioning_profile": "$PROFILE_STATUS",
  "installer_package": "$INSTALLER_STATUS",
  "pkg": "$PKG",
  "pkg_size_bytes": $PKG_SIZE,
  "pkg_sha256": "$PKG_SHA",
  "entitlements": "$ENTITLEMENTS_OUT"
}
EOF

cat "$REPORT"
