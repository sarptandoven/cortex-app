# The 60-Second Cross-AI Demo — Runbook

**The point:** one local memory, three different AI vendors, the same cited answers.
Claude Desktop, Cursor, and ChatGPT (via the Cortex browser extension) all answer from
the SAME Cortex memory running on this laptop — and every answer carries citations into
the user's own notes. That is the yes-flipper: memory is the layer, not the chatbot.

Everything below is driven by `scripts/demo_seed.py`. Rehearse headlessly any time with
`python3 scripts/demo_seed.py --self-test` (boots a scratch server on a spare port,
simulates all three clients over their real transports, prints the same pass table).

---

## 15-second cold open (say this, verbatim-able)

> "Every AI app you use starts from zero — you re-explain yourself to Claude, to Cursor,
> to ChatGPT, every day. This Mac has one memory. It's called Cortex, it runs locally,
> and I'm going to ask three different companies' AIs about my life. Watch two things:
> they all know the same me — and every answer shows its receipts."

## The 60 seconds

| Clock | Client | Ask | What the audience sees | Callout |
|---|---|---|---|---|
| 0:00–0:15 | — | (cold open above) | Cortex menu-bar app / graph on screen for two beats | "One memory. Local. Mine." |
| 0:15–0:30 | **Claude Desktop** | "What did I decide about the vector database, and why?" | The specific decision: pgvector over Pinecone, ~70% infra saving, Priya's June 12 latency bakeoff — with citations | Point at the citation: "that's my own note, not a system prompt" |
| 0:30–0:45 | **Cursor** | "Before I write any code — search my memory for the Meridian Freight pilot. What does Tom Okafor need?" | SAML SSO before their August security review, prioritized over mobile | "Different company's product. Same memory. Zero re-teaching." |
| 0:45–1:00 | **ChatGPT (extension)** | "What do you know about me?" | Founder profile: Driftline, Priya/Jonas/Elena, Tuesday-only deploys, decision-first writing style, marathon training | Close: "Three vendors, one memory, citations everywhere — and it never left this laptop." |

**The citation callout is the demo.** Say the word "citation" out loud at least twice and
point at a cited source title (e.g. *"Vector store decision for Atlas — notes"*). Cited
recall is what separates this from a canned system-prompt trick.

## Pre-demo checklist (T-minus 15 minutes)

1. **Cortex app running** (menu bar icon; backend on `127.0.0.1:8766`).
2. **Seed:** `python3 scripts/demo_seed.py --api-key <key>`
   (key: Cortex → Connections & Privacy → Advanced Setup). Wait for **`DEMO READY`** —
   the script probes the headline question and confirms it comes back cited.
3. **Paste configs** from `~/cortex-demo-kit/` (each client gets its OWN labeled token —
   that's how the verify table attributes reads per client):
   - `claude_desktop.mcp.json` → merge into `~/Library/Application Support/Claude/claude_desktop_config.json`, then fully quit + relaunch Claude Desktop.
   - `cursor.mcp.json` → merge into `~/.cursor/mcp.json` (or Cursor Settings → MCP).
   - `chatgpt_extension_pairing.txt` → follow it (extension Options → paste Base URL + token → Test connection), then open chatgpt.com.
   - Note: paste from the demo kit, not from the app's own copy buttons — seeding
     (re)registers the tokens under these three labels.
4. **Warm each client once** with a throwaway question ("when do we deploy?") so MCP
   handshakes/tool discovery are done before the room.
5. **Prove it:** `python3 scripts/demo_seed.py --verify --api-key <key>` → all three ✅:
   `Claude Desktop ✅ 2 reads · Cursor ✅ 1 read · ChatGPT (extension) ✅ 1 read`
6. **Screen layout:** Claude Desktop left half, Cursor right half, browser (ChatGPT tab)
   ready behind Claude. Do Not Disturb ON. Font sizes up.

## Failure recovery

- **A client hangs mid-demo** → don't debug on stage. Jump to the next client with the
  next question from the bank; the story lands with two clients. Return only if time allows.
- **Claude Desktop lost its MCP connection** → fastest fix is quit + relaunch (it
  re-spawns the stdio bridge). Meanwhile, ask its question in Cursor — same memory,
  the punchline survives.
- **An answer comes back thin** → re-ask from the seeded question bank below; each maps
  1:1 to a seeded note.
- **Suspect the backend** → `python3 scripts/demo_seed.py --verify --api-key <key>` shows
  in five seconds which client is dark; the other two carry the demo.
- **Total meltdown** → `--self-test` output (run the night before, keep a screenshot) is
  the honest fallback artifact: all three clients simulated over real transports, passing.

## Seeded question bank (all produce cited answers)

1. What did I decide about the vector database, and why? *(pgvector decision + bakeoff)*
2. Search my memory for the Meridian Freight pilot — what does Tom Okafor need? *(SSO/August)*
3. What do you know about me? *(founder profile synthesis)*
4. How should I write the investor update for Elena? *(Monday cadence, NRR focus, decision-first style)*
5. Who is Priya and what does she care about? *(infra owner, Go, no premature microservices)*
6. When do we ship deploys? *(Tuesdays, green reliability report)*
7. What did I decide about hiring a sales lead? *(not before $40k MRR, founder-led)*
8. What are Driftline's latest metrics? *(14 design partners, $18k MRR, 128% NRR)*

## Persona (what's in the vault)

Maya Chen, founder & CEO of Driftline (AI logistics copilot): 12 deterministic captures —
2 product decisions (pgvector, usage-based pricing), a pilot commitment (Meridian/SSO),
3 people (Priya, Jonas, Elena/Northlight), ops rules (Tuesday deploys, deep-work block),
writing style, June metrics, hiring policy, and personal texture (Berlin Marathon,
vegetarian, espresso). Seeded through the real capture → extract → review pipeline, so
profile, graph, and citations are real — not fixtures.
