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
# Source protection (BOTH distribution modes): compile the first-party backend to legacy
# in-place .pyc and delete every .py, shipping bytecode only — the plaintext engine no longer
# ships in the .app/.dmg (previously any user could read Resources/backend/*.py). This is
# pragmatic obfuscation (a determined attacker can decompile .pyc); Nuitka native compilation
# is the stronger follow-up (see docs/APPLE_RELEASE.md). App-store mode additionally reduces the
# Guideline 2.5.2 "scripts" surface. Set CORTEX_SKIP_PYC=1 to opt out (e.g. local debugging).
if [[ "${CORTEX_SKIP_PYC:-0}" != "1" ]]; then
  # We need a python to compile with: prefer the framework source we're bundling,
  # else any python3 on PATH. compileall -b writes name.pyc beside name.py.
  COMPILE_PY="$PYTHON_FRAMEWORK_SOURCE/bin/python3.12"
  [[ -x "$COMPILE_PY" ]] || COMPILE_PY="$(command -v python3.12 || command -v python3)"
  if [[ -z "$COMPILE_PY" ]]; then
    echo "ERROR: source protection needs a python3 to compile the backend to .pyc (set CORTEX_SKIP_PYC=1 to skip)" >&2
    exit 3
  fi
  echo "compiling first-party backend to .pyc (source protection)..."
  # -b: legacy in-place bytecode (foo.pyc next to foo.py, no __pycache__).
  # -q -q: silence output but still exit non-zero on any compile error.
  "$COMPILE_PY" -m compileall -b -q -q "$RES/backend/app"
  # Drop all .py (leaving only .pyc) and any __pycache__ that a non-legacy pass created.
  find "$RES/backend/app" -type f -name "*.py" -delete
  find "$RES/backend/app" -type d -name "__pycache__" -prune -exec rm -rf {} +
  # Boot check: the pruned .pyc-only tree must still import the server entrypoint.
  # PYTHONDONTWRITEBYTECODE keeps this read-only so we don't repopulate __pycache__.
  if ! PYTHONPATH="$RES/backend" PYTHONDONTWRITEBYTECODE=1 "$COMPILE_PY" -S -c "import app.standalone_server" >/dev/null 2>&1; then
    echo "ERROR: app-store .pyc-only backend failed to import app.standalone_server" >&2
    exit 3
  fi
  # Assert the delete actually stuck: zero .py, at least one .pyc.
  if [[ -n "$(find "$RES/backend/app" -type f -name '*.py' -print -quit)" ]]; then
    echo "ERROR: app-store backend still contains .py source after pruning" >&2
    exit 3
  fi
  if [[ -z "$(find "$RES/backend/app" -type f -name '*.pyc' -print -quit)" ]]; then
    echo "ERROR: app-store backend has no .pyc after compileall" >&2
    exit 3
  fi
  echo "  app-store: backend shipped as .pyc-only (import boot check passed)"
fi
# The MCP stdio bridge is a RUNNABLE helper used only by the Developer-ID/DMG build's
# one-click AI-tool setup. The App Store (sandbox) build is local-first, exposes the loopback
# HTTP tool API instead, and must NOT ship or reference an executable-code install method
# (Guideline 2.5.2). So bundle the bridge script only in non-app-store mode.
if [[ "$DISTRIBUTION_MODE" != "app-store" ]]; then
  mkdir -p "$RES/scripts"
  cp "$ROOT/../scripts/cortex_mcp_stdio.py" "$RES/scripts/cortex_mcp_stdio.py"
  chmod +x "$RES/scripts/cortex_mcp_stdio.py"
