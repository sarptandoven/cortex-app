from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request


REQUIRED_FEATURES = {"local-vault", "trust-controls", "installer-updates", "reliability-hardening", "simple-product-loop", "operational-readiness"}


def request(base_url: str, token: str, path: str) -> dict:
    headers = {"Authorization": f"Bearer {token}"}
    req = urllib.request.Request(base_url.rstrip("/") + path, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fail(message: str, payload: dict | None = None) -> None:
    print(json.dumps({"status": "failed", "message": message, "payload": payload or {}}, indent=2), file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate that a running Cortex backend has the expected reliability contract.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--token", default="")
    args = parser.parse_args()
    if not args.token:
        fail("Pass --token with the local Cortex API token.")

    try:
        health = request(args.base_url, args.token, "/health")
    except urllib.error.URLError as exc:
        fail(f"Backend is unreachable: {exc}")

    if health.get("status") != "ok":
        fail("Health endpoint is not ok.", health)
    if int(health.get("health_contract") or 0) < 3:
        fail("Backend health contract is too old.", health)
    missing_features = sorted(REQUIRED_FEATURES - set(health.get("features") or []))
    if missing_features:
        fail("Backend is missing required reliability features.", {"missing_features": missing_features, "health": health})

    diagnostics = request(args.base_url, args.token, "/v1/diagnostics")
    if diagnostics.get("quick_check") != "ok":
        fail("SQLite quick_check failed.", diagnostics)
    if not diagnostics.get("vault") or diagnostics["vault"].get("format") != "cortex-local-vault":
        fail("Vault diagnostics are missing or malformed.", diagnostics)

    report = request(args.base_url, args.token, "/v1/reliability/report")
    if int(report.get("health_contract") or 0) < 3:
        fail("Reliability report contract is too old.", report)
    check_names = {check.get("name") for check in report.get("checks", [])}
    required_checks = {"sqlite_quick_check", "search_index", "relationships", "vault_layout", "backup_recency", "vector_index"}
    if not required_checks.issubset(check_names):
        fail("Reliability report is missing expected checks.", {"check_names": sorted(check_names), "report": report})
    critical = [check for check in report["checks"] if check.get("status") == "critical"]
    if critical:
        fail("Reliability report contains critical failures.", {"critical": critical})

    ready = request(args.base_url, args.token, "/ready")
    if ready.get("status") != "ok":
        fail("Ready endpoint is not ok.", ready)

    support = request(args.base_url, args.token, "/v1/support/bundle")
    if int(support.get("bundle_schema") or 0) < 1:
        fail("Support bundle schema is missing or too old.", support)
    privacy = support.get("privacy") or {}
    if privacy.get("contains_raw_capture_text") or privacy.get("contains_memory_content") or privacy.get("contains_context_pack"):
        fail("Support bundle contains user content flags.", support)

    print(json.dumps({
        "status": "ok",
        "backend_version": health["backend_version"],
        "health_contract": health["health_contract"],
        "reliability_status": report["status"],
        "support_bundle_schema": support["bundle_schema"],
        "vault_path": health["vault_path"],
        "checks": {check["name"]: check["status"] for check in report["checks"]},
    }, indent=2))


if __name__ == "__main__":
    main()
