#!/usr/bin/env python3
"""CMP scale benchmark — proves the Contextual Memory Protocol retrieval layer is efficient
at scale, with real numbers, using only the standard library.

What it does
------------
1. Boots the worktree backend (``python3 -m app.standalone_server``) on a spare port against a
   throwaway DB/vault/accounts dir, with the deterministic ``hash`` embedding provider so runs
   are reproducible and need no API keys.
2. Seeds a realistic, deterministically-generated personal-memory corpus at three scales
   (200, 1000, 3000 memories) via ``POST /v1/captures`` (auto-approved, so retrievable at once).
   The corpora are nested (200 ⊂ 1000 ⊂ 3000) — the natural "as your memory grows" story.
3. Measures and PRINTS, per scale:
     * assemble_context latency (p50/p95) for a mixed query set through ``/v1/context?format=smp``,
     * model-aware packing: token budget vs used (utilization) and item count for model=claude
       (12k pack budget) vs model=cursor (3k pack budget) — the small-model pack visibly shrinks,
     * SESSION-DELTA savings: a 5-turn session with a stable session_id, cumulative delta tokens
       vs naive full-resend, as a percentage (the "pay once per fact" win),
     * dedup/compression: distinct-facts-per-1k-tokens at a fixed tight budget.
4. Asserts sane efficiency FLOORS (latency ceiling at 3000 memories, delta clearly < 60% of naive,
   utilization sane and never over budget, density above a floor) and prints a summary table,
   exiting 0 with "CMP SCALE BENCH OK" — or non-zero naming the failing metric.

Run:  cd /tmp/cortex-connect && python3 scripts/cmp_scale_bench.py
"""

from __future__ import annotations

import concurrent.futures as cf
import http.client
import json
import math
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
API_KEY = "cmp-scale-bench-key-0f3a"
USER = "local"
PORT = 8829
BASE = f"http://127.0.0.1:{PORT}"

SCALES = (200, 1000, 3000)

# Efficiency FLOORS (the bar). Chosen with generous margin over observed local numbers so the
# gate fails only on a genuine regression, not machine noise.
LATENCY_P95_CEILING_MS = 1200.0        # p95 assemble_context latency at the largest scale
DELTA_MAX_FRACTION_OF_NAIVE = 0.60     # session-delta tokens must be clearly < 60% of naive resend
DENSITY_FLOOR_FACTS_PER_1K = 2.0       # distinct cited facts per 1k tokens at a tight budget

# Chars→token estimate for the session-delta accounting. 3.8 chars/token matches the `claude`
# profile the delta session runs under, so pack-vs-delta is measured on one consistent ruler.
CHARS_PER_TOKEN = 3.8


# --------------------------------------------------------------------------------------------
# HTTP helpers (stdlib only)
# --------------------------------------------------------------------------------------------
# A ThreadingHTTPServer under load will occasionally close a socket before writing a response
# (RemoteDisconnected) or refuse a connection for a beat — transient, not a real failure. We retry
# those; a genuine 4xx (other than 429) surfaces immediately so real bugs are never masked.
_TRANSIENT = (http.client.RemoteDisconnected, http.client.IncompleteRead, ConnectionError, TimeoutError)
_RETRYABLE_HTTP = {408, 425, 429, 500, 502, 503, 504}


