# Cortex — Forward Product Plan

_Last updated: 2026-07-05 (branch `mass-scale-app-redesign`)._

## North star

**Your memory, private on your Mac, one keystroke away — and usable by every AI tool you already use.**

A user should be able to say, honestly, after 10 minutes: "I connected my notes, Cortex learned
from them, and now both I (⌃⌥Space) and my AI tools can recall my own information — with sources."

## Product principles (what "easiest and most useful" means here)

1. **One core loop, nothing else on the critical path:** Connect a source → Review what Cortex
   learned → Ask (or let your AI tools ask). Everything else is optional and stays out of the way.
2. **Never trap, never lie.** Every screen has a way forward (skip, retry, or open the fix).
   Status text tells the truth ("synced 12 notes", not "connected" when nothing synced).
3. **No jargon.** Users see "notes", "memory", "AI tools" — never MCP, layers, captures, vaults,
   or Obsidian.
4. **Content reads like content.** Filenames and titles headline; raw paths are secondary,
   deduped, and truncated in the middle.
5. **Quiet by default, alive when working.** Dense diagnostics appear only when something needs
   the user. The menu-bar icon animates only when Cortex is actually doing something.
6. **Verified before claimed.** Backend behavior is proven by tests/live probes; GUI behavior is
   click-tested by a human before we call it done.

## Where we are (verified as of this commit)

- Core loop works end-to-end: notes-folder sync, ChatGPT/Claude export import (auto-detect +
  drag-drop), review approve/archive, cited Ask, agent access via MCP tools.
- Menu bar: animated status icon (spin during any real work — now level-signaled so it can't be
  missed; count badge when review items wait; checkmark on completion), Cortex Spotlight popover
  (Quick Ask with cited answers + Quick Note), ⌃⌥Space global hotkey, right-click quick menu.
- Onboarding: animated 5-step intro with per-step how-to; only hard gates are "engine ready" and
  "one source connected"; Review/Ask are skippable.
- 1124 backend tests + UI contract tests green. App builds clean, bundle correctly sealed.
- Still unsigned (ad-hoc): users right-click → Open on first launch; notarization pending
  Apple Developer credentials.

## Phase 1 — Ship the launch candidate (now → days)

Goal: a stranger can download, install, and complete the core loop unassisted.

1. **Human click-test of the full loop** (the one thing automation can't do here):
   fresh vault → onboarding → connect notes or import a ChatGPT export → approve one memory →
   Ask with citation → ⌃⌥Space Quick Ask → menu-bar states (launch spin, sync spin, count,
   checkmark). Fix whatever this surfaces; nothing else jumps this queue.
2. **Notarize + sign** (needs the Apple Developer ID cert — the only external blocker;
   runbook: `docs/runbooks/HERMES_NOTARIZE_AND_SHIP.md`). Removes the right-click→Open hurdle.
3. **Repackage the DMG** with `package_release.sh` (bundles Python + model; verify
   `/ready` → `provider=model2vec`, ~80MB) and ship to the site with checksums + update feed.
4. **Update the website copy/screenshots** to match the new UI (light theme, tabs, Spotlight).

## Phase 2 — First-week user experience (next 1–2 weeks)

Goal: users who install keep using it, because value shows up fast and friction stays low.

1. **First-run magic moment.** After the first source syncs, surface one concrete cited insight
   ("You've written about X in 14 notes — ask me about it") — the Mirror card exists in the
   backend; give it a home on Home and in the Spotlight panel's idle state.
2. **Spotlight v1.1:** inline Approve/Archive for the top pending item, recent-answers history,
   and a "copy answer with sources" button. (The panel is the daily surface; deepen it.)
3. **Review at scale:** batch approve/archive with per-item error reporting (single-item pattern
   already exists), and smart grouping of near-identical items (dedupe shipped; group next).
4. **Content quality pass on real data:** run typical exports through the pipeline; tune what
   becomes a "memory" so Review is signal, not chore. Skip low-value path-only fragments at the
   extractor level (they're now displayed cleanly, but the deeper fix is not proposing noise).
5. **AI-tools setup polish:** one-click enable for detected apps (works outside App Store builds),
   with a visible "your tool asked Cortex 3 times today" activity line for trust.

## Phase 3 — Growth (after launch is stable)

Ordered by expected user demand, not engineering appetite:

1. **More one-click sources:** Apple Notes export helper, browser bookmarks/history, calendar —
   each shipped only with the show-exactly-how setup guide pattern already in Connections.
2. **Multi-device:** encrypted vault sync/backup between Macs (foundation exists; CRDT later).
3. **Cloud accounts (optional tier):** hosted backend already exists behind sign-in; keep
   local-first as the default story.
4. **Team/shared memory** — only if users ask; local-first individuals are the wedge.

## Quality gates (every change, no exceptions)

- `swiftc` build clean; full backend `pytest` green; UI contract tests green.
- Smoke-test on spare ports with temp vaults; never against the app's live 8766.
- Clean re-sealed bundle install (never patch files inside a signed .app — it breaks the seal).
- Adversarial review for any non-trivial new surface before it ships.
- A human eyeballs any UI-visible change before we call it done (agent can't attach to the GUI).

## Known constraints

- Unsigned build until Apple credentials arrive (Phase 1, item 2).
- Agent sessions can't GUI-launch the app or click-test — code/backend verification is
  automated; visual verification is a human step by design.
