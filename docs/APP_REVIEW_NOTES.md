# Doppl — App Review Notes (Mac App Store)

Paste the relevant parts of this into **App Store Connect → App Review Information → Notes**,
and put the demo credentials in the **Sign-In Information** fields (they are not public).
This addresses the four issues raised in the previous review and reflects the current build,
which **requires a signed-in account**.

> The Mac App Store build is named **Doppl**. The **Doppl** app menu shows
> **Quit Doppl (⌘Q)**.

---

## 2.1(a) — "We need a sample backend server address" + how to review

**Backend server address (production):** `https://api.signindoppl.com`
(Health check: `https://api.signindoppl.com/health` returns `{"status":"ok", ...}`.)

**No account needed to review the app.** The sign-in screen has a
**"Explore with sample notes, no account needed"** button. Tapping it dismisses the sign-in screen
and runs the entire app **locally on the Mac** (no network, no account): the bundled sample notes are
distilled on-device and Home / Constellation / Ask / Review become fully exercisable. Use this to
review every feature offline.

**Demo account (optional — for reviewing account sync):**
- **Email:** `review@trydoppl.com`
- **Password:** `<PASTE IN APP STORE CONNECT SIGN-IN INFORMATION>`
- (Or sign in with the reviewer's own Apple ID via **Sign in with Apple** — see 4.8.)

Doppl builds a private, cited memory for the user. The memory is created and stored **on the user's
Mac** (local-first); signing in enables the app's account-based functionality: that on-device memory
**syncs to the user's account** so it's durable, available across their devices, and reachable from
their AI tools. Sign-in + sync run over HTTPS (`https://api.signindoppl.com`) using the system
networking stack (Swift `URLSession`).

**How to review every feature (no account, no downloads, no files needed):**
1. Launch Doppl. On the sign-in screen tap **"Explore with sample notes, no account needed"**
   (or, to review account sync, sign in with the demo account above / Sign in with Apple).
2. Choose **Explore with sample notes** — Doppl loads a small bundled set of example notes and
   distills them **on-device** (a progress bar shows it building). No network is required for this.
3. Open **Home**: the distilled profile ("What Doppl has learned") across every memory type, and
   **Your Constellation**, an interactive map of people, projects, and topics you can click.
4. Open **Ask** and type e.g. "how do I like to write?" — you get a cited answer sourced from the
   sample notes. Open **Review** to see and approve captured memories.
5. **Connect an AI tool** (in Connections) shows guided, copy‑paste instructions only; the app does
   not install, write, or launch any external code.

Choosing **Explore with sample notes** (either offline via the no-account button, or after signing
in) distills the bundled notes on-device, so Home / Review / Ask / Constellation are exercisable
within a few seconds — no server-side pre-load or reviewer setup required.

## 5.1.1(v) — Why an account is required

The account is not a gate in front of otherwise-local features — it **is** the feature. Doppl is a
personal-memory product: your memory lives under your account so it can (a) sync across your
devices and (b) be reached by your AI assistants and tools through the account. This is
account-based functionality per 5.1.1(v), which is why sign-in is required.

**In-app account deletion (5.1.1(v)).** A signed-in user can permanently delete their account and
all of its data from within the app: open **Connections** (the settings surface) → the **Doppl
Cloud** section → **Delete account…** → confirm. This calls `DELETE /v1/auth/account`, which
crypto-shreds the user's key material, deletes their memory (and backups), and revokes every
session — no website detour needed.

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
- The app is **local-first**: a bundled, code‑signed engine on `127.0.0.1` (loopback only) creates,
  stores, and retrieves the user's memory **on the user's Mac**. When signed in (which the build
  requires), that on‑device memory **syncs to the user's account** on the hosted backend
  (`https://api.signindoppl.com`) over HTTPS via Swift `URLSession`, so it is durable and available
  across the user's devices. The account is the user's identity + sync anchor; the on‑device store is
  where memory lives and is read.
- Data collected: the account **email** and **name** the user provides at sign‑in, and the **memory
  content** the user chooses to add to their account (their own notes/thoughts) — all **Linked** to
  the account, used only to operate the account (**App Functionality**), **not** used for tracking,
  and never shared with third parties for advertising. See `macos/PrivacyInfo.xcprivacy`.

---

## Privacy questionnaire (App Store Connect → App Privacy)
- **Email Address** — "Yes, we collect", **Linked to the user**, **Not used for tracking**, purpose
  **App Functionality**.
- **Name** — same settings.
- **User Content → Other User Content** (the memory / notes the user adds to their account) —
  collected, **Linked to the user**, **Not used for tracking**, purpose **App Functionality**.
- Answer **No** to tracking. Nothing else is collected.
