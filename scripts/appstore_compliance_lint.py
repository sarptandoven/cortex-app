#!/usr/bin/env python3
"""App Store compliance lint — pins the Mac App Store submission "attributes" so they cannot
silently regress (the way an un-gated Obsidian-plugin installer once did). Pure stdlib, no build:
it statically checks the source against the four rejection guidelines + the account/privacy pivot.

Run: python3 scripts/appstore_compliance_lint.py    (exit 0 = aligned, 1 = drift found)
Wire into CI so every commit re-verifies these invariants.
"""
from __future__ import annotations

import plistlib
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MACOS = ROOT / "macos"

failures: list[str] = []
checks = 0


def check(ok: bool, label: str, detail: str = "") -> None:
    global checks
    checks += 1
    if not ok:
        failures.append(f"{label}" + (f" — {detail}" if detail else ""))


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8") if path.exists() else ""


def load_plist(path: Path) -> dict:
    try:
        with path.open("rb") as fh:
            return plistlib.load(fh)
    except Exception as exc:  # noqa: BLE001
        failures.append(f"could not parse {path.relative_to(ROOT)}: {exc}")
        return {}


# ---------------------------------------------------------------- 2.5.1 + 2.5.2: build.sh
build = read(MACOS / "build.sh")
check("app-store" in build, "build.sh: has an app-store branch")
for ext in ("_ssl*.so", "_tkinter*.so", "_hashlib*.so"):
    check(f'strip_ext "{ext}"' in build, f"build.sh: strips {ext} (2.5.1 / OpenSSL hygiene)")
check('rm -f "$PY_STDLIB/ssl.py"' in build, "build.sh: removes ssl.py in app-store mode")
# The runnable helpers (MCP bridge + Obsidian plugin) must be bundled ONLY in non-app-store mode.
check(
    re.search(r'DISTRIBUTION_MODE"\s*!=\s*"app-store".*?cortex_mcp_stdio\.py', build, re.S) is not None,
    "build.sh: MCP stdio bridge gated to non-app-store (2.5.2)",
)
check(
    re.search(r'DISTRIBUTION_MODE"\s*!=\s*"app-store".*?obsidian-cortex-plugin', build, re.S) is not None,
    "build.sh: Obsidian plugin payload gated to non-app-store (2.5.2)",
)
check("no runnable helper scripts or plugin payload" in build, "build.sh: asserts no runnable code in app-store bundle (2.5.2)")

# ---------------------------------------------------------------- entitlements
ent = load_plist(MACOS / "AppStore.entitlements")
check(ent.get("com.apple.security.app-sandbox") is True, "AppStore.entitlements: App Sandbox enabled")
check(ent.get("com.apple.security.network.client") is True, "AppStore.entitlements: network.client (loopback backend)")
check("Default" in (ent.get("com.apple.developer.applesignin") or []), "AppStore.entitlements: Sign in with Apple (Guideline 4.8)")
cs_keys = [k for k in ent if k.startswith("com.apple.security.cs.")]
check(not cs_keys, "AppStore.entitlements: NO hardened-runtime cs.* exceptions", f"found {cs_keys}")

# ---------------------------------------------------------------- privacy manifest
priv = load_plist(MACOS / "PrivacyInfo.xcprivacy")
check(priv.get("NSPrivacyTracking") is False, "PrivacyInfo: NSPrivacyTracking = false")
collected = {d.get("NSPrivacyCollectedDataType") for d in priv.get("NSPrivacyCollectedDataTypes", [])}
# Accounts collect email + name — the manifest MUST declare them (must match the ASC nutrition label).
check("NSPrivacyCollectedDataTypeEmailAddress" in collected, "PrivacyInfo: declares Email collection (accounts)")
check("NSPrivacyCollectedDataTypeName" in collected, "PrivacyInfo: declares Name collection (accounts)")
api_types = {d.get("NSPrivacyAccessedAPIType") for d in priv.get("NSPrivacyAccessedAPITypes", [])}
for reason in ("FileTimestamp", "UserDefaults", "SystemBootTime"):
    check(f"NSPrivacyAccessedAPICategory{reason}" in api_types, f"PrivacyInfo: declares {reason} required-reason API")

# ---------------------------------------------------------------- Info.plist
info = load_plist(MACOS / "Info.plist")
check(info.get("ITSAppUsesNonExemptEncryption") is False, "Info.plist: ITSAppUsesNonExemptEncryption = false")
check(bool(info.get("LSApplicationCategoryType")), "Info.plist: LSApplicationCategoryType present")
check(bool(info.get("CFBundleVersion")), "Info.plist: CFBundleVersion present")
check(bool(info.get("NSScreenCaptureUsageDescription")), "Info.plist: screen-capture usage string present")

# ---------------------------------------------------------------- Swift gating (2.5.2 install paths)
swift = read(MACOS / "Sources" / "CortexApp.swift")
check(
    re.search(r"func installObsidianPluginIfPossible[\s\S]{0,1200}?guard !DistributionMode\.isAppStore", swift) is not None,
    "CortexApp: installObsidianPluginIfPossible gated off in app-store mode (2.5.2)",
)
check(
    re.search(r"func installIntegration[\s\S]{0,300}?if DistributionMode\.isAppStore", swift) is not None,
    "CortexApp: installIntegration gated in app-store mode (2.5.2)",
)
check(
    re.search(r'func mcpServerDefinition[\s\S]{0,2000}?if DistributionMode\.isAppStore[\s\S]{0,600}?"type": "http"', swift) is not None,
    "CortexApp: mcpServerDefinition returns an HTTP descriptor (no runnable command) in app-store mode",
)

# ---------------------------------------------------------------- report
print(f"App Store compliance lint: {checks - len(failures)}/{checks} checks passed")
if failures:
    print("\nDRIFT DETECTED — these App Store attributes no longer hold:")
    for f in failures:
        print(f"  ✗ {f}")
    print("\nFix the source (or the App Store Connect attribute it mirrors) before submitting.")
    sys.exit(1)
print("✅ All App Store submission attributes are aligned.")
sys.exit(0)
