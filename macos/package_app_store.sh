#!/usr/bin/env bash
set -euo pipefail

# ---------------------------------------------------------------------------
# Mac App Store (MAS) packaging pipeline for Doppl / Cortex.
#
# This script ONLY builds the sandboxed, local-first App Store build
# (CORTEX_DISTRIBUTION_MODE=app-store). It never touches the Developer-ID /
# notarized-DMG path (package_release.sh) — that stays byte-identical.
#
# Strategy:
#   - Mac App Store build  = sandboxed; REQUIRES a signed-in account (Info.plist
#     CortexRequireAccount=true). Sign-in + memory sync run over HTTPS via the
#     system URLSession (Swift), NOT the bundled Python (whose _ssl/ssl.py are
#     still stripped by build.sh in app-store mode — that path is loopback-only).
#     MCP setup is guided-manual only (no automatic config install). Data: email +
#     name + user content, Linked, not tracking (PrivacyInfo.xcprivacy).
#   - Direct/notarized DMG = the full-featured power-user path (package_release.sh).
#
# The script ALWAYS produces the closest local package possible. Without an
# Apple Distribution signing identity and a Mac App Store provisioning profile
# (the current reality), it does a clean DRY RUN: it still builds an
# ad-hoc-signed .pkg so the structure can be inspected, and prints an ordered
# FOUNDER CHECKLIST of the account-owner-only steps. A dry run never errors out.
# ---------------------------------------------------------------------------

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
CHECKLIST_OUT="$OUT_DIR/FOUNDER_CHECKLIST.txt"

SIGN_IDENTITY="${CORTEX_APPSTORE_SIGN_IDENTITY:-}"
INSTALLER_IDENTITY="${CORTEX_APPSTORE_INSTALLER_IDENTITY:-}"
# CORTEX_MAS_PROFILE is the documented name; the older names stay accepted.
PROFILE="${CORTEX_MAS_PROFILE:-${CORTEX_APPSTORE_PROVISIONING_PROFILE:-${CORTEX_PROVISIONING_PROFILE:-}}}"
ENTITLEMENTS="$ROOT/AppStore.entitlements"
PRIVACY_SRC="$ROOT/PrivacyInfo.xcprivacy"

# Upload credentials (all optional). Present => the optional upload step runs.
ASC_API_KEY_ID="${CORTEX_ASC_API_KEY_ID:-}"
ASC_API_ISSUER_ID="${CORTEX_ASC_API_ISSUER_ID:-}"
ASC_API_KEY_PATH="${CORTEX_ASC_API_KEY_PATH:-}"
ASC_APPLE_ID="${CORTEX_ASC_APPLE_ID:-}"
ASC_APP_PASSWORD="${CORTEX_ASC_APP_PASSWORD:-}"
DO_UPLOAD="${CORTEX_APPSTORE_UPLOAD:-0}"

