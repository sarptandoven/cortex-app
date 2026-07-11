# Cortex — Your AI Memory, That You Own, That Works in Every AI

**Prepared for:** YC application + partner interview
**Status:** The definitive company plan. Cortex is a shipped, code-verified product — a local-first macOS app (Mac App Store: "Doppl") plus a hosted backend at `api.signindoppl.com` with ~578 backend tests. Every "we already built this" claim maps to real code in `backend/app/`, `macos/`, `sdk/`, and `extension/`. Where something is designed-but-unbuilt, we say so in §11 and §14 — we never sand off a gap.

> **Positioning note.** An earlier draft of this file pivoted the company to an abstract agent-accountability / "black-box receipt" infrastructure thesis (working name *Attesta*). That thesis is preserved in full at `docs/appendix-attesta-evidence-layer.md` as an optional far-future trust primitive. **This document is the real company** — the user-facing, user-owned portable-memory product that today's Cortex users actually touch. If both ever coexist, portable memory is the *land* and anything receipt-flavored is a much-later, optional layer, never the wedge.

---

## 1. THE PITCH

**The one-liner ("X for Y"):**

> **1Password for your AI memory** — the private, portable model of who you are that *you* own, and that works inside every AI tool, so you never start from zero and never get locked into one vendor's brain.

**The core central message (one memorable sentence):**

> **Every AI now has a memory of you — and every one is a walled garden it owns and can switch off; we're the one memory that lives with you and works in all of them.**

**The cold-open hook (first 15 seconds — the everyday pain, not an abstraction):**

> *"You've spent months teaching ChatGPT who you are — your projects, your style, how you think. Then you open Claude, or Cursor, or Gemini, and it's a stranger. You re-explain yourself, again, from zero. Every tool remembers you, and none of them share. You don't own the memory of you — four different vendors do, and any of them can change the terms, region-lock it, or get acquired and shut it down. We fix that. Cortex builds one private memory of you, from the data you already have, that lives on your machine and shows up inside every AI you use."*

**The 60-second spoken pitch:**

> *"Nearly a billion people use AI every day, and most use more than one tool — ChatGPT for writing, Claude for code, Cursor, Gemini, Copilot. Every one of them shipped a 'memory' in the last year. And every one of those memories is trapped inside that one vendor. So the moment you switch tools — which 60% of AI users do — you're a stranger again, re-explaining your whole life. There's no way to export it, back it up, or take it with you. It's lock-in by design.*
>
> *Cortex is the memory that's yours. You do one import — your ChatGPT history, your Claude exports, your notes, your Gmail and GitHub — and Cortex distills it, on your own machine, into a private, cited model of who you are: your projects, your people, your decisions, how you write. You can literally see it — a profile in your own words and an interactive map of your world, every claim traceable to its source. Then one click wires that memory into Claude, Cursor, ChatGPT and any AI agent over MCP. Now every tool you use already knows you.*
>
> *We already shipped the hard part — it's live on the Mac App Store, with real connectors, a cited retrieval engine, a knowledge graph, and universal reach into every AI app. OpenAI and Anthropic will never build this, because unifying your memory across their competitors destroys their lock-in. We're the neutral, user-owned layer they structurally can't be. We start with the AI-native power users who feel this pain every single day, and expand to everyone who touches more than one AI."*

**Three ways to remember us, by audience:**
- **Power user / demo:** "one import, and every AI tool you use already knows you — cited, private, yours."
- **Prosumer / mainstream:** "the memory of you that you own, that no vendor can lock in or shut off."
- **Inevitability:** "as AI personalizes everything, the context should belong to the person — the portable context layer for the agent era."

---

## 2. PROBLEM & WHY NOW

### The everyday pain (it fires for anyone using more than one AI)

You teach ChatGPT your projects, your voice, your preferences. Switch to Claude, Cursor, or Gemini and it's amnesia — you re-explain everything. This isn't an edge case: **64% of knowledge workers use 2+ AI tools regularly, 31% use 3+**, and **60% of all AI users combine a general assistant with specialized tools** (Menlo Ventures, *State of Consumer AI 2025*; promptlatte). Professionals lose an estimated **200+ hours/year** rebuilding context across tools (Plurality). The pain is voiced daily, not inferred — a developer describing juggling Claude → Cursor → Copilot: *"You spend 20 minutes brainstorming a brilliant database schema in Claude Web. Then you switch over to Cursor… and the AI has Alzheimer's. The context fragmentation was driving me insane"* (DEV.to). A LinkedIn post titled *"I hate switching between ChatGPT and Claude because it forgets my entire project context"* drew **173 comments** itemizing the daily ritual of copy-pasting the same context.

### Three structural failures

