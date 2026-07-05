#!/usr/bin/env bash
set -euo pipefail
umask 022

ROOT="$(cd "$(dirname "$0")" && pwd)"
APP_BUNDLE_NAME="${CORTEX_APP_BUNDLE_NAME:-Cortex}"
DISPLAY_NAME="${CORTEX_DISPLAY_NAME:-$APP_BUNDLE_NAME}"
PRODUCT_NAME="${CORTEX_PRODUCT_NAME:-$DISPLAY_NAME}"
APP="$ROOT/build/$APP_BUNDLE_NAME.app"
MACOS="$APP/Contents/MacOS"
RES="$APP/Contents/Resources"
CACHE="$ROOT/build/ModuleCache"
SIGN_IDENTITY="${CORTEX_CODESIGN_IDENTITY:--}"
ENTITLEMENTS="${CORTEX_ENTITLEMENTS:-}"
CHILD_ENTITLEMENTS="${CORTEX_CHILD_ENTITLEMENTS:-}"
DEVID_ENTITLEMENTS="${CORTEX_DEVID_ENTITLEMENTS:-$ROOT/DeveloperID.entitlements}"
PROVISIONING_PROFILE="${CORTEX_PROVISIONING_PROFILE:-}"
DISTRIBUTION_MODE="${CORTEX_DISTRIBUTION_MODE:-direct}"
TIMESTAMP="${CORTEX_CODESIGN_TIMESTAMP:-1}"
PYTHON_FRAMEWORK_SOURCE="${CORTEX_PYTHON_FRAMEWORK_SOURCE:-/Library/Frameworks/Python.framework/Versions/3.12}"
PYTHON_FRAMEWORK_VERSION="${CORTEX_PYTHON_FRAMEWORK_VERSION:-$(basename "$PYTHON_FRAMEWORK_SOURCE")}"
PYTHON_FRAMEWORK_BIN="$PYTHON_FRAMEWORK_SOURCE/bin/python$PYTHON_FRAMEWORK_VERSION"
BUNDLE_PYTHON="${CORTEX_BUNDLE_PYTHON:-}"
BUNDLE_BACKEND_DEPS="${CORTEX_BUNDLE_BACKEND_DEPS:-1}"
NESTED_SIGN_IDENTITY="${CORTEX_NESTED_CODESIGN_IDENTITY:-$SIGN_IDENTITY}"
if [[ -z "$CHILD_ENTITLEMENTS" && "$DISTRIBUTION_MODE" == "app-store" && -f "$ROOT/AppStoreChild.entitlements" ]]; then
  CHILD_ENTITLEMENTS="$ROOT/AppStoreChild.entitlements"
fi
# Developer-ID (non-App-Store) hardened-runtime entitlements gate.
# A bundled CPython framework under the hardened runtime needs a small set of
# entitlements (allow-jit, allow-unsigned-executable-memory,
# disable-library-validation, allow-dyld-environment-variables) to launch and to
# pass notarization/Gatekeeper. We apply DeveloperID.entitlements only when this
# is a real Developer ID signing path: NOT app-store mode AND a real signing
# identity (not ad-hoc "-"). Ad-hoc/dev builds and the app-store path are left
# exactly as before. When active, both the nested Mach-O (Python framework
# binaries, .so wheels) and the outer .app receive these entitlements.
USE_DEVID_ENTITLEMENTS="0"
if [[ "$DISTRIBUTION_MODE" != "app-store" && "$SIGN_IDENTITY" != "-" && "$NESTED_SIGN_IDENTITY" != "-" && -n "$DEVID_ENTITLEMENTS" && -f "$DEVID_ENTITLEMENTS" ]]; then
  USE_DEVID_ENTITLEMENTS="1"
fi
if [[ -z "$BUNDLE_PYTHON" ]]; then
  if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
    BUNDLE_PYTHON="1"
  else
    BUNDLE_PYTHON="0"
  fi
fi
export COPYFILE_DISABLE=1