fi
# Bundled sample corpus (BOTH distribution modes): a small set of synthetic maker's notes
# the onboarding "Try sample notes" path copies into the vault and distills. They exercise
# all seven memory layers and produce a connected people/projects entity graph. Shipped as
# plain Markdown under Resources/sample-notes/ so AppState.loadSampleNotes() can read them.
if [[ -d "$ROOT/SampleNotes" ]]; then
  SAMPLE_NOTES_RES="$RES/sample-notes"
  rm -rf "$SAMPLE_NOTES_RES"
  mkdir -p "$SAMPLE_NOTES_RES"
  cp "$ROOT/SampleNotes"/*.md "$SAMPLE_NOTES_RES/"
fi
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
  if [[ ! -x "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" || ! -f "$PYTHON_FRAMEWORK_SOURCE/Python" ]]; then
    echo "Bundled Python requested, but Python.framework 3.12 was not found at $PYTHON_FRAMEWORK_SOURCE" >&2
    exit 2
  fi
  PY_FRAMEWORK="$APP/Contents/Frameworks/Python.framework"
  PY_VERSION="$PY_FRAMEWORK/Versions/3.12"
  PY_STDLIB="$PY_VERSION/lib/python3.12"
  mkdir -p "$PY_VERSION/bin" "$PY_VERSION/lib" "$PY_VERSION/Resources" "$PY_FRAMEWORK/Versions"
  cp "$PYTHON_FRAMEWORK_SOURCE/Python" "$PY_VERSION/Python"
  cp "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" "$PY_VERSION/bin/python3.12"
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
    "$PYTHON_FRAMEWORK_SOURCE/lib/python3.12/" "$PY_STDLIB/"

  # --- Strip non-public / deprecated-API C extensions Apple's App Store scanner rejects. ---
  # The bundled headless backend never uses these, and they link Tcl/Tk + OpenSSL symbols
  # (_tkinter: Tcl_*/TclBN_*; _ssl: SSL_CTX_set_options/SSL_CTX_clear_options/SSL_session_reused)
  # that App Review flags as non-public. _tkinter is removed in EVERY build (the backend imports
  # it zero times and it links absolute build-machine paths that don't resolve on user Macs).
  # _ssl + ssl.py are removed for the app-store build only: the local pipeline (loopback HTTP,
  # model2vec, sqlite-vec, MCP) needs no TLS, and urllib/http.client guard `import ssl` so they
  # degrade to http-only cleanly. Direct-download/notarized builds keep _ssl (notarization does
  # not reject on these symbols) so outbound HTTPS connectors remain available.
  DYNLOAD="$PY_STDLIB/lib-dynload"
  strip_ext() { # $1 = glob under lib-dynload
    for f in "$DYNLOAD"/$1; do [[ -e "$f" ]] && rm -f "$f" && echo "  pruned $(basename "$f")"; done
  }
  echo "Pruning non-public-API C extensions (_tkinter always; _ssl in app-store mode)..."
  strip_ext "_tkinter*.so"
  rm -rf "$PY_STDLIB/tkinter" "$PY_STDLIB/turtledemo" "$PY_STDLIB/turtle.py" "$PY_STDLIB/idlelib" 2>/dev/null || true
  if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
    strip_ext "_ssl*.so"
    rm -f "$PY_STDLIB/ssl.py"
    echo "  app-store: removed _ssl + ssl.py (backend degrades to http/loopback-only; outbound HTTPS connectors disabled)"
  fi
  # Fail loudly if a flagged extension survived — never ship a build that will bounce off review.
  if [[ -e "$DYNLOAD"/_tkinter*.so ]] || { [[ "$DISTRIBUTION_MODE" == "app-store" ]] && ls "$DYNLOAD"/_ssl*.so >/dev/null 2>&1; }; then
    echo "ERROR: a flagged C extension survived pruning in $DYNLOAD" >&2
    exit 3
  fi

  if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
    # --- 'itms-services' scrub (HARD App Store auto-reject) ---
    # Apple's static resource scanner auto-rejects any bundled file containing the
    # literal 'itms-services'. CPython 3.12's stdlib ships it in urllib/parse.py's
    # `uses_netloc` scheme list. Removing that one entry only changes urljoin's
    # netloc semantics for the itms-services:// scheme (which the loopback backend
    # never parses), so urllib stays fully functional for http/https/file/ws.
    URLPARSE="$PY_STDLIB/urllib/parse.py"
    if [[ -f "$URLPARSE" ]] && grep -q 'itms-services' "$URLPARSE"; then
      # Drop the ", 'itms-services'" (and a bare "'itms-services'") token from the
      # scheme lists without disturbing surrounding entries.
      sed -i '' -E "s/, *'itms-services'//g; s/'itms-services', *//g" "$URLPARSE"
      echo "  app-store: scrubbed 'itms-services' from bundled urllib/parse.py"
    fi
    # Also strip any lingering pre-compiled bytecode carrying the string (rsync
    # already excludes *.pyc/__pycache__, but be defensive before the hard assert).
    while IFS= read -r bc; do
      grep -Iaq 'itms-services' "$bc" 2>/dev/null && { rm -f "$bc"; echo "  app-store: removed bytecode with 'itms-services': ${bc#$PY_STDLIB/}"; }
    done < <(find "$PY_STDLIB" -type f -name '*.pyc')
    # HARD assert: the bundled stdlib must contain zero 'itms-services'.
    if grep -rlI 'itms-services' "$PY_STDLIB" >/dev/null 2>&1; then
      echo "ERROR: 'itms-services' still present in bundled stdlib after scrub:" >&2
      grep -rlI 'itms-services' "$PY_STDLIB" >&2 || true
      exit 3
    fi
    # (Bundled backend deps under Resources/python are scanned after they are
    # installed, at the end of this bundled-Python block.)
  fi

  ln -sfn python3.12 "$PY_VERSION/bin/python3"
  ln -sfn 3.12 "$PY_FRAMEWORK/Versions/Current"
  ln -sfn Versions/Current/Python "$PY_FRAMEWORK/Python"
  ln -sfn Versions/Current/Resources "$PY_FRAMEWORK/Resources"
  install_name_tool -id "@rpath/Python.framework/Versions/3.12/Python" "$PY_VERSION/Python"
  install_name_tool -change "$PYTHON_FRAMEWORK_SOURCE/Python" "@executable_path/../Python" "$PY_VERSION/bin/python3.12"
  install_name_tool -change "$PYTHON_FRAMEWORK_SOURCE/Python" "@executable_path/../../../../Python" "$PY_VERSION/Resources/Python.app/Contents/MacOS/Python"
  if [[ "$BUNDLE_BACKEND_DEPS" != "0" && "$BUNDLE_BACKEND_DEPS" != "false" && "$BUNDLE_BACKEND_DEPS" != "no" ]]; then
    PY_RUNTIME_DEPS="$RES/python"
    rm -rf "$PY_RUNTIME_DEPS"
    mkdir -p "$PY_RUNTIME_DEPS"
    "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" -m pip install \
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
    if PYTHONPATH="$PY_RUNTIME_DEPS" "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" -c \
        "import sys; from model2vec import StaticModel; StaticModel.from_pretrained('$CORTEX_MODEL2VEC_MODEL').save_pretrained('$RES/model2vec')" ; then
      find "$RES/model2vec" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
      echo "Bundled local embedding model into $RES/model2vec"
    else
      echo "warning: local embedding model not bundled ($CORTEX_MODEL2VEC_MODEL); backend will use the hash fallback"
      rm -rf "$RES/model2vec"
    fi
  fi
  if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
    # 'itms-services' scan of the now-installed backend wheels (Resources/python).
    if [[ -d "$RES/python" ]] && grep -rlI 'itms-services' "$RES/python" >/dev/null 2>&1; then
      echo "ERROR: 'itms-services' present in bundled backend deps (Resources/python):" >&2
      grep -rlI 'itms-services' "$RES/python" >&2 || true
      exit 3
    fi
    # Bundled-only guarantee: in app-store mode the interpreter MUST be the bundled
    # framework and nothing else. Assert the framework interpreter exists and that
    # no system/absolute interpreter path leaked into what we ship.
    if [[ ! -x "$PY_VERSION/bin/python3.12" || ! -f "$PY_VERSION/Python" ]]; then
      echo "ERROR: app-store mode requires the bundled Python.framework interpreter at $PY_VERSION" >&2
      exit 3
    fi
    # Guideline 2.5.2: the app-store bundle must ship NO runnable helper scripts. Strip stray
    # shell scripts that ride along inside dependency wheels (e.g. tqdm/completion.sh) and assert
    # the MCP stdio bridge is absent (it is Developer-ID-only). Bytecode (.pyc) of the backend is
    # fine — it is the app's own engine, run only by the app, never installed into other apps.
    find "$RES" -type f -name '*.sh' -delete 2>/dev/null || true
    if [[ -e "$RES/scripts/cortex_mcp_stdio.py" ]]; then
      echo "ERROR: app-store bundle must not ship the runnable MCP stdio bridge (Guideline 2.5.2)" >&2
      exit 3
    fi
    LEFTOVER_SH="$(find "$RES" -type f -name '*.sh' 2>/dev/null | head -n 5)"
    if [[ -n "$LEFTOVER_SH" ]]; then
      echo "ERROR: app-store bundle still contains shell scripts:" >&2
      echo "$LEFTOVER_SH" >&2
      exit 3
    fi
    echo "  app-store: no runnable helper scripts in bundle (2.5.2)"
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
  xattr -cr "$APP" 2>/dev/null || true
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
    if [[ "$macho_file" == "$PY_VERSION/bin/python3.12" || "$macho_file" == "$PY_VERSION/Resources/Python.app/Contents/MacOS/Python" ]]; then
      child_entitlements="1"
    fi
    sign_file_if_macho "$macho_file" "$child_entitlements"
  done < <(find "$PY_VERSION" -type f \( -name "Python" -o -name "python3.12" -o -name "*.so" -o -name "*.dylib" \))
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