1. **Context is fragmented across every AI.** Each tool's memory works only inside that tool. There is no SMTP-for-memory — the community literally reaches for that analogy: *"AI memory needs something similar to SMTP for email interoperability"* (HN).
2. **Vendor memory is shallow and forgetful — even within one tool.** It "covers basic preferences and explicitly stated facts, but does not cover conversation structure, decisions made, rejected ideas, project throughlines, or how your thinking evolved. When the saved memory store fills up, older entries get dropped silently" (Fostera). A documented ChatGPT bug: when memory is full it gives *no warning* yet still claims it remembered — silent data loss.
3. **You don't own it and can't take it with you.** *"You can view and delete memories but can't export them; if you decide to switch AI providers there's no way to take ChatGPT memories with you… creating platform lock-in through accumulated context"* (synthreo). Business-tier users can't export at all. Even Anthropic's "import your ChatGPT memory in 60 seconds" tool is a lock-in move — pull into Claude, don't set free. As one builder put it: *"You import once, and from that moment your contexts diverge again."* Migration ≠ ownership.

### Why now — four forces that all landed inside ~12 months

1. **Memory shipped everywhere in 2025–26 — and siloed.** OpenAI (memory referencing all past chats, now "Dreaming V3"), Anthropic (per-project memory + Memory Import), Google Gemini (automatic Memories), Microsoft Copilot — all shipped cross-session memory to a combined **~1.8B users**. The moment ~1B people got a memory *inside each tool*, cross-tool fragmentation became the **universal, felt pain**. Every silo the labs ship is our tailwind.
2. **MCP became the universal distribution rail — and didn't exist 18 months ago.** Anthropic announced MCP (Nov 2024); OpenAI adopted it (Mar 2025); Google confirmed support; it was **donated to the Linux Foundation's Agentic AI Foundation (Dec 2025)** with OpenAI/Google/Microsoft/AWS as members. **10,000+ public MCP servers, 97M monthly SDK downloads.** This is the plug Cortex already speaks — a portable memory is *technically distributable now* in a way it wasn't a year ago.
3. **Agents need a persistent, portable model of the user to act.** The agentic-AI market is projected at **$47–53B by 2030** (MarketsandMarkets, 46% CAGR). An agent acting *on your behalf* is useless without a durable, portable model of *you*.
4. **The betrayal is fresh and repeating — a live refugee cohort exists right now.** **Meta acquired Limitless (formerly Rewind) and killed the local-first Mac app on Dec 19, 2025**, giving users days to export before deletion and instantly cutting off EU/UK users. Rewind was beloved *specifically because* of its local-first architecture. A wave of burned, privacy-primed, technically sophisticated users is hunting for a durable, owned home *today*. (The #QuitGPT movement — 2.5M+ pledged cancellations — shows users will move en masse over trust, and every mass switch re-triggers the "but my memory is stuck there" pain.)

### The validation nobody can wave away

**YC already funded the adjacent thesis.** Mem0 raised **$24M (YC + Peak XV + Basis Set + GitHub Fund, Oct 2025)** to build a portable "memory passport" — API calls 35M → 186M in two quarters, chosen as AWS's exclusive Agent SDK memory provider. Their own framing of the moat is ours: *"big AI labs have little incentive to make memory portable."* But Mem0 is **developer infrastructure** — cloud-hosted memory keyed to an app's users. **The user-owned side of that exact thesis is wide open** — and it's precisely where Cortex already lives.

---

## 3. THE COMPANY / PRODUCT

**What we're building:** your portable, private, user-owned memory of who you are — built from the data you already have, living on your own machine, and usable inside every AI tool and agent.

### The magical user experience (three beats — the "aha")

1. **The Mirror.** You drop in your ChatGPT/Claude export or point Cortex at your notes. It distills — *on your machine* — a legible model of you: a profile in your own words ("You decline meetings before 10am — from your calendar, 6×"; "You prefer terse, no-preamble writing") plus an interactive **Constellation** knowledge graph of your people, projects, and topics. Every claim is cited to its source and correct-in-one-tap. This is the *"holy shit, it actually knows me — and I can see it, and it's mine"* moment.
2. **The Reach.** One click generates your MCP config (Claude Desktop, Cursor, VS Code) and a browser-extension pairing token. Your memory is now *live* in the tools you already use — a 60-second install with zero behavior change.
3. **The Proof.** You open a *fresh* chat in a *different* tool and it already knows your context, citing your own memory back to you. Continuity across vendors, felt. No re-typing, ever again.

### The product pillars

- **The Mirror — a whole-person model of you.** Not a flat fact-list: a distilled, cited profile assembled deterministically into legible sections (how you work, your voice & style, preferences, what you avoid, key decisions, facts, timeline, focus, people & projects, open loops). Sections with nothing well-supported are omitted — it's honest about what it doesn't know.
- **The Constellation — your knowledge graph.** An auto-built, interactive, force-directed map of the people/projects/topics your life orbits, surfacing "god nodes" (what you center on), communities, and surprising cross-cluster connections. Screenshot-native and inherently personal — the shareable object.
- **Cited Ask — answers you can trust.** Every answer traces to its source with a memory ID, or Cortex abstains. Cite-or-abstain, never confident-and-wrong.
- **The Vault — a portable Markdown memory you literally own.** Your memory is a real folder on your disk (canonical JSON/JSONL/Markdown), inspectable, backup-able, and rebuildable. SQLite is a disposable index; the vault is the source of truth. "Export everything, including from us" is a button, and portable signed bundles let memory move *between* tenants with lineage intact. This is the anti-lock-in claim, made technically true.
- **Universal Reach — works inside every AI.** Your memory shows up as tools inside Claude/Cursor/VS Code via **MCP**, wires into any function-calling app via an **OpenAI/Anthropic/OpenAPI tool schema**, is reachable over **REST + SDKs (Python/TypeScript)**, and bridges web apps (ChatGPT.com, Claude.ai) via a **browser extension**.
- **Local-first privacy — it still works with the plug pulled.** The whole memory lives and runs on your machine. An account is *identity + sync target*, not a data grab. Privacy is felt (a real folder you own, one-tap export), not preached.

---

## 4. WHY US / UNFAIR ADVANTAGE — we already shipped the hard part

While competitors pitch the "memory layer" sentence pre-product, we argue from a **running product with users touching it** — the rarest thing in a YC batch full of memory-layer decks. Every capability below is shipped code, not roadmap.

| Product pillar | Already built (files) | What it means for us |
|---|---|---|
| **The Mirror (whole-person profile)** | `backend/app/profile.py`, `storage.py::personal_profile` (7-layer coverage + 0–100 readiness score + explicit `limitations`), `macos/Sources/ModelTab.swift` | The "model of you" surface OpenAI locked inside ChatGPT — we shipped it *portable and cited*. |
| **The Constellation (knowledge graph)** | `backend/app/graph_analysis.py` (deterministic, stdlib-only — god nodes, communities, bridges), `macos/Sources/MemoryMapView.swift` (78KB interactive map) | The shareable, novel artifact competitors (invisible vector stores) structurally lack. |
| **Cited Ask** | `/v1/ask`, `/v1/search`, `/v1/context` in `backend/app/main.py`; `AskTab.swift`, `CitationDisplay.swift` | Cite-or-abstain retrieval, eval-gated — the credibility floor that turns a trial into a habit. |
| **Layered, provenanced memory** | `backend/app/extractor.py` (7 memory layers + authorship gate), `provenance.py` (author_class, recomputable trust_score, HMAC-signed authorship) | Typed, atomic, trustworthy memory — not a blob. Fills the exact "shallow vendor memory" gap. |
| **The portable Vault** | `backend/app/vault.py` (1723 lines), `vault_markdown.py`, `docs/LOCAL_VAULT_FORMAT.md`; tombstones, self-contained backups, rebuild-from-vault | A memory the user owns as a folder — the Rewind-refugee's exact ask. |
| **Universal Reach** | `backend/app/mcp_tools.py` (3420 lines, `POST /mcp`), `/v1/tools/schema` (openai/anthropic/openapi), `sdk/python`, `sdk/typescript`, `extension/` (MV3), signed content-addressed context packs | Your memory works *inside* every AI app today — the real moat, and it's the hardest thing to build. *(Honest footnote: SDKs are code-complete but not yet published to PyPI/npm, and the tools-schema surface is being mirrored to the hosted API — both are packaging, not research.)* |
| **Ingestion breadth** | `backend/app/source_ingest.py` (30+ import parsers, no OAuth), `connectors/` (14 live incremental-sync clients), `oauth_broker.py`, GitHub Device Flow sign-in | Any export a user already has becomes cited memory; connect-once sources keep pulling. |

**The founder story (the earned secret — insight from building, not a market map):** *"We didn't build a memory API and go looking for users. We built a local-first personal-memory product people actually use, and because we were paranoid about our users owning their own data, we over-engineered the parts everyone else skips — a portable vault, cited provenance, a real knowledge graph, and universal outbound reach into every AI tool. Then the memory wars started, everyone got locked into a silo, and we realized we'd already built the one thing the labs structurally can't: the neutral, user-owned layer that spans all of them."*

Honest counter we state up front: this is founder-market *skill* fit; the missing piece is founder-market *demand* fit at scale — which §11 (traction plan) attacks directly. We shipped the depth; the reach/liveness/identity axis is the remaining build (§10, §14).

---

## 5. WHY WON'T OPENAI / ANTHROPIC JUST DO THIS

This is the central objection, and the answer is structural, not a feature race.

**They are financially disqualified from the actual job.** Memory is the labs' primary retention moat now that models commoditize. Analysts are explicit: *"AI companies view user memory as their primary competitive advantage… asking them to participate in portable memory is asking them to voluntarily surrender it"* (Plurality). **OpenAI will never make your memory work better inside Claude — doing so is suicide to their moat.** Anthropic's "Memory Import" *proves* the point: it pulls memory *into* Claude, it doesn't set it free. Unifying a user's context across competitors is definitionally the thing no vendor will build, because it destroys their lock-in. A neutral, user-owned layer can only be built by someone whose business model is *alignment with the user's portability* — a **who-can-build-it moat, not a feature moat.**

**They can't be local-first / user-owned.** Their entire architecture is server-side retention — that *is* the product. We win on the two axes they structurally cannot occupy: **neutrality** (cross-vendor) and **ownership** (local, portable, yours).

### Objections & answers

- **"OpenAI's Dreaming V3 already does zero-effort, high-accuracy memory for 800M people — you're redundant."** We don't compete on *better single-vendor memory*; we sit *above* all of them. The more silos the labs ship, the more cross-silo pain grows. Platform memory is our tailwind, not our tombstone. And Dreaming V3 reportedly *shrinks* the audit trail — so we differentiate *harder* over time on cited provenance.
- **"Portable memory is a fallacy — embeddings are vendor-specific" (Zep's "wallet fallacy").** Concede the weak version, win the strong version. We don't port one vendor's opaque vectors. Our portable unit is a **human-readable, cited Markdown vault + a re-embeddable source-of-truth** the user owns, re-projected into whatever model needs it. Portability of *meaning and provenance*, not of vectors. And because the memory is built from the **user's own source data** (Gmail/GitHub/notes/chat exports) and served over open MCP, **we need zero lab cooperation.**
- **"Apple/Google will do on-device memory across 2B devices."** The strongest base-rate threat. Our honest edge is *cross-vendor neutrality* — Apple's memory will be Apple-siloed too. We stay narrow on the seam none of them will sit on; we don't claim to be "the trust layer for humanity."
- **"'Own your data' is a graveyard (Solid: 8 years right and dead)."** Correct — which is why we lead with **utility** (continuity: one AI that knows you everywhere), and let ownership be *felt*, not preached. Privacy is the reassuring subtext, not the hook.

---

## 6. MARKET — TAM / SAM / SOM (with arithmetic)

**Top-of-funnel population:** ~1.8B gen-AI users; ChatGPT alone at **~900M weekly / 1B+ monthly**, 92% of the Fortune 500, **9M+ paying business users**. **60% are multi-tool** → ~1B people experience cross-tool context loss today.

**TAM (category-mature ceiling, ~2030):**
- Consumer/prosumer: if the category reaches OpenAI-scale paid conversion (**~220M people paying for AI by 2030**, The Information) at a **$60–120/yr** portable-memory attach (below the $240 flagship, since it's a companion layer): 220M × $90 = **~$20B/yr**.
- Developer/API: rides the **$47–53B agentic-AI market by 2030**; the memory slice (Mem0/Letta category) is a low-single-digit-billion line growing fast.
- Adjacent PKM pool convertible: **$4.9B by 2034** (Dataintelo).
- **Blended TAM ≈ $20–25B/yr.**

**SAM (reachable, higher-intent):** English-first prosumers/knowledge workers who already pay for AI and live in MCP-capable clients. Using the **~44M who already pay for ChatGPT** as a willingness-to-pay proxy: 44M × $90/yr = **~$4B/yr**, plus a developer-API SAM riding Mem0's proven curve. **SAM ≈ $4–6B/yr.**

**SOM (bottoms-up, Years 1–3):**
- **Year 1:** ~30–50K free installs (MCP + extension distribution — Mem0 hit 41K GitHub stars, so this order of magnitude is precedented) → ~5% paid conversion (matches ChatGPT's own WAU→paid) → **~2,000 paying × $120/yr ≈ $0.25M ARR** + a couple of team pilots.
- **Year 3:** funnel to ~500K–1M engaged users; ~5% paid consumer + a developer/API line + early team seats → 50K × $120 = $6M + $4M API/team → **~$10M ARR realistic; $15–20M if the multi-tool wedge and API attach both fire.**

**Honest framing:** ~$20–25B TAM, ~$4–6B SAM, **~$10–20M SOM by Year 3** — a strong seed→Series-A outcome. The ceiling argument is the **flywheel** (every AI tool a user adds increases the value of one portable memory), not a top-down TAM slide.

---

## 7. COMPETITION & MOAT

| Category | Players | What they are | The gap they leave open |
|---|---|---|---|
| **Vendor silos** | OpenAI, Anthropic, Google, Microsoft memory | Deep, automated, single-vendor memory | Structurally can't be cross-vendor or user-owned. **Validate the category; can't be the neutral layer.** This is our tailwind. |
| **Developer memory APIs** | Mem0 ($24M), Letta ($70M), Zep, Cognee, Supermemory | Memory-as-a-service to *app builders*; lives in *their* cloud, keyed to an app's users | The end human never owns or ports it. Different buyer (developer, not person). **Adjacent, a monetization comp, not head-to-head.** |
| **Emerging portable-memory** | Plurality/AI Context Flow, OpenMemory, MemoryLake, Memobase, browser "sync memory" extensions | Thin browser extensions syncing memory across chatbots | **Direct competitors and demand proof** — but no whole-person profile, no knowledge graph, no cited/provenanced memory, no local vault. Cortex is the *deep, owned* version of what they hack around. |
| **Note/PKM tools + AI** | Notion AI, Obsidian, Mem, Reflect, Tana | Own *authoring/organizing* your knowledge | Storage, not a memory-of-you; siloed in their app; no cross-AI outbound reach. Obsidian holds ownership, Tana holds a graph — **none hold the combination.** |
| **Consumer recall (cautionary)** | Rewind→Limitless (killed by Meta), Personal.ai | Record-everything / "model of you" | Rewind = the why-now refugee wave; Personal.ai = proof you must deliver single-player utility fast, not a persona in search of a use. |

### The moat: the stack no single competitor holds together

Six ingredients — **no competitor holds more than 2**: (1) whole-person profile, (2) auto-built knowledge graph, (3) cited/provenanced memory, (4) local-first ownership + portable Markdown vault, (5) eval-gated retrieval quality, (6) universal MCP/REST/schema/extension outbound reach. The moat is the *combination* plus the **ownership model that makes it un-copyable by lock-in-motivated incumbents.**

**Defensibility layers:**
- **User-ownership / neutrality (the who-can-build-it moat):** the labs are structurally barred (§5). The switching cost accrues to the *user*, not to us — which is the trust flywheel: users invest *because* they can leave.
- **Compounding personal corpus:** every source connected and correction made makes the memory deeper and more valuable, raising the cost of going back to re-explaining yourself.
- **Two-sided reach network:** every new AI tool that speaks MCP makes the memory more valuable (demand side); every connector added makes it deeper (supply side).
- **Distribution via MCP:** we plug into every AI client without permission — a rail that didn't exist 18 months ago, where our exact ICP already browses (439+ connectors in the Claude directory, 8M+ MCP downloads).

---

## 8. GTM

**ICP (the beachhead):** the AI-native power user / builder — founders, engineers, indie hackers, PMs, researchers who live in Cursor + Claude Code + ChatGPT + notes all day. Why them first: (1) they already live in MCP clients → **60-second install, zero behavior change**; (2) they *already hand-maintain `CLAUDE.md`/`session.md`* as the manual workaround (demand by revealed behavior); (3) they're the **burned, privacy-primed refugee cohort** (Rewind); (4) they already pay for AI tools ($8–200/mo). Honest sizing: thousands to seed — a beachhead, not the TAM. **Secondary ICP:** the "many-AI-app" knowledge worker (writer, consultant, analyst) reached via the browser extension.

**Anti-ICP (do not chase first):** enterprise security/compliance buyers, "org brain" RFPs, the receipt/attestation thesis. That drift abandons the user who touches the real product. Team is the *expand*, not the land.

**The killer wedge (one acute promise):**
> **"Never re-explain yourself again. Your memory now works in ChatGPT *and* Claude *and* Cursor — cited, private, and yours."**

The wedge is **continuity across vendors**, not privacy-as-a-cause. Privacy is the reason to *trust* it, not the reason to *install* it.

**The aha onboarding (the 10-minute first-run that makes a user tell a friend):** import ChatGPT/Claude/Obsidian in one click → **the Mirror** (profile + Constellation, live, cited, on your machine) → **the Reach** (one paste wires MCP + extension) → **the Proof** (open a fresh chat in a different tool; it already knows you). The **tell-a-friend object** is the **Constellation card** — a beautiful graph of *your* world, screenshot-native, "Spotify Wrapped for your knowledge."

**PLG loop:** Import (free, local) → Wire everywhere (free, the retention hook — once your memory is in your daily tools, ripping it out means going back to re-explaining yourself) → Show off (Constellation card → X/HN/Reddit) → Distribution re-seeds via the **MCP/Claude Connectors directory** + Chrome Web Store + privacy-community write-ups (the Rewind-refugee angle).

**Land → expand:** individual portable memory → **shared/team memory** ("your team never re-onboards a new hire or a new agent") → the **developer API + portable-memory protocol** as the default "bring your own context" layer every AI app reads.

**Pricing:** *Never charge someone to own their own data.* Core memory + vault + import + export is **free forever**; you charge for cross-tool sync, scale, sharing, and API.

| Tier | Who | Price | Included | Anchor |
|---|---|---|---|---|
| **Free (Own)** | Everyone | **$0** | Local memory, one-click imports, the Mirror + Constellation, cited Ask, portable vault, export-everything, MCP + extension to your own tools | Mem0 Hobby (free); Supermemory Free |
| **Pro (Sovereign)** | AI power user / knowledge worker | **$12/mo** ($10 annual) | Hosted cross-device + cross-tool sync, all live connectors, always-on hosted MCP endpoint, priority re-embed, larger limits | Notion Plus $12; ChatGPT Go $8; 1Password $3.99; under Mem0 $19 / Supermemory $19 |
| **Team (Shared Memory)** | Teams, agencies, startups | **$10/user/mo** (min ~3) | Shared project/team memory, who-wrote-what provenance, per-member MCP tokens, admin controls | Undercuts ChatGPT Business $20, Notion Business $24 |
| **Developer / API** | Builders embedding portable context | **$29/mo base + metered** (free dev tier ~$5) | Cortex context/memory API + OpenAI/Anthropic/OpenAPI schema + portable-memory protocol as the neutral standard | Mem0 $19/$79/$249; Supermemory $19/$100/$399 |

---

## 9. BUSINESS MODEL — how it becomes big ARR

Three revenue engines, one product:

1. **Prosumer subscription ($12/mo Pro).** The primary engine. Prices at the Notion-Plus / productivity line the ICP pays reflexively, while the free tier gives away *ownership* so nobody feels taxed for their own data. Comps prove the willingness-to-pay: Notion ($600M ARR / 4M paying), Obsidian ($25M ARR, 7 people, zero VC on a user-owns-the-files model), ChatGPT ($20/mo, 44M+ paying).
2. **Team ($10/seat).** The land→expand trigger fires the first time a user wants a *shared* memory (a project's context, a team's decisions). Deliberately undercuts ChatGPT Business / Notion Business to make individual→team frictionless. This is where retention and net-revenue-retention compound.
3. **Developer API (metered).** Builders embed portable, cited, local-optional context into their own AI apps via the API + the portable-memory protocol as the neutral "bring your own context" standard. Mirrors the Mem0/Supermemory shape a builder already recognizes, with our differentiators (cited, local-optional, user-owned) as the reason to choose it.

**Path to scale:** free ownership → $12 Pro cross-tool sync → $10/seat team memory → developer API as the standard. The compounding driver is the flywheel: value grows with every AI tool the user adds and every source connected, on a base (multi-tool AI users) that only grows.

---

## 10. ROADMAP (implementation-grade; existing product → what's next)

Grounded in the honest gap list — the remaining work is almost entirely on the **reach / liveness / identity** axis, not the depth axis (which is shipped).

**Phase 0 (0–3 mo) — Make the memory real and the aha undeniable.**
- ✅ **Real on-device embeddings — SHIPPED.** Model2Vec (potion-base-8M, 256-dim static) is implemented, bundled into every DMG/App Store build, and default-on at launch (verified in shipped 0.2.0-2: `/ready provider=model2vec`). The ownership+privacy story is technically true offline today. *Named open work: upgrade to a bge-class model for higher semantic fidelity — an improvement, not a gap.*
- **The one-import → Mirror onboarding** polished to a screenshot-worthy 10-minute first-run; the **Constellation card** shareable artifact.
- Ship the MCP + extension "wire everywhere in 60 seconds" flow as the headline path.

**Phase 1 (3–6 mo) — Liveness across tools.**
- **Bidirectional / push sync** so memory updates flow into ChatGPT/Claude/Cursor in real time (today the extension is pull/inject only, one-directional — `extension/README.md`). Add server→client SSE push.
- **Hosted multi-device sync — push half SHIPPED.** The local→hosted push loop is built, tested, and wired (`CortexPushSync.swift`, `POST /v1/sync/ingest`, `GET /v1/sync/captures`, idempotent capture upsert, device registration, `test_sync_push.py`). Remaining: the **pull leg** (hosted→second device), delete/tombstone propagation, and **end-to-end encryption of synced content** — today sync payloads are Ed25519-*signed* but stored plaintext on the hosted store, which we state plainly (§14) until E2EE ships.

**Phase 2 (6–12 mo) — Reach the rest of the multi-tool population.**
- **Mobile capture/read surface** (iOS) — a portable-memory company that "lives with you" needs at least a mobile read/capture surface (currently macOS-only, deliberately deferred in `MVP_ROADMAP.md`).
- **Durable cross-device identity** — finish required-accounts / "sign in with your context" (Sign in with Apple + GitHub Device Flow shipped; full identity is founder-blocked Phase 5). This is the beginning of *context-as-login*.
- Harden the hosted plane (refactor the ~18k-line storage god-object flagged in `CORTEX_STRATEGY.txt`).

**Phase 3 (12–24 mo) — Network effects & the standard.**
- **Teams / shared memory as a product** (protocol primitives exist in `provenance.py` + `PORTABLE_MEMORY_PROTOCOL_V2.md`; UX/invites/network effects don't) — shared project brains, new hires and agents inherit the team's context.
- **Developer platform** — the portable-memory protocol as the neutral "bring your own context" standard other apps read; SDKs + API GA.
- Inevitability: **"bring your context" becomes the primitive every AI app checks on entry** — the OAuth-of-context moment.

---

## 11. TRACTION PLAN & METRICS

**North-star metric:** **memories actively used across N different AIs per week** (weekly cross-AI recall) — the number that proves the core promise (one memory, working everywhere) is actually happening. Secondary: multi-tool activation (does an external AI visibly cite your context), 4-week retention, sources connected per user, Constellation cards shared.

**Honest current status:** the product is shipped and real (Mac App Store, ~1,845 backend tests, real connectors, cited retrieval with on-device Model2Vec embeddings default-on, Constellation, universal reach, local→hosted push sync), and the honest remaining gaps are: the pull leg of multi-device sync, E2E encryption of synced content, real-time push into tools, no mobile, no team product — and above all **no public pull-numbers and, until this build, no surface that even computed our own north-star metric.** We have the depth; the evidence loop (demo + metric + users) is the single biggest thing to close before YC — see `docs/YC_EXECUTION_PLAN.md` for the judgment and the build closing it.

**What to build + show for YC (ranked by yes-flipping power):**
1. **The 60-second cross-AI demo, live in the room:** one memory answering the same question — correctly, with citations — inside *three different tools*. This is the demoable "holy shit," not a TAM slide.
2. **A real pull number:** put the free import + MCP flow in front of 50–200 real AI-native users and walk in with *"N installs, M memories used across ≥2 AIs this week, here's the thread of someone posting their Constellation card."* A real usage curve beats fifteen sections of market math.
3. **The Mirror that lands:** the on-device-embeddings fix shipped so the profile is genuinely "it knows me," not keyword-search-in-disguise.
4. **One or two prosumer testimonials in the user's own words** — the continuity aha, felt.

---

## 12. TEAM & HIRING

**Founding team (the credibility hook):** the founders who already conceived, built, and shipped Cortex — a live local-first macOS app on the Mac App Store, a hosted backend, ~578 backend tests, real connectors, an eval-gated retrieval pipeline, a knowledge graph, and universal outbound reach. Founder-market fit is *demonstrated by the artifact*, not asserted.

**First hires (seed):**
1. **Founding engineer — sync/infra:** owns real-time bidirectional sync + hosted multi-device sync (the Phase-1 reach axis) and the storage refactor.
2. **Founding engineer — client/mobile:** owns the polished onboarding, the Constellation card, and the iOS read/capture surface.
3. **ML/retrieval engineer:** owns on-device embeddings, eval-gated retrieval quality, and the "sounds-like-me" fidelity bar.
4. **Growth/DevRel:** owns the MCP-directory + browser-extension + privacy-community distribution loops and the developer API on-ramp.

Deliberately lean and product-heavy; no enterprise sales motion until team/expand is validated.

---

## 13. FUNDRAISING (YC + seed)

**Round:** YC + a seed to fund the reach/liveness/identity build and the growth loop.

**Use of funds:**
- On-device embeddings + polished aha onboarding (Phase 0).
- Real-time bidirectional sync + hosted multi-device sync + storage refactor (Phase 1).
- Mobile + durable identity (Phase 2).
- Growth: MCP-directory presence, browser-extension distribution, the Constellation-card viral loop.

**Milestones the money buys:**
- **0–6 mo:** the Mirror is real (on-device embeddings), the 60-second cross-AI demo is live, first public pull number (installs + cross-AI weekly recall), hosted multi-device sync shipped.
- **6–12 mo:** mobile read surface, durable cross-device identity, first Pro revenue, first team pilots.
- **12–24 mo:** team/shared-memory product, developer API GA, portable-memory protocol as an emerging standard; the flywheel (cross-AI recall per user) trending up.

Honest scale statement: this is a seed→Series-A trajectory to ~$10–20M ARR by Year 3; the fund-returner case rests on the flywheel and the endgame (context-as-login for the agent era), not a top-down TAM.

---

## 14. RISKS & HONEST MITIGATIONS

- **Platform absorption (OpenAI/Anthropic/Apple extend "remember me").** The #1 risk. *Mitigation:* structural — they're financially disqualified from cross-vendor neutrality and architecturally disqualified from local-first ownership (§5). Every silo they ship grows the cross-silo pain we solve. We stay narrow on the seam none of them will sit on.
- **"Feature, not a company."** *Mitigation:* the wedge (paste-and-inject continuity) is a feature; the company is the *owned, cited, whole-person model + graph + universal reach + portable vault* — a stack no one holds together, on an ownership model incumbents can't copy. The switching cost (users invest because they can leave) is the trust flywheel.
- **Privacy/trust as the pitch (the Solid graveyard).** *Mitigation:* lead with **utility/continuity**, not "own your data." Ownership is felt (a folder, an export button, "works with the plug pulled"), never preached.
- **"A model of you does nothing on day one" (the Personal.ai failure).** *Mitigation:* the aha is single-player-useful *immediately* — the Mirror is a screenshot-worthy "it knows me" moment and continuity is felt inside the tools you already use, in 10 minutes.
- **Retention / is it a vitamin.** *Mitigation:* the retention hook is that once your memory is wired into your daily tools, ripping it out means going back to re-explaining yourself — and the corpus compounds. North-star is cross-AI weekly recall, not novelty.
- **"Portability is a fallacy" (Zep).** *Mitigation:* we port human-readable, cited Markdown + a re-embeddable source-of-truth, not vendor vectors; built from the user's own data over open MCP, needing zero lab cooperation (§5).
- **Depth-shipped, reach-unbuilt.** *Mitigation:* named plainly (§10/§11). The remaining work (bidirectional sync, pull-leg multi-device sync, mobile) is engineering, not research — sequenced Phase 0–2, with on-device embeddings and push-sync already shipped.
- **The hosted-sync privacy gap (we name it before a journalist does).** Synced captures are currently stored *plaintext* on our hosted store — portable bundles are Ed25519-**signed** for integrity, not encrypted — and local purges don't yet propagate to the hosted copy or other devices. This is the one place the ownership pitch breaks today. *Mitigation:* stated honestly in-product and here; E2E encryption of sync payloads (per-user key, age-style) and tombstone propagation are the first post-wave engineering items (`docs/YC_EXECUTION_PLAN.md` §2); until then, sync is opt-in and local-only mode loses nothing.

---

## 15. YC APPLICATION ANSWERS (draft)

**What do you make?**
Cortex is a private, portable memory of who you are — your projects, people, decisions, and style — that you own on your own machine and that works inside every AI tool (ChatGPT, Claude, Cursor, any agent via MCP). One import, and every AI you use already knows you, cited and yours. Live on the Mac App Store today.

**Why you / domain expertise?**
We already built and shipped it: a local-first macOS app, a hosted backend, ~578 backend tests, real connectors, a cited retrieval engine, a knowledge graph, and universal outbound reach into every AI app. We over-built the hard parts — a portable vault, cited provenance, cross-AI reach — because we were building for users who own their own data, and then the memory wars made that exact stack the thing the labs structurally can't build.

**Competitors, and what do you understand that they don't?**
Vendor memory (OpenAI/Anthropic/Google) validates the category but is siloed by design. Developer memory APIs (Mem0, Letta, Zep) are plumbing for *app builders* — the memory lives in their cloud, the user never owns it. Thin portable-memory extensions (Plurality, OpenMemory) prove the demand but have no whole-person profile, graph, cited memory, or local vault. What we understand: the durable product isn't "better memory" — it's the **neutral, user-owned layer above all of them**, which the labs are financially barred from building and the API players aim at the wrong customer.

**How do you make money, and how big?**
Free to own your memory; **$12/mo Pro** for cross-tool + cross-device sync; **$10/seat** team memory; a metered developer API. Comps: Notion ($600M ARR), Obsidian ($25M ARR on user-owns-the-files), Mem0 ($24M raised). TAM ~$20–25B, SAM ~$4–6B, realistic ~$10–20M ARR by Year 3, with a flywheel where value compounds with every AI tool the user adds.

**Why now?**
In the last 12 months: every lab shipped memory (validated) but siloed (the gap); MCP became the universal distribution rail (technically distributable now); Rewind/Limitless was killed by Meta (a live refugee cohort of privacy-primed users); and agents now need a portable model of the user to act. The pain (fragmentation) and the enabling standard (MCP) arrived together.

**Progress so far?**
Shipped: Mac App Store app, hosted backend, 30+ import parsers + 14 live connectors, cited Ask with on-device embeddings (default-on, no cloud key), a whole-person profile, an interactive knowledge graph, MCP + OpenAI/Anthropic/OpenAPI schemas + SDKs + browser extension, a portable Markdown vault, and local→hosted push sync. Next: the cross-AI usage meter + live demo + share card (in build), pull-leg multi-device sync, E2E-encrypted sync, mobile.

**One-line summary.**
1Password for your AI memory — the private, portable model of who you are that you own and that works in every AI.

---

## 16. NAMING & BRAND

**Does "Cortex/Doppl" fit?** Partially. "Cortex" is evocative of a personal brain/memory and is already the shipped app name — but it collides with *Palo Alto Cortex XDR* (a damaging security adjacency) and reads slightly clinical for a mass-market consumer product. "Doppl" (the Mac App Store name) leans "digital double," which risks the exact "digital twin / it IS you" overclaim we avoid. For the power-user beachhead, **keeping Cortex is fine**; for a mainstream consumer expansion, consider a warmer, ownership-forward name.

**Candidate names for a user-owned portable-memory product:**
- **Keep** — "your memory, kept" — ownership-forward, warm, verb-native ("keep this in Keep").
- **Mem** *(taken by a notes app — avoid)*; instead **Kith** — "your people and context," intimate, memorable.
- **Loom** *(taken)*; instead **Warp/Weave** family → **Weft** — "the thread that runs through everything you do."
- **Anchor** — "your context, anchored to you, not a vendor" — stability + ownership.
- **Ferry / Carry** — "the memory you carry between AIs" — names the portability directly.

**Brand posture:** utility-first and ownership-felt. Lead with continuity ("one AI that knows you everywhere"), let ownership and privacy be the reassuring subtext. Anti-companion, anti-"digital twin" — always "the model *of you* they can't take, portable across any AI," never "the AI they can't switch off." (Naming is a footnote, not a milestone — don't spend rebranding energy before the aha and the first pull-number land.)

---

**The through-line a partner buys in ten seconds:** *Everyone has a memory in every AI now, and every one is a walled garden they don't own. We already shipped the neutral, user-owned memory that works in all of them — cited, private, portable — and the labs structurally can't follow us there. We start with the power users who feel the pain daily, and expand to everyone who touches more than one AI, as context becomes the login for the agent era.*