rm -rf "$APP"
mkdir -p "$MACOS" "$RES" "$CACHE"
cp "$ROOT/Info.plist" "$APP/Contents/Info.plist"
plutil -replace CFBundleName -string "$PRODUCT_NAME" "$APP/Contents/Info.plist"
plutil -replace CFBundleDisplayName -string "$DISPLAY_NAME" "$APP/Contents/Info.plist"
plutil -replace CortexDistributionMode -string "$DISTRIBUTION_MODE" "$APP/Contents/Info.plist"
if [[ -n "$PROVISIONING_PROFILE" ]]; then
  cp "$PROVISIONING_PROFILE" "$APP/Contents/embedded.provisionprofile"
fi
mkdir -p "$RES/backend"
cp -R "$ROOT/../backend/app" "$RES/backend/app"
find "$RES/backend/app" -type d -name "__pycache__" -prune -exec rm -rf {} +
find "$RES/backend/app" -type f -name "*.pyc" -delete
mkdir -p "$RES/scripts"
cp "$ROOT/../scripts/cortex_mcp_stdio.py" "$RES/scripts/cortex_mcp_stdio.py"
chmod +x "$RES/scripts/cortex_mcp_stdio.py"
if [[ -f "$ROOT/update-feed.example.json" ]]; then
  cp "$ROOT/update-feed.example.json" "$RES/update-feed.example.json"
fi
if [[ -f "$ROOT/Assets/AppIcon.icns" ]]; then
  cp "$ROOT/Assets/AppIcon.icns" "$RES/AppIcon.icns"
fi
PLUGIN_DIR="$ROOT/../packages/obsidian-cortex-plugin"
if [[ -d "$PLUGIN_DIR" ]]; then
  for plugin_file in manifest.json main.js versions.json; do
    if [[ ! -f "$PLUGIN_DIR/$plugin_file" ]]; then
      echo "Obsidian plugin package is missing $plugin_file. Run scripts/check_obsidian_plugin.sh before building Cortex." >&2
      exit 2
    fi
  done
  PLUGIN_RES="$RES/obsidian-cortex-plugin"
  rm -rf "$PLUGIN_RES"
  mkdir -p "$PLUGIN_RES"
  cp "$PLUGIN_DIR/manifest.json" "$PLUGIN_RES/manifest.json"
  cp "$PLUGIN_DIR/main.js" "$PLUGIN_RES/main.js"
  cp "$PLUGIN_DIR/versions.json" "$PLUGIN_RES/versions.json"
