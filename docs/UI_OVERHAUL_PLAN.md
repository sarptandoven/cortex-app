# Cortex macOS — UI Overhaul Plan

**Status:** Implementation-grade spec. Ready to build.
**Owner verdict this addresses:** *"far too complex AND basic at the same time."* Verbose walls of text, tiny/dense type, "awful, unoriginal" stock buttons, and fussy Archive decoration sitting on a basic, inconsistent layout.
**North star:** simple, calm, modern, effortless — the polish of Linear / Raycast / Things / Craft / Superhuman — while keeping a distinctive soul (warm paper, one wax-red ink, a serif reading voice, the margin spine).

---

## 0. Why it feels "complex AND basic" (the root cause, in one paragraph)

The Archive theme adds *ornamental complexity* (spines, accession stamps, three type voices, a gradient-ruled section header) on top of a *basic, inconsistent, verbose foundation*: there is **no button component** (115 raw `.buttonStyle` sites, ~12 different pill heights, stock macOS blue that ignores the wax-red palette), the **type floor is too low** (body 13pt, caption 12pt, mono hints 10.5pt) yet display jumps to 30pt (a bimodal scale with a crowded middle), and **copy is written as paragraphs** (44 string literals >90 chars in `CortexApp.swift` alone). Fix the foundation first — a real button, a bigger/tighter type scale, brevity by default — then every screen calms down almost for free.

---

## 1. Direction — "The Archive, uncluttered"

Keep the soul; demote ornament from *default* to *accent*; put a confident, consistent type + control system underneath. The target feel is **a calm reading room with exactly one clear thing to do per screen.**

Three commitments govern every decision below:

1. **One primary action per screen, and make it loud.** A single wax-red filled `CortexButton(.primary)`. Everything else is `.secondary` (hairline) or `.ghost` (borderless). No two prominent buttons competing.
2. **Words are labels, not paragraphs.** Cap primary copy at ~8 words, supporting copy at ~14, one sentence per state. Healthy states are near-silent. Anything longer lives behind "Learn more."
3. **Hierarchy through space, size, and surface — not ornament.** Use the paper→panel→quiet surface ladder and generous whitespace to separate things; allow at most **one** Archive signature (spine *or* stamp) per card, never three.

The keystone move: **build the missing primitives in `CortexDesign.swift` and route everything through them.** That is where "basic" and "unoriginal" are both fixed.

---

## 2. DESIGN SYSTEM — build this FIRST (before touching any screen)

All of section 2 lands in `/Users/sarptandoven/conductor/workspaces/cortex-by-doppl/taipei/macos/Sources/CortexDesign.swift`.

### 2.1 `CortexButton` + `CortexIconButton` (the single highest-leverage change)

Today: 115 `.buttonStyle(...)` sites, 0 reusable button. `.borderedProminent` renders system-blue Aqua and only 6 of ~39 prominent buttons even patch `.tint(accent)`. `CortexEmptyState` itself (CortexDesign.swift:304–308) reaches for raw `.borderedProminent`. `minHeight` appears at 13 distinct values (30/34/36/38/40/42/44/46/48/60/72/84…). This is the direct cause of "buttons look awful, unoriginal, bad" and of the primary action not being obvious.

**API:**

```swift
enum CortexButtonRole { case primary, secondary, ghost, destructive }
enum CortexButtonSize { case small, regular, large }   // 30 / 40 / 44 pt heights

struct CortexButton: View {
    let title: String
    var systemImage: String? = nil
    var role: CortexButtonRole = .secondary
    var size: CortexButtonSize = .regular
    var isLoading: Bool = false     // swaps ProgressView in at FIXED width — no layout jump
    var fullWidth: Bool = false
    let action: () -> Void
    // internal: @State hover; @Environment(\.isEnabled); GestureState pressed
}

/// Square, tooltip-mandatory. For clear-search ✕, map/overlay controls, row overflow.
struct CortexIconButton: View {
    let systemImage: String
    var help: String                // tooltip is required, not optional
    var role: CortexButtonRole = .ghost
    var size: CortexButtonSize = .regular
    let action: () -> Void
}
```

**Visual spec (resolved from role × state):**

