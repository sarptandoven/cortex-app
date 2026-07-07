# Doppl — App Review Notes (Mac App Store)

Paste the relevant parts of this into **App Store Connect → App Review Information → Notes**.
It answers the four issues raised in review.

> The Mac App Store build is named **Doppl** (the engine/product is "Cortex" internally, so
> "Cortex" and "Doppl" refer to the same app in the notes below). The **Doppl** app menu shows
> **Quit Doppl (⌘Q)**.

## 2.1(a) — "We need a sample backend server address"

**There is no backend server to provide, and none is required to review the app.**

Cortex is a **fully local, offline app**. The memory engine runs **on the user's own Mac**, bound
to `127.0.0.1` (loopback) only — it is bundled inside the app and started automatically on launch.
Nothing is sent to, and nothing needs to be fetched from, any remote server.

- No account, sign-in, or network connection is needed to use any feature.
- The App Store build has cloud sign-in and all outbound network connectors **disabled** — it is
  local-only by design.
- The loopback server (`127.0.0.1:8766`) is an internal implementation detail; it is not
  reachable off-device and there is no hosted endpoint.

**How to review every feature (no server, no login, no download, no files needed):**
1. Launch Cortex. It opens straight into the app — no login wall, no network needed.
2. On the first screen click **Explore with sample notes**. Cortex loads a small bundled set of
   example notes and distills them **entirely on-device** — a progress bar shows it building.
3. Open **Home**: you'll see the distilled profile ("What Cortex has learned") across every
   memory type, and **Your Constellation** — an interactive map of the people, projects, and
   topics with connections you can click and select.
4. Open **Ask** and type e.g. "how do I like to write?" or "what did we decide about the
   database?" — you get a cited answer sourced from the sample notes. Open **Review** to see and
   approve captured memories.
5. **Connect an AI tool** (in Connections) shows guided, copy‑paste instructions only; the app
   does not install, write, or launch any external code.
6. To test your own data, use **Connect notes** and pick any folder of `.md`/`.txt` notes —
   Cortex reads and distills it locally.

No hosted backend, demo account, or credentials are needed — the built-in **Explore with sample
notes** path exercises the entire pipeline with zero setup.

## 2.5.2 — "The app installed or launched executable code (3rd party MCP tools installation)"

The auto-install of MCP configuration into other apps has been **removed from the App Store build**.
The App Store build never writes, installs, or launches any executable, script, or configuration
outside its own container. Connecting an external AI tool (Claude Desktop, Cursor, etc.) is now a
**guided manual step**: Cortex displays the configuration text and the user copies it into their
own AI tool themselves. All code the app runs ships inside the signed app bundle; nothing is
downloaded or generated at runtime.

## 2.5.1 — Non-public / deprecated APIs

The bundled Python runtime no longer includes the `_tkinter` or `_ssl` extension modules that
referenced the flagged Tcl/Tk and OpenSSL symbols. A full symbol scan of the shipped bundle is
clean of those references.

## 4 — Missing Quit option

The app now provides a standard **App menu with Quit (⌘Q)** (plus Edit and Window menus), in
addition to the existing status-bar menu.

---

### Architecture summary for the reviewer
- SwiftUI + AppKit front end.
- A bundled, code-signed local memory engine (started on `127.0.0.1`, loopback only).
- No tracking; the only data collected is the account email/name a signed-in user provides
  (see `PrivacyInfo.xcprivacy`), used to operate their account.

---

## Accounts build (when CortexRequireAccount = true)

This applies only once the build **requires** an account (`Info.plist CortexRequireAccount=true`,
enabled after the hosted backend is live). Until then the app is usable locally without an account
and the notes above stand.

- **Sign-in options.** The app offers **Sign in with Apple** (native), plus Google, GitHub, and
  email/password. Sign in with Apple is presented first and prominently, satisfying Guideline 4.8.
- **What the account provides (Guideline 5.1.1(v)).** Signing in enables cross-device sync and
  cloud backup of the user's memory — real account-based functionality, which is why an account is
  required. Sign-in runs over HTTPS to `https://api.trydoppl.com` (Swift URLSession).
- **Demo account for review.** Username: `<APP REVIEW DEMO EMAIL>` · Password:
  `<APP REVIEW DEMO PASSWORD>`. (Fill these in from an active account before submitting; or use
  Sign in with Apple with your own Apple ID.) The demo account has sample memory pre-loaded so the
  reviewer can exercise Home / Review / Ask immediately after signing in.
- **Privacy.** Email and name are collected to operate the account (Linked to identity, **not**
  used for tracking, purpose App Functionality) — matching `PrivacyInfo.xcprivacy` and the App
  Store Connect privacy questionnaire. No data is shared with third parties for advertising.
