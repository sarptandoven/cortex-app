# UI Overhaul V2 — "The Archive, Unsealed"

The app looked unoriginal/default because `CortexDesign.swift:25` made **"no textures or
skeuomorphism" a hard contract** — while the identity (wax seals, index cards, catalog stamps,
warm paper) is inherently physical. So every primitive rendered flat and the app was forbidden
from looking like its own concept. The fix: **relax that one contract to "restrained physical
craft"** — procedural depth, letterpress edges, one wax-seal moment per surface. Never bitmap
textures. Never on the live-activity surfaces. Keep the serif/SF/mono three-voice typography and
destructive-red exactly.

## Signature moves (the new look)
1. **Wax-seal primary** (`CortexSealSurface`) — the one wax-red action per surface renders as a
   domed, sheened, rim-darkened seal that "sets into the paper" on press (scale 0.97 + sheen/
   edge-light drop, 0.09s). One token backs all ~109 buttons.
2. **The wax-seal mark** (`CortexWaxSeal`) — a domed wax disc with an embossed serif "C",
   replacing every stock SF-Symbol hero (sign-in, onboarding, sealed recovery-code envelope).
3. **Two-layer paper depth + hover lift** — cards get ambient+contact shadows off a formal
   `Elevation` scale + a top-edge light + optional interactive hover lift.
4. **Letterpress embossed edges** (`embossedBorder`) — two-stop gradient stroke; kills the
   default-rounded-rect tell with zero bitmap texture.
5. **The dark vault door** — the sign-in wall is the single dark iron-gall surface while the app
   stays light warm-paper: opening Cortex feels like unsealing a private archive.
6. **Giant New York numeral + living portrait** — Home leads with the cross-AI north-star number
   as a display(64+) serif numeral beside a breathing real-graph `ConstellationMiniPreview`.
7. **Ink-bleed spine + procedural paper grain** — feathered `archiveSpine` + ±1.5% Canvas
   luminance grain on panels, so warm paper is real, not asserted.

## Workstreams (ranked; #1 gates the rest)
1. **CortexDesign backbone** (CortexDesign.swift) — CortexSealSurface, CortexWaxSeal, Elevation +
   two-layer card shadows + interactive hover, embossedBorder, paperGrain, feathered archiveSpine,
   the missing SectionHeader wax tick, unified CortexPressStyle spring (kills the hover/press
   stutter), CortexMotion tokens, AnimatableNumber in CortexStatView, and the two missing
   primitives **CortexField** + **CortexToggle**. Re-skins 109 buttons + 37 cards with zero
   call-site churn.
2. **Sign-in wall — the dark vault door** (CortexApp.swift CortexSignInWall + CortexCloudAuth) —
   dark ground, CortexWaxSeal hero, provider buttons → full-width CortexButtons (one wax primary),
   mono email divider, all fields → CortexField, "I saved my recovery code" → CortexToggle,
   inline severity banner, sealed-envelope recovery card. + fix Cancel-during-password, re-fetch
   providers on hosted-URL change, Apple fallback.
3. **Home — the Mirror surface** (ModelTab + CortexNorthStar) — living two-zone hero, giant
   New York north-star numeral + readers ledger, horizontal stat ledger, tappable drill-ins,
   Canvas sync beam. + mount RecallProofWatcher live, tappable "Most recalled", twin loader.
4. **Night-sky family** (MemoryMapView + MemoryWrapped + MenuBarQuickPanel) — give the LIVE map
   the share card's night identity + permanent god-node halos; trophy share previews; CortexButton
   the panel. + wire the weekly-notify toggle, render-failure/retry, tap-vs-drag, Ask-clear/handoff.
5. **Ask + Review** (AskTab + ReviewTab) — cited answer as an annotated archive page with a live
   source margin (real excerpts, tappable [n]); custom disclosure/confirm; kind-rail review cards
   with keyboard-seal triage. + expose retrieved set, reachable copy, delete dead code.
6. **Connections + Import-diff** (SourceConnectionComponents + ImportDiffView + ConnectionsPrivacySheet)
   — ledger cards, import-diff as two side-by-side ledgers with a verdict band, drop-zone + .zip
   accept, CortexField paste, disclosure primitive. + wizard finish escape, first-run auto-detect,
   disconnect parity.

## Functionality must-fix (all must work before the next build) — 24 items
Wired into the workstream that owns each file; the notable ones:
- MemoryWrapped weekly notification can NEVER fire (setOptedIn never called) — wire a toggle.
- Share/Constellation renderCard() silent-fails → Copy/Save/Share disabled forever — add retry.
- Sign-in Cancel doesn't cancel the password request (can still sign in) — guard/cancel.
- Providers not re-fetched on hosted-URL change.
- Import-diff can't open the .zip export users are told to download; no drop-zone.
- Connect wizard traps a new user (memoryPack test ok:false) — add "finish anyway".
- First-run export auto-detect unreachable; connector with no removable import has no disconnect.
- Onboarding Finish silently bounces (status never rendered); restore branch dead-ends on
  non-cloud builds; restore auto-advances without confirming which account.
- Home north-star stale (RecallProofWatcher not mounted); "Most recalled" is dead text.
- Ask drops the retrieved set when there's a cited answer; Copy unreachable without hover.
- Quick panel Ask-clear/handoff leave stale asking-flag/answer.
- Delete dead code: ReviewCaptureCard/ReviewPreviewList, SourceConnectorStatusCard non-obsidian
  branch, dead contract-only props.

Execution: Wave 1 = CortexDesign backbone (solo, gates all). Wave 2 = the 5 surface workstreams
in parallel (file-disjoint). Wave 3 = whole-module typecheck + central contract-test fixes +
adversarial review + build. No build ships until it's genuinely great AND every flow works.
