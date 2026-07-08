# Doppl — App Review Notes (Mac App Store)

Paste the relevant parts of this into **App Store Connect → App Review Information → Notes**,
and put the demo credentials in the **Sign-In Information** fields (they are not public).
This addresses the four issues raised in the previous review and reflects the current build,
which **requires a signed-in account**.

> The Mac App Store build is named **Doppl** (the engine/product is "Cortex" internally, so
> "Cortex" and "Doppl" refer to the same app in the notes below). The **Doppl** app menu shows
> **Quit Doppl (⌘Q)**.

---

## 2.1(a) — "We need a sample backend server address" + how to review

**Backend server address (production):** `https://api.signindoppl.com`
(Health check: `https://api.signindoppl.com/health` returns `{"status":"ok", ...}`.)

**Demo account (already provisioned, ready to sign in):**
- **Email:** `review@trydoppl.com`
- **Password:** `<PASTE IN APP STORE CONNECT SIGN-IN INFORMATION>`
- (Or sign in with the reviewer's own Apple ID via **Sign in with Apple** — see 4.8.)

Doppl builds a private, cited memory for the user. Signing in enables the app's account-based
functionality: the user's memory is stored under their account, synced across their devices, and
reachable from their AI tools. Sign-in runs over HTTPS (`https://api.signindoppl.com`) using the
system networking stack (Swift `URLSession`).

**How to review every feature after signing in (no downloads or files needed):**
1. Launch Doppl and sign in with the demo account above (or Sign in with Apple).
2. On first run choose **Explore with sample notes** — Doppl loads a small bundled set of example
   notes into your account and distills them (processed and stored under your account on the hosted
   backend); a progress bar shows it building.
3. Open **Home**: the distilled profile ("What Doppl has learned") across every memory type, and
   **Your Constellation**, an interactive map of people, projects, and topics you can click.
4. Open **Ask** and type e.g. "how do I like to write?" — you get a cited answer sourced from the
   sample notes. Open **Review** to see and approve captured memories.
5. **Connect an AI tool** (in Connections) shows guided, copy‑paste instructions only; the app does
   not install, write, or launch any external code.

The demo account has sample memory pre‑loaded, so Home / Review / Ask are exercisable immediately
after signing in.

## 5.1.1(v) — Why an account is required

The account is not a gate in front of otherwise-local features — it **is** the feature. Doppl is a
personal-memory product: your memory lives under your account so it can (a) sync across your
devices and (b) be reached by your AI assistants and tools through the account. This is
account-based functionality per 5.1.1(v), which is why sign-in is required.

## 4.8 — Sign in with Apple

The sign-in screen offers **Sign in with Apple** (native `ASAuthorizationController` flow),
presented **first and prominently**, alongside Google, GitHub, and email/password. Sign in with
Apple meets the 4.8 requirements: it limits data collection to name and email, and does not collect
interactions for advertising. It uses the native OS flow — no token rides a browser redirect.

## 2.5.2 — "The app installed or launched executable code (3rd party MCP tools installation)"

The auto-install of MCP configuration into other apps has been **removed from the App Store build**.
The App Store build never writes, installs, or launches any executable, script, or configuration
outside its own container. Connecting an external AI tool (Claude Desktop, Cursor, etc.) is a
**guided manual step**: Doppl displays the configuration text and the user copies it into their own
AI tool themselves. All code the app runs ships inside the signed app bundle; nothing is downloaded
or generated at runtime. (The build script asserts the bundle carries no runnable helper scripts or
plugin payloads.)

## 2.5.1 — Non-public / deprecated APIs

The bundled Python runtime no longer includes the `_tkinter` or `_ssl` extension modules that
referenced the flagged Tcl/Tk and OpenSSL symbols (the App Store build performs all HTTPS via the
system networking stack, not bundled OpenSSL). A full symbol scan of the shipped bundle is clean of
those references.

## 4 — Missing Quit option

The app provides a standard **App menu with Quit (⌘Q)** (plus Edit and Window menus), in addition to
the status‑bar menu.

---

### Architecture summary for the reviewer
- SwiftUI + AppKit front end.
- When signed in (which the build requires), the user's memory is created, stored, and retrieved in
  **their account on the hosted backend** (`https://api.signindoppl.com`), reached over HTTPS via
  Swift `URLSession`. That is the account functionality: the memory the app distills belongs to, and
  is synced from, the account. (The app also bundles a code‑signed local engine that runs on
  `127.0.0.1` loopback for on‑device preprocessing; the account's hosted backend is the system of
  record for a signed‑in user.)
- Data collected: the account **email** and **name** the user provides at sign‑in, and the **memory
  content** the user chooses to add to their account (their own notes/thoughts) — all **Linked** to
  the account, used only to operate the account (**App Functionality**), **not** used for tracking,
  and never shared with third parties for advertising. See `PrivacyInfo.xcprivacy`.

---

## Privacy questionnaire (App Store Connect → App Privacy)
- **Email Address** — "Yes, we collect", **Linked to the user**, **Not used for tracking**, purpose
  **App Functionality**.
- **Name** — same settings.
- **User Content → Other User Content** (the memory / notes the user adds to their account) —
  collected, **Linked to the user**, **Not used for tracking**, purpose **App Functionality**.
- Answer **No** to tracking. Nothing else is collected.
