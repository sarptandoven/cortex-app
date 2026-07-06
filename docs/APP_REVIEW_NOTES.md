# Cortex — App Review Notes (Mac App Store)

Paste the relevant parts of this into **App Store Connect → App Review Information → Notes**.
It answers the four issues raised in review.

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

**How to review every feature (no server, no login, no download required):**
1. Launch Cortex. It opens straight into the app — no login wall, no network needed.
2. Create a folder anywhere (e.g. on the Desktop) with two small text files, for example
   `note1.md` containing "I prefer concise, direct writing." and `note2.md` containing
   "We decided to use PostgreSQL for the main store." (Any `.md`/`.txt` files work.)
3. In Cortex click **Connect notes** and choose that folder. Cortex reads the files **locally**
   and builds your memory; a progress bar shows the import.
4. Open **Home** to see the distilled profile, **Review** to approve captured memories, and
   **Ask** to query your memory (e.g. "how do I like to write?") — all fully offline.
5. **Connect an AI tool** (in Connections) shows guided, copy‑paste instructions only; the app
   does not install or launch any external code.

No hosted backend or sample credentials are needed — the two text files above exercise the
entire pipeline. If a hosted sample export file is nonetheless required, we will host one at a
public `https://trydoppl.com/…` URL on request (the app itself never fetches it; import is local).

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
- No third-party/social login and no account creation in the App Store build.
- No tracking, no data collected off device (see `PrivacyInfo.xcprivacy`).
