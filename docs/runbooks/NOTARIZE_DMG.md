# Runbook — Sign & notarize the Cortex DMG (Developer ID)

**Goal:** turn the current *unsigned (ad-hoc)* DMG — which Gatekeeper rejects with
"unidentified developer / can't be opened" — into a **Developer-ID-signed, notarized,
stapled** DMG that opens with a normal double-click on any Mac. The build pipeline
already does the signing + notarization; this runbook is the **credential setup** the
founder does once, plus the one command to run.

Who does what:
- **Founder (once, irreducibly human):** create the Developer ID certificate and the
  notarytool credential (both need Apple ID + the paid Developer Program, which is
  already enrolled). ~20 minutes.
- **Hermes/agent or founder (repeatable):** run one command; verify.

---

## 0. Prerequisites
- Apple Developer Program membership (enrolled ✓).
- A Mac with Xcode **or** Command Line Tools (`xcode-select -p` succeeds).
- The repo checked out; `macos/package_release.sh` present.
- Know your **Team ID** (App Store Connect → Membership, a 10-char string like `AB12CD34EF`).

---

## 1. Create the Developer ID Application certificate (founder, once)
Easiest path (Xcode):
1. Xcode → Settings → Accounts → add your Apple ID → select the team → **Manage
   Certificates** → **+** → **Developer ID Application**.
2. This installs the cert + private key into your login keychain.

CLI check that it exists:
```bash
security find-identity -v -p codesigning | grep "Developer ID Application"
# => "Developer ID Application: Your Name (TEAMID)"   <-- copy this whole string
```
That full string is your `CORTEX_CODESIGN_IDENTITY`.

> No Mac/Xcode handy? Create the cert at developer.apple.com → Certificates → **+** →
> "Developer ID Application", generate a CSR from Keychain Access
> (Keychain Access → Certificate Assistant → Request a Certificate from a CA), upload
> it, download the `.cer`, and double-click to install.

---

## 2. Create a notarytool credential (founder, once)
Store an App-Store-Connect credential in the keychain under a profile name Cortex will
reference. Two options — pick one.

**Option A — app-specific password (simplest):**
1. appleid.apple.com → Sign-In & Security → **App-Specific Passwords** → generate one
   (label it "cortex notarytool"). Copy it.
2. Store it as a reusable profile:
```bash
xcrun notarytool store-credentials "cortex-notary" \
  --apple-id "you@example.com" \
  --team-id "TEAMID" \
  --password "the-app-specific-password"
```

**Option B — App Store Connect API key (better for CI/agents, no interactive password):**
1. App Store Connect → Users and Access → Integrations → App Store Connect API →
   generate a key with the **Developer** role. Download the `.p8` (once only), note the
   **Key ID** and **Issuer ID**.
2. Store it:
```bash
xcrun notarytool store-credentials "cortex-notary" \
  --key "/secure/path/AuthKey_XXXXXX.p8" \
  --key-id "KEYID" \
  --issuer "ISSUER-UUID"
```

`cortex-notary` is your `CORTEX_NOTARY_PROFILE`.

---

## 3. Build + sign + notarize + staple — one command
From the repo root:
```bash
cd macos
CORTEX_CODESIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
CORTEX_NOTARY_PROFILE="cortex-notary" \
./package_release.sh --production
```

`--production` makes packaging **require** the identity + notary profile (and HTTPS
download URLs and bundled Python), so it fails loudly rather than silently shipping an
unsigned build.

### What that runs under the hood (already implemented; no action needed)
- `build.sh` bundles the Python framework + vector runtime + model, then **signs every
  nested Mach-O** (Python framework binaries, native `.so`/`.dylib` wheels) and the
  outer `.app` with the Developer ID identity, `--options runtime` (hardened runtime)
  and `macos/DeveloperID.entitlements` (the four entitlements a bundled CPython needs:
  allow-jit, allow-unsigned-executable-memory, disable-library-validation,
  allow-dyld-environment-variables).
- `codesign --verify --deep --strict` the `.app`.
- Sign the `.dmg`, then `xcrun notarytool submit "$DMG" --keychain-profile
  cortex-notary --wait` (blocks until Apple returns Accepted, usually 1–5 min).
- `xcrun stapler staple "$DMG"` + `stapler validate` (so it verifies offline).
- `spctl -a -vvv -t open "$DMG"` Gatekeeper gate.

Output lands in `outputs/Cortex-<version>/`.

---

## 4. Verify (must all pass)
```bash
APP="/Volumes/Cortex <version>/Cortex.app"   # after mounting the DMG
codesign -dv --verbose=2 "$APP" 2>&1 | grep -E "Authority|TeamIdentifier|flags"
#   Authority=Developer ID Application: ...   TeamIdentifier=TEAMID   flags=0x10000(runtime)
spctl -a -vvv --type execute "$APP"          # => accepted, source=Notarized Developer ID
xcrun stapler validate "outputs/Cortex-<version>/Cortex-<version>.dmg"   # => validated
```
The previous ad-hoc build showed `Signature=adhoc`, `TeamIdentifier=not set`,
`spctl: rejected` — those must now read as above.

**Real-world check:** download the DMG on a *different* Mac (or a fresh user account)
and double-click — it should open with no right-click and no warning.

---

## 5. Publish the notarized build
1. Copy the notarized artifacts into the served download dir:
   `cp outputs/Cortex-<version>/{Cortex-*.dmg,Cortex-*.app.zip,Cortex-*.checksums.txt,latest.json} site/downloads/`
2. Confirm `site/downloads/latest.json` sha256 + size match the notarized files
   (`shasum -a256`), then `python3 scripts/check_distribution_site.py`.
3. Deploy the site.

---

## 6. Troubleshooting
- **`notarytool` → "Invalid" status:** run
  `xcrun notarytool log <submission-id> --keychain-profile cortex-notary` — it lists the
  exact unsigned/short-signed binary. Almost always a nested wheel `.so` that wasn't
  signed; re-run `package_release.sh` (build.sh signs every Mach-O, so a clean rebuild
  usually fixes it).
- **"The binary uses an SDK older than 10.9":** not applicable here (min is macOS 13).
- **Gatekeeper still rejects after stapling:** confirm `flags=0x10000(runtime)` in
  `codesign -dv` — if missing, the hardened runtime wasn't applied (identity was `-`).
- **Cert not found by codesign:** the login keychain must be unlocked in the session
  running the build (`security unlock-keychain`).

---

## 7. Handoff summary
Founder provides: the **Developer ID Application identity string** (§1) and a stored
**notarytool profile** name (§2). After that, the entire sign→notarize→staple→verify
flow is the single `package_release.sh --production` command in §3, repeatable by the
agent for every release. Nothing else about the app changes.