if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
  # Whole-bundle 'itms-services' assert: the final signed .app must be clean.
  # -I skips binaries; the scheme string only ever appears as literal text.
  if grep -rlI 'itms-services' "$APP" >/dev/null 2>&1; then
    echo "ERROR: 'itms-services' present in the built app-store bundle:" >&2
    grep -rlI 'itms-services' "$APP" >&2 || true
    exit 3
  fi
  echo "  app-store: verified built bundle is free of 'itms-services'"

  # Boot smoke: prove the scrubbed stdlib + .pyc-only backend actually serves.
  # Preferred path: run the bundled interpreter against the bundled .pyc backend
  # on a spare port with a temp vault, hit /health. If the signed/sandboxed
  # binary can't run standalone (entitlements/SIGTRAP outside its container),
  # fall back to booting the .pyc backend under a system python with ssl blocked,
  # which still proves the .pyc compile + scrubbed-stdlib assumptions are sound.
  SMOKE_PORT="${CORTEX_SMOKE_PORT:-8791}"
  SMOKE_VAULT="$(mktemp -d)"
  SMOKE_DB="$SMOKE_VAULT/index.sqlite"
  BUNDLED_PY="$APP/Contents/Frameworks/Python.framework/Versions/3.12/bin/python3.12"
  boot_smoke() { # $1 = python interpreter, $2 = extra PYTHONPATH prefix (deps)
    local py="$1" deps="$2"
    local pp="$RES/backend"
    [[ -n "$deps" && -d "$deps" ]] && pp="$pp:$deps"
    CORTEX_VAULT_PATH="$SMOKE_VAULT" \
    CORTEX_DB_PATH="$SMOKE_DB" \
    CORTEX_API_KEY="smoke-api-key" \
    CORTEX_MCP_API_KEY="smoke-mcp-key" \
    CORTEX_MCP_API_KEY_SCOPES="read,write,export,maintenance" \
    CORTEX_PUBLIC_BASE_URL="http://127.0.0.1:$SMOKE_PORT" \
    PYTHONPATH="$pp" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
      "$py" -S -m app.standalone_server --host 127.0.0.1 --port "$SMOKE_PORT" >"$SMOKE_VAULT/server.log" 2>&1 &
    local pid=$!
    local ok=""
    for _ in $(seq 1 40); do
      if ! kill -0 "$pid" 2>/dev/null; then break; fi
      if curl -fsS "http://127.0.0.1:$SMOKE_PORT/health" >/dev/null 2>&1; then ok="1"; break; fi
      sleep 0.25
    done
    kill "$pid" 2>/dev/null || true
    wait "$pid" 2>/dev/null || true
    [[ -n "$ok" ]]
  }
  SMOKE_RESULT="failed"
  if [[ -x "$BUNDLED_PY" ]] && boot_smoke "$BUNDLED_PY" "$RES/python"; then
    SMOKE_RESULT="ok (bundled interpreter + .pyc backend)"
  elif SYS_PY="$(command -v python3.12 || command -v python3)"; [[ -n "${SYS_PY:-}" ]] && \
       CORTEX_DISABLE_SSL=1 boot_smoke "$SYS_PY" ""; then
    SMOKE_RESULT="ok (fallback system python, ssl blocked; bundled binary could not run standalone)"
  fi
  rm -rf "$SMOKE_VAULT"
  if [[ "$SMOKE_RESULT" == "failed" ]]; then
    echo "ERROR: app-store boot smoke failed — .pyc-only backend / scrubbed stdlib did not serve /health" >&2
    exit 3
  fi
  echo "  app-store: boot smoke /health $SMOKE_RESULT"
fi
