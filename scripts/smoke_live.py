#!/usr/bin/env python3
"""Smoke-test a LIVE Cortex backend over its public URL.

Read-only by default (health + readiness); `--deep` runs the full product loop
end to end against the running server: signup -> login -> mint scoped token ->
capture -> approve -> ask, and asserts a CITED answer comes back, then cleans up
the scoped token it minted. Stdlib-only (matches the shipping backend), so it
runs anywhere python3 does.

    python3 scripts/smoke_live.py --base-url https://api.signindoppl.com
    python3 scripts/smoke_live.py --base-url https://<host> --deep

Exit code 0 = all checks passed, 1 = something failed. Suitable for a cron /
uptime check. `--deep` writes one throwaway account to the server; the account
remains (accounts have no self-delete here) but its scoped token is revoked.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request


def _call(base, method, path, body=None, token=None, q=None, timeout=45):
    url = base.rstrip("/") + path + ("?" + urllib.parse.urlencode(q) if q else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if body is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode() or "{}"), time.time() - t0
    except urllib.error.HTTPError as e:
        try:
            payload = json.loads(e.read().decode() or "{}")
        except Exception:
            payload = {}
        return e.code, payload, time.time() - t0
    except Exception as e:  # network / TLS / DNS — the ephemeral-tunnel-died case
        return 0, {"error": str(e)}, time.time() - t0


def _ok(label, cond, detail=""):
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}" + (f"  ({detail})" if detail else ""))
    return bool(cond)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--base-url", required=True, help="public backend URL, e.g. https://api.signindoppl.com")
    ap.add_argument("--deep", action="store_true", help="run the full signup->capture->approve->ask loop")
    args = ap.parse_args()
    base = args.base_url
    passed = True

    print(f"== Cortex live smoke: {base} ==")
    s, health, dt = _call(base, "GET", "/health")
    passed &= _ok("/health 200", s == 200, f"http {s}  {dt*1000:.0f}ms")
    if s == 200:
        passed &= _ok("health status ok", health.get("status") == "ok", f"auth={health.get('auth')}")
        emb = (((health.get("hosted_readiness") or {}).get("runtime") or {}).get("storage") or {}).get("embedding") or {}
        if emb and not emb.get("index_compatible", True):
            print(f"  [WARN] vector index dim mismatch: model={emb.get('dimensions')} "
                  f"schema={emb.get('schema_dimensions')} — semantic search may fall back to keyword.")
    s, ready, dt = _call(base, "GET", "/ready")
    passed &= _ok("/ready 200 + ok", s == 200 and ready.get("status") == "ok", f"http {s}  {dt*1000:.0f}ms")

    if not args.deep:
        print("PASS" if passed else "FAIL", "(shallow: pass --deep for the full product loop)")
        return 0 if passed else 1

    print("-- deep: full product loop --")
    email = f"smoke-{int(time.time())}@trydoppl.com"
    pw = f"smoke-longpassword-{int(time.time())}-cortex"
    _call(base, "POST", "/v1/auth/signup", {"email": email, "password": pw, "display_name": "smoke"})
    s, login, _ = _call(base, "POST", "/v1/auth/login", {"email": email, "password": pw, "client": "smoke"})
    tok = login.get("access_token") if isinstance(login, dict) else None
    active = ((login.get("account") or {}).get("status") == "active") if isinstance(login, dict) else False
    passed &= _ok("signup+login -> active session", s == 200 and bool(tok) and active)
    if not tok:
        print("FAIL (cannot continue without a session token)")
        return 1

    s, mint, _ = _call(base, "POST", "/v1/auth/tokens", {"audience": "api", "label": "smoke"}, token=tok)
    api = mint.get("token") if isinstance(mint, dict) else None
    passed &= _ok("mint scoped API token", s == 201 and bool(api))

    s, cap, _ = _call(base, "POST", "/v1/captures",
                      {"content": "My favorite programming language is Rust. I prefer dark-roast coffee.",
                       "source": "smoke", "title": "smoke facts"}, token=api)
    cid = cap.get("capture_id") if isinstance(cap, dict) else None
    passed &= _ok("capture accepted", s == 200 and bool(cid))

    if cid:
        s, _, _ = _call(base, "POST", f"/v1/captures/{cid}/approve", token=api)
        passed &= _ok("approve capture (leave Review)", s == 200)

    s, ans, dt = _call(base, "GET", "/v1/ask", token=api, q={"query": "favorite programming language"})
    answer = (ans.get("answer") or "") if isinstance(ans, dict) else ""
    cites = ans.get("citations") or [] if isinstance(ans, dict) else []
    passed &= _ok("ask returns a CITED answer", s == 200 and len(cites) >= 1 and "rust" in answer.lower(),
                  f"{len(cites)} citation(s)  {dt*1000:.0f}ms")

    # cleanup: revoke the scoped token we minted (least-privilege hygiene)
    if api and isinstance(mint, dict) and mint.get("token_id"):
        _call(base, "DELETE", "/v1/auth/tokens/" + str(mint["token_id"]), token=tok)
        print("  [info] revoked the scoped token this run minted")

    print("PASS" if passed else "FAIL")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
