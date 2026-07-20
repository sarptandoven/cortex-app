#!/usr/bin/env python3
"""Definitive end-to-end pipeline verification for Stream D.

One script that proves the WHOLE data journey works against the SHIPPING backend:

    ENTRANCE (data in) -> ORGANIZATION (structured) -> RETRIEVAL (out to an AI)

It boots app.standalone_server from worktree source on a spare port with temporary
DB / VAULT / ACCOUNTS and a throwaway CORTEX_API_KEY, using the deterministic hash
embedding provider so the run is reproducible offline (no ANTHROPIC_API_KEY, no
network model). It then walks the exact path a real user + a real AI agent walk and
prints PASS / FAIL / SKIP for every stage:

  1. ENTRANCE   — import a realistic mixed corpus (a ChatGPT-shape conversations.json,
                  a couple of direct /v1/captures, and a small mbox), asserting NO
                  silent drop (records_found matches the input; paginated import
                  completes and its pages sum to the total) and an HONEST capture
                  status (review_status present and consistent with usability).
  2. ORGANIZATION — after approve + /v1/jobs/run, assert the memories are organized:
                  every active memory has a layer, entities are resolved and linked
                  (/v1/entities + /v1/graph), occurred_at is populated from temporal
                  sources, and no orphan (zero-entity + no-source) active remains.
                  Organization features that may not be merged yet SKIP-with-note.
  3. RETRIEVAL  — the AI-serving exit: /v1/search (numeric relevance + inline snippet),
                  /v1/context?format=smp&model=claude (a valid SMP envelope validating
                  against its own legend, cited-only), a 2nd same-session call showing a
                  working_memory delta, /v1/ask (a cited answer from the ingested data),
                  /v1/tools/call query_memory (MQL provably NARROWS), and a paraphrased
                  query resolving to the right memory.

Design contract (per Stream-D brief): robust to features still landing — an
organization feature that isn't merged yet SKIPs with a note rather than failing — but
HARD-fails on data loss, a lying status, an invalid SMP envelope, or a failed retrieval.

Ends with exactly one line:
    FULL PIPELINE OK: entrance->organization->retrieval verified          (exit 0)
    FULL PIPELINE FAIL: <stage> <reason>                                  (exit != 0)

Usage: python3 scripts/verify_full_pipeline.py [--keep] [--port N]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"

# Layers a real corpus lands in; the memory-id / task-id prefixes a cited ref must carry.
CITED_REF_RE = re.compile(r"^(mem_|task_)")


# --------------------------------------------------------------------------------------
# Result harness
# --------------------------------------------------------------------------------------
class Results:
    """Collects (stage, name, status, detail). status: PASS | FAIL | SKIP.

    A FAIL is a HARD failure (data loss / lying status / invalid envelope / failed
    retrieval) and fails the run. A SKIP is a feature-not-present note and never fails
    the run. The first HARD failure decides the single FAIL line at the end."""

    def __init__(self) -> None:
        self.rows: list[tuple[str, str, str, str]] = []

    def record(self, stage: str, name: str, status: str, detail: str = "") -> None:
        self.rows.append((stage, name, status, detail))
        icon = {"PASS": "PASS", "FAIL": "FAIL", "SKIP": "SKIP"}[status]
        line = f"  {icon}  [{stage}] {name}"
        if detail and status != "PASS":
            line += f" — {detail}"
        print(line, flush=True)

    def ok(self, stage: str, name: str, condition: bool, detail: str = "") -> bool:
        self.record(stage, name, "PASS" if condition else "FAIL", detail)
        return bool(condition)

    def skip(self, stage: str, name: str, detail: str) -> None:
        self.record(stage, name, "SKIP", detail)

    def failures(self) -> list[tuple[str, str, str, str]]:
        return [r for r in self.rows if r[2] == "FAIL"]

    def counts(self) -> tuple[int, int, int]:
        p = sum(1 for r in self.rows if r[2] == "PASS")
        f = sum(1 for r in self.rows if r[2] == "FAIL")
        s = sum(1 for r in self.rows if r[2] == "SKIP")
        return p, f, s


class PipelineError(Exception):
    """A structural problem (endpoint unreachable / crashed) mid-stage. Carries the
    stage so the final FAIL line names it."""

    def __init__(self, stage: str, reason: str) -> None:
        super().__init__(reason)
        self.stage = stage
        self.reason = reason


# --------------------------------------------------------------------------------------
# HTTP client
# --------------------------------------------------------------------------------------
class Client:
    def __init__(self, base_url: str, token: str) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token

    def request(self, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
        data = json.dumps(body).encode("utf-8") if body is not None else None
        req = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                raw = resp.read().decode("utf-8")
                return resp.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8") if exc.fp else ""
            try:
                return exc.code, json.loads(raw)
            except json.JSONDecodeError:
                return exc.code, {"detail": raw}

    def get(self, path: str, **params) -> tuple[int, object]:
        if params:
            path = path + "?" + urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
        return self.request("GET", path)

    def post(self, path: str, body: dict | None = None) -> tuple[int, object]:
        return self.request("POST", path, body if body is not None else {})


# --------------------------------------------------------------------------------------
# Server lifecycle
# --------------------------------------------------------------------------------------
def _port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("127.0.0.1", port))
            return True
        except OSError:
            return False


def _pick_port(preferred: int | None) -> int:
    # Honor "spare port ~8831" but tolerate a parallel agent already holding it: scan a
    # small band, then fall back to an OS-assigned ephemeral port.
    candidates = [preferred] if preferred else list(range(8831, 8890))
    for port in candidates:
        if port and _port_free(port):
            return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def boot_server(workdir: Path, token: str, preferred_port: int | None) -> tuple[subprocess.Popen, str, Path]:
    """Boot standalone_server on a free port; retry on a lost port race / slow start.
    Returns (process, base_url, logpath)."""
    last_error = ""
    for attempt in range(4):
        port = _pick_port(preferred_port if attempt == 0 else None)
        base_url = f"http://127.0.0.1:{port}"
        logpath = workdir / f"server-{port}.log"
        env = dict(os.environ)
        env.update(
            {
                "PYTHONPATH": str(BACKEND),
                "CORTEX_VAULT_PATH": str(workdir / "Cortex.vault"),
                "CORTEX_DB_PATH": str(workdir / "index.sqlite"),
                "CORTEX_ACCOUNTS_DB_PATH": str(workdir / "accounts.sqlite"),
                "CORTEX_API_KEY": token,
                "CORTEX_EMBEDDING_PROVIDER": "hash",  # deterministic, offline, reproducible
                "CORTEX_PORT": str(port),
            }
        )
        # Deterministic extraction is what an offline user ships with — no cloud model.
        env.pop("ANTHROPIC_API_KEY", None)
        logfh = open(logpath, "w")
        proc = subprocess.Popen(
            [sys.executable, "-m", "app.standalone_server", "--port", str(port)],
            cwd=str(BACKEND),
            env=env,
            stdout=logfh,
            stderr=subprocess.STDOUT,
        )
        try:
            _wait_health(base_url, proc, logpath, timeout=90)
            return proc, base_url, logpath
        except RuntimeError as exc:
            last_error = str(exc)
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
            # "address already in use" => a racing bind; just try another port.
            if "address already in use" not in last_error.lower() and attempt >= 1:
                # Non-port failure that persisted across a retry — stop wasting attempts.
                break
    raise PipelineError("boot", f"server never became healthy: {last_error[:400]}")


def _wait_health(base_url: str, proc: subprocess.Popen, logpath: Path, timeout: float) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if proc.poll() is not None:
            tail = _tail(logpath)
            raise RuntimeError(f"server exited early (status {proc.returncode}); log: {tail}")
        try:
            with urllib.request.urlopen(base_url + "/health", timeout=2) as resp:
                if resp.status == 200:
                    return
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    raise RuntimeError(f"no /health within {timeout:.0f}s; log: {_tail(logpath)}")


def _tail(path: Path, n: int = 1200) -> str:
    try:
        return path.read_text()[-n:]
    except OSError:
        return "(no log)"


# --------------------------------------------------------------------------------------
# Corpus fixtures
# --------------------------------------------------------------------------------------
def _conversation(title: str, create_time: float, messages: list[tuple[str, str, float]]) -> dict:
    """Build a ChatGPT-export-shaped conversation: {title, create_time, mapping:{node:{message:{
    author.role, create_time, content.parts}}}} — exactly the shape source_ingest._parse_chatgpt reads."""
    mapping = {}
    for i, (role, text, mt) in enumerate(messages):
        node_id = f"node-{i}"
        mapping[node_id] = {
            "id": node_id,
            "message": {
                "id": secrets.token_hex(8),
                "author": {"role": role},
                "create_time": mt,
                "content": {"content_type": "text", "parts": [text]},
            },
        }
    return {
        "title": title,
        "create_time": create_time,
        "update_time": create_time + 100,
        "id": secrets.token_hex(8),
        "mapping": mapping,
    }


def write_corpus(import_dir: Path) -> dict:
    """Write the mixed corpus to disk and return the expected input counts + markers."""
    # ChatGPT conversations.json — 4 conversations, distinct facts across layers.
    convs = [
        _conversation(
            "Backend language choice",
            1_700_000_000,
            [
                ("user", "Which language should we use for backend services?", 1_700_000_000),
                ("assistant", "You leaned toward static typing before.", 1_700_000_050),
                ("user", "Right. I prefer the TypeScript language over JavaScript for all backend services.", 1_700_000_100),
            ],
        ),
        _conversation(
            "Analytics migration",
            1_700_100_000,
            [
                ("user", "We decided last Tuesday to migrate the analytics pipeline to ClickHouse.", 1_700_100_010),
                ("assistant", "Noted — ClickHouse for the analytics pipeline.", 1_700_100_060),
            ],
        ),
        _conversation(
            "Analytics tooling",
            1_700_150_000,
            [
                ("user", "I prefer ClickHouse for all our analytics workloads going forward.", 1_700_150_010),
                ("assistant", "Understood — ClickHouse is your analytics preference.", 1_700_150_060),
            ],
        ),
        _conversation(
            "Team structure",
            1_700_200_000,
            [
                ("user", "My manager is Dana Alvarez, she leads the Platform team at Northwind.", 1_700_200_010),
                ("assistant", "Got it — Dana Alvarez leads Platform.", 1_700_200_060),
            ],
        ),
    ]
    (import_dir / "conversations.json").write_text(json.dumps(convs))

    # A small mbox (two messages). If no mbox parser exists in this build, the entrance
    # stage detects records_found == 0 and SKIPs the mbox path rather than hard-failing.
    mbox = (
        "From alice@example.com Mon Jan  1 00:00:00 2024\n"
        "From: Alice Founder <alice@example.com>\n"
        "To: me@example.com\n"
        "Subject: Project Falcon kickoff\n"
        "Date: Mon, 1 Jan 2024 09:00:00 +0000\n"
        "\n"
        "Project Falcon kicks off next Monday. Bring the budget numbers for the Helsinki office.\n"
        "\n"
        "From bob@example.com Tue Jan  2 00:00:00 2024\n"
        "From: Bob Vendor <bob@example.com>\n"
        "To: me@example.com\n"
        "Subject: Invoice terms\n"
        "Date: Tue, 2 Jan 2024 10:00:00 +0000\n"
        "\n"
        "Our net-30 invoice terms are confirmed for the ClickHouse hosting contract.\n"
    )
    (import_dir / "mail.mbox").write_text(mbox)

    return {
        "chatgpt_path": str(import_dir / "conversations.json"),
        "mbox_path": str(import_dir / "mail.mbox"),
        "chatgpt_records": 4,
        "mbox_records": 2,
        "direct_captures": [
            "I met Rajiv Menon at the Berlin summit to discuss the Q4 partnership.",
            "Remember to renew the SSL certificate before it expires next month.",
        ],
    }


# --------------------------------------------------------------------------------------
# Stage 1 — ENTRANCE
# --------------------------------------------------------------------------------------
def stage_entrance(client: Client, r: Results, corpus: dict) -> dict:
    stage = "entrance"
    state = {"mbox_present": False}

    # -- analyze: the parser must find exactly the input records (no silent drop at parse).
    status, body = client.post("/v1/imports/analyze", {"paths": [corpus["chatgpt_path"]]})
    if status != 200 or not isinstance(body, dict):
        raise PipelineError(stage, f"/v1/imports/analyze failed ({status}): {str(body)[:200]}")
    found = int(body.get("records_found") or 0)
    r.ok(stage, "analyze finds every ChatGPT conversation (no parse drop)",
         found == corpus["chatgpt_records"], f"records_found={found} expected={corpus['chatgpt_records']}")

    status, mbox_body = client.post("/v1/imports/analyze", {"paths": [corpus["mbox_path"]]})
    mbox_found = int(mbox_body.get("records_found") or 0) if isinstance(mbox_body, dict) else 0
    state["mbox_present"] = mbox_found > 0

    # -- paginated import: page through with a small window; pages must sum to the total,
    #    the final page must report has_more=false, and nothing may be dropped.
    page_size = 2
    offset = 0
    pages = 0
    sum_found = 0
    sum_saved = 0
    records_available = None
    guard = 0
    while True:
        guard += 1
        if guard > 50:
            raise PipelineError(stage, "pagination did not terminate (possible has_more loop)")
        status, page = client.post(
            "/v1/imports",
            {"paths": [corpus["chatgpt_path"]], "processing": "sync",
             "max_records": page_size, "offset": offset, "auto_approve": False},
        )
        if status != 200 or not isinstance(page, dict):
            raise PipelineError(stage, f"/v1/imports failed ({status}): {str(page)[:200]}")
        pages += 1
        sum_found += int(page.get("records_found") or 0)
        sum_saved += int(page.get("saved") or 0) + int(page.get("skipped") or 0)
        records_available = int(page.get("records_available") or 0)
        if page.get("has_more"):
            nxt = page.get("next_offset")
            if not isinstance(nxt, int) or nxt <= offset:
                raise PipelineError(stage, f"has_more=true but next_offset invalid ({nxt!r})")
            offset = nxt
        else:
            # This runs only in the has_more==false branch, so `not page.get('has_more')` would be
            # vacuously true and mask a final page that clears has_more but leaves a stale non-zero
            # cursor. Assert the cursor is genuinely cleared on the terminal page.
            r.ok(stage, "paginated import terminates (final page clears its cursor)",
                 page.get("next_offset") in (None, 0),
                 f"next_offset={page.get('next_offset')}")
            break

    r.ok(stage, "import pages sum to the full corpus (no silent drop)",
         sum_found == records_available == corpus["chatgpt_records"] and pages >= 2,
         f"pages={pages} sum_found={sum_found} records_available={records_available}")
    r.ok(stage, "every found record is accounted for (saved or deduped)",
         sum_saved == sum_found, f"saved+skipped={sum_saved} found={sum_found}")

    # -- mbox import (conditional on a parser existing in this build).
    if state["mbox_present"]:
        status, mbox = client.post(
            "/v1/imports",
            {"paths": [corpus["mbox_path"]], "processing": "sync", "max_records": 100, "auto_approve": False},
        )
        if status != 200 or not isinstance(mbox, dict):
            raise PipelineError(stage, f"mbox /v1/imports failed ({status}): {str(mbox)[:200]}")
        r.ok(stage, "mbox import loses no message (records_found == mailbox size)",
             int(mbox.get("records_available") or 0) == mbox_found == corpus["mbox_records"],
             f"records_available={mbox.get('records_available')} analyze={mbox_found}")
    else:
        r.skip(stage, "mbox import", "no mbox parser detected in this build (records_found=0)")

    # -- direct captures: response must carry an HONEST review_status (a pending capture
    #    must not claim to be usable — that would be a lying status).
    for i, content in enumerate(corpus["direct_captures"]):
        status, cap = client.post("/v1/captures", {"content": content, "source": "note"})
        if status != 200 or not isinstance(cap, dict):
            raise PipelineError(stage, f"/v1/captures failed ({status}): {str(cap)[:200]}")
        review_status = cap.get("review_status")
        has_status = isinstance(review_status, str) and review_status != ""
        r.ok(stage, f"direct capture {i + 1} reports review_status",
             has_status, f"review_status={review_status!r}")
        if review_status == "pending":
            r.ok(stage, f"direct capture {i + 1} status is honest (pending => not usable yet)",
                 int(cap.get("usable_count") or 0) == 0,
                 f"pending but usable_count={cap.get('usable_count')}")

    return state


# --------------------------------------------------------------------------------------
# Stage 2 — ORGANIZATION
# --------------------------------------------------------------------------------------
def stage_organization(client: Client, r: Results) -> None:
    stage = "organization"

    # Approve the whole review backlog (the "Approve all" tap), then run due jobs.
    status, approved = client.post("/v1/captures/approve-all", {})
    if status != 200 or not isinstance(approved, dict):
        raise PipelineError(stage, f"/v1/captures/approve-all failed ({status}): {str(approved)[:200]}")
    r.ok(stage, "approve-all clears captures", int(approved.get("approved") or 0) > 0,
         f"approved={approved.get('approved')}")

    status, jobs = client.post("/v1/jobs/run?limit=50", {})
    if status != 200 or not isinstance(jobs, dict):
        raise PipelineError(stage, f"/v1/jobs/run failed ({status}): {str(jobs)[:200]}")
    r.ok(stage, "/v1/jobs/run returns a well-formed run report",
         "processed" in jobs and "failed" in jobs, str(jobs)[:160])

    # After approval the review backlog must be empty — a lingering pending count while
    # claiming approval would be a lying status.
    status, stats = client.get("/v1/stats")
    if status != 200 or not isinstance(stats, dict):
        raise PipelineError(stage, f"/v1/stats failed ({status}): {str(stats)[:200]}")
    r.ok(stage, "review backlog is empty after approve (honest state)",
         int(stats.get("pending_captures") or 0) == 0, f"pending_captures={stats.get('pending_captures')}")

    total_memories = int(stats.get("memories") or 0)
    r.ok(stage, "corpus produced memories", total_memories > 0, f"memories={total_memories}")

    # Every active memory carries a layer: the by_layer histogram must cover all memories.
    by_layer = stats.get("by_layer") or []
    layered = sum(int(entry.get("count") or 0) for entry in by_layer if isinstance(entry, dict))
    distinct_layers = {str(entry.get("layer")) for entry in by_layer if isinstance(entry, dict) and entry.get("layer")}
    r.ok(stage, "every memory is organized into a layer",
         layered == total_memories and total_memories > 0,
         f"layered={layered} total={total_memories} layers={sorted(distinct_layers)}")

    # Entities resolved + linked (organization feature — SKIP if this build has none).
    status, ents = client.get("/v1/entities", limit=100)
    entities = ents.get("results") if isinstance(ents, dict) else None
    if not entities:
        r.skip(stage, "entity resolution", "no /v1/entities results in this build")
    else:
        names = {str(e.get("name", "")).lower() for e in entities if isinstance(e, dict)}
        want = {"dana alvarez", "clickhouse", "rajiv menon"}
        hit = want & names
        r.ok(stage, "named entities resolved from the corpus", bool(hit),
             f"looked for {sorted(want)}, resolved {sorted(names)[:12]}")
        # Linkage: at least one memory→entity mention edge in the graph.
        status, graph = client.get("/v1/graph", limit=200)
        edges = graph.get("edges") if isinstance(graph, dict) else []
        mention_edges = [e for e in edges if isinstance(e, dict) and e.get("kind") == "mentions"]
        r.ok(stage, "entities are linked to memories (graph mention edges)",
             len(mention_edges) > 0, f"mention_edges={len(mention_edges)}")

    # occurred_at populated from temporal sources (organization feature — SKIP if absent).
    status, search = client.get("/v1/search", query="TypeScript backend", limit=5)
    results = search.get("results") if isinstance(search, dict) else []
    dated = [it for it in (results or []) if isinstance(it, dict) and it.get("occurred_at")]
    if dated:
        r.ok(stage, "occurred_at is set on time-anchored memories", True,
             f"e.g. {dated[0].get('occurred_at')}")
    else:
        r.skip(stage, "occurred_at population", "no occurred_at on retrieved memories in this build")

    # No orphan actives: every retrieved active memory is sourced (has a source_url).
    # (A zero-entity + no-source active would be an unflagged orphan.)
    probe_terms = ["TypeScript backend", "ClickHouse analytics", "Dana Alvarez", "Rajiv Menon"]
    seen: dict[str, dict] = {}
    for term in probe_terms:
        _, sr = client.get("/v1/search", query=term, limit=10)
        for it in (sr.get("results") if isinstance(sr, dict) else []) or []:
            if isinstance(it, dict) and it.get("id"):
                seen[str(it["id"])] = it
    orphans = [mid for mid, it in seen.items()
               if not str(it.get("source_url") or "").strip() and not (it.get("entity_ids") or [])]
    r.ok(stage, "no orphan active memories (every active is sourced or entity-linked)",
         len(orphans) == 0 and len(seen) > 0, f"orphans={orphans} inspected={len(seen)}")


# --------------------------------------------------------------------------------------
# Stage 3 — RETRIEVAL
# --------------------------------------------------------------------------------------
def _validate_smp(env: object) -> tuple[bool, str]:
    """Validate an SMP envelope against the legend that travels inside it. Returns
    (ok, reason). Invalid => a HARD failure per the brief."""
    if not isinstance(env, dict):
        return False, "envelope is not an object"
    if env.get("smp") != "1":
        return False, f"smp version tag != '1' ({env.get('smp')!r})"
    legend = env.get("legend")
    if not isinstance(legend, dict) or legend.get("protocol") != "smp":
        return False, "missing/invalid self-describing legend"
    env_legend = legend.get("envelope")
    item_legend = legend.get("item")
    if not isinstance(env_legend, dict) or not isinstance(item_legend, dict):
        return False, "legend does not describe envelope/item fields"
    # Legend<->payload parity: the envelope must expose exactly the keys the legend documents.
    missing_env = set(env_legend) - set(env)
    if missing_env:
        return False, f"envelope missing legend keys: {sorted(missing_env)}"
    items = env.get("items")
    if not isinstance(items, list) or not items:
        return False, "envelope carries no items"
    item_keys = set(item_legend)
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            return False, f"item {idx} is not an object"
        missing = item_keys - set(item)
        if missing:
            return False, f"item {idx} missing legend-described fields: {sorted(missing)}"
        ref = str(item.get("ref") or "")
        if not ref or not CITED_REF_RE.match(ref):
            return False, f"item {idx} has a non-cited ref ({ref!r}) — cited-only invariant broken"
    return True, "ok"


def stage_retrieval(client: Client, r: Results) -> None:
    stage = "retrieval"

    # -- R1: /v1/search returns a numeric relevance + an inline snippet for a direct query.
    status, search = client.get("/v1/search", query="TypeScript backend", limit=5)
    if status != 200 or not isinstance(search, dict):
        raise PipelineError(stage, f"/v1/search failed ({status}): {str(search)[:200]}")
    results = search.get("results") or []
    ts_hits = [it for it in results if isinstance(it, dict) and "typescript" in str(it.get("content", "")).lower()]
    r.ok(stage, "search retrieves the right memory for a direct query", bool(ts_hits),
         f"results={[str(it.get('content'))[:40] for it in results][:3]}")
    if ts_hits:
        top = ts_hits[0]
        numeric = isinstance(top.get("relevance"), (int, float))
        snippet = bool(str(top.get("content") or top.get("raw_excerpt") or "").strip())
        r.ok(stage, "search result carries a numeric relevance", numeric, f"relevance={top.get('relevance')!r}")
        r.ok(stage, "search result carries an inline snippet", snippet, "content/raw_excerpt empty")

    # -- R2: /v1/context?format=smp&model=claude — a valid SMP envelope, cited-only.
    session_id = "verify-" + secrets.token_hex(4)
    task = "what language should we use for backend services"
    status, smp1 = client.get(
        "/v1/context", format="smp", model="claude", task=task, session_id=session_id, token_budget=1500
    )
    if status != 200:
        raise PipelineError(stage, f"/v1/context?format=smp failed ({status}): {str(smp1)[:200]}")
    valid, reason = _validate_smp(smp1)
    r.ok(stage, "SMP envelope validates against its own legend (cited-only)", valid, reason)
    if isinstance(smp1, dict):
        r.ok(stage, "SMP envelope is calibrated to the requested model", smp1.get("model") == "claude",
             f"model={smp1.get('model')!r}")

    # -- R3: a 2nd same-session call shows a working_memory delta (only the change is sent).
    status, smp2 = client.get(
        "/v1/context", format="smp", model="claude", task=task, session_id=session_id, token_budget=1500
    )
    wm1 = smp1.get("working_memory") if isinstance(smp1, dict) else None
    wm2 = smp2.get("working_memory") if isinstance(smp2, dict) else None
    if not isinstance(wm1, dict) or not isinstance(wm2, dict):
        r.skip(stage, "working_memory session delta", "working_memory not populated in this build")
    else:
        new1 = wm1.get("new") if isinstance(wm1.get("new"), list) else []
        new2 = wm2.get("new") if isinstance(wm2.get("new"), list) else []
        # First call seeds the session (items are new); the repeat call, having already
        # served them, must send a SMALLER delta (fewer/no new items).
        r.ok(stage, "session working_memory shrinks on repeat (working delta)",
             len(new1) > 0 and len(new2) < len(new1),
             f"new(call1)={len(new1)} new(call2)={len(new2)}")

    # -- R4: /v1/ask returns a cited answer grounded in the ingested data.
    status, ask = client.get("/v1/ask", query="which database did we migrate the analytics pipeline to")
    if status != 200 or not isinstance(ask, dict):
        raise PipelineError(stage, f"/v1/ask failed ({status}): {str(ask)[:200]}")
    citations = ask.get("citations") or []
    answer_text = str(ask.get("answer") or "").lower()
    cited = bool(citations) and all(isinstance(c, dict) and c.get("id") for c in citations)
    grounded = "clickhouse" in answer_text or any(
        "clickhouse" in str(c.get("excerpt", "")).lower() for c in citations
    )
    r.ok(stage, "ask answers from cited ingested memory", cited and grounded,
         f"status={ask.get('status')} citations={len(citations)} grounded={grounded}")

    # -- R5: /v1/tools/call query_memory — MQL provably NARROWS (a strict subset).
    status, broad = client.post("/v1/tools/call", {"name": "query_memory", "arguments": {"find": "analytics", "k": 10}})
    status2, narrow = client.post(
        "/v1/tools/call", {"name": "query_memory", "arguments": {"find": "analytics", "layers": ["decision"], "k": 10}}
    )
    if status != 200 or status2 != 200:
        raise PipelineError(stage, f"query_memory failed ({status}/{status2}): {str(broad)[:120]} {str(narrow)[:120]}")
    broad_res = broad.get("result", broad) if isinstance(broad, dict) else {}
    narrow_res = narrow.get("result", narrow) if isinstance(narrow, dict) else {}
    broad_items = broad_res.get("items") or []
    narrow_items = narrow_res.get("items") or []
    broad_refs = {str(it.get("ref")) for it in broad_items if isinstance(it, dict)}
    narrow_refs = {str(it.get("ref")) for it in narrow_items if isinstance(it, dict)}
    subset = narrow_refs <= broad_refs
    strictly_narrows = len(narrow_items) < len(broad_items)
    layer_respected = all(str(it.get("layer")) == "decision" for it in narrow_items if isinstance(it, dict))
    r.ok(stage, "query_memory MQL narrows to a strict, layer-correct subset",
         len(broad_items) >= 2 and strictly_narrows and subset and layer_respected and len(narrow_items) >= 1,
         f"broad={len(broad_items)} narrowed={len(narrow_items)} subset={subset} layer_ok={layer_respected}")

    # -- R6: semantic/paraphrase retrieval — a differently-worded query finds the memory.
    status, para = client.get(
        "/v1/search", query="which backend programming language do I prefer", limit=5
    )
    para_results = para.get("results") if isinstance(para, dict) else []
    para_hit = any(
        isinstance(it, dict) and "typescript" in str(it.get("content", "")).lower()
        for it in (para_results or [])
    )
    r.ok(stage, "paraphrased query resolves to the right memory (semantic retrieval)",
         para_hit, f"got={[str(it.get('content'))[:40] for it in (para_results or [])][:3]}")


# --------------------------------------------------------------------------------------
# Orchestration
# --------------------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=None, help="preferred port (default: scan from ~8831)")
    parser.add_argument("--keep", action="store_true", help="keep the temp vault/DB for inspection")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="cortex-full-pipeline-"))
    import_dir = workdir / "imports"
    import_dir.mkdir(parents=True, exist_ok=True)
    token = "cxa_verify_" + secrets.token_hex(16)
    corpus = write_corpus(import_dir)
    results = Results()
    proc: subprocess.Popen | None = None
    fail_stage = ""
    fail_reason = ""

    print("=" * 78)
    print("Cortex FULL PIPELINE verification — entrance -> organization -> retrieval")
    print("=" * 78)

    try:
        proc, base_url, logpath = boot_server(workdir, token, args.port)
        print(f"server up at {base_url} (hash embeddings, temp vault {workdir})\n")
        client = Client(base_url, token)

        print("-- STAGE 1: ENTRANCE (data in) " + "-" * 45)
        stage_entrance(client, results, corpus)
        print("\n-- STAGE 2: ORGANIZATION (structured) " + "-" * 38)
        stage_organization(client, results)
        print("\n-- STAGE 3: RETRIEVAL (out to an AI) " + "-" * 39)
        stage_retrieval(client, results)

    except PipelineError as exc:
        fail_stage, fail_reason = exc.stage, exc.reason
        print(f"  FAIL  [{exc.stage}] {exc.reason}")
    except Exception as exc:  # unexpected — surface it as a hard failure, not a crash
        fail_stage, fail_reason = "harness", f"{type(exc).__name__}: {exc}"
        print(f"  FAIL  [harness] {fail_reason}")
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        if args.keep:
            print(f"\n(temp workspace kept at {workdir})")
        else:
            shutil.rmtree(workdir, ignore_errors=True)

    passed, failed, skipped = results.counts()
    print("\n" + "=" * 78)
    print(f"summary: {passed} PASS  {failed} FAIL  {skipped} SKIP")
    if skipped:
        for s in [row for row in results.rows if row[2] == "SKIP"]:
            print(f"  note: [{s[0]}] {s[1]} skipped — {s[3]}")

    hard_failures = results.failures()
    if not fail_stage and hard_failures:
        fail_stage, _, _, fail_reason = hard_failures[0][0], *hard_failures[0][1:]
        fail_reason = f"{hard_failures[0][1]}: {hard_failures[0][3]}"

    if fail_stage:
        print(f"\nFULL PIPELINE FAIL: {fail_stage} {fail_reason}"[:400])
        return 1

    print("\nFULL PIPELINE OK: entrance->organization->retrieval verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