fi
if [[ "$BUNDLE_PYTHON" != "0" && "$BUNDLE_PYTHON" != "false" && "$BUNDLE_PYTHON" != "no" ]]; then
  if [[ ! -x "$PYTHON_FRAMEWORK_BIN" || ! -f "$PYTHON_FRAMEWORK_SOURCE/Python" ]]; then
    echo "Bundled Python requested, but Python.framework $PYTHON_FRAMEWORK_VERSION was not found at $PYTHON_FRAMEWORK_SOURCE" >&2
    exit 2
  fi
  PY_FRAMEWORK="$APP/Contents/Frameworks/Python.framework"
  PY_VERSION="$PY_FRAMEWORK/Versions/$PYTHON_FRAMEWORK_VERSION"
  PY_STDLIB="$PY_VERSION/lib/python$PYTHON_FRAMEWORK_VERSION"
  mkdir -p "$PY_VERSION/bin" "$PY_VERSION/lib" "$PY_VERSION/Resources" "$PY_FRAMEWORK/Versions"
  cp "$PYTHON_FRAMEWORK_SOURCE/Python" "$PY_VERSION/Python"
  cp "$PYTHON_FRAMEWORK_BIN" "$PY_VERSION/bin/python$PYTHON_FRAMEWORK_VERSION"
  cp "$PYTHON_FRAMEWORK_SOURCE/Resources/Info.plist" "$PY_VERSION/Resources/Info.plist"
  cp -R "$PYTHON_FRAMEWORK_SOURCE/Resources/Python.app" "$PY_VERSION/Resources/Python.app"
  rsync -a --delete \
    --exclude "__pycache__" \
    --exclude "*.pyc" \
    --exclude "site-packages" \
    --exclude "test" \
    --exclude "idlelib" \
    --exclude "tkinter" \
    --exclude "turtledemo" \
    --exclude "ensurepip" \
    "$PYTHON_FRAMEWORK_SOURCE/lib/python$PYTHON_FRAMEWORK_VERSION/" "$PY_STDLIB/"
  ln -sfn "python$PYTHON_FRAMEWORK_VERSION" "$PY_VERSION/bin/python3"
  ln -sfn "$PYTHON_FRAMEWORK_VERSION" "$PY_FRAMEWORK/Versions/Current"
  ln -sfn Versions/Current/Python "$PY_FRAMEWORK/Python"
  ln -sfn Versions/Current/Resources "$PY_FRAMEWORK/Resources"
  install_name_tool -id "@rpath/Python.framework/Versions/$PYTHON_FRAMEWORK_VERSION/Python" "$PY_VERSION/Python"
  install_name_tool -change "$PYTHON_FRAMEWORK_SOURCE/Python" "@executable_path/../Python" "$PY_VERSION/bin/python$PYTHON_FRAMEWORK_VERSION"
  install_name_tool -change "$PYTHON_FRAMEWORK_SOURCE/Python" "@executable_path/../../../../Python" "$PY_VERSION/Resources/Python.app/Contents/MacOS/Python"
  if [[ "$BUNDLE_BACKEND_DEPS" != "0" && "$BUNDLE_BACKEND_DEPS" != "false" && "$BUNDLE_BACKEND_DEPS" != "no" ]]; then
    PY_RUNTIME_DEPS="$RES/python"
    rm -rf "$PY_RUNTIME_DEPS"
    mkdir -p "$PY_RUNTIME_DEPS"
    "$PYTHON_FRAMEWORK_BIN" -m pip install \
      --disable-pip-version-check \
      --only-binary=:all: \
      --target "$PY_RUNTIME_DEPS" \
      -r "$ROOT/../backend/runtime-requirements.txt"
    find "$PY_RUNTIME_DEPS" -type d -name "__pycache__" -prune -exec rm -rf {} +
    find "$PY_RUNTIME_DEPS" -type f -name "*.pyc" -delete
    # Bundle the local embedding model (~8MB) so semantics work fully offline with no API key and
    # no first-run network download. Non-fatal: if bundling fails, the app leaves the provider
    # unset and the backend uses its deterministic hash fallback.
    CORTEX_MODEL2VEC_MODEL="${CORTEX_MODEL2VEC_MODEL:-minishlab/potion-base-8M}"
    if PYTHONPATH="$PY_RUNTIME_DEPS" "$PYTHON_FRAMEWORK_BIN" -c \
        "import sys; from model2vec import StaticModel; StaticModel.from_pretrained('$CORTEX_MODEL2VEC_MODEL').save_pretrained('$RES/model2vec')" ; then
      find "$RES/model2vec" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
      echo "Bundled local embedding model into $RES/model2vec"
    else
      echo "warning: local embedding model not bundled ($CORTEX_MODEL2VEC_MODEL); backend will use the hash fallback"
      rm -rf "$RES/model2vec"
    fi
  fi
fi

export CLANG_MODULE_CACHE_PATH="$CACHE"