def _request_once(method: str, path: str, body: dict | None, timeout: float):
    data = None
    headers = {"Authorization": f"Bearer {API_KEY}"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(BASE + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        raw = resp.read()
    return resp.status, (json.loads(raw) if raw else {})


def _is_transient(exc: Exception) -> bool:
    if isinstance(exc, urllib.error.HTTPError):
        return exc.code in _RETRYABLE_HTTP
    if isinstance(exc, urllib.error.URLError):
        # URLError wraps connection-refused/reset etc. HTTPError is a subclass, handled above.
        return True
    return isinstance(exc, _TRANSIENT)


def _request(method: str, path: str, body: dict | None = None, timeout: float = 60.0, attempts: int = 8):
    last: Exception | None = None
    for k in range(attempts):
        try:
            return _request_once(method, path, body, timeout)
        except Exception as exc:  # noqa: BLE001 - retry only transient, re-raise the rest
            if not _is_transient(exc):
                raise
            last = exc
            # Patient backoff: a capture can 500 when a writer waits out the 5s SQLite busy_timeout
            # under seed contention, so cumulative backoff across attempts must exceed that window.
            time.sleep(min(2.0, 0.3 * (k + 1)))
    raise RuntimeError(f"{method} {path} failed after {attempts} attempts: {last}")


def _post_capture(content: str, source: str = "cmp-bench") -> None:
    _request("POST", "/v1/captures", {"content": content, "source": source}, timeout=30.0)


def _context_body(task: str, model: str | None, token_budget: int, session_id: str | None) -> dict:
    body: dict = {"task": task, "format": "smp", "token_budget": token_budget}
    if model:
        body["model"] = model
    if session_id:
        body["session_id"] = session_id
    return body


def _context_smp(task: str, *, model: str | None, token_budget: int, session_id: str | None = None) -> dict:
    _status, payload = _request("POST", "/v1/context", _context_body(task, model, token_budget, session_id))
    return payload


def _timed_context(task: str, *, model: str | None, token_budget: int, attempts: int = 5) -> tuple[dict, float]:
    """Return (payload, elapsed_ms) for one successful SMP context call. Transient socket drops are
    retried and NOT counted toward the timing, so latency reflects real served-request time."""
    body = _context_body(task, model, token_budget, None)
    last: Exception | None = None
    for k in range(attempts):
        try:
            t0 = time.perf_counter()
            _status, payload = _request_once("POST", "/v1/context", body, 60.0)
            return payload, (time.perf_counter() - t0) * 1000.0
        except Exception as exc:  # noqa: BLE001
            if not _is_transient(exc):
                raise
            last = exc
            time.sleep(0.2 * (k + 1))
    raise RuntimeError(f"context call failed after {attempts} attempts: {last}")


# --------------------------------------------------------------------------------------------
# Deterministic realistic corpus
# --------------------------------------------------------------------------------------------
PROJECTS = [
    "Atlas", "Beacon", "Cirrus", "Delta", "Ember", "Falcon", "Gecko", "Harbor",
    "Iris", "Juno", "Kestrel", "Lyra", "Mesa", "Nova", "Onyx", "Pallas",
]
DATABASES = ["PostgreSQL", "MySQL", "MongoDB", "DynamoDB", "Cassandra", "CockroachDB", "Spanner"]
CACHES = ["Redis", "Memcached", "Hazelcast", "a Caffeine in-process cache", "Varnish"]
GATEWAYS = ["Kong", "Envoy", "an nginx reverse proxy", "AWS API Gateway", "Traefik"]
REASONS = [
    "its jsonb support", "strong consistency guarantees", "cheaper horizontal scaling",
    "the team's prior operational experience", "better tooling for our workload",
    "lower p99 latency under load", "native multi-region replication",
]
FEATURES = [
    "the mobile onboarding flow", "billing reconciliation", "the search relevance rework",
    "offline sync", "the notifications overhaul", "SSO for enterprise",
    "the analytics pipeline", "the admin console redesign",
]
ROLES = ["lead designer", "staff engineer", "eng manager", "product lead", "SRE on-call owner", "tech lead"]
FIRST = ["Maria", "David", "Priya", "Sam", "Lena", "Omar", "Grace", "Tariq", "Nina", "Wes", "Ivy", "Ravi"]
LAST = ["Chen", "Okoro", "Nguyen", "Alvarez", "Kowalski", "Haddad", "Sørensen", "Iyer", "Bauer", "Reyes"]
CI_TOOLS = ["Buildkite", "GitHub Actions", "CircleCI", "the internal Argo", "Jenkins"]
PREFS = [
    "decline meetings before 10am", "keep Fridays free of deploys",
    "write design docs before code", "batch code reviews in the afternoon",
    "avoid standing meetings longer than 25 minutes",
]


def _mem(rng: random.Random, i: int) -> str:
    """One realistic memory. Templates are chosen to spread facts across the retrieval layers
    (decisions / constraints / semantic facts / people / procedures / preferences) so the packer
    exercises every layer, and to be rich enough (~140-280 chars) that a tight pack budget binds."""
    p = PROJECTS[i % len(PROJECTS)]
    kind = i % 8
    if kind == 0:
        db = rng.choice(DATABASES)
        return (f"We decided to standardize the {p} project on {db} because of {rng.choice(REASONS)}; "
                f"the migration off the previous datastore is tracked in the {p} platform epic and "
                f"was signed off by the {p} architecture review.")
    if kind == 1:
        return (f"Never deploy {p} on Fridays or the day before a public holiday; the {p} on-call "
                f"rotation cannot absorb a bad rollout over a weekend, so freeze windows are enforced "
                f"in the {p} release pipeline.")
    if kind == 2:
        person = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        return (f"{person} is the {rng.choice(ROLES)} on {p} and owns the roadmap for {rng.choice(FEATURES)}; "
                f"route {p} scoping questions to them before committing to a quarter.")
    if kind == 3:
        return (f"The {p} Q{1 + (i % 4)} roadmap prioritizes {rng.choice(FEATURES)} ahead of "
                f"{rng.choice(FEATURES)}, with the explicit goal of cutting {p} activation time in half.")
    if kind == 4:
        return (f"{p} uses {rng.choice(CACHES)} for caching hot session and profile reads; the {p} cache "
                f"TTL is 15 minutes and cache stampedes are guarded with a single-flight lock.")
    if kind == 5:
        return (f"The {p} API gateway runs on {rng.choice(GATEWAYS)}; all {p} public traffic terminates "
                f"there, and rate limits for {p} partners are configured per API key at that layer.")
    if kind == 6:
        return (f"To deploy {p}, run the {rng.choice(CI_TOOLS)} pipeline named ship-{p.lower()}, wait for "
                f"green integration checks, then promote the {p} canary to 10% before a full rollout.")
    return (f"I prefer to {rng.choice(PREFS)} while working on {p}; scheduling around this keeps {p} "
            f"deep-work blocks intact and is a standing personal constraint.")


def build_corpus(n: int, seed: int = 20260720) -> list[str]:
    rng = random.Random(seed)
    return [_mem(rng, i) for i in range(n)]


# --------------------------------------------------------------------------------------------
# Query sets
# --------------------------------------------------------------------------------------------
def mixed_queries() -> list[str]:
    templates = [
        "which database did we choose for the {p} project and why",
        "who is the lead on {p} and what do they own",
        "what is the deploy policy and freeze window for {p}",
        "what does the {p} Q roadmap prioritize",
        "what caching does {p} use and what is the TTL",
        "how do we deploy {p} and promote a canary",
        "which API gateway runs {p} public traffic",
    ]
    qs: list[str] = []
    # Spread across many projects so retrieval touches a broad slice of the corpus.
    for k, p in enumerate(PROJECTS):
        qs.append(templates[k % len(templates)].format(p=p))
        qs.append(templates[(k + 3) % len(templates)].format(p=p))
    return qs


def delta_session_turns(project: str = "Atlas") -> list[str]:
    """Five related turns of one continuing conversation about a single project. Later turns
    re-surface facts earlier turns already pulled, so the working_memory delta stays tiny after
    turn 1 — the pay-once-per-fact win."""
    return [
        f"tell me everything we know about the {project} project",
        f"which database and caching does {project} use and why did we choose them",
        f"who works on {project} and what does the roadmap prioritize this quarter",
        f"what is the deployment policy, freeze window, and API gateway for {project}",
        f"summarize {project} architecture, the team, and the key decisions we made",
    ]


PACKING_QUERY = (
    "give me the full picture of the Atlas project: database decision, caching, API gateway, "
    "deploy policy, roadmap priorities, the team and who leads it, and how we ship it"
)
DENSITY_QUERY = PACKING_QUERY


# --------------------------------------------------------------------------------------------
# Metrics
# --------------------------------------------------------------------------------------------
def _est_tokens(text: str) -> int:
    return max(1, math.ceil(len(str(text)) / CHARS_PER_TOKEN))


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = pct / 100.0 * (len(ordered) - 1)
    lo = math.floor(rank)
    hi = math.ceil(rank)
    if lo == hi:
        return ordered[int(rank)]
    frac = rank - lo
    return ordered[lo] * (1 - frac) + ordered[hi] * frac


def measure_latency(queries: list[str]) -> tuple[float, float, int]:
    """Sequential p50/p95 of the SMP context endpoint (model=claude) over the mixed query set.
    One warmup call primes caches/JIT-free imports so the first sample isn't an outlier."""
    if queries:
        _timed_context(queries[0], model="claude", token_budget=6000)  # warmup, discarded
    samples: list[float] = []
    for q in queries:
        payload, dt = _timed_context(q, model="claude", token_budget=6000)
        assert isinstance(payload.get("items"), list), "context response missing items[]"
        samples.append(dt)
    return _percentile(samples, 50), _percentile(samples, 95), len(samples)


def measure_packing(model: str) -> dict:
    """Model-aware packing at a deliberately over-large request budget so the profile cap binds."""
    payload = _context_smp(PACKING_QUERY, model=model, token_budget=20000)
    budget = payload.get("budget") or {}
    tokens = int(budget.get("tokens") or 0)
    used = int(budget.get("used") or 0)
    items = payload.get("items") or []
    util = (used / tokens) if tokens else 0.0
    return {"model": model, "tokens": tokens, "used": used, "items": len(items), "util": util}


def measure_delta(session_id: str) -> dict:
    """Five-turn session: cumulative content tokens if you naively re-send the whole pack each turn
    vs. paying only for the working_memory `new` delta (each fact sent once)."""
    ref_content: dict[str, str] = {}
    naive_total = 0
    delta_total = 0
    per_turn: list[dict] = []
    for turn, task in enumerate(delta_session_turns(), start=1):
        payload = _context_smp(task, model="claude", token_budget=6000, session_id=session_id)
        items = payload.get("items") or []
        for it in items:
            ref = str(it.get("ref") or "")
            if ref:
                ref_content[ref] = str(it.get("content") or "")
        pack_tokens = sum(_est_tokens(it.get("content") or "") for it in items)

        wm = payload.get("working_memory") or {}
        new_refs = [str(x.get("ref") or "") for x in (wm.get("new") or [])]
        # Pay-once cost of this turn = content tokens of only the facts newly introduced this turn.
        new_tokens = sum(_est_tokens(ref_content.get(r, "")) for r in new_refs if r)

        naive_total += pack_tokens          # naive: resend the entire pack every turn
        delta_total += new_tokens           # CMP: send only the delta content
        per_turn.append({
            "turn": turn,
            "pack_items": len(items),
            "pack_tokens": pack_tokens,
            "new": len(new_refs),
            "new_tokens": new_tokens,
        })
    savings = (1.0 - (delta_total / naive_total)) if naive_total else 0.0
    return {
        "naive_total": naive_total,
        "delta_total": delta_total,
        "savings_pct": savings * 100.0,
        "fraction_of_naive": (delta_total / naive_total) if naive_total else 0.0,
        "per_turn": per_turn,
    }


def measure_density(tight_budget: int = 1000) -> dict:
    """Distinct cited facts delivered per 1k tokens at a tight budget — the payoff of dedup + fold."""
    payload = _context_smp(DENSITY_QUERY, model="claude", token_budget=tight_budget)
    budget = payload.get("budget") or {}
    used = int(budget.get("used") or 0)
    items = payload.get("items") or []
    coverage = payload.get("coverage") or {}
    facts_per_1k = (len(items) / (used / 1000.0)) if used else 0.0
    return {
        "budget": int(budget.get("tokens") or tight_budget),
        "used": used,
        "distinct_facts": len(items),
        "facts_per_1k": facts_per_1k,
        "deduped": int(coverage.get("deduped") or 0),
    }


# --------------------------------------------------------------------------------------------
# Server lifecycle
# --------------------------------------------------------------------------------------------
def boot_server(workdir: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "PYTHONPATH": str(BACKEND),
        "PYTHONDONTWRITEBYTECODE": "1",
        "CORTEX_DB_PATH": str(workdir / "index.sqlite"),
        "CORTEX_VAULT_PATH": str(workdir / "vault"),
        "CORTEX_ACCOUNTS_DB_PATH": str(workdir / "accounts.sqlite"),
        "CORTEX_API_KEY": API_KEY,
        "CORTEX_EMBEDDING_PROVIDER": "hash",
        "CORTEX_AUTO_APPROVE_CAPTURES": "1",
        # This is a throughput/latency probe, not a quota test: take the wedge guards out of the way.
        "CORTEX_RATE_LIMIT_RPS": "0",
        "CORTEX_MAX_CONCURRENT_REQUESTS": "64",
        "CORTEX_PORT": str(PORT),
        "CORTEX_STANDALONE_WORKER_ENABLED": "0",
    }
    log = open(workdir / "server.log", "w")
    proc = subprocess.Popen(
        [sys.executable, "-m", "app.standalone_server"],
        cwd=str(BACKEND), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 40
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early (code {proc.returncode}); see {workdir/'server.log'}")
        try:
            status, _ = _request("GET", "/health", timeout=3.0)
            if status == 200:
                return proc
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(0.3)
    raise RuntimeError(f"server did not become healthy; see {workdir/'server.log'}")


def seed(items: list[str], workers: int = 3) -> float:
    # Modest concurrency: the store serializes writes behind a 5s SQLite busy_timeout, so a high
    # writer count trades throughput for lock-wait 500s. 3 writers keeps contention low while still
    # ~doubling single-thread throughput; _post_capture retries any residual busy 500 patiently.
    t0 = time.time()
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(_post_capture, items))
    return time.time() - t0


def settle(timeout: float = 45.0, quiet_rounds: int = 3) -> None:
    """Wait for post-seed background work (extraction/embedding/indexing) to quiesce before we
    measure. Auto-approved captures kick off async processing that holds the SQLite write lock;
    hammering /v1/context (esp. the session-delta WRITE path) mid-drain starves that write past
    busy_timeout and shows up as a connect timeout. Poll /v1/stats until the memory count stops
    moving for `quiet_rounds` consecutive polls (or `timeout` elapses), then a short breather."""
    last = None
    stable = 0
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            _status, stats = _request("GET", "/v1/stats", timeout=10.0)
        except Exception:
            time.sleep(0.5)
            continue
        count = int(stats.get("total_memories") or stats.get("memories") or stats.get("count") or 0)
        if count == last:
            stable += 1
            if stable >= quiet_rounds:
                break
        else:
            stable = 0
            last = count
        time.sleep(0.5)
    time.sleep(0.5)


# --------------------------------------------------------------------------------------------
# Main
# --------------------------------------------------------------------------------------------
def main() -> int:
    workdir = Path(tempfile.mkdtemp(prefix="cmp-scale-bench-"))
    proc: subprocess.Popen | None = None
    results: list[dict] = []
    try:
        print(f"[boot] launching backend from {BACKEND} on {BASE} (hash embedder, temp {workdir.name})", flush=True)
        proc = boot_server(workdir)
        print("[boot] backend healthy", flush=True)

        full = build_corpus(max(SCALES))
        queries = mixed_queries()
        seeded_so_far = 0
        for scale in SCALES:
            chunk = full[seeded_so_far:scale]
            dt = seed(chunk)
            seeded_so_far = scale
            rate = (len(chunk) / dt) if dt else 0.0
            print(f"[seed] +{len(chunk)} -> {scale} memories in {dt:.1f}s ({rate:.0f}/s)", flush=True)
            settle()

            p50, p95, n = measure_latency(queries)
            claude = measure_packing("claude")
            cursor = measure_packing("cursor")
            delta = measure_delta(session_id=f"cmp-delta-{scale}")
            density = measure_density(tight_budget=1000)

            row = {
                "scale": scale, "p50": p50, "p95": p95, "n_queries": n,
                "claude": claude, "cursor": cursor, "delta": delta, "density": density,
            }
            results.append(row)
            print(
                f"[scale {scale:>4}] latency p50={p50:6.1f}ms p95={p95:6.1f}ms | "
                f"claude used={claude['used']:>5}/{claude['tokens']} ({claude['util']*100:4.1f}%, {claude['items']} items) | "
                f"cursor used={cursor['used']:>5}/{cursor['tokens']} ({cursor['util']*100:4.1f}%, {cursor['items']} items) | "
                f"delta save={delta['savings_pct']:4.1f}% | facts/1k={density['facts_per_1k']:.1f}",
                flush=True,
            )

        # ------------------------------------------------------------------------------------
        # Summary table
        # ------------------------------------------------------------------------------------
        print("\n" + "=" * 104)
        print("CMP SCALE BENCH — retrieval efficiency at scale (hash embedder, single local backend)")
        print("=" * 104)
        header = (
            f"{'memories':>9} | {'p50 ms':>7} | {'p95 ms':>7} | "
            f"{'claude pack':>18} | {'cursor pack':>18} | {'delta save':>10} | {'facts/1k':>9}"
        )
        print(header)
        print("-" * len(header))
        for r in results:
            c, u, d, dn = r["claude"], r["cursor"], r["delta"], r["density"]
            print(
                f"{r['scale']:>9} | {r['p50']:>7.1f} | {r['p95']:>7.1f} | "
                f"{c['used']:>5}/{c['tokens']:<5} {c['util']*100:>4.0f}% {c['items']:>2}i | "
                f"{u['used']:>5}/{u['tokens']:<5} {u['util']*100:>4.0f}% {u['items']:>2}i | "
                f"{d['savings_pct']:>9.1f}% | {dn['facts_per_1k']:>9.1f}"
            )
        print("-" * len(header))
        biggest = results[-1]
        d = biggest["delta"]
        print(
            f"\nAt {biggest['scale']} memories: session-delta sends only {d['delta_total']} tokens vs "
            f"{d['naive_total']} for naive full-resend across 5 turns "
            f"({d['savings_pct']:.1f}% saved — pay once per fact)."
        )
        print("Per-turn delta (largest scale):")
        for t in d["per_turn"]:
            print(
                f"   turn {t['turn']}: pack {t['pack_items']} items / {t['pack_tokens']} tok  ->  "
                f"delta {t['new']} new / {t['new_tokens']} tok"
            )
        print(
            f"Model-aware packing shrinks the pack for the small model: claude budget "
            f"{biggest['claude']['tokens']} vs cursor {biggest['cursor']['tokens']} "
            f"(cursor keeps {biggest['cursor']['items']} of {biggest['claude']['items']} items, "
            f"utilization {biggest['cursor']['util']*100:.0f}% vs {biggest['claude']['util']*100:.0f}%)."
        )

        # ------------------------------------------------------------------------------------
        # Efficiency FLOORS
        # ------------------------------------------------------------------------------------
        failures: list[str] = []
        big = results[-1]

        if big["p95"] > LATENCY_P95_CEILING_MS:
            failures.append(
                f"latency p95 {big['p95']:.1f}ms exceeds ceiling {LATENCY_P95_CEILING_MS:.0f}ms at {big['scale']} memories"
            )

        for r in results:
            frac = r["delta"]["fraction_of_naive"]
            if not (frac < DELTA_MAX_FRACTION_OF_NAIVE):
                failures.append(
                    f"session-delta at {r['scale']} is {frac*100:.1f}% of naive (must be < {DELTA_MAX_FRACTION_OF_NAIVE*100:.0f}%)"
                )

        for r in results:
            for prof in ("claude", "cursor"):
                m = r[prof]
                if m["used"] <= 0:
                    failures.append(f"{prof} used_tokens is {m['used']} at {r['scale']} (empty pack)")
                if m["used"] > m["tokens"]:
                    failures.append(
                        f"{prof} overflowed budget at {r['scale']}: used {m['used']} > budget {m['tokens']}"
                    )
            # small-model pack must not be larger than the big-model pack (model-aware shrink holds)
            if r["cursor"]["items"] > r["claude"]["items"]:
                failures.append(
                    f"cursor pack ({r['cursor']['items']} items) larger than claude ({r['claude']['items']}) at {r['scale']}"
                )
            # the tighter budget must genuinely bind (cursor cap < claude cap)
            if not (r["cursor"]["tokens"] < r["claude"]["tokens"]):
                failures.append(f"cursor budget not tighter than claude at {r['scale']}")

        for r in results:
            fpk = r["density"]["facts_per_1k"]
            if not (fpk >= DENSITY_FLOOR_FACTS_PER_1K):
                failures.append(
                    f"density at {r['scale']} is {fpk:.1f} facts/1k (must be >= {DENSITY_FLOOR_FACTS_PER_1K})"
                )

        print("\n" + "-" * 104)
        if failures:
            print("CMP SCALE BENCH FAILED:")
            for f in failures:
                print(f"  ✗ {f}")
            return 1

        print(
            "CMP SCALE BENCH OK — "
            f"p95 {big['p95']:.0f}ms <= {LATENCY_P95_CEILING_MS:.0f}ms @ {big['scale']} memories; "
            f"delta {big['delta']['fraction_of_naive']*100:.0f}% of naive (>{100-DELTA_MAX_FRACTION_OF_NAIVE*100:.0f}% saved); "
            f"cursor packs {big['cursor']['items']}/{big['claude']['items']} of claude items; "
            f"density {big['density']['facts_per_1k']:.1f} facts/1k."
        )
        return 0
    finally:
        if proc is not None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
