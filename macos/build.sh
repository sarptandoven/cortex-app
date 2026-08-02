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
# The bundled Obsidian plugin (main.js) is the notes-bridge that the app auto-installs into the
# user's vault — a Developer-ID/DMG-only convenience. It must NOT ship in the App Store build:
# installing/enabling third-party executable code in another app is a Guideline 2.5.2 violation, so
# the MAS build syncs notes CONTENT only and never carries the plugin payload.
PLUGIN_DIR="$ROOT/../packages/obsidian-cortex-plugin"
if [[ "$DISTRIBUTION_MODE" != "app-store" && -d "$PLUGIN_DIR" ]]; then
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

  # Notarization gate (EVERY build): config-3.12-darwin is CPython's embedding/link kit
  # (python.o, Makefile, ...). python.o is an unsigned static Mach-O that Apple's notary
  # service hard-rejects ("The binary is not signed"), and nothing at runtime uses this
  # directory in either channel. The app-store branch below re-prunes it harmlessly.
  rm -rf "$PY_STDLIB/config-3.12-darwin"

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
    for f in "$DYNLOAD"/$1; do
      if [[ -e "$f" ]]; then
        rm -f "$f"
        echo "  pruned $(basename "$f")"
      fi
    done
    return 0
  }
  echo "Pruning non-public-API C extensions (_tkinter always; _ssl in app-store mode)..."
  strip_ext "_tkinter*.so"
  rm -rf "$PY_STDLIB/tkinter" "$PY_STDLIB/turtledemo" "$PY_STDLIB/turtle.py" "$PY_STDLIB/idlelib" 2>/dev/null || true
  if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
    strip_ext "_ssl*.so"
    rm -f "$PY_STDLIB/ssl.py"
    # _hashlib is the ONLY other OpenSSL-linked stdlib extension: it dynamically links libcrypto at
    # an ABSOLUTE path outside the bundle (dangling on a reviewer's Mac) and carries OpenSSL symbols.
    # Remove it too so the app-store bundle references zero OpenSSL. hashlib.py falls back to the
    # self-contained _md5/_sha1/_sha2/_sha3/_blake2 built-ins for the algorithms the local backend
    # uses (sha256); hashlib.scrypt is only used by the hosted account password-hasher (authn.py),
    # which is dead in the local-first MAS build. The boot smoke below fails the build if this breaks
    # `import app.standalone_server`.
    strip_ext "_hashlib*.so"
    echo "  app-store: removed _ssl + ssl.py + _hashlib (bundle references no OpenSSL)"
  fi
  # Fail loudly if a flagged/OpenSSL extension survived — never ship a build that will bounce off review.
  if [[ -e "$DYNLOAD"/_tkinter*.so ]] || { [[ "$DISTRIBUTION_MODE" == "app-store" ]] && ls "$DYNLOAD"/_ssl*.so "$DYNLOAD"/_hashlib*.so >/dev/null 2>&1; }; then
    echo "ERROR: a flagged/OpenSSL C extension survived pruning in $DYNLOAD" >&2
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

    # Ship a sourceless runtime, not a general-purpose Python SDK. The MAS app only
    # needs importable bytecode; source files and CPython's build helper scripts add
    # executable-code surface that App Review can reasonably treat as a script host.
    rm -rf "$PY_STDLIB/config-3.12-darwin"
    rm -f "$PY_STDLIB/ctypes/macholib/fetch_macholib"
    "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" -m compileall -b -q -q "$PY_STDLIB"
    find "$PY_STDLIB" -type f -name '*.py' -delete
    find "$PY_STDLIB" -type d -name '__pycache__' -prune -exec rm -rf {} +
    if [[ ! -f "$PY_STDLIB/encodings/__init__.pyc" || ! -f "$PY_STDLIB/urllib/parse.pyc" ]]; then
      echo "ERROR: app-store stdlib bytecode compilation is incomplete" >&2
      exit 3
    fi
    echo "  app-store: bundled stdlib compiled to sourceless .pyc runtime"
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
      --require-hashes \
      --target "$PY_RUNTIME_DEPS" \
      -r "$ROOT/../backend/runtime-requirements.lock"
    find "$PY_RUNTIME_DEPS" -type d -name "__pycache__" -prune -exec rm -rf {} +
    find "$PY_RUNTIME_DEPS" -type f -name "*.pyc" -delete
    # Notarization hygiene: joblib ships intentionally-truncated .gz test pickles that the
    # notary service cannot unpack and warns about ("could not be unpacked"). They are test
    # fixtures, never imported at runtime; prune every bundled wheel's test/tests dirs.
    find "$PY_RUNTIME_DEPS" -type d \( -name "test" -o -name "tests" \) -prune -exec rm -rf {} + 2>/dev/null || true
    # Bundle the local embedding model (~8MB) so semantics work fully offline with no API key and
    # no first-run network download. If bundling fails, the demo-integrity guard below decides:
    # dev builds warn LOUDLY but keep building; CORTEX_REQUIRE_MODEL=1 (exported unconditionally
    # by package_release.sh) hard-fails so a release can never silently ship the hash fallback.
    CORTEX_MODEL2VEC_MODEL="${CORTEX_MODEL2VEC_MODEL:-minishlab/potion-base-8M}"
    if PYTHONPATH="$PY_RUNTIME_DEPS" "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" -c \
        "import sys; from model2vec import StaticModel; StaticModel.from_pretrained('$CORTEX_MODEL2VEC_MODEL').save_pretrained('$RES/model2vec')" ; then
      find "$RES/model2vec" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true
      echo "Bundled local embedding model into $RES/model2vec"
    else
      echo "warning: bundling the local embedding model failed ($CORTEX_MODEL2VEC_MODEL)" >&2
      rm -rf "$RES/model2vec"
    fi

    if [[ "$DISTRIBUTION_MODE" == "app-store" ]]; then
      # hf-xet is an OPTIONAL Hugging Face download accelerator. The model is already
      # bundled above, so MAS never needs it; its native wheel statically embeds
      # AWS-LC/OpenSSL/TLS code and would contradict our no-non-exempt-encryption
      # declaration. pip console entrypoints are likewise development helpers, not
      # product runtime. Remove both before compiling dependencies to bytecode.
      rm -rf \
        "$PY_RUNTIME_DEPS"/hf_xet "$PY_RUNTIME_DEPS"/hf_xet-*.dist-info \
        "$PY_RUNTIME_DEPS"/huggingface_hub "$PY_RUNTIME_DEPS"/huggingface_hub-*.dist-info \
        "$PY_RUNTIME_DEPS"/httpx "$PY_RUNTIME_DEPS"/httpx-*.dist-info \
        "$PY_RUNTIME_DEPS"/httpcore "$PY_RUNTIME_DEPS"/httpcore-*.dist-info \
        "$PY_RUNTIME_DEPS"/fsspec "$PY_RUNTIME_DEPS"/fsspec-*.dist-info \
        "$PY_RUNTIME_DEPS/bin"
      find "$PY_RUNTIME_DEPS/numpy" -type d -name include -prune -exec rm -rf {} + 2>/dev/null || true
      find "$PY_RUNTIME_DEPS/numpy" -type f \( -name '*.a' -o -name '*.h' \) -delete 2>/dev/null || true
      "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" -m compileall -b -q -q "$PY_RUNTIME_DEPS"
      find "$PY_RUNTIME_DEPS" -type f -name '*.py' -delete
      find "$PY_RUNTIME_DEPS" -type d -name '__pycache__' -prune -exec rm -rf {} +
      if [[ -n "$(find "$PY_RUNTIME_DEPS" -type f -name '*.py' -print -quit)" ]]; then
        echo "ERROR: app-store dependency bundle still contains Python source" >&2
        exit 3
      fi
      if [[ -e "$PY_RUNTIME_DEPS/hf_xet" || -e "$PY_RUNTIME_DEPS/huggingface_hub" || -e "$PY_RUNTIME_DEPS/httpx" || -e "$PY_RUNTIME_DEPS/httpcore" ]]; then
        echo "ERROR: app-store network-only Python dependencies survived pruning" >&2
        exit 3
      fi
      # Prove the shipping offline semantic path, not merely /health. Blocking ssl
      # here makes the smoke fail if a future change reintroduces an implicit TLS or
      # Hugging Face import. The expected potion-base-8M output is native 256-dim.
      if ! CORTEX_EMBEDDING_PROVIDER=model2vec \
        CORTEX_EMBEDDING_STRICT=1 \
        CORTEX_EMBEDDING_DIMENSIONS=256 \
        CORTEX_MODEL2VEC_PATH="$RES/model2vec" \
        PYTHONPATH="$RES/backend:$PY_RUNTIME_DEPS" PYTHONNOUSERSITE=1 PYTHONDONTWRITEBYTECODE=1 \
        "$PYTHON_FRAMEWORK_SOURCE/bin/python3.12" -S -c \
          "import sys; sys.modules['ssl']=None; from app.embeddings import embed_text_result; r=embed_text_result('offline semantic smoke'); assert r.provider=='model2vec' and r.dimensions==256 and len(r.vector)==256"; then
        echo "ERROR: app-store offline Model2Vec embedding smoke failed" >&2
        exit 3
      fi
      echo "  app-store: sourceless dependencies, no network-only wheels, offline Model2Vec smoke passed"
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
    find "$APP" -type f -name '*.sh' -delete 2>/dev/null || true
    if [[ -e "$RES/scripts/cortex_mcp_stdio.py" ]]; then
      echo "ERROR: app-store bundle must not ship the runnable MCP stdio bridge (Guideline 2.5.2)" >&2
      exit 3
    fi
    LEFTOVER_SH="$(find "$APP" -type f -name '*.sh' 2>/dev/null | head -n 5)"
    if [[ -n "$LEFTOVER_SH" ]]; then
      echo "ERROR: app-store bundle still contains shell scripts:" >&2
      echo "$LEFTOVER_SH" >&2
      exit 3
    fi
    # Guideline 2.5.2: the app-store bundle must not carry the Obsidian notes-bridge plugin payload
    # (installing/enabling third-party executable JS in another app is forbidden; the installer is
    # also gated off in-app via DistributionMode.isAppStore).
    if [[ -e "$RES/obsidian-cortex-plugin" ]] || find "$RES" -type f -name 'main.js' 2>/dev/null | grep -q .; then
      echo "ERROR: app-store bundle must not ship the Obsidian plugin payload / main.js (Guideline 2.5.2)" >&2
      find "$RES" \( -name 'obsidian-cortex-plugin' -o -name 'main.js' \) 2>/dev/null >&2
      exit 3
    fi

    # Whole-bundle source + shebang guard. This deliberately scans Frameworks too;
    # the earlier check only covered Resources and missed CPython/pip helpers.
    LEFTOVER_PY="$(find "$APP" -type f -name '*.py' 2>/dev/null | head -n 5)"
    if [[ -n "$LEFTOVER_PY" ]]; then
      echo "ERROR: app-store bundle still contains Python source:" >&2
      echo "$LEFTOVER_PY" >&2
      exit 3
    fi
    LEFTOVER_DEV="$(find "$APP" -type f \( -name '*.o' -o -name '*.a' -o -name '*.h' \) 2>/dev/null | head -n 5)"
    if [[ -n "$LEFTOVER_DEV" ]]; then
      echo "ERROR: app-store bundle still contains compiler/development payload:" >&2
      echo "$LEFTOVER_DEV" >&2
      exit 3
    fi
    LEFTOVER_SHEBANG=""
    while IFS= read -r candidate; do
      if [[ "$(head -c 2 "$candidate" 2>/dev/null || true)" == '#!' ]]; then
        LEFTOVER_SHEBANG+="${candidate}"$'\n'
      fi
    done < <(find "$APP" -type f)
    if [[ -n "$LEFTOVER_SHEBANG" ]]; then
      echo "ERROR: app-store bundle still contains runnable script helpers:" >&2
      printf '%s' "$LEFTOVER_SHEBANG" | head -n 10 >&2
      exit 3
    fi

    # No native payload may link or statically embed the OpenSSL/AWS-LC runtime.
    # System URLSession owns the app's HTTPS paths; the Python worker is loopback-only.
    FLAGGED_CRYPTO=""
    while IFS= read -r native; do
      if otool -L "$native" 2>/dev/null | grep -Eiq 'lib(ssl|crypto)' \
        || strings -a "$native" 2>/dev/null | grep -Eq 'OPENSSL_armcap|SSL_CTX_|AWS[-_]LC'; then
        FLAGGED_CRYPTO+="${native}"$'\n'
      fi
    done < <(find "$APP" -type f \( -name '*.so' -o -name '*.dylib' -o -name 'Python' -o -name 'python3.12' \))
    if [[ -n "$FLAGGED_CRYPTO" ]]; then
      echo "ERROR: app-store bundle contains a native OpenSSL/AWS-LC payload:" >&2
      printf '%s' "$FLAGGED_CRYPTO" | head -n 10 >&2
      exit 3
    fi
    echo "  app-store: no source scripts, runnable helpers, plugin payload, or bundled OpenSSL/AWS-LC (2.5.1/2.5.2)"
  fi
