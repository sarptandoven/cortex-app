# Code Signing Status — Cortex/Doppl (build 36)

_Verified 2026-07-12 against `outputs/Doppl-AppStore-0.2.0-36/`. This is the honest
state of "#1 code signing": what is done and machine-verified, and the single
remaining step that needs your Apple Developer account._

## ✅ DONE and machine-verified (Mac App Store / Doppl)

The App Store submission artifact is already correctly code-signed. Verified with
`codesign`/`pkgutil`:

| Check | Result |
|---|---|
| App signature | `Doppl.app` signed by **3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)** |
| Hardened runtime | present (`flags=0x10000(runtime)`) |
| Deep strict verify | **exit 0** — `valid on disk` + `satisfies its Designated Requirement`, **including the nested bundled `Python.framework`** (the hard part) |
| Sandbox entitlement | `com.apple.security.app-sandbox = true` (embedded) |
| Sign in with Apple entitlement | `com.apple.developer.applesignin = [Default]` (embedded) — the 4.8 fix will authenticate |
| App identifier / team | `P4H96J3VXY.com.cortex.doppl` / `P4H96J3VXY` |
| Installer `.pkg` | signed by **3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)** |
| Binary compliance scan | clean — `_tkinter`/`_ssl`/`_hashlib` pruned, zero OpenSSL, backend `.pyc`-only, `itms-services` scrubbed, no runnable scripts/plugins |

Signing identities present in the keychain:
- `3rd Party Mac Developer Application: Sarp Doven (P4H96J3VXY)` (app)
- `3rd Party Mac Developer Installer: Sarp Doven (P4H96J3VXY)` (installer)

The packaging pipeline (`macos/package_app_store.sh` → `macos/build.sh`) already
embeds a provisioning profile at `Contents/embedded.provisionprofile` **before**
it seals/signs, when you pass `CORTEX_MAS_PROFILE`.

## ⏳ The ONE remaining signing step (needs your Apple Developer account)

The build report shows `"provisioning_profile": "missing"`. An App Store upload
requires a **Mac App Store provisioning profile** for App ID `com.cortex.doppl`
with the **Sign in with Apple** capability. Only you can create it:

1. Apple Developer portal → Certificates, IDs & Profiles → Identifiers →
   ensure App ID `com.cortex.doppl` has **Sign in with Apple** enabled (its
   entitlements must match `macos/AppStore.entitlements`).
2. Profiles → **+** → **Mac App Store** distribution → App ID `com.cortex.doppl`
   → your distribution cert → Download the `.provisionprofile`.
3. Produce the uploadable, provisioned, signed `.pkg` in ONE command:
   ```sh
   CORTEX_MAS_PROFILE="/path/to/Doppl_Mac_App_Store.provisionprofile" \
     ./macos/package_app_store.sh
   ```
   The report then flips to `"status": "upload-candidate"`, `"uploadable": true`.
   (If Apple asks for an "Apple Distribution" cert instead of the legacy 3rd Party
   Mac Developer certs, create it in the portal and set
   `CORTEX_APPSTORE_SIGN_IDENTITY` / `CORTEX_APPSTORE_INSTALLER_IDENTITY`; the
   script accepts both.)
4. Also set `CORTEX_OIDC_APPLE_CLIENT_ID` on the hosted backend so Sign in with
   Apple actually authenticates (the entitlement is embedded; the backend token
   exchange needs this).

Then upload (Transporter.app drag-in, or `CORTEX_APPSTORE_UPLOAD=1` with ASC API
creds) and Submit — see `outputs/Doppl-AppStore-0.2.0-36/FOUNDER_CHECKLIST.txt`
for the full step-by-step + review notes.

## Separate channel: Developer ID (the direct-download DMG) — BLOCKED on a cert

The direct-download `Cortex.dmg` (not the App Store) is currently **unsigned**
(local xattr workaround). Signing + notarizing it needs a **Developer ID
Application** certificate, which is **not** in the keychain (only the two MAS
certs above are). To enable it: create a Developer ID Application cert in the
portal, install it, then `deploy`/notarize per `docs/FOUNDER_GO_LIVE.md`. This is
independent of the App Store submission and not required for it.

## Bottom line

Code signing for the App Store is **done and verified** end-to-end (app +
installer + nested framework + entitlements). The only thing between the signed
`.pkg` and "uploadable" is the provisioning profile, which is a portal action only
you can take — after which it is a single `package_app_store.sh` run.
