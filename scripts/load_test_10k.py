"""10k-user hosted load test (roadmap J).

Boots the FastAPI hosted plane under uvicorn in sharded-SQLite mode (the sanctioned 10k
tier), provisions N tenants through the real control plane, seeds a corpus for a sample of
them, then drives steady mixed traffic (search/ask/context/recent/stats/capture) with a
thread pool and reports throughput + latency percentiles + error rate against pass/fail
gates.

Usage:
  python3 scripts/load_test_10k.py --users 10000 --seed-users 150 --duration 60 --concurrency 32

Everything is stdlib except the backend's own dependencies (uvicorn/fastapi, dev-only).
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import random
import signal
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ADMIN_TOKEN = "load-test-admin-token"

SEED_FACTS = [
    "We decided to use PostgreSQL for the Atlas project because of jsonb support.",
    "Maria Chen is the lead designer on Atlas.",
    "I prefer to decline meetings before 10am.",
    "Never deploy Atlas on Fridays.",
    "The Q3 roadmap prioritizes the mobile onboarding flow.",
]
QUERIES = [
    "which database did we decide on for Atlas",
    "who is the lead designer",
    "what are my meeting preferences",
    "deploy policy for Atlas",
    "Q3 roadmap priorities",
]


def http(base: str, token: str, method: str, path: str, body: dict | None = None, user: str | None = None, timeout: float = 30.0):
    headers = {"Authorization": f"Bearer {token}"}
    if user:
        headers["X-Cortex-User"] = user
    data = None
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(base.rstrip("/") + path, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.status, resp.read()


def boot_server(port: int, shard_root: Path) -> subprocess.Popen:
    env = {
        **os.environ,
        "CORTEX_API_KEY": ADMIN_TOKEN,
        "CORTEX_SHARD_MODE": "bucket",
        "CORTEX_SHARD_ROOT": str(shard_root),
        "CORTEX_SHARD_COUNT": "16",
        "CORTEX_REQUIRE_SCOPED_API_TOKENS": "1",
        "CORTEX_DB_PATH": str(shard_root / "control" / "index.sqlite"),
        "CORTEX_VAULT_PATH": str(shard_root / "control" / "vault"),
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    (shard_root / "control").mkdir(parents=True, exist_ok=True)
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
        cwd=str(ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
    )
    deadline = time.time() + 60
    while time.time() < deadline:
        try:
            status, _ = http(f"http://127.0.0.1:{port}", ADMIN_TOKEN, "GET", "/health", timeout=3)
            if status == 200:
                return process
        except Exception:
            time.sleep(0.4)
    process.kill()
    raise RuntimeError("hosted plane did not become healthy within 60s")


def provision(base: str, index: int) -> dict | None:
    user_id = f"load-user-{index:05d}"
    try:
        status, raw = http(
            base, ADMIN_TOKEN, "POST", "/v1/admin/users",
            {"user_id": user_id, "plan": "free", "api_scopes": ["read", "write", "maintenance"], "mcp_scopes": ["read"]},
        )
    except urllib.error.HTTPError as exc:
        print(f"  provision {user_id} failed: {exc.code}", file=sys.stderr)
        return None
    payload = json.loads(raw)
    return {"user_id": user_id, "token": payload["api_token"]["token"]}


def seed_user(base: str, tenant: dict) -> bool:
    # Tenant isolation means even the admin token cannot act as another user in sharded
    # mode (correctly), so seeding is the real user journey: capture -> inbox -> approve,
    # all with the tenant's own scoped token.
    try:
        for fact in SEED_FACTS:
            http(base, tenant["token"], "POST", "/v1/captures?processing=sync", {"content": fact, "source": "macos"}, user=tenant["user_id"])
        status, raw = http(base, tenant["token"], "GET", "/v1/inbox", user=tenant["user_id"])
        inbox = json.loads(raw)
        for item in inbox.get("results") or []:
            capture_id = item.get("capture_id") or item.get("id")
            if capture_id:
                http(base, tenant["token"], "POST", f"/v1/captures/{capture_id}/approve", user=tenant["user_id"])
        return True
    except Exception as exc:
        print(f"  seed {tenant['user_id']} failed: {exc}", file=sys.stderr)
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--users", type=int, default=10000)
    parser.add_argument("--seed-users", type=int, default=150)
    parser.add_argument("--duration", type=int, default=60, help="steady-traffic seconds")
    parser.add_argument("--concurrency", type=int, default=32)
    parser.add_argument("--port", type=int, default=8850)
    parser.add_argument("--seed", type=int, default=10007)
    parser.add_argument("--max-p95-ms", type=float, default=750.0, help="p95 gate for interactive ops (search/ask/recent/stats/capture)")
    parser.add_argument("--max-context-p95-ms", type=float, default=3000.0, help="p95 gate for the context engine (a once-per-session call that composes many retrievals)")
    parser.add_argument("--max-error-rate", type=float, default=0.005)
    parser.add_argument("--base-url", default="", help="target an already-running plane instead of booting one")
    args = parser.parse_args()
    rng = random.Random(args.seed)

    tmp = None
    process = None
    if args.base_url:
        base = args.base_url
    else:
        tmp = tempfile.TemporaryDirectory(prefix="cortex-load-")
        shard_root = Path(tmp.name)
        print(f"booting hosted plane (sharded-SQLite bucket mode) on :{args.port} ...")
        process = boot_server(args.port, shard_root)
        base = f"http://127.0.0.1:{args.port}"

    try:
        print(f"provisioning {args.users} tenants through the control plane ...")
        started = time.time()
        tenants: list[dict] = []
        with futures.ThreadPoolExecutor(max_workers=16) as pool:
            for result in pool.map(lambda i: provision(base, i), range(args.users)):
                if result:
                    tenants.append(result)
                if len(tenants) % 1000 == 0 and len(tenants) > 0:
                    print(f"  {len(tenants)} provisioned ({time.time() - started:.0f}s)")
        provision_seconds = time.time() - started
        print(f"provisioned {len(tenants)}/{args.users} in {provision_seconds:.0f}s")
        if len(tenants) < args.users * 0.99:
            print("FAIL: >1% of provisioning calls failed", file=sys.stderr)
            return 1

        seeded = tenants[: args.seed_users]
        print(f"seeding {len(seeded)} tenants with a small corpus ...")
        with futures.ThreadPoolExecutor(max_workers=12) as pool:
            seed_ok = sum(1 for ok in pool.map(lambda t: seed_user(base, t), seeded) if ok)
        print(f"seeded {seed_ok}/{len(seeded)}")

        print(f"steady traffic: {args.concurrency} workers x {args.duration}s ...")
        stop_at = time.time() + args.duration
        lock = threading.Lock()
        latencies: dict[str, list[float]] = {}
        errors: list[str] = []
        requests_done = [0]

        ops = [
            ("search", 30), ("ask", 20), ("context", 15), ("recent", 15), ("stats", 10), ("capture", 10),
        ]
        op_names = [name for name, weight in ops for _ in range(weight)]

        def one_request(worker_rng: random.Random) -> None:
            # Reads hit any tenant; writes and rich retrieval bias to seeded tenants so the
            # mix exercises both cold and warm shards.
            op = worker_rng.choice(op_names)
            tenant = worker_rng.choice(seeded if op in {"ask", "context", "capture"} and seeded else tenants)
            query = urllib.parse.quote(worker_rng.choice(QUERIES))
            try:
                begin = time.perf_counter()
                if op == "search":
                    http(base, tenant["token"], "GET", f"/v1/search?query={query}&limit=5", user=tenant["user_id"], timeout=15)
                elif op == "ask":
                    http(base, tenant["token"], "GET", f"/v1/ask?query={query}", user=tenant["user_id"], timeout=15)
                elif op == "context":
                    http(base, tenant["token"], "GET", f"/v1/context?task={query}&token_budget=1200", user=tenant["user_id"], timeout=15)
                elif op == "recent":
                    http(base, tenant["token"], "GET", "/v1/recent?limit=5", user=tenant["user_id"], timeout=15)
                elif op == "stats":
                    http(base, tenant["token"], "GET", "/v1/stats", user=tenant["user_id"], timeout=15)
                else:
                    http(
                        base, tenant["token"], "POST", "/v1/captures?processing=sync",
                        {"content": f"Steady-load note {worker_rng.randrange(10**6)}: checked the Atlas dashboard.", "source": "macos"},
                        user=tenant["user_id"], timeout=20,
                    )
                elapsed_ms = (time.perf_counter() - begin) * 1000
                with lock:
                    latencies.setdefault(op, []).append(elapsed_ms)
                    requests_done[0] += 1
            except Exception as exc:
                with lock:
                    errors.append(f"{op}: {exc}")

        def worker(worker_index: int) -> None:
            worker_rng = random.Random(args.seed + worker_index)
            while time.time() < stop_at:
                one_request(worker_rng)

        threads = [threading.Thread(target=worker, args=(index,)) for index in range(args.concurrency)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        total = requests_done[0]
        error_count = len(errors)
        error_rate = error_count / max(total + error_count, 1)
        all_latencies = sorted(value for values in latencies.values() for value in values)

        def pct(values: list[float], p: float) -> float:
            if not values:
                return 0.0
            return values[min(int(len(values) * p), len(values) - 1)]

        print("\n==== RESULTS ====")
        print(f"tenants: {len(tenants)} | provision time: {provision_seconds:.0f}s")
        print(f"requests: {total} ok, {error_count} errors ({error_rate * 100:.2f}%) in {args.duration}s -> {total / args.duration:.1f} req/s")
        print(f"overall latency ms: p50={pct(all_latencies, 0.50):.0f} p95={pct(all_latencies, 0.95):.0f} p99={pct(all_latencies, 0.99):.0f}")
        for op in sorted(latencies):
            values = sorted(latencies[op])
            print(f"  {op:8s} n={len(values):6d} p50={pct(values, 0.5):6.0f} p95={pct(values, 0.95):6.0f} p99={pct(values, 0.99):6.0f} max={values[-1]:6.0f}")
        if errors:
            print("sample errors:")
            for line in errors[:5]:
                print("  -", line)

        # Per-op-class gates: search/ask/recent/stats/capture are per-message interactive
        # calls; the context engine is a once-per-session assembly that composes many
        # retrievals, so it gets its own (looser) bound instead of hiding inside an average.
        interactive = sorted(
            value for op, values in latencies.items() if op != "context" for value in values
        )
        context_values = sorted(latencies.get("context") or [])
        interactive_p95 = pct(interactive, 0.95)
        context_p95 = pct(context_values, 0.95)
        passed = (
            error_rate <= args.max_error_rate
            and interactive_p95 <= args.max_p95_ms
            and context_p95 <= args.max_context_p95_ms
            and total > 0
        )
        print(
            f"\nGATES: interactive p95 {interactive_p95:.0f}ms <= {args.max_p95_ms:.0f}ms, "
            f"context p95 {context_p95:.0f}ms <= {args.max_context_p95_ms:.0f}ms, "
            f"error rate {error_rate * 100:.2f}% <= {args.max_error_rate * 100:.2f}% -> {'PASS' if passed else 'FAIL'}"
        )
        return 0 if passed else 1
    finally:
        if process is not None:
            process.send_signal(signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
        if tmp is not None:
            tmp.cleanup()


if __name__ == "__main__":
    sys.exit(main())