swiftc \
  -parse-as-library \
  -module-cache-path "$CACHE" \
  -target arm64-apple-macosx13.0 \
  -framework AppKit \
  -framework SwiftUI \
  -framework Carbon \
  -framework UserNotifications \
  -framework Security \
  "$ROOT"/Sources/*.swift \
  -o "$MACOS/Cortex"

scrub_bundle_metadata() {
  find "$APP" \( -name ".DS_Store" -o -name "._*" \) -type f -delete
  if command -v xattr >/dev/null 2>&1; then
    xattr -c "$APP" 2>/dev/null || true
    find "$APP" -mindepth 1 -print0 | xargs -0 xattr -c 2>/dev/null || true
  fi
}

normalize_bundle_permissions() {
  find "$APP" -type d -exec chmod 755 {} +
  find "$APP" -type f -exec chmod u+rw,go+r {} +
}

scrub_bundle_metadata
normalize_bundle_permissions

sign_file_if_macho() {
  local file="$1"
  local use_child_entitlements="${2:-0}"
  if file "$file" | grep -q "Mach-O"; then
    local args=(--force)
    if [[ "$use_child_entitlements" == "1" && -n "$CHILD_ENTITLEMENTS" ]]; then
      args+=(--entitlements "$CHILD_ENTITLEMENTS")
    elif [[ "$USE_DEVID_ENTITLEMENTS" == "1" ]]; then
      # Developer-ID hardened-runtime path: every nested Mach-O (Python framework
      # binaries and native .so/.dylib wheels) gets the hardened-runtime
      # entitlements the bundled interpreter needs. Notarization checks the whole
      # bundle, so this must reach the nested binaries, not just the outer .app.
      args+=(--entitlements "$DEVID_ENTITLEMENTS")
    fi
    if [[ "$NESTED_SIGN_IDENTITY" == "-" ]]; then
      args+=(--sign -)
    else
      args+=(--options runtime --sign "$NESTED_SIGN_IDENTITY")
    fi
    args+=("$file")
    codesign "${args[@]}" >/dev/null
  fi
}

if [[ -d "${PY_VERSION:-}" ]]; then
  while IFS= read -r macho_file; do
    child_entitlements="0"
    if [[ "$macho_file" == "$PY_VERSION/bin/python$PYTHON_FRAMEWORK_VERSION" || "$macho_file" == "$PY_VERSION/Resources/Python.app/Contents/MacOS/Python" ]]; then
      child_entitlements="1"
    fi
    sign_file_if_macho "$macho_file" "$child_entitlements"
  done < <(find "$PY_VERSION" -type f \( -name "Python" -o -name "python$PYTHON_FRAMEWORK_VERSION" -o -name "*.so" -o -name "*.dylib" \))
  if [[ -d "$PY_VERSION/Resources/Python.app" ]]; then
    PY_APP_SIGN_ARGS=(--force)
    if [[ -n "$CHILD_ENTITLEMENTS" ]]; then
      PY_APP_SIGN_ARGS+=(--entitlements "$CHILD_ENTITLEMENTS")
    elif [[ "$USE_DEVID_ENTITLEMENTS" == "1" ]]; then
      PY_APP_SIGN_ARGS+=(--entitlements "$DEVID_ENTITLEMENTS")
    fi
    if [[ "$NESTED_SIGN_IDENTITY" == "-" ]]; then
      PY_APP_SIGN_ARGS+=(--sign -)
    else
      PY_APP_SIGN_ARGS+=(--options runtime --sign "$NESTED_SIGN_IDENTITY")
    fi
    PY_APP_SIGN_ARGS+=("$PY_VERSION/Resources/Python.app")
    codesign "${PY_APP_SIGN_ARGS[@]}" >/dev/null
  fi
fi

if [[ -d "$RES/python" ]]; then
  while IFS= read -r macho_file; do
    sign_file_if_macho "$macho_file" "0"
  done < <(find "$RES/python" -type f \( -name "*.so" -o -name "*.dylib" \))
fi

SIGN_ARGS=(--force --deep)
if [[ -n "$ENTITLEMENTS" ]]; then
  SIGN_ARGS+=(--entitlements "$ENTITLEMENTS")
elif [[ "$USE_DEVID_ENTITLEMENTS" == "1" ]]; then
  # Outer .app under the Developer-ID hardened-runtime path gets the same
  # hardened-runtime entitlements as the nested Python binaries. An explicit
  # CORTEX_ENTITLEMENTS override still wins.
  SIGN_ARGS+=(--entitlements "$DEVID_ENTITLEMENTS")
fi
if [[ "$SIGN_IDENTITY" == "-" ]]; then
  SIGN_ARGS+=(--sign -)
else
  SIGN_ARGS+=(--options runtime)
  if [[ "$TIMESTAMP" != "0" && "$TIMESTAMP" != "false" && "$TIMESTAMP" != "no" ]]; then
    SIGN_ARGS+=(--timestamp)
  fi
  SIGN_ARGS+=(--sign "$SIGN_IDENTITY")
fi
codesign "${SIGN_ARGS[@]}" "$APP" >/dev/null
normalize_bundle_permissions
scrub_bundle_metadata
echo "Built $APP"