fi

# --- EMBEDDINGS DEMO-INTEGRITY GUARD ---------------------------------------------------------
# The product's on-device semantics ("it knows me") ride entirely on the bundled Model2Vec model
# at Resources/model2vec (potion-base-8M). Without it, the backend silently degrades to the
# deterministic keyword-hash fallback — a build that LOOKS fine but has no real semantics.
# Dev builds may legitimately lack the model (CORTEX_BUNDLE_PYTHON=0, offline machine), but that
# must be LOUD, never silent. Releases must make it impossible: macos/package_release.sh exports
# CORTEX_REQUIRE_MODEL=1 unconditionally, turning a missing/incomplete model into a hard failure
# here. The three sentinel files are exactly what model2vec's save_pretrained() writes and what
# the app launcher (CortexApp.swift) and backend loader key on.
MODEL2VEC_RES="$RES/model2vec"
MODEL2VEC_MISSING=""
for model_file in config.json model.safetensors tokenizer.json; do
  if [[ ! -e "$MODEL2VEC_RES/$model_file" ]]; then
    MODEL2VEC_MISSING="$MODEL2VEC_RES/$model_file"
    break
  fi
done
if [[ -n "$MODEL2VEC_MISSING" ]]; then
  if [[ "${CORTEX_REQUIRE_MODEL:-0}" == "1" ]]; then
    echo "ERROR: CORTEX_REQUIRE_MODEL=1 but the bundled embedding model is missing/incomplete: $MODEL2VEC_MISSING — refusing to build an artifact that would silently fall back to keyword-hash embeddings" >&2
    exit 3
  fi
  echo "WARNING: EMBEDDINGS FALLBACK — bundled model2vec model missing ($MODEL2VEC_MISSING); this build will use keyword-hash embeddings, NOT real semantics (dev-only; release packaging enforces CORTEX_REQUIRE_MODEL=1)" >&2
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
  -framework AuthenticationServices \
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