usage() {
  cat <<'EOF'
Usage: macos/package_app_store.sh

Builds the Mac App Store (sandboxed, local-first) .pkg for Doppl/Cortex.

Environment (all optional; absent => clean dry run + founder checklist):
  CORTEX_APPSTORE_SIGN_IDENTITY        App signing identity. Accepts
                                       'Apple Distribution: ...' or legacy
                                       '3rd Party Mac Developer Application: ...'.
  CORTEX_APPSTORE_INSTALLER_IDENTITY   Installer identity for productbuild.
                                       Accepts 'Apple Distribution Installer: ...'
                                       or '3rd Party Mac Developer Installer: ...'.
  CORTEX_MAS_PROFILE                   Path to the Mac App Store provisioning
                                       profile (embedded at
                                       Contents/embedded.provisionprofile).
  CORTEX_TEAM_ID                       Apple Team ID. Default: P4H96J3VXY.
  CORTEX_APPSTORE_OUTPUT               Output root. Default: ../outputs.

Optional upload (App Store Connect); the step runs only when creds are present
and CORTEX_APPSTORE_UPLOAD=1:
  CORTEX_APPSTORE_UPLOAD=1             Enable the upload step.
  CORTEX_ASC_API_KEY_ID                App Store Connect API key id (notarytool/altool).
  CORTEX_ASC_API_ISSUER_ID             App Store Connect API issuer id.
  CORTEX_ASC_API_KEY_PATH              Path to the .p8 API key.
  CORTEX_ASC_APPLE_ID                  Apple ID (alternative to API key).
  CORTEX_ASC_APP_PASSWORD              App-specific password for that Apple ID.

Without an Apple Distribution identity AND a Mac App Store provisioning profile,
the package is an ad-hoc DRY RUN: not uploadable, but structurally inspectable.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

log() { printf '%s\n' "$*"; }
section() { printf '\n== %s ==\n' "$*"; }

find_identity() {
  # Return the first codesigning identity whose name contains ANY of the given
  # patterns (accepts modern + legacy cert names).
  local line
  while IFS= read -r line; do
    local p
    for p in "$@"; do
      if [[ "$line" == *"$p"* ]]; then
        # extract the quoted identity name
        printf '%s\n' "$line" | sed -E 's/^[^"]*"//; s/".*$//'
        return 0
      fi
    done
  done < <(security find-identity -v -p codesigning 2>/dev/null)
  return 0
}

# --- Resolve the app signing identity (modern first, then legacy). ---------
if [[ -z "$SIGN_IDENTITY" ]]; then
  SIGN_IDENTITY="$(find_identity "Apple Distribution:" "3rd Party Mac Developer Application:")"
fi
if [[ -z "$SIGN_IDENTITY" ]]; then
  SIGN_IDENTITY="-"
fi

# --- Resolve the installer signing identity (modern first, then legacy). ---
if [[ -z "$INSTALLER_IDENTITY" ]]; then
  INSTALLER_IDENTITY="$(find_identity "Apple Distribution Installer:" "3rd Party Mac Developer Installer:")"
fi

PROFILE_PRESENT="0"
if [[ -n "$PROFILE" && -f "$PROFILE" ]]; then
  PROFILE_PRESENT="1"
elif [[ -n "$PROFILE" ]]; then
  log "WARNING: CORTEX_MAS_PROFILE points at '$PROFILE' but no file is there; treating profile as ABSENT."
  PROFILE=""
fi

SIGNING_STATUS="dry-run"
if [[ "$SIGN_IDENTITY" != "-" && "$PROFILE_PRESENT" == "1" && -n "$INSTALLER_IDENTITY" ]]; then
  SIGNING_STATUS="upload-candidate"
fi

section "MAS PACKAGING PLAN"
log "app                 : $APP_NAME ($BUNDLE_ID)  $VERSION ($BUILD)"
log "team                : $TEAM_ID"
log "app sign identity   : ${SIGN_IDENTITY}"
log "installer identity  : ${INSTALLER_IDENTITY:-<none>}"
log "provisioning profile: $([[ "$PROFILE_PRESENT" == "1" ]] && echo "$PROFILE" || echo "<none>")"
log "mode                : $SIGNING_STATUS"

# Report exactly what is missing (task requirement) when we cannot make an
# upload candidate. This never aborts the run.
MISSING=()
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  MISSING+=("Apple Distribution app-signing certificate (or legacy '3rd Party Mac Developer Application') for team $TEAM_ID — set CORTEX_APPSTORE_SIGN_IDENTITY")
fi
if [[ "$PROFILE_PRESENT" != "1" ]]; then
  MISSING+=("Mac App Store provisioning profile for $BUNDLE_ID — set CORTEX_MAS_PROFILE to its path")
fi
if [[ -z "$INSTALLER_IDENTITY" ]]; then
  MISSING+=("Mac Installer Distribution certificate ('Apple Distribution Installer' or legacy '3rd Party Mac Developer Installer') — set CORTEX_APPSTORE_INSTALLER_IDENTITY")
fi
if [[ ${#MISSING[@]} -gt 0 ]]; then
  section "MISSING FOR AN UPLOADABLE BUILD (dry run will proceed)"
  for m in "${MISSING[@]}"; do log "  - $m"; done
fi

mkdir -p "$OUT_DIR"

# --- Build the sandboxed app-store .app via build.sh. ----------------------
# Passing CORTEX_PROVISIONING_PROFILE makes build.sh embed the profile at
# Contents/embedded.provisionprofile BEFORE it seals (signs) the bundle.
section "BUILD app-store bundle"
CORTEX_APP_BUNDLE_NAME="$APP_NAME" \
CORTEX_DISPLAY_NAME="$DISPLAY_NAME" \
CORTEX_PRODUCT_NAME="$DISPLAY_NAME" \
CORTEX_DISTRIBUTION_MODE="app-store" \
CORTEX_ENTITLEMENTS="$ENTITLEMENTS" \
CORTEX_CODESIGN_IDENTITY="$SIGN_IDENTITY" \
CORTEX_CODESIGN_TIMESTAMP="${CORTEX_APPSTORE_CODESIGN_TIMESTAMP:-0}" \
CORTEX_PROVISIONING_PROFILE="$PROFILE" \
"$ROOT/build.sh"

APP_INFO="$APP/Contents/Info.plist"
APP_BUNDLE_ID="$(plutil -extract CFBundleIdentifier raw -o - "$APP_INFO")"
APP_MODE="$(plutil -extract CortexDistributionMode raw -o - "$APP_INFO")"
if [[ "$APP_BUNDLE_ID" != "$BUNDLE_ID" ]]; then
  echo "Expected bundle id $BUNDLE_ID, got $APP_BUNDLE_ID" >&2
  exit 2
fi
if [[ "$APP_MODE" != "app-store" ]]; then
  echo "Expected CortexDistributionMode app-store, got $APP_MODE" >&2
  exit 2
fi

# --- App Store required artifacts injected into the built bundle. ----------
# These mutate ONLY the build-output bundle (never the source Info.plist), so
# the Developer-ID/DMG path is untouched. We mutate before re-signing below.
section "INJECT App Store artifacts (privacy manifest + encryption declaration)"

# 1) Privacy manifest at Contents/Resources/PrivacyInfo.xcprivacy.
RES_DIR="$APP/Contents/Resources"
mkdir -p "$RES_DIR"
if [[ -f "$PRIVACY_SRC" ]]; then
  cp "$PRIVACY_SRC" "$RES_DIR/PrivacyInfo.xcprivacy"
  log "  copied $PRIVACY_SRC -> Contents/Resources/PrivacyInfo.xcprivacy"
else
  # Robust fallback: generate a local-first (collects nothing) manifest so the
  # MAS build always ships a privacy manifest even if the source file is absent.
  log "  WARNING: $PRIVACY_SRC not found; writing a generated local-first privacy manifest."
  cat > "$RES_DIR/PrivacyInfo.xcprivacy" <<'PRIVACY'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>NSPrivacyTracking</key>
  <false/>
  <key>NSPrivacyTrackingDomains</key>
  <array/>
  <key>NSPrivacyCollectedDataTypes</key>
  <array/>
  <key>NSPrivacyAccessedAPITypes</key>
  <array>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryFileTimestamp</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>C617.1</string></array>
    </dict>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategoryUserDefaults</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>CA92.1</string></array>
    </dict>
    <dict>
      <key>NSPrivacyAccessedAPIType</key>
      <string>NSPrivacyAccessedAPICategorySystemBootTime</string>
      <key>NSPrivacyAccessedAPITypeReasons</key>
      <array><string>35F9.1</string></array>
    </dict>
  </array>
</dict>
</plist>
PRIVACY
fi

# 2) Export-compliance declaration. The app-store build strips _ssl/ssl.py, so
#    it makes no use of non-exempt encryption. Declaring this lets App Store
#    Connect skip the manual export-compliance question. We set it on the built
#    bundle's Info.plist only (build artifact), never the source Info.plist.
if plutil -extract ITSAppUsesNonExemptEncryption raw -o - "$APP_INFO" >/dev/null 2>&1; then
  plutil -replace ITSAppUsesNonExemptEncryption -bool false "$APP_INFO"
else
  plutil -insert ITSAppUsesNonExemptEncryption -bool false "$APP_INFO"
fi
log "  set ITSAppUsesNonExemptEncryption = false in the app bundle Info.plist"

# --- Re-seal the bundle. build.sh already signed it, but we just mutated
#     Resources/ and Info.plist, so the seal is stale and MUST be refreshed.
#     Real identity => Apple Distribution signing (upload path). Otherwise
#     ad-hoc so the dry-run bundle still verifies locally. --------------------
section "RE-SIGN app after artifact injection"
RESIGN_ARGS=(--force --deep --entitlements "$ENTITLEMENTS")
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  RESIGN_ARGS+=(--sign -)
  log "  ad-hoc re-sign (dry run)"
else
  RESIGN_ARGS+=(--options runtime --sign "$SIGN_IDENTITY")
  log "  re-sign with: $SIGN_IDENTITY"
fi
codesign "${RESIGN_ARGS[@]}" "$APP" >/dev/null

codesign --verify --deep --strict "$APP"
codesign -d --entitlements :- "$APP" > "$ENTITLEMENTS_OUT" 2>/dev/null || true

# --- Confirm the App Store artifacts survived signing. ---------------------
PROFILE_STATUS="missing"
if [[ -f "$APP/Contents/embedded.provisionprofile" ]]; then
  PROFILE_STATUS="embedded"
fi
PRIVACY_STATUS="missing"
if [[ -f "$RES_DIR/PrivacyInfo.xcprivacy" ]]; then
  PRIVACY_STATUS="present"
fi

# --- Stage the payload and build the installer .pkg. -----------------------
section "BUILD installer .pkg"
rm -rf "$PKG_STAGE"
mkdir -p "$PKG_STAGE"
DITTONORSRC=1 ditto --norsrc "$APP" "$APP_FOR_PACKAGE"
codesign --verify --deep --strict "$APP_FOR_PACKAGE"

rm -f "$PKG"
INSTALLER_STATUS="unsigned"
if [[ -n "$INSTALLER_IDENTITY" ]]; then
  COPYFILE_DISABLE=1 productbuild --component "$APP_FOR_PACKAGE" /Applications --sign "$INSTALLER_IDENTITY" "$PKG" >/dev/null
  INSTALLER_STATUS="signed"
  log "  productbuild signed with: $INSTALLER_IDENTITY"
else
  COPYFILE_DISABLE=1 productbuild --component "$APP_FOR_PACKAGE" /Applications "$PKG" >/dev/null
  log "  productbuild UNSIGNED (dry run; no installer identity)"
fi

PKG_SHA="$(shasum -a 256 "$PKG" | awk '{print $1}')"
PKG_SIZE="$(wc -c < "$PKG" | tr -d ' ')"
UPLOADABLE=false
if [[ "$SIGNING_STATUS" == "upload-candidate" && "$INSTALLER_STATUS" == "signed" && "$PROFILE_STATUS" == "embedded" ]]; then
  UPLOADABLE=true
fi

# --- Optional upload step. Only runs on an uploadable pkg + present creds. --
UPLOAD_STATUS="skipped"
section "UPLOAD to App Store Connect (optional)"
if [[ "$UPLOADABLE" != "true" ]]; then
  UPLOAD_STATUS="skipped-not-uploadable"
  log "  Package is not an upload candidate; skipping upload."
elif [[ "$DO_UPLOAD" != "1" ]]; then
  UPLOAD_STATUS="skipped-disabled"
  log "  Uploadable pkg built. Set CORTEX_APPSTORE_UPLOAD=1 to upload, or upload manually via Transporter."
elif [[ -n "$ASC_API_KEY_ID" && -n "$ASC_API_ISSUER_ID" && -n "$ASC_API_KEY_PATH" ]]; then
  log "  Uploading with notarytool/altool using App Store Connect API key $ASC_API_KEY_ID ..."
  if xcrun --find altool >/dev/null 2>&1; then
    xcrun altool --upload-app -f "$PKG" -t macos \
      --apiKey "$ASC_API_KEY_ID" --apiIssuer "$ASC_API_ISSUER_ID" \
      && UPLOAD_STATUS="uploaded" || UPLOAD_STATUS="upload-failed"
  else
    log "  altool unavailable; falling back to Transporter CLI (iTMSTransporter)."
    UPLOAD_STATUS="manual-transporter"
    log "  Upload $PKG with Transporter.app or: xcrun iTMSTransporter -m upload -assetFile \"$PKG\" ..."
  fi
elif [[ -n "$ASC_APPLE_ID" && -n "$ASC_APP_PASSWORD" ]]; then
  log "  Uploading with altool using Apple ID $ASC_APPLE_ID ..."
  if xcrun --find altool >/dev/null 2>&1; then
    xcrun altool --upload-app -f "$PKG" -t macos \
      -u "$ASC_APPLE_ID" -p "$ASC_APP_PASSWORD" \
      && UPLOAD_STATUS="uploaded" || UPLOAD_STATUS="upload-failed"
  else
    UPLOAD_STATUS="manual-transporter"
    log "  altool unavailable; open Transporter.app and drag in: $PKG"
  fi
else
  UPLOAD_STATUS="manual-transporter"
  log "  No upload credentials set. Open Transporter.app (Mac App Store) and drag in:"
  log "    $PKG"
fi

# --- Founder checklist (account-owner-only steps). -------------------------
# Written whenever the build is not a finished upload; always for a dry run.
write_checklist() {
  cat <<EOF
================================================================================
FOUNDER CHECKLIST — Mac App Store submission for $APP_NAME ($BUNDLE_ID)
Team $TEAM_ID · version $VERSION ($BUILD)
Only the Apple Developer account owner can do these. Do them in order.
================================================================================

  1. Enroll in / confirm the Apple Developer Program and that this Mac's Apple
     account has access to team $TEAM_ID (Account Holder or Admin role).

  2. In App Store Connect, create the app record:
       - Platform: macOS
       - Name: $APP_NAME
       - Bundle ID: $BUNDLE_ID  (register the explicit App ID first in
         Certificates, Identifiers & Profiles if it does not exist)
       - SKU + primary language as desired

  3. In Certificates, Identifiers & Profiles, generate the signing certs:
       - "Apple Distribution" (app signing)            [or legacy
         "3rd Party Mac Developer Application"]
       - "Mac Installer Distribution" (.pkg signing)   [or legacy
         "3rd Party Mac Developer Installer"]
     Install both (with private keys) into this Mac's login keychain.

  4. Create the Mac App Store provisioning profile for $BUNDLE_ID (App Store
     distribution) with the App Sandbox + Keychain-sharing entitlements that
     match macos/AppStore.entitlements. Download it and point CORTEX_MAS_PROFILE
     at the .provisionprofile file.

  5. Export compliance: already handled in-build — this script sets
     ITSAppUsesNonExemptEncryption = false in the app bundle (the app-store
     build strips _ssl/ssl.py, so it uses no non-exempt encryption). Confirm
     the "no" answer when App Store Connect asks.

  6. Re-run this script with the identities + profile set:
       export CORTEX_APPSTORE_SIGN_IDENTITY="Apple Distribution: ... ($TEAM_ID)"
       export CORTEX_APPSTORE_INSTALLER_IDENTITY="Apple Distribution Installer: ... ($TEAM_ID)"
       export CORTEX_MAS_PROFILE="/path/to/${APP_NAME}_Mac_App_Store.provisionprofile"
       ./macos/package_app_store.sh
     The report must then show "status": "upload-candidate" and "uploadable": true.

  7. Upload the signed .pkg:
       - EITHER set CORTEX_APPSTORE_UPLOAD=1 with App Store Connect API creds
         (CORTEX_ASC_API_KEY_ID / _ISSUER_ID / _KEY_PATH) or Apple-ID creds
         (CORTEX_ASC_APPLE_ID / _APP_PASSWORD) and re-run this script,
       - OR open Transporter.app and drag in:
             $PKG

  8. Answer the App Store Connect export-compliance prompt (it should be
     pre-answered by step 5: no non-exempt encryption).

  9. Attach the build to the app version, complete metadata / screenshots /
     privacy nutrition label, then Submit for Review. This build DOES collect
     data (see docs/APP_REVIEW_NOTES.md): Email + Name + User Content, all
     Linked to the account, NOT used for tracking, purpose App Functionality.

NOTES FOR REVIEW (paste docs/APP_REVIEW_NOTES.md into App Review > Notes):
  - This build REQUIRES a signed-in account (Sign in with Apple / Google /
    GitHub / email). Sign-in + memory sync run over HTTPS via the system
    URLSession to https://api.signindoppl.com; the bundled Python's OpenSSL is
    still stripped (it is not used for that path, so 2.5.1 stays clean). Put the
    demo account from APP_REVIEW_NOTES.md in App Store Connect > Sign-In Information.
  - Connecting an external AI tool (MCP) is a guided MANUAL copy/paste step; the
    app never writes into other apps' config files or runs downloaded code.
================================================================================
EOF
}

if [[ "$UPLOADABLE" != "true" || "$UPLOAD_STATUS" != "uploaded" ]]; then
  write_checklist | tee "$CHECKLIST_OUT"
fi

# --- Machine-readable report. ----------------------------------------------
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
  "installer_identity": "${INSTALLER_IDENTITY:-}",
  "provisioning_profile": "$PROFILE_STATUS",
  "privacy_manifest": "$PRIVACY_STATUS",
  "installer_package": "$INSTALLER_STATUS",
  "upload_status": "$UPLOAD_STATUS",
  "pkg": "$PKG",
  "pkg_size_bytes": $PKG_SIZE,
  "pkg_sha256": "$PKG_SHA",
  "entitlements": "$ENTITLEMENTS_OUT",
  "checklist": "$([[ -f "$CHECKLIST_OUT" ]] && echo "$CHECKLIST_OUT" || echo "")"
}
EOF

section "REPORT"
cat "$REPORT"