| Role | Fill | Border | Text | Use for |
|---|---|---|---|---|
| `.primary` | `accent` (wax-red) | none | white/paper | the ONE action per screen (Ask, Approve, Connect, Continue/Finish) |
| `.secondary` | `panelBackground` | `softBorder` 1px | `ink` | Archive, Check Again, Copy config |
| `.ghost` | clear (hover: `quietBackground`) | none | `inkSecondary` | Back, Skip, inline nav, links |
| `.destructive` | clear | `softBorder` | system red | Remove / Delete / Revoke; owns its own confirm affordance |

- **Height** from size: small 30 / regular 40 (= existing `controlHeight`) / large 44. **Corners `Radius.md` (8)** so button and card corners agree.
- **Label:** `Typography.label` (new — SF 14 medium). Horizontal padding 18/14/10 by size.
- **States (define once, they are absent everywhere today):**
  - *hover:* primary fill +6% lightness; secondary/ghost fade in a `quietBackground` ground.
  - *pressed:* `scaleEffect(0.98)` + fill −6% (≈90ms, springless) — the tactile Things/Linear feel.
  - *disabled:* `inkFaint` text, drop fill and shadow (NOT SwiftUI's default 30% opacity, which muddies on warm paper).
  - *loading:* inline `ProgressView().controlSize(.small)` at fixed width.

**Migration is mechanical.** This:
```swift
Button { approve() } label: {
    Label("Approve", systemImage: "checkmark.seal").frame(minWidth:150, minHeight:48)
}.buttonStyle(.borderedProminent).controlSize(.large).disabled(isInFlight)
```
becomes:
```swift
CortexButton("Approve", systemImage: "checkmark.seal", role: .primary, isLoading: isInFlight, action: approve)
```
Height, padding, fill, hover, press, disabled become impossible to get wrong. Delete all 115 ad-hoc styles and the ~37 `minHeight`/`minWidth` frames.

### 2.2 Typography — raise the floor, shrink the set, one mono role

Current: body 13, caption 12, stamp 11, **hint 10.5** mono; display jumps to 26–30. Plus 89 raw `.system(size:)` at 26 distinct sizes and 280 semantic-font escapes (`.font(.body/.callout/.headline)`). Result: "too small" and "too many voices" simultaneously (a card can stack serif title → SF caption → mono stamp → mono hint = four fonts).

**New scale (SF = working voice, Serif = the archive's voice, Mono = provenance ONLY):**

```swift
enum Typography {
    // SERIF — archive voice: titles + long-form answer/memory prose only.
    static func display(_ s: CGFloat = 28) -> Font { .system(size: s, weight: .semibold, design: .serif) }
    static let title   = Font.system(size: 18, weight: .semibold, design: .serif) // 16 → 18
    static func prose(_ s: CGFloat = 15) -> Font { .system(size: s, weight: .regular, design: .serif) } // 14 → 15
    static let stat    = Font.system(size: 26, weight: .semibold, design: .serif) // 22 → 26

    // SF — working voice: every control, label, list row, description.
    static let bodyLarge = Font.system(size: 15, weight: .regular)  // NEW default body (was 13)
    static let body      = Font.system(size: 14, weight: .regular)  // 13 → 14 (dense/secondary rows)
    static let label     = Font.system(size: 14, weight: .medium)   // NEW — button + field labels
    static let caption   = Font.system(size: 13, weight: .regular)  // 12 → 13

    // MONO — one role. Provenance/telemetry only. `hint` is DELETED.
    static let stamp     = Font.system(size: 11, weight: .medium, design: .monospaced)
}
```

- Body/caption each +1–2pt, title +2, mono collapses **two roles → one** (delete `hint`; fold keyboard-hint text into `caption`). No paragraph below 13pt anywhere.
- **Ambient/menu-bar type floor:** headline ≥15, secondary ≥13 (notch, HUD, ticker, quick-panel are the worst offenders today at 10.5–12pt).
- **Answer body (Ask) switches from serif `prose()` to SF 16 with `.lineSpacing(5)`** — reserve serif for the *echoed question* and memory prose, not the AI answer body.

**Enforcement — add a role modifier so the escapes have a real target:**
```swift
enum TextRole { case display, title, prose, body, caption, stamp }
func cortexText(_ role: TextRole) -> some View   // sets font AND the correct ink together
```
This kills the 101 `.foregroundColor(.secondary)` (system gray looks dead on warm paper → must be `inkSecondary`) and gives the 280 semantic-font escapes a mechanical replacement. Bake uppercase+kerning into the `stamp` path so the 34 inline `.kerning` + 29 `.uppercased()` calls disappear.

### 2.3 `StatView` — one number treatment (Home needs this)

Today there are three divergent stat treatments (`StatBox` = `.title3`, `twinStat` = serif 22, readiness = mono stamp). Add one:
```swift
struct StatView: View { let value: String; let label: String }  // Typography.stat number + caption label, monospacedDigit
```
Use it for Home's headline strip and retire `StatBox`'s `.title3` variant and fold `TwinScorecardCard` onto it.

### 2.4 Spacing / Radii / Elevation

- **Spacing:** keep the ramp, add `Space.xxs = 4` and `Space.xxl = 40`. Audit raw `.padding(14)`/`.padding(20)` onto `md`(16)/`lg`(22).
- **Radii:** keep 3 (6/8/12). Buttons fold onto `Radius.md`(8) to agree with cards. No new radii.
- **Elevation — name it, prefer geometry over shadow (Linear/Raycast):**
  ```swift
  enum Elevation { case flat, raised, floating }  // none / card hairline / popover+menu
  ```
  Replace `CortexCard`'s contact shadow (CortexDesign.swift:169) with a **hairline-only** raised style; reserve a real shadow for `.floating` (Ask panel, quick-panel, menu popovers) only. Kills the "cards floating on a desk" skeuomorphic tell.
- **`contentMaxWidth` token:** define one column width (e.g. 760) and use it for header, tab bar, and every tab. Today header=760, tab bar=720, Home=760, sign-in=480, tab underline-cap=720 don't align.

### 2.5 Archive decoration — KEEP / SIMPLIFY / CUT

**KEEP (the soul):**
- Warm-paper + iron-gall + wax-red palette, and the paper→panel→quiet **surface ladder** (make surface = hierarchy the rule).
- **Serif reading voice** for display titles + memory/answer prose — but bigger and airier (§2.2).
- **The margin spine** (`archiveSpine`) — the most recognizable, lowest-cost mark; carries state (gold/wax/ink) with zero text. Keep as the *one* signature per card.
- **`AccessionStamp`** as the single metadata language — but only where provenance genuinely matters (source + date on a memory card).
- Gold-as-fill discipline; moss privacy dot.

**SIMPLIFY:**
- **`SectionHeader`** — drop the gradient wax-tick rule (CortexDesign.swift:209–218). A plain 18pt serif title + optional one-line caption + hairline is calmer.
- Type voices **3 → effectively 2 + 1 rare** (serif + SF, mono only for true stamps).
- Empty states → icon + one line + one primary button (cut the second sentence).

**CUT:**
- `Typography.hint` (10.5 mono) — deleted (§2.2).
- **`CortexStatusPill`** (CortexDesign.swift:262–276) — 1 call site, its own doc says prefer `AccessionStamp`. Delete.
- Accession stamps used as *default* card metadata: "WILL REMEMBER", "OR REVIEW ONE AT A TIME", "PROFILE READINESS 62/100 COMPILED …", "CORTEX NOTICED". Keep the component for rare provenance only.
- The 140–246-char explanatory strings baked into the UI (move behind a `CortexLearnMore(_:)` inline disclosure or delete).

---

## 3. Top problems across the app (ranked by leverage)

1. **No button component → "awful, unoriginal" buttons + ~12 inconsistent heights + primary action not obvious.** 115 raw `.buttonStyle`, 0 reusable. `CortexDesign.swift` (no button; `controlHeight:121` defined then ignored). Fix = §2.1.
2. **Type floor too low + too many voices → "too small" and "too much."** body 13 / caption 12 / hint 10.5; 89 off-scale sizes; ambient surfaces at 10.5–12pt. `CortexDesign.swift:128–148`. Fix = §2.2.
3. **Copy written as paragraphs → "walls of text."** e.g. `ModelTab.swift:570`, `OnboardingView.swift:255/300`, `ReviewTab.swift:751`, `ConnectionsPrivacySheet.swift:44/2246`, `AskTab.swift:104`. Fix = brevity-by-default (§1.2), healthy states silent.
4. **`ConnectionsPrivacySheet.swift` is a 3122-line, 11-panel, 4-level-deep junk drawer that also serves as Settings** (⌘, routes here). Fix = re-scope to 4 sections; move diagnostics to a real Settings window (§4.5).
5. **Two most-seen cards bypass the design system.** `CaptureCard` (CortexApp.swift:10853) and `MemoryCard` (:10894) use raw `.font(.headline/.body/.caption)`, `.foregroundColor(.secondary)`, unstyled `Button("Approve")`. Rebuild on `cortexCard` + `CortexButton` or delete `CaptureCard` if superseded.
6. **Fussy decoration stacked on basic layout.** Spine + stamp + gradient rule + wax bullets on the same card. Fix = one signature per card (§2.5), simplify `SectionHeader`.
7. **Ambient surfaces: 7 competing, overlapping, tiny.** Up to 5 reactions to one "memory formed" event. Fix = cut 7 → 3 (§4.7).
8. **Flat, untiered screens.** Home is 7 equal-weight sibling cards (`ModelTab.swift:9`, uniform `spacing:28`); no hero. Fix = tiering (§4.2).
9. **Onboarding is 6 steps, half read-only lectures, with a fake "aha."** `OnboardingView.swift:23–32`. Fix = 3 steps, real preview (§4.6).
10. **Misaligned widths / redundant chrome in the shell.** min 560 (SwiftUI) vs 820 (NSWindow); 3 stacked header rows; text-only weightless tab bar. `CortexApp.swift:8205–8350, 11543–11560`. Fix = §4.1.

---

## 4. Per-screen overhaul spec

### 4.1 Shell / Navigation — `CortexApp.swift:8187–8350`, window `:11543–11560`; `ProductFlowTypes.swift`

Problems: redundant wordmark row + tab bar + divider = 3 chrome rows before content; **text-only, weightless tab bar** (CortexApp.swift:8125–8167) that ignores the `systemImage`/`label` already defined on `AppTab` (ProductFlowTypes.swift:16–22); "Connections" header button is a catch-all; contradictory min widths (560 vs 820); cramped 2-line technical footer (:8320–8338); non-interactive `LiveActivityTicker` overlay can occlude content.

Overhaul:
- **Collapse header + tab bar into one unified toolbar row** in the titlebar. Wordmark (or just tabs) leading with a **~78pt safe inset** for traffic lights; settings/Connections as a trailing `CortexIconButton`. Reclaim a full vertical row; drop the extra Divider.
- **Give the tab bar weight:** a quiet-ground segmented container with a raised selected card (Linear/Things), the **icons `AppTab` already defines**, a bumped active label, and keep the ink-tick as the accent. It must read as *navigation*, not a heading.
- **Reconcile widths:** one `contentMaxWidth` (§2.4) for header/tab bar/content; delete the dead `minWidth:560`; single NSWindow min.
- **Footer:** one calm sentence at ≥13pt, one line, one icon; multi-line technical text → tooltip/diagnostics. The footer reassures, it doesn't dump logs.
- **`LiveActivityTicker`:** dock into the footer region (reserve space) or shrink to a compact inline pill; never overlap actionable content.
- **Constellation:** give it a clear, consistent entry (a `.secondary` toolbar/Home action), not just a menu-bar item + a raw `.bordered` on Home.
- Route the two title literals (`.system(size:20)` wordmark, `.system(size:26,.bold)` sign-in) through `Typography.display()` (semibold guardrail).

### 4.2 Home — `ModelTab.swift` (rename surface "Model" → "Home"/"You")

Problems: 7 sibling cards, uniform `spacing:28`, no hero (`ModelTab.swift:9`); two competing titles (30pt task-nudge hero `:624` vs the genuinely valuable 22pt Mirror headline `:209`); triple-redundant source status (title `:523` + detail `:562–571` + status row `:687`); **no headline stats displayed** though `memoryCount`/`pendingCount` are computed; dense `ProfileCard` (360-char statements, wax-red mono section titles); fussy stack (readiness stamp + spine + italic footnote); 4 raw button treatments.

Overhaul:
- **Tier the layout:** **Tier 1 — the Mirror Moment IS the hero** (largest type, top). **Tier 2 — a compact `StatView` strip** (Memories · Entities · Reviewed) — this is the "valuable at a glance" that's currently missing. **Tier 3 — clearly-secondary cards** (Constellation, Profile, Twin). Replace uniform `spacing:28` with grouped spacing (tight within, `Space.xl`+ between tiers).
- **Demote the task-nudge** from a 30pt headline to one calm inline `.secondary`/`.ghost` action; let hero content, not a button, own the eye.
- **Kill redundancy:** source status lives in the status row ONLY; drop the redundant `detail` lines (`:552–594`) when the title stands alone (extend the discipline healthy states already use).
- **Calm `ProfileCard`:** cap statement ~140 chars / 2 lines; section title = SF-semibold sentence case (not wax-red mono stamp); keep "Where this comes from" collapsed; one signature (spine OR stamp), drop the readiness stamp and the italic footnote.
- Replace all 4 raw buttons with `CortexButton`; one primary max.

### 4.3 Ask — `AskTab.swift` (the most important surface for "text too small")

Problems: **answer prose is 14.5pt serif** (`:888–894`) — the core "too small" on the primary payload; `AskMemoryContextStrip` is a 7-data-point diagnostic dashboard wedged between query and answer (`:292–353`); 6 differently-sized raw buttons; the question is never echoed (built but unwired, `:527`); a duplicate citation renderer (`AskResultsSection`/`AskSourceDetailRow` :745–831); over-designed citation "footnote ledger" with a hand-drawn dotted leader + uppercase mono stamp (`:981–1042`, `AskDottedLeader:1096`); multi-sentence empty-state copy.

Overhaul:
- **Answer body → SF 16, `.lineSpacing(5)`** (`:888–889`). Serif only for the echoed question.
- **Echo the question** above the answer: pass `question: state.searchQuery` at `:527`.
- **Replace the diagnostic strip** with a single-line, single-action banner ("3 items waiting in Review → Review"). Remove the three metric tiles and per-source confidence chips from Ask entirely — that belongs in Sources/Health.
- **Simplify citations to one clickable row each:** numbered pill + source label (13pt) + muted location; delete `AskDottedLeader` and the uppercase mono leader stamp. Fold/kill the duplicate `AskResultsSection` renderer.
- Route all six buttons (`:147/499/601/611/621/866`) through `CortexButton` (primary = wax-red 40pt).
- Trim every empty-state detail to one short sentence (`:39/99/102/104/266/535`).

### 4.4 Review — `ReviewTab.swift`

Problems: 11 raw buttons at 3 different heights (34/40/48) — the same Approve/Archive rendered inconsistently; **`ReviewQueueCaptureCard` is a wall** (title 3 lines + 360-char summary + "WILL REMEMBER" stamp + up to 3×280-char memory rows — ~1,400 chars to approve one item, `:681–765`); serif body at 13.5pt; **Approve/Archive are co-equal** and Archive is on the *left* (`:735/759`); two ungated heavy "extra" sections push the queue below the fold (`TwinAlertsViews.swift:33/187`); two competing batch models; 4 divergent empty states; ~180 lines of dead code (`ReviewCaptureCard`/`ReviewPreviewList` :907–1088).

Overhaul:
- **Collapse the card to title + ONE preview line (≤140 chars) in ≥15pt SF;** drop the "WILL REMEMBER" stamp; full detail behind hover/expand. Card clears the eye in <2s.
- **Make Approve dominant:** filled wax-red `CortexButton(.primary)`, alone on the trailing edge; demote Archive to `.ghost`/icon-only secondary.
- Replace all 11 raw buttons with `CortexButton` (one canonical height).
- **Gate/demote the two extra sections** into a single "N things to look at" banner that expands on demand, or move below the queue.
- **Pick one batch model;** make the batch bar a persistent, visually distinct sticky footer (count + Approve-all).
- **Standardize on `CortexEmptyState`** for all empty/all-clear/starting states.
- **Delete the dead `ReviewCaptureCard` family** (`:907–1088`) after confirming no test refs.

### 4.5 Connections & Privacy — `ConnectionsPrivacySheet.swift` (3122 lines) + `CortexApp.swift` `TrustPolicySection:9623`

Problems: 11 stacked panels, ~14 sub-sections nested inside one "Developer & diagnostics" bucket (`:328–399`) — the whole Settings pane smuggled into a "Connections" sheet, up to 4 disclosure levels deep; 6 inconsistent raw primaries + 20+ copy-pasted `minWidth/minHeight`; **3–4 near-identical "Copy config" buttons**; `TrustPolicySection` is a disclosure-in-disclosure-in-disclosure with 8 toggles that the segmented preset already abstracts; wall-of-text subtitles; caption-size explanations; the local-only promise repeated 5+ times; per-connector rows with 4 buttons + 5 text lines; auto-advancing 2.5s instructional tickers; "coming soon" dead-end tiles.

Overhaul — **re-scope from 11 sections to 4:**
1. **Sources** — Primary notes + Import chats + Add more sources (merge current 1–3).
2. **Privacy & trust** — the segmented preset (Private/Balanced/Full) + "Back Up Now" + the local-only line **once**; the 8 raw toggles live behind a single "Custom…" that appears only on "Advanced" (flatten the triple nesting).
3. **AI apps** — exactly two verbs: **"Copy MCP config"** and **"Copy memory pack"** + connected-tool rows with Test.
4. **Your data** — per-source counts + purge.

- **Move sections 9–11 (Activity & alerts, Backups & recovery, Developer & diagnostics) out to a real Settings window** (or Help → Diagnostics). Removes ~half the perceived complexity. Every capability stays reachable — regrouped, right-sized, one level shallower.
- Replace all 26 raw buttons with `CortexButton`; one primary per card.
- No `DisclosureGroup` more than 1 level deep. Promote explanatory captions to 13pt body; cap subtitles at ~8 words; caveats → `?` popovers.
- Per-connector row: one primary action (Sync/Sign in/Resume) + an overflow "…" menu for Pause/Remove; one status line.
- Static numbered checklists (drop the 2.5s ticker / gate behind Reduce Motion); hide non-connectable connectors and their footnotes.

### 4.6 Onboarding — `OnboardingView.swift` (1215 lines); presented `CortexApp.swift:8242–8254`

Problems: **6 steps, half read-only lectures** (welcome/privacy/seeYourself ask for nothing); step 3 (`addMemory`) offers **4+ parallel source paths** with no default (`:349–392`) — the densest, most overwhelming first action; Continue is never gated so users click through without doing anything; the "aha" (step 4) shows a **fake** constellation identical whether or not data was added (`:624/1004`); long serif paragraphs + caption-sized escape hatches; **6 different button treatments**; a 176pt card for one button (`:1070–1113`); step 6 crams the backup decision in; Finish shows the full celebration then can silently *not* finish (`:183–189` → `CortexApp.swift:7334–7347`).

Overhaul — **collapse to 3 steps:**
1. **Welcome** — one line, privacy reassurance folded in as a single sentence (no separate privacy screen).
2. **Add your memory** — ONE big primary `CortexButton` ("Connect your notes") + ONE frictionless escape ("Try it with sample notes" as a *comfortably sized* control, not a caption link). Move the app-connect grid + AI-export drop target into Connections (or behind a small "Other ways to add…" disclosure).
3. **You're set** — the constellation reveal populated by the user's real/sample data (lean on `exploreWithSampleNotes()` auto-advance) + the ⌃⌥Space hotkey + optional Connections/quick-capture links.

- Every button through `CortexButton`; one primary per step; Skip/Back are `.ghost`.
- Halve every paragraph; promote actionable text out of caption size; drop the 176pt card and the status pills.
- Default backup silently (surface in Settings) or make it a one-line toggle — don't ask a new user to reason about snapshots on the way out.
- **Only show the celebration when setup is genuinely complete** (gate on `canCompleteOnboarding`), or make the last action *be* completion.
- One lightweight progress indicator (drop the mono "Step N of 6" + "The Archive" header tag).

### 4.7 Ambient / Menu-bar — `LiveActivity.swift`, `NotchNotifier.swift`, `MenuBarQuickPanel.swift`, `LiveActivityPill.swift`, `MenuBarAnimator.swift`, `QuickCapture.swift`

Problems: **7 surfaces**, up to **5 reactions to one "memory formed" event** (menu-bar sparkle + notch + ripple + ticker + edge-glow); notch and bottom HUD celebrate the same moment top *and* bottom; type at 10.5–12pt (often faded to 0.28 opacity); 5 different button languages; the ticker's faint 2-line "ghost stack" is unreadable decoration; multiple perpetual looping animations create restlessness; inconsistent icons per concept; two near-duplicate capsule components.

Overhaul — **cut 7 surfaces → 3:**
1. **Menu-bar icon** — identity + status (one glyph per concept, defined once).
2. **Ask popover** — the ONE interactive surface. Make it do one thing brilliantly: *ask your memory.* Remove the inline pending-review card, demote "Note" to a secondary affordance; idle = field + 2 suggestions with air (not a mini-dashboard).
3. **ONE bottom progress+celebration HUD** — delete the ripple (P4), fold/drop the ticker (P6) and route it through the coordinator; reserve the **notch strictly for explicit quick-capture confirmation**.
- Add a shared **`AmbientCapsule`** to `CortexDesign.swift` (dedupe the notch `pill` and HUD `pill`) and route every ambient button through `CortexButton`.
- **Ambient type floor 15/13pt;** delete the faint ticker ghost-stack.
- **Reduce perpetual motion to one signal** while working (the progress bar); drop the breathe-glow and the `LivePulse` dot; gate remaining motion behind Reduce Motion.

---

## 5. Implementation sequencing (foundation → screens)

**Phase 0 — Design-system foundation (do all of this before any screen):**
1. Add `CortexButton` + `CortexIconButton` (§2.1); make `CortexEmptyState` (CortexDesign.swift:304–308) dogfood it.
2. Revise `Typography` (§2.2): bigger scale, delete `hint`, add `label`/`bodyLarge`; add `cortexText(role:)`.
3. Add `StatView` (§2.3); `Elevation`, `contentMaxWidth`, `Space.xxs/xxl` (§2.4).
4. Simplify `SectionHeader` (drop gradient rule); delete `CortexStatusPill`.
5. Add `AmbientCapsule` (§4.7) and, if adopted, `CortexLearnMore`.

**Phase 1 — Shell (unlocks the frame):** unified toolbar + weighty tab bar + reconciled widths + calm footer (§4.1). Migrate its buttons.

**Phase 2 — Core loop screens (highest daily value), in order:**
6. **Review** (§4.4) — collapse the card, make Approve dominant, delete dead code.
7. **Ask** (§4.3) — 16pt SF answer, echo question, kill the diagnostic strip, one-line citations.
8. **Home** (§4.2) — tier the layout, Mirror hero, `StatView` strip, calm ProfileCards.
9. Rebuild `CaptureCard`/`MemoryCard` (CortexApp.swift:10853–10987) on tokens, or delete `CaptureCard`.

**Phase 3 — First-run & onboarding (§4.6):** 3 steps, real aha, honest celebration.

**Phase 4 — Connections/Settings split (§4.5):** re-scope to 4 sections; stand up a real Settings window for diagnostics.

**Phase 5 — Ambient subtraction (§4.7):** 7 → 3 surfaces, `AmbientCapsule`, type floor, motion reduction.

**Cross-cutting copy pass (runs alongside Phases 1–4):** rewrite copy to labels across all screens; healthy states go near-silent; long strings → `CortexLearnMore` or deleted.

Rationale: Phase 0 changes buttons/type *app-wide in one edit* (biggest visible win, least risk). Shell frames everything. Then the daily loop (Review/Ask/Home) where users spend real time. Onboarding and the settings decomposition are larger refactors, done after the vocabulary is proven. Ambient is pure subtraction, safe to do last.

---

## 6. Open design decisions for the founder

1. **Home tab name:** "Home", "You", or "Mirror"? (Content is "what Cortex learned about you"; internal name "Model" is confusing.)
2. **Constellation's home:** a first-class 4th tab, a Home hero action, or a toolbar toggle? (Today it's a hidden menu-bar/Home entry.)
3. **Connections vs Settings split:** confirm moving Activity/Backups/Developer-diagnostics into a dedicated Settings window (⌘,) and re-scoping the sheet to 4 sections — this is the single biggest simplification but relocates ~14 sub-sections.
4. **Onboarding default source path:** should "Try it with sample notes" be the visually prominent happy path for new users/reviewers (fastest aha), with "Connect your notes" as the equal-weight alternative?
5. **Backup during onboarding:** default it silently (decide later in Settings) vs a one-line toggle vs keep the card?
6. **Serif reach:** keep serif for section/card titles too, or restrict serif to display + memory/answer prose only and make all UI-structure titles SF? (Affects how "literary" vs "clean" it reads.)
7. **Elevation style:** go fully flat/hairline (Linear) for cards, or keep a whisper of contact shadow as an Archive tell? (Plan proposes hairline for cards, shadow only for floating surfaces.)
8. **Ambient aggressiveness:** is cutting the ripple + ticker + one notch path (7 → 3 surfaces) acceptable, or is the "memories streaming past" motion considered part of the soul worth keeping in a quieter form?
9. **Keyboard-first push:** how far to lean into Superhuman-style shortcuts (surface ⌘↩/⌘⌫ hints prominently, add a command palette)?
