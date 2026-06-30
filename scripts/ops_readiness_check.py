from __future__ import annotations

import argparse
import ast
import json
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


REQUIRED_DOCS = (
    "docs/ARCHITECTURE.md",
    "docs/DISTRIBUTION.md",
    "docs/INSTALLER_AND_UPDATES.md",
    "docs/OPERATIONAL_READINESS.md",
    "docs/PRODUCTION_READINESS.md",
    "docs/RELIABILITY_HARDENING.md",
    "docs/TRUST_CONTROLS.md",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def workspace_root(root: Path) -> Path:
    return root.parent.parent if root.parent.name == "work" else root


def run_command(root: Path, command: list[str], timeout: int = 120) -> dict:
    completed = subprocess.run(command, cwd=root, text=True, capture_output=True, timeout=timeout)
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-4000:],
        "stderr": completed.stderr[-4000:],
        "ok": completed.returncode == 0,
    }


def add_check(checks: list[dict], name: str, ok: bool, detail: str, payload: dict | None = None) -> None:
    checks.append({"name": name, "status": "ok" if ok else "failed", "detail": detail, "payload": payload or {}})


def ast_syntax_check(root: Path) -> dict:
    errors: list[dict] = []
    for path in sorted([*root.glob("backend/app/*.py"), *root.glob("backend/tests/*.py"), *root.glob("scripts/*.py")]):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append({"path": str(path.relative_to(root)), "line": exc.lineno, "message": exc.msg})
    return {"ok": not errors, "errors": errors, "files_checked": len([*root.glob("backend/app/*.py"), *root.glob("backend/tests/*.py"), *root.glob("scripts/*.py")])}


def latest_release_dir(output_root: Path) -> Path | None:
    outputs = output_root.expanduser().resolve()
    candidates = [path for path in outputs.glob("Cortex-*") if path.is_dir() and (path / "latest.json").exists()]
    if not candidates:
        return None
    return max(candidates, key=lambda path: (path / "latest.json").stat().st_mtime)


