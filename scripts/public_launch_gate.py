#!/usr/bin/env python3
"""Cortex public-launch go/no-go gate.

Machine-checks the subset of docs/COMPLETE_LAUNCH_INSTRUCTIONS.txt PART 18 that a
program CAN verify against a running backend + the local repo, and clearly PRINTS
the human/external items it cannot (so an automated "green" is never mistaken for
"launch ready"). Exit code is non-zero if any automated check fails.

Usage:
  # Automated checks against a running (staging or prod) backend + local repo:
  python3 scripts/public_launch_gate.py --base-url https://api.trydoppl.com
  # Beta profile is more lenient (autoverify allowed, TLS/notarization optional):
  python3 scripts/public_launch_gate.py --base-url https://api.trydoppl.com --profile beta

Notes:
  - Provide --admin-token (or CORTEX_ADMIN_TOKEN) to also check admin-only surfaces.
  - Stdlib only; safe to run from CI or your laptop.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"


class Gate:
    def __init__(self) -> None:
        self.failures: list[str] = []
        self.warnings: list[str] = []

    def check(self, ok: bool, label: str, detail: str = "", *, warn_only: bool = False) -> bool:
        mark = f"{GREEN}PASS{RESET}" if ok else (f"{YELLOW}WARN{RESET}" if warn_only else f"{RED}FAIL{RESET}")
        print(f"  [{mark}] {label}" + (f"  {DIM}{detail}{RESET}" if detail else ""))
        if not ok:
            (self.warnings if warn_only else self.failures).append(label)
        return ok


def _get(base: str, path: str, token: str | None = None, timeout: float = 15.0):
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    req = urllib.request.Request(base.rstrip("/") + path, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as exc:
        return exc.code, None
    except Exception as exc:  # noqa: BLE001
        return 0, f"error: {exc}"


def automated_checks(base_url: str, admin_token: str | None, profile: str) -> Gate:
    gate = Gate()
    public = profile == "public"

    print(f"\n{'='*70}\nAUTOMATED CHECKS — backend at {base_url} (profile: {profile})\n{'='*70}")

    # TLS (public launch must be https; beta may be http/sslip for testing).
    gate.check(base_url.startswith("https://"), "API served over HTTPS", base_url, warn_only=not public)

    # Health.
    status, health = _get(base_url, "/health")
    ok_health = status == 200 and isinstance(health, dict) and health.get("status") == "ok"
    gate.check(ok_health, "GET /health is 200 and status ok", f"http {status}")

    # Readiness contract — the heart of the gate.
    status, ready = _get(base_url, "/ready")
    if isinstance(ready, dict) and "hosted_readiness" in ready:
        ready = ready["hosted_readiness"]
    if isinstance(ready, dict) and "checks" in ready:
        overall_ok = ready.get("status") == "ok"
        gate.check(overall_ok, "GET /ready overall status ok",
                   f"http {status}" if overall_ok else "see blocked checks below")
        for chk in ready.get("checks", []):
            name, cstatus = chk.get("name"), chk.get("status")
            # In beta these are advisory; in public they gate.
            gate.check(cstatus == "ok", f"readiness: {name}",
                       "" if cstatus == "ok" else str(chk.get("detail", ""))[:90],
                       warn_only=not public)
    else:
        # Non-hosted (local) mode returns a simpler /ready; that's a fail for a launch.
        gate.check(False, "GET /ready exposes the hosted readiness contract",
                   f"http {status} — is this deployment in hosted (bucket) mode?", warn_only=not public)

    # Accounts front door is live.
    status, _ = _get(base_url, "/account/signup")
    gate.check(status == 200, "Web signup page (/account/signup) is live", f"http {status}")

    # Auth API present (providers endpoint answers when auth is enabled).
    status, providers = _get(base_url, "/v1/auth/providers")
    gate.check(status == 200, "Auth API is enabled (/v1/auth/providers)", f"http {status}")
    if isinstance(providers, dict):
        names = [p.get("provider") or p.get("name") for p in (providers.get("providers") or providers.get("results") or [])]
        gate.check(bool(names), "At least one social login configured (GitHub/Google)",
                   f"providers: {names}", warn_only=True)

    # A session token must never reach admin (spot-check the boundary exists).
    status, _ = _get(base_url, "/v1/admin/users", token="cxs_not_a_real_session_token")
    gate.check(status in (401, 403), "Admin endpoints reject a session/bogus token", f"http {status}")

    # Admin-authenticated checks (optional).
    if admin_token:
        status, _ = _get(base_url, "/v1/admin/users", token=admin_token)
        gate.check(status == 200, "Admin token authenticates to the control plane", f"http {status}")

    return gate


def repo_checks(profile: str) -> Gate:
    gate = Gate()
    print(f"\n{'='*70}\nLOCAL REPO CHECKS\n{'='*70}")

    # Distribution site consistency (landing page + update manifest + artifact hashes).
    checker = ROOT / "scripts" / "check_distribution_site.py"
    if checker.exists():
        result = subprocess.run([sys.executable, str(checker)], capture_output=True, text=True)
        gate.check(result.returncode == 0, "Distribution site validates (check_distribution_site.py)",
                   (result.stdout or result.stderr).strip().splitlines()[-1] if (result.stdout or result.stderr).strip() else "",
                   warn_only=profile != "public")
    else:
        gate.check(False, "check_distribution_site.py present", warn_only=True)

    # Legal pages exist (drafts are fine to exist; lawyer review is a MANUAL item).
    for page in ("privacy.html", "terms.html", "subprocessors.html"):
        exists = (ROOT / "site" / page).exists()
        gate.check(exists, f"site/{page} exists", warn_only=profile != "public")

    # No false encryption claims anywhere on the site. A NEGATED mention ("it is NOT
    # zero-knowledge") is honest and allowed; only an affirmative claim fails.
    import re as _re

    bad = []
    for page in (ROOT / "site").glob("*.html"):
        text = page.read_text("utf-8", "replace").lower()
        for phrase in ("zero-knowledge", "end-to-end encrypt"):
            for m in _re.finditer(_re.escape(phrase), text):
                window = text[max(0, m.start() - 40): m.start()]
                if not _re.search(r"\b(not|isn't|is not|never|no|without)\b", window):
                    bad.append(f"{page.name} (affirmative '{phrase}')")
                    break
    gate.check(not bad, "No affirmative 'zero-knowledge'/'end-to-end' claims on the site", ", ".join(bad))

    return gate


MANUAL_ITEMS = [
    "Legal entity formed; privacy/terms reviewed by a lawyer; draft banners removed.",
    "support@ and privacy@ inboxes exist and are monitored.",
    "DMG is notarized (spctl accepts it) under the entity's Team ID.",
    "Download link works from another computer; checksum matches the manifest.",
    "Verification + reset emails land in a real Gmail AND Outlook inbox (not spam).",
    "Autoverify is OFF for public (CORTEX_AUTH_AUTOVERIFY=0) with real SMTP set.",
    "KEK escrowed in 2 places, neither in the data backup.",
    "Restore drill done in the last 30 days (data recovered + credential decrypted).",
    "Offsite backups confirmed; nightly backup green for 7 consecutive nights.",
    "Uptime monitor + phone alert verified by a forced failure; status page live.",
    "External port scan shows only 22/80/443 open.",
    "Google OAuth consent screen PUBLISHED (not 'testing'); GitHub login round-trips.",
    "Mac app: hosted sign-in works on a clean machine; capture -> cited answer; sign out.",
    "Signup flood from one IP gets rate-limited (memory flat); captcha if needed.",
    "(If charging) Paddle/Stripe live checkout flips the plan; cancel path tested.",
    "Usability test: >=80% of 5-8 non-technical users reach a cited answer unaided <10 min.",
    "Encrypted-credentials enforce flag flipped after backfill gauge hits 0.",
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base-url", help="Running backend to check (e.g. https://api.trydoppl.com)")
    ap.add_argument("--admin-token", default=os.environ.get("CORTEX_ADMIN_TOKEN", ""))
    ap.add_argument("--profile", choices=("beta", "public"), default="public",
                    help="'public' gates strictly; 'beta' downgrades TLS/readiness/site items to warnings")
    args = ap.parse_args()

    all_failures: list[str] = []
    all_warnings: list[str] = []

    if args.base_url:
        g = automated_checks(args.base_url, args.admin_token or None, args.profile)
        all_failures += g.failures
        all_warnings += g.warnings
    else:
        print(f"\n{YELLOW}No --base-url given; skipping live backend checks.{RESET}")

    g = repo_checks(args.profile)
    all_failures += g.failures
    all_warnings += g.warnings

    print(f"\n{'='*70}\nMANUAL ITEMS — a human must confirm these (cannot be auto-checked)\n{'='*70}")
    for item in MANUAL_ITEMS:
        print(f"  [ ] {item}")

    print(f"\n{'='*70}\nRESULT\n{'='*70}")
    if all_warnings:
        print(f"  {YELLOW}{len(all_warnings)} warning(s){RESET} (advisory for this profile): " + "; ".join(all_warnings))
    if all_failures:
        print(f"  {RED}NO-GO: {len(all_failures)} automated check(s) failed:{RESET} " + "; ".join(all_failures))
        print(f"  {DIM}Fix the failures, then confirm every MANUAL item above before launch.{RESET}")
        return 1
    print(f"  {GREEN}Automated checks passed.{RESET} Launch only when every MANUAL item above is also checked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
