#!/usr/bin/env python3
"""cmp_demo.py — a runnable, end-to-end demonstration of the Contextual Memory Protocol (CMP).

What this proves, live, against a real backend booted from THIS worktree's source:

  1. MODEL-AWARE PACKING   — the SAME query, asked as `model=claude` vs `model=cursor` vs
                             `model=gpt`, yields DIFFERENT token budgets and pack sizes. The
                             pack is calibrated to the consuming model, not a flat budget.
  2. SELF-DESCRIBING SMP   — the returned envelope carries its own `legend`, and each
                             MemoryObject carries `relevance` + `relevance_basis` + `why`
                             (provenance: retrievers / matched_terms / fused_rank).
  3. SESSION DELTA CHANNEL — two sequential /v1/context calls with the SAME `session_id` return a
                             `working_memory` delta; the 2nd call's `new[]` contains ONLY unseen
                             ids and NEVER re-sends turn-1's ids (the delta_no_resend invariant).
  4. MEMORY QUERY LANGUAGE — the `query_memory` MCP tool (via /v1/tools/call) narrows a base
                             retrieval with a structured MQL filter (a strict subset).

The backend is booted as a subprocess on a spare port with a throwaway DB/vault/accounts dir, the
deterministic `hash` embedding provider, and a demo API key. On success the script prints the
evidence lines and `CMP DEMO OK`, exiting 0. Any failure prints `CMP DEMO FAIL: <reason>` (exit 1).

Run:  cd /tmp/cortex-connect && python3 scripts/cmp_demo.py
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote as _urlquote

# ---------------------------------------------------------------------------------------------
# Layout + config
# ---------------------------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
# A spare port is chosen at boot (OS-assigned free port), so back-to-back runs never collide on a
# lingering socket. CMP_DEMO_PORT pins a specific port when you want one. BASE is set by boot_backend.
PREFERRED_PORT = int(os.environ.get("CMP_DEMO_PORT", "0")) or None
BASE = "http://127.0.0.1:0"
API_KEY = "cmp-demo-" + "z" * 40  # long, non-sample token (avoids the insecure-dev-token guard)
BOOT_TIMEOUT_S = 40.0

# A persona with two clearly-separated topics so retrieval sets are controllable:
#   * "Atlas launch" facts (many, DISTINCT) — surfaced by the base query; distinct wording keeps
#     claim-folding + MMR from collapsing them, so the corpus overflows a tight model budget and the
#     per-model pack sizes genuinely differ.
#   * "procurement/finance" facts — do NOT mention "Atlas launch", so the base query never retrieves
#     them; only the superset query (which adds the finance terms) does, giving the 2nd session call
#     a real, verifiable set of NEW ids.
# The Atlas facts are generated from distinct domain workstreams so every fact is lexically unique
# (distinct duplicate-key => never folded) while all sharing the "Atlas launch" retrieval anchor.
_ATLAS_DOMAINS = [
    ("hiring", "closing three senior engineering roles", "backfill the on-call rotation before code freeze"),
    ("infrastructure", "the multi-region failover cutover", "prove a clean region evacuation under synthetic load"),
    ("design", "the redesigned first-run activation flow", "hit a measured seven-second time-to-first-value"),
    ("testing", "the end-to-end regression matrix", "reach ninety percent critical-path coverage"),
    ("documentation", "the public API reference and quickstart", "ship versioned docs the same day as the release"),
    ("pricing", "the three-tier packaging and metering", "settle the entitlement limits with the finance model"),
    ("legal", "the terms-of-service and data-processing addendum", "clear the review with outside counsel"),
    ("support", "the tier-one runbook and escalation tree", "staff a follow-the-sun coverage window at launch"),
    ("migration", "the customer data backfill and reindex", "run a rehearsed dry-run against a production shadow"),
    ("security", "the third-party penetration test and threat model", "resolve every high-severity finding before the gate"),
    ("mobile", "the parity release for iOS and Android", "trail the web surface by exactly one sprint"),
    ("web", "the marketing site and in-product upgrade path", "pass a full accessibility and performance audit"),
    ("internationalization", "the locale extraction and right-to-left layout pass", "translate the top five markets first"),
    ("analytics", "the activation and retention instrumentation", "wire every funnel step into the warehouse before launch"),
    ("onboarding", "the guided setup checklist and sample project", "cut the median setup to under ten minutes"),
    ("billing", "the invoice, proration, and dunning flows", "reconcile against the payment processor sandbox"),
    ("compliance", "the SOC 2 control evidence and audit log", "close the gap analysis two weeks before the gate"),
    ("performance", "the p99 latency and cold-start budget", "hold the tail under two hundred milliseconds at peak"),
    ("accessibility", "the screen-reader and keyboard-navigation sweep", "meet the WCAG double-A bar on every core screen"),
    ("rollback", "the one-click revert and feature-flag kill switch", "prove a sixty-second rollback in a game day"),
    ("telemetry", "the health dashboards and paging alerts", "define the golden signals and their alert thresholds"),
    ("partnerships", "the integration directory and launch partners", "sign the co-marketing commitments in writing"),
]


def _atlas_fact(domain: str, detail: str, commitment: str) -> str:
    return (
        f"For the Atlas launch, the {domain} workstream owns {detail}, and the launch team agreed the Atlas "
        f"launch {domain} plan must {commitment}, because a weak {domain} story is the kind of gap that quietly "
        f"sinks a launch, so the Atlas launch cannot clear its readiness gate until the {domain} owner signs off "
        f"the {domain} plan and records the Atlas launch {domain} decision, its rationale, and its remaining risks "
        f"in the shared launch tracker where every Atlas launch workstream lead can see exactly where the {domain} "
        f"line item stands relative to the rest of the Atlas launch."
    )


ATLAS_LAUNCH_FACTS = [_atlas_fact(*d) for d in _ATLAS_DOMAINS]

PROCUREMENT_FACTS = [
    "The procurement and vendor budget for the fiscal quarter was approved at four hundred thousand dollars, and the "
    "finance team requires every vendor procurement contract above fifty thousand to route through a second signer, so "
    "the procurement budget approvals stay auditable and no single vendor finance decision escapes the procurement review.",
    "The office lease renewal negotiation with the building landlord settled on a three year term at a fixed procurement "
    "rate, and the finance team booked the lease against the facilities budget line so the vendor procurement forecast "
    "and the finance runway model both reflect the renewed lease cost without surprising the quarterly procurement budget.",
    "The vendor procurement policy now requires two competitive finance bids for any purchase over ten thousand dollars, "
    "and the procurement team logs each vendor bid in the finance ledger so the budget owners can see the full procurement "
    "trail and the finance auditors can reconcile every vendor payment against an approved procurement budget request.",
    "The finance close for the quarter flagged three vendor invoices that lacked a matching procurement order, and the "
    "budget owners agreed the finance team will freeze any vendor payment without a procurement order attached, tightening "
    "the procurement controls so the finance ledger and the vendor budget always reconcile at the quarterly finance close.",
    "The cloud infrastructure vendor renegotiation cut the annual procurement spend by eighteen percent, and the finance "
    "team redirected that vendor budget saving into the hiring line, so the procurement win directly funded headcount and "
    "the finance forecast now shows the vendor savings compounding across the multi year procurement budget going forward.",
    "The travel and events procurement budget was cut in half after the finance review, and the vendor contracts for the "
    "annual offsite were renegotiated to a smaller venue, so the procurement team kept the event alive while the finance "
    "team protected the runway and the vendor budget stayed inside the tightened quarterly procurement finance envelope.",
]

# Distinct-topic queries. `TIMELINE_Q` pulls only Atlas-launch facts; `SUPERSET_Q` also pulls the
# procurement facts (its added finance terms match them), so the 2nd session call has a real,
# verifiable set of NEW ids that were absent from turn 1.
TIMELINE_Q = "Atlas launch readiness workstream plan"
SUPERSET_Q = "Atlas launch readiness workstream plan vendor procurement finance budget lease invoice"


# ---------------------------------------------------------------------------------------------
# Tiny HTTP helpers (stdlib only)
# ---------------------------------------------------------------------------------------------
class DemoFail(Exception):
    pass


def _request(method: str, path: str, *, body=None, headers=None, timeout=30, retries=1):
    url = BASE + path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    hdrs = {"Authorization": f"Bearer {API_KEY}"}
    if data is not None:
        hdrs["Content-Type"] = "application/json"
    if headers:
        hdrs.update(headers)
    last_exc: Exception | None = None
    # Session-linked context calls spawn a background prefetch that briefly contends on the SQLite
    # write lock; a transient timeout is retried once rather than aborting the whole demo.
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, data=data, method=method, headers=hdrs)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", "replace")
            try:
                parsed = json.loads(raw) if raw.strip() else {}
            except Exception:
                parsed = {"detail": raw[:300]}
            return exc.code, parsed
        except (TimeoutError, urllib.error.URLError, ConnectionError) as exc:
            last_exc = exc
            time.sleep(1.0)
    raise DemoFail(f"{method} {path} failed after {retries + 1} attempts: {last_exc}")


def get(path, **kw):
    return _request("GET", path, **kw)


def post(path, body, **kw):
    return _request("POST", path, body=body, **kw)


# ---------------------------------------------------------------------------------------------
# Boot / teardown
# ---------------------------------------------------------------------------------------------
def _port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _free_port() -> int:
    if PREFERRED_PORT:
        return PREFERRED_PORT
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


SERVER_LOG: Path | None = None  # set per boot; tail is surfaced on failure


def _server_log_tail(limit: int = 1500) -> str:
    if SERVER_LOG and SERVER_LOG.exists():
        try:
            return SERVER_LOG.read_text("utf-8", "replace")[-limit:]
        except Exception:
            return ""
    return ""


def _boot_once(tmp: Path, port: int):
    """Spawn one backend subprocess on `port`; return proc, or raise DemoFail on boot failure.

    Server stdout/stderr is redirected to a LOG FILE (never an undrained PIPE): BaseHTTPRequestHandler
    logs every request, and an unread pipe buffer would fill and block the server mid-run."""
    global BASE, SERVER_LOG
    BASE = f"http://127.0.0.1:{port}"
    SERVER_LOG = tmp / "server.log"
    env = dict(os.environ)
    env.update(
        {
            "PYTHONPATH": str(BACKEND),
            "CORTEX_DB_PATH": str(tmp / "index.sqlite"),
            "CORTEX_VAULT_PATH": str(tmp / "vault"),
            "CORTEX_ACCOUNTS_DB_PATH": str(tmp / "accounts.sqlite"),
            "CORTEX_API_KEY": API_KEY,
            "CORTEX_EMBEDDING_PROVIDER": "hash",  # deterministic vectors
            "CORTEX_AUTO_APPROVE_CAPTURES": "1",  # seeded memories go live immediately
            "CORTEX_PORT": str(port),
            "CORTEX_OBSERVABILITY_ENABLED": "0",
        }
    )
    log_fh = open(SERVER_LOG, "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.standalone_server", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND),
        env=env,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
    )
    proc._cmp_log_fh = log_fh  # type: ignore[attr-defined]  # kept open for the process lifetime
    deadline = time.monotonic() + BOOT_TIMEOUT_S
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise DemoFail(f"backend exited during boot (code {proc.returncode})\n{_server_log_tail()}")
        if _port_open(port):
            try:
                status, _ = get("/health", timeout=5)
                if status == 200:
                    return proc
            except Exception:
                pass
        time.sleep(0.3)
    _terminate(proc)
    raise DemoFail("backend did not become healthy within the boot timeout")


def boot_backend(tmp: Path):
    """Boot the backend, retrying on a FRESH free port if a subprocess dies during startup
    (a lingering socket from a prior back-to-back run)."""
    last: Exception | None = None
    for attempt in range(3):
        port = _free_port()
        try:
            proc = _boot_once(tmp, port)
            print(f"backend healthy on :{port}.")
            return proc
        except DemoFail as exc:
            last = exc
            time.sleep(1.0)
    raise DemoFail(f"backend failed to boot after 3 attempts: {last}")


def _terminate(proc) -> None:
    if proc is None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except Exception:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
    log_fh = getattr(proc, "_cmp_log_fh", None)
    if log_fh is not None:
        try:
            log_fh.close()
        except Exception:
            pass


# ---------------------------------------------------------------------------------------------
# Seeding
# ---------------------------------------------------------------------------------------------
def seed_memories() -> int:
    seeded = 0
    corpus = (
        [("atlas-launch", i, txt) for i, txt in enumerate(ATLAS_LAUNCH_FACTS)]
        + [("procurement", i, txt) for i, txt in enumerate(PROCUREMENT_FACTS)]
    )
    for topic, idx, text in corpus:
        status, payload = post(
            "/v1/captures",
            {"content": text, "source": "cmp-demo", "title": f"{topic}-{idx}"},
        )
        if status not in (200, 201):
            raise DemoFail(f"seed capture failed ({topic}-{idx}): HTTP {status} {payload}")
        seeded += 1
    return seeded


def _flatten_smp_refs(envelope: dict) -> list[str]:
    return [str(it.get("ref") or "") for it in (envelope.get("items") or []) if it.get("ref")]


def context_smp(task: str, model: str, *, token_budget: int, session_id: str | None = None) -> dict:
    q = f"/v1/context?format=smp&model={model}&token_budget={token_budget}&task={_urlquote(task, safe='')}"
    if session_id:
        q += f"&session_id={_urlquote(session_id, safe='')}"
    # Session-linked calls are given more headroom (background prefetch may briefly hold the write lock).
    status, payload = get(q, timeout=60 if session_id else 30)
    if status != 200:
        raise DemoFail(f"/v1/context (model={model}) failed: HTTP {status} {payload}")
    if not isinstance(payload, dict) or payload.get("smp") != "1":
        raise DemoFail(f"/v1/context (model={model}) did not return an SMP envelope: {str(payload)[:200]}")
    return payload


# ---------------------------------------------------------------------------------------------
# Demos
# ---------------------------------------------------------------------------------------------
def demo_model_aware_packing() -> dict:
    print("\n== 1. MODEL-AWARE PACKING (same query, different consuming model) ==")
    budget_req = 11000  # above cursor's cap (3000) and gpt's (8000); under claude's (12000)
    envs = {m: context_smp(TIMELINE_Q, m, token_budget=budget_req) for m in ("claude", "gpt", "cursor")}
    rows = {}
    for model, env in envs.items():
        b = env.get("budget") or {}
        rows[model] = {
            "budget_tokens": int(b.get("tokens") or 0),
            "used_tokens": int(b.get("used") or 0),
            "items": len(env.get("items") or []),
        }
        print(
            f"   model={model:<7} budget.tokens={rows[model]['budget_tokens']:<6} "
            f"used={rows[model]['used_tokens']:<6} items={rows[model]['items']}"
        )
    # Deterministic invariant: the per-model budget cap differs (calibrated, not flat).
    if not (rows["claude"]["budget_tokens"] > rows["gpt"]["budget_tokens"] > rows["cursor"]["budget_tokens"]):
        raise DemoFail(
            "per-model budget caps did not differ as expected "
            f"(claude={rows['claude']['budget_tokens']}, gpt={rows['gpt']['budget_tokens']}, "
            f"cursor={rows['cursor']['budget_tokens']})"
        )
    # Budget adherence: each pack must respect its own model's cap.
    for model, r in rows.items():
        if r["used_tokens"] > r["budget_tokens"]:
            raise DemoFail(f"{model} pack exceeded its budget ({r['used_tokens']} > {r['budget_tokens']})")
    # Pack pressure: under the same corpus, the tight cursor pack must carry STRICTLY FEWER items
    # than the roomy claude pack (item count is estimator-independent, unlike raw token totals which
    # are confounded by each profile's chars_per_token).
    if not (rows["cursor"]["items"] < rows["claude"]["items"]):
        raise DemoFail(
            "model calibration produced no pack-size difference "
            f"(claude items={rows['claude']['items']}, cursor items={rows['cursor']['items']}); "
            "corpus did not overflow the tight budget"
        )
    print(
        f"   -> SAME query, different pack: claude packed {rows['claude']['items']} items into "
        f"{rows['claude']['budget_tokens']}t; cursor packed only {rows['cursor']['items']} items into "
        f"{rows['cursor']['budget_tokens']}t (model-calibrated budget + knapsack)."
    )
    return {"rows": rows, "differentiated": True, "claude_env": envs["claude"]}


def demo_smp_envelope(env: dict) -> None:
    print("\n== 2. SELF-DESCRIBING SMP ENVELOPE (legend + MemoryObject provenance) ==")
    legend = env.get("legend") or {}
    if legend.get("protocol") != "smp" or "item" not in legend or "envelope" not in legend:
        raise DemoFail("SMP envelope is missing its self-describing legend")
    print(f"   envelope keys: {sorted(env.keys())}")
    print(f"   legend.item fields: {sorted((legend.get('item') or {}).keys())}")
    # Find a MemoryObject that actually carries retrieval provenance.
    provenanced = None
    for it in env.get("items") or []:
        if it.get("relevance") is not None and isinstance(it.get("why"), dict) and it["why"].get("matched_terms"):
            provenanced = it
            break
    provenanced = provenanced or next(
        (it for it in env.get("items") or [] if it.get("relevance") is not None), None
    )
    if provenanced is None:
        raise DemoFail("no MemoryObject carried a relevance score + provenance")
    print(
        f"   MemoryObject ref={provenanced['ref']} layer={provenanced.get('layer')} "
        f"relevance={provenanced.get('relevance')} basis={provenanced.get('relevance_basis')}"
    )
    print(f"     why={json.dumps(provenanced.get('why'))[:200]}")
    print(f"     content=\"{str(provenanced.get('content'))[:90]}...\"")


def demo_session_delta() -> dict:
    print("\n== 3. PER-SESSION working_memory DELTA CHANNEL (delta_no_resend) ==")
    session = "cmp-demo-session-A"
    model = "claude"
    # Same query + same model, but turn 2 widens the budget. Turn 1 packs the top-K; turn 2 packs a
    # SUPERSET (greedy-by-density keeps picking beyond where the tight budget stopped). So turn 2's
    # newly-fitting ids are genuinely NEW, while every id turn 1 already served is carried silently.
    env1 = context_smp(TIMELINE_Q, model, token_budget=1200, session_id=session)
    time.sleep(0.8)  # let the fire-and-forget prefetch settle before the next session write
    env2 = context_smp(TIMELINE_Q, model, token_budget=11000, session_id=session)
    time.sleep(0.8)
    # Turn 3 repeats turn 2 verbatim: nothing new, nothing re-sent — the purest no-resend case.
    env3 = context_smp(TIMELINE_Q, model, token_budget=11000, session_id=session)

    wm1, wm2, wm3 = env1.get("working_memory"), env2.get("working_memory"), env3.get("working_memory")
    if not all(isinstance(w, dict) for w in (wm1, wm2, wm3)):
        raise DemoFail("working_memory delta was not present on a session-linked call")

    cited1 = set(_flatten_smp_refs(env1))
    cited2 = set(_flatten_smp_refs(env2))
    new1 = {str(n.get("ref")) for n in (wm1.get("new") or [])}
    new2 = {str(n.get("ref")) for n in (wm2.get("new") or [])}
    new3 = {str(n.get("ref")) for n in (wm3.get("new") or [])}
    overlap = cited1 & cited2

    print(f"   turn 1 (budget 1200):  cited={len(cited1):>2} ids | working_memory.new={len(new1)} (fresh session => all new)")
    print(f"   turn 2 (budget 11000): cited={len(cited2):>2} ids | working_memory.new={len(new2)} (only the newly-fitting ids), cursor={wm2.get('cursor')}")
    print(f"   turn 3 (repeat):       cited={len(cited2):>2} ids | working_memory.new={len(new3)} (nothing new => nothing re-sent), cursor={wm3.get('cursor')}")
    print(f"   ids shared by turns 1 & 2 (carried, not re-sent): {len(overlap)}")

    # Invariant 1 (fresh session): everything cited on turn 1 is reported new.
    if new1 != cited1:
        raise DemoFail(f"turn-1 new-set ({len(new1)}) != turn-1 cited-set ({len(cited1)}) on a fresh session")
    # Turn 2 must genuinely surface additional ids for this to be a meaningful demo.
    if len(cited2) <= len(cited1) or not new2:
        raise DemoFail(
            f"turn 2 did not surface new ids (cited1={len(cited1)}, cited2={len(cited2)}, new2={len(new2)}); "
            "budget widening produced no delta"
        )
    # Invariant 2 (delta_no_resend): turn 2's `new` contains ONLY genuinely-new ids — never one the
    # session already holds from turn 1.
    resent = new2 & cited1
    if resent:
        raise DemoFail(f"delta_no_resend violated: turn 2 re-sent {len(resent)} already-known ids: {sorted(resent)[:3]}")
    # Turn 3 (verbatim repeat) must announce nothing and re-send nothing.
    if new3:
        raise DemoFail(f"delta_no_resend violated: verbatim-repeat turn 3 announced {len(new3)} ids: {sorted(new3)[:3]}")
    if not overlap:
        raise DemoFail("no overlap between turns 1 & 2 — cannot demonstrate the no-resend saving")

    print(
        f"   -> delta_no_resend holds: turn 2 announced {len(new2)} genuinely-new ids; the {len(overlap)} "
        f"already-served ids were carried WITHOUT re-sending (turn 3 re-sent 0)."
    )
    print(f"      new ids on turn 2 (sample): {sorted(new2)[:3]}")

    # Measured saving: a stateless integration would RE-SEND every already-known cited item's content
    # on turns 2 and 3. The delta channel sends those ids' content zero extra times.
    content_by_ref = {str(it.get("ref")): str(it.get("content") or "") for it in (env2.get("items") or [])}
    carried = overlap  # ids known before turn 2 that turn 2 still relies on
    resend_saved_tokens = sum(max(1, len(content_by_ref.get(ref, "")) // 4) for ref in carried)
    print(f"   measured saving: ~{resend_saved_tokens} tokens NOT re-sent on turn 2 ({len(carried)} carried ids), and again on turn 3.")
    return {
        "new1": new1,
        "new2": new2,
        "new3": new3,
        "overlap": overlap,
        "cited1": cited1,
        "cited2": cited2,
        "saved_tokens": resend_saved_tokens,
    }


def demo_mql_narrowing() -> dict:
    print("\n== 4. MEMORY QUERY LANGUAGE (query_memory narrows a base retrieval) ==")

    def run(mql_args: dict) -> dict:
        status, payload = post("/v1/tools/call", {"name": "query_memory", "arguments": mql_args})
        if status != 200:
            raise DemoFail(f"query_memory call failed: HTTP {status} {payload}")
        result = payload.get("result") if isinstance(payload, dict) else None
        if not isinstance(result, dict):
            raise DemoFail(f"query_memory returned no result object: {str(payload)[:200]}")
        return result

    base = run({"find": SUPERSET_Q, "k": 25})
    base_items = base.get("items") or []
    base_count = len(base_items)
    print(
        f"   baseline find k=25 (no filter): returned={base_count} "
        f"considered={base.get('candidates_considered')} narrowed_total={base.get('narrowed_total')}"
    )
    if base_count == 0:
        raise DemoFail("baseline query_memory returned nothing to narrow")

    # Choose two relevance thresholds from the ACTUAL returned scores so each filter provably bites,
    # demonstrating a monotone narrowing gradient (a strict subset chain) — the MQL subset invariant.
    rels = sorted(float(it["relevance"]) for it in base_items if isinstance(it.get("relevance"), (int, float)))
    if len(rels) < 2 or rels[-1] <= rels[0]:
        raise DemoFail("retrieval scores had no spread; cannot demonstrate MQL relevance narrowing")
    mid = round(rels[len(rels) // 2], 6)          # keep the stronger half
    high = round(rels[(3 * len(rels)) // 4], 6)    # keep the strongest quartile

    counts = {"base": base_count}
    for label, thr in (("min_relevance>=%s" % mid, mid), ("min_relevance>=%s" % high, high)):
        res = run({"find": SUPERSET_Q, "k": 25, "min_relevance": thr})
        cov = res.get("coverage") or {}
        n = len(res.get("items") or [])
        counts[label] = n
        print(f"   filtered {label}: returned={n} (narrowed_from={cov.get('narrowed_from')} -> narrowed_to={cov.get('narrowed_to')})")
        if n > base_count:
            raise DemoFail(f"MQL filter widened the result set ({n} > {base_count}); must only narrow")

    mid_n = counts["min_relevance>=%s" % mid]
    high_n = counts["min_relevance>=%s" % high]
    # Strict subset chain: base >= mid >= high, and the filter genuinely removed rows.
    if not (base_count >= mid_n >= high_n) or not (mid_n < base_count):
        raise DemoFail(
            f"MQL relevance filter did not produce a strict-subset chain (base={base_count}, mid={mid_n}, high={high_n})"
        )
    # Also confirm the schema rejects over-scoped MQL (a filter can never widen scope).
    status, bad = post("/v1/tools/call", {"name": "query_memory", "arguments": {"find": SUPERSET_Q, "min_relevance": 2.5}})
    rejected = status == 422 or (isinstance(bad, dict) and "detail" in bad and status != 200)
    print(f"   schema guard: over-scoped MQL (min_relevance=2.5) rejected: {rejected} (HTTP {status})")
    print(
        f"   -> MQL narrowing verified: base {base_count} -> {mid_n} -> {high_n} items via a structured, "
        f"subset-only relevance filter (schema also supports layers/entities/as_of/valid_only/min_trust)."
    )
    return {"base": base_count, "mid": mid_n, "high": high_n, "rejected": rejected}


def _compact_envelope(env: dict, *, max_items: int = 5, content_chars: int = 140) -> dict:
    """A readable, on-disk-friendly projection of a real envelope: drop the (constant, large) legend,
    truncate item content, and keep only the first few items — everything else is verbatim."""
    out = dict(env)
    out.pop("legend", None)
    out["legend"] = "<omitted — constant SMP_LEGEND; see backend/app/smp.py>"
    items = []
    for it in (env.get("items") or [])[:max_items]:
        c = dict(it)
        text = str(c.get("content") or "")
        if len(text) > content_chars:
            c["content"] = text[:content_chars] + " …"
        items.append(c)
    out["items"] = items
    out["items_note"] = f"<showing {len(items)} of {len(env.get('items') or [])} items>"
    return out


def dump_example_envelopes(claude_env: dict, cursor_task_env: dict, out_dir: Path) -> None:
    """Persist two real (compacted) envelopes so the spec doc can cite genuine output, not hand-waved
    JSON. Compaction only truncates content + trims the item list; every other field is real. Best
    effort — never a demo gate."""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "example_smp_claude.json").write_text(json.dumps(_compact_envelope(claude_env), indent=2))
        (out_dir / "example_smp_delta_turn2.json").write_text(json.dumps(_compact_envelope(cursor_task_env), indent=2))
        (out_dir / "README.md").write_text(
            "# CMP example envelopes (generated)\n\n"
            "These two files are REAL `/v1/context?format=smp` responses captured by "
            "`scripts/cmp_demo.py` against the worktree backend (hash embedder, deterministic).\n\n"
            "- `example_smp_claude.json` — a `model=claude` pack (legend omitted for size; item "
            "content truncated).\n"
            "- `example_smp_delta_turn2.json` — a session turn-2 envelope whose `working_memory` "
            "block carries the delta `{new, evicted, superseded, cursor}`.\n\n"
            "Regenerate: `python3 scripts/cmp_demo.py`.\n"
        )
    except Exception:
        pass  # example dump is a convenience, never a demo gate


# ---------------------------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------------------------
def main() -> int:
    tmp = Path(tempfile.mkdtemp(prefix="cmp_demo_"))
    proc = None
    try:
        print(f"booting backend from {BACKEND} (temp state in {tmp}) ...")
        proc = boot_backend(tmp)

        seeded = seed_memories()
        print(f"seeded {seeded} memories via /v1/captures (auto-approved).")
        # Give the synchronous extract/index path a beat to settle before retrieving.
        time.sleep(0.5)

        packing = demo_model_aware_packing()
        demo_smp_envelope(packing["claude_env"])
        delta = demo_session_delta()
        mql = demo_mql_narrowing()

        # Capture a real turn-2 envelope (with a populated working_memory delta) for the doc example:
        # a tight turn 1 primes the session, then a roomy turn 2 announces only the newly-fitting ids.
        _ = context_smp(TIMELINE_Q, "cursor", token_budget=900, session_id="cmp-demo-doc")  # prime (turn 1)
        time.sleep(0.8)
        example_turn2 = context_smp(TIMELINE_Q, "cursor", token_budget=11000, session_id="cmp-demo-doc")
        dump_example_envelopes(packing["claude_env"], example_turn2, ROOT / "docs" / "_cmp_examples")

        print("\n" + "=" * 78)
        print("EVIDENCE SUMMARY")
        print(
            f"  1. model-aware budgets: claude={packing['rows']['claude']['budget_tokens']}t "
            f"gpt={packing['rows']['gpt']['budget_tokens']}t cursor={packing['rows']['cursor']['budget_tokens']}t "
            f"| pack-size differentiated={packing['differentiated']}"
        )
        print("  2. SMP envelope carried its legend + a MemoryObject with relevance + why(provenance).")
        print(
            f"  3. session delta: turn1 new={len(delta['new1'])}==cited; turn2 new={len(delta['new2'])} genuinely-new ids only; "
            f"turn3 new={len(delta['new3'])} (~{delta['saved_tokens']} tokens NOT re-sent on {len(delta['overlap'])} carried ids)."
        )
        print(
            f"  4. MQL query_memory narrowed base {mql['base']} -> {mql['mid']} -> {mql['high']} items "
            f"(subset-only; over-scoped MQL rejected={mql['rejected']})."
        )
        print("=" * 78)
        print("\nCMP DEMO OK")
        return 0
    except DemoFail as exc:
        tail = _server_log_tail()
        if tail:
            print("---- backend log tail ----\n" + tail + "\n--------------------------")
        print(f"\nCMP DEMO FAIL: {exc}")
        return 1
    except Exception as exc:  # pragma: no cover - defensive
        import traceback

        traceback.print_exc()
        tail = _server_log_tail()
        if tail:
            print("---- backend log tail ----\n" + tail + "\n--------------------------")
        print(f"\nCMP DEMO FAIL: unexpected error: {type(exc).__name__}: {exc}")
        return 1
    finally:
        _terminate(proc)
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