def live_get(base_url: str, token: str, path: str) -> dict:
    request = urllib.request.Request(base_url.rstrip("/") + path, headers={"Authorization": f"Bearer {token}"}, method="GET")
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def offline_support_bundle(root: Path) -> dict:
    sys.path.insert(0, str(root))
    from backend.app.config import load_settings
    from backend.app.database import init_db
    from backend.app.storage import CortexStore

    settings = load_settings()
    with tempfile.TemporaryDirectory() as tmp:
        db_path = Path(tmp) / "ops-readiness.sqlite"
        vault_path = Path(tmp) / "Cortex.vault"
        init_db(db_path)
        store = CortexStore(db_path, vault_path)
        bundle = store.support_bundle(settings.default_user_id)
    return bundle


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cortex local operational readiness checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--token", default="dev-local-key")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--refresh-site", action="store_true", help="Run prepare_distribution_site.py before validating the static site.")
    parser.add_argument("--include-package", action="store_true", help="Run macos/package_release.sh. This may require macOS disk-image permissions.")
    parser.add_argument("--output-root", default=None, help="Directory containing packaged Cortex-* releases. Defaults to the workspace outputs directory.")
    parser.add_argument("--require-live", action="store_true", help="Fail if the running local backend cannot pass live checks.")
    args = parser.parse_args()

    root = repo_root()
    output_root = Path(args.output_root).expanduser() if args.output_root else workspace_root(root) / "outputs"
    checks: list[dict] = []

    syntax = ast_syntax_check(root)
    add_check(checks, "python_syntax", syntax["ok"], f"Parsed {syntax['files_checked']} Python files.", syntax)

    missing_docs = [path for path in REQUIRED_DOCS if not (root / path).exists()]
    add_check(checks, "operator_docs", not missing_docs, "Required operator docs exist.", {"missing": missing_docs})
    docs_current_result = run_command(root, [sys.executable, "scripts/check_docs_current.py"], timeout=60)
    add_check(checks, "docs_current", docs_current_result["ok"], "Beta docs match the current five-tab local-first app and release manifest.", docs_current_result)

    if not args.skip_tests:
        test_result = run_command(root, [sys.executable, "-W", "error::ResourceWarning", "-m", "unittest", "discover", "backend/tests"], timeout=120)
        add_check(checks, "backend_unit_tests", test_result["ok"], "Backend unit tests pass with ResourceWarning treated as an error.", test_result)
        retrieval_result = run_command(root, [sys.executable, "scripts/retrieval_eval.py"], timeout=120)
        add_check(checks, "retrieval_quality_eval", retrieval_result["ok"], "Retrieval quality eval passes noisy-import and layer-recall gates.", retrieval_result)
        adaptation_result = run_command(root, [sys.executable, "scripts/adaptation_eval.py"], timeout=120)
        add_check(checks, "adaptation_quality_eval", adaptation_result["ok"], "Agent adaptation eval passes layer, citation, safety, and pending-memory gates.", adaptation_result)

    if not args.skip_build:
        build_result = run_command(root, ["./macos/build.sh"], timeout=180)
        add_check(checks, "macos_build", build_result["ok"], "macOS app builds and signs locally.", build_result)

    if args.include_package:
        package_result = run_command(root, ["./macos/package_release.sh", "--output", str(output_root)], timeout=240)
        add_check(checks, "macos_package", package_result["ok"], "macOS DMG/ZIP package is generated.", package_result)

    if args.refresh_site:
        prepare_result = run_command(root, [sys.executable, "scripts/prepare_distribution_site.py"], timeout=60)
        add_check(checks, "prepare_distribution_site", prepare_result["ok"], "Static site downloads refreshed from latest package.", prepare_result)

    site_result = run_command(root, [sys.executable, "scripts/check_distribution_site.py"], timeout=60)
    add_check(checks, "distribution_site", site_result["ok"], "Static site links, artifacts, sizes, and hashes validate.", site_result)

    site_manifest_result = run_command(root, [sys.executable, "scripts/validate_update_manifest.py", "site/downloads/latest.json"], timeout=60)
    add_check(checks, "site_update_manifest", site_manifest_result["ok"], "Site update feed validates.", site_manifest_result)

    release_dir = latest_release_dir(output_root)
    if release_dir:
        release_manifest_result = run_command(root, [sys.executable, "scripts/validate_update_manifest.py", str(release_dir / "latest.json")], timeout=60)
        add_check(checks, "release_update_manifest", release_manifest_result["ok"], "Latest packaged release update feed validates.", release_manifest_result)
    elif args.include_package or args.refresh_site:
        add_check(checks, "release_update_manifest", False, f"No packaged Cortex release found under {output_root}.")
    else:
        add_check(checks, "release_update_manifest", True, f"No packaged Cortex release found under {output_root}; release manifest validation skipped for local readiness.")

    try:
        bundle = offline_support_bundle(root)
        bundle_ok = (
            bundle.get("bundle_schema") == 1
            and bundle.get("privacy", {}).get("contains_raw_capture_text") is False
            and "operational-readiness" in bundle.get("backend", {}).get("features", [])
        )
        add_check(checks, "offline_support_bundle", bundle_ok, "Offline support bundle can be generated without user content.", {"bundle_schema": bundle.get("bundle_schema"), "summary": bundle.get("summary")})
    except Exception as exc:
        add_check(checks, "offline_support_bundle", False, "Offline support bundle failed.", {"error": str(exc)})

    try:
        health = live_get(args.base_url, args.token, "/health")
        reliability = live_get(args.base_url, args.token, "/v1/reliability/report")
        support = live_get(args.base_url, args.token, "/v1/support/bundle")
        live_ok = (
            health.get("status") == "ok"
            and int(health.get("health_contract") or 0) >= 3
            and reliability.get("health_contract") >= 3
            and support.get("bundle_schema") == 1
        )
        add_check(checks, "live_backend_optional", live_ok, "Running backend exposes health, reliability, and support-bundle contracts.", {"health": health, "reliability_status": reliability.get("status"), "support_schema": support.get("bundle_schema")})
    except (urllib.error.URLError, TimeoutError) as exc:
        add_check(checks, "live_backend_optional", not args.require_live, "Running backend was not reachable." if not args.require_live else "Running backend is required but unreachable.", {"error": str(exc), "required": args.require_live})

    failures = [check for check in checks if check["status"] != "ok"]
    payload = {
        "status": "failed" if failures else "ok",
        "checks": checks,
        "failures": [{"name": check["name"], "detail": check["detail"], "payload": check["payload"]} for check in failures],
    }
    print(json.dumps(payload, indent=2))
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
