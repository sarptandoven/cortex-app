from __future__ import annotations

import argparse
import ast
import hashlib
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

REQUIRED_HANDOFF_SECTIONS = (
    "## Generated Artifact Verification",
    "## Install And Run The Packaged App",
    "## Update Or Roll Back",
    "## Required Manual QA",
    "## Known Limitations",
)

REQUIRED_BETA_READINESS_KEYS = (
    "manual_qa_required",
    "install_steps",
    "update_steps",
    "rollback_steps",
    "known_limitations",
    "manual_qa_checklist",
    "generated_artifact_verification",
)

REQUIRED_MANUAL_QA_TERMS = (
    "clean macos",
    "first-run",
    "sources",
    "review",
    "ask",
    "trust",
    "backup",
    "support bundle",
    "roll back",
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


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def checksum_entries(path: Path) -> dict[str, str]:
    entries: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        parts = line.split()
        if len(parts) < 2:
            raise ValueError(f"Malformed checksum line: {raw_line}")
        digest, filename = parts[0].lower(), parts[-1].lstrip("*")
        entries[filename] = digest
    return entries


def verify_release_artifacts(release_dir: Path, strict_beta_metadata: bool) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    artifact_summaries: list[dict] = []

    def beta_issue(message: str) -> None:
        if strict_beta_metadata:
            errors.append(message)
        else:
            warnings.append(message)

    if not release_dir.exists():
        return {"ok": False, "release_dir": str(release_dir), "errors": [f"Release directory does not exist: {release_dir}"], "warnings": warnings, "artifacts": artifact_summaries}

    manifest_path = release_dir / "latest.json"
    if not manifest_path.exists():
        return {"ok": False, "release_dir": str(release_dir), "errors": ["latest.json is missing"], "warnings": warnings, "artifacts": artifact_summaries}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "release_dir": str(release_dir), "errors": [f"latest.json is invalid JSON: {exc}"], "warnings": warnings, "artifacts": artifact_summaries}

    version = str(manifest.get("version", ""))
    build = str(manifest.get("build", ""))
    checksums_path = release_dir / f"Cortex-{version}-{build}.checksums.txt"
    handoff_path = release_dir / "BETA_HANDOFF.md"

    artifacts = manifest.get("artifacts")
    if not isinstance(artifacts, list) or not artifacts:
        errors.append("latest.json must include non-empty artifacts.")
        artifacts = []

    kinds = {str(artifact.get("kind")) for artifact in artifacts if isinstance(artifact, dict)}
    for required_kind in ("dmg", "zip"):
        if required_kind not in kinds:
            errors.append(f"latest.json must include a {required_kind} artifact.")

    try:
        checksums = checksum_entries(checksums_path)
    except FileNotFoundError:
        errors.append(f"Checksum file is missing: {checksums_path.name}")
        checksums = {}
    except ValueError as exc:
        errors.append(str(exc))
        checksums = {}

    for artifact in artifacts:
        if not isinstance(artifact, dict):
            errors.append("latest.json contains a non-object artifact.")
            continue
        filename = str(artifact.get("filename", ""))
        if not filename:
            errors.append("Artifact is missing filename.")
            continue
        path = release_dir / filename
        if not path.exists():
            errors.append(f"Artifact file is missing: {filename}")
            continue
        actual_size = path.stat().st_size
        if actual_size <= 0:
            errors.append(f"Artifact is empty: {filename}")
        try:
            expected_size = int(artifact.get("size_bytes", -1))
        except (TypeError, ValueError):
            expected_size = -1
        if actual_size != expected_size:
            errors.append(f"Artifact size mismatch for {filename}: manifest={expected_size} actual={actual_size}")
        actual_hash = sha256(path)
        expected_hash = str(artifact.get("sha256", "")).lower()
        if actual_hash != expected_hash:
            errors.append(f"Artifact sha256 mismatch for {filename}: manifest={expected_hash} actual={actual_hash}")
        checksum_hash = checksums.get(filename)
        if checksum_hash != expected_hash:
            errors.append(f"Checksum file mismatch for {filename}: checksums={checksum_hash} manifest={expected_hash}")
        artifact_summaries.append({"kind": artifact.get("kind"), "filename": filename, "size_bytes": actual_size, "sha256": actual_hash})

    if not handoff_path.exists():
        beta_issue("BETA_HANDOFF.md is missing.")
    else:
        handoff_text = handoff_path.read_text(encoding="utf-8")
        missing_sections = [section for section in REQUIRED_HANDOFF_SECTIONS if section not in handoff_text]
        if missing_sections:
            beta_issue(f"BETA_HANDOFF.md is missing sections: {', '.join(missing_sections)}")

    beta_readiness = manifest.get("beta_readiness")
    if not isinstance(beta_readiness, dict):
        beta_issue("latest.json is missing beta_readiness metadata.")
    else:
        missing_keys = [key for key in REQUIRED_BETA_READINESS_KEYS if key not in beta_readiness]
        if missing_keys:
            beta_issue(f"beta_readiness is missing keys: {', '.join(missing_keys)}")
        if beta_readiness.get("manual_qa_required") is not True:
            beta_issue("beta_readiness.manual_qa_required must be true.")
        qa_text = "\n".join(str(item) for item in beta_readiness.get("manual_qa_checklist", [])).lower()
        missing_terms = [term for term in REQUIRED_MANUAL_QA_TERMS if term not in qa_text]
        if missing_terms:
            beta_issue(f"beta_readiness.manual_qa_checklist is missing terms: {', '.join(missing_terms)}")

    return {
        "ok": not errors,
        "release_dir": str(release_dir),
        "strict_beta_metadata": strict_beta_metadata,
        "errors": errors,
        "warnings": warnings,
        "artifacts": artifact_summaries,
        "handoff": str(handoff_path),
        "checksums": str(checksums_path),
    }


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
    parser.add_argument("--token", default="")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--refresh-site", action="store_true", help="Run prepare_distribution_site.py before validating the static site.")
    parser.add_argument("--include-package", action="store_true", help="Run macos/package_release.sh. This may require macOS disk-image permissions.")
    parser.add_argument("--output-root", default=None, help="Directory containing packaged Cortex-* releases. Defaults to the workspace outputs directory.")
    parser.add_argument("--release-dir", type=Path, help="Specific packaged Cortex release directory to verify.")
    parser.add_argument("--require-package-artifacts", action="store_true", help="Fail unless release artifacts, checksums, beta handoff, and beta-readiness manifest metadata verify.")
    parser.add_argument("--require-live", action="store_true", help="Fail if the running local backend cannot pass live checks.")
    args = parser.parse_args()
    if args.require_live and not args.token:
        raise SystemExit("Pass --token when using --require-live.")

    root = repo_root()
    output_root = Path(args.output_root).expanduser() if args.output_root else workspace_root(root) / "outputs"
    release_dir_arg = None
    if args.release_dir:
        release_dir_arg = args.release_dir.expanduser()
        if not release_dir_arg.is_absolute():
            release_dir_arg = root / release_dir_arg
        release_dir_arg = release_dir_arg.resolve()
    checks: list[dict] = []

    syntax = ast_syntax_check(root)
    add_check(checks, "python_syntax", syntax["ok"], f"Parsed {syntax['files_checked']} Python files.", syntax)

    missing_docs = [path for path in REQUIRED_DOCS if not (root / path).exists()]
    add_check(checks, "operator_docs", not missing_docs, "Required operator docs exist.", {"missing": missing_docs})
    docs_current_result = run_command(root, [sys.executable, "scripts/check_docs_current.py"], timeout=60)
    add_check(checks, "docs_current", docs_current_result["ok"], "Beta docs match the current five-tab local-first app and release manifest.", docs_current_result)
    smoke_result = run_command(root, [sys.executable, "scripts/backend_beta_smoke.py"], timeout=120)
    add_check(checks, "backend_beta_smoke", smoke_result["ok"], "Backend beta smoke passes with temp data and blocked network sockets.", smoke_result)

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

    release_dir = release_dir_arg or latest_release_dir(output_root)
    strict_package_artifacts = args.include_package or args.require_package_artifacts or release_dir_arg is not None
    if release_dir:
        release_manifest_result = run_command(root, [sys.executable, "scripts/validate_update_manifest.py", str(release_dir / "latest.json")], timeout=60)
        add_check(checks, "release_update_manifest", release_manifest_result["ok"], "Latest packaged release update feed validates.", release_manifest_result)
        release_artifacts = verify_release_artifacts(release_dir, strict_beta_metadata=strict_package_artifacts)
        if strict_package_artifacts:
            release_detail = "Packaged DMG, ZIP, checksums, latest.json, BETA_HANDOFF, and beta-readiness metadata verify."
        else:
            release_detail = "Packaged DMG, ZIP, checksums, and latest.json verify. Beta handoff/readiness metadata warnings are nonblocking unless --require-package-artifacts or --include-package is used."
        add_check(checks, "release_artifacts", release_artifacts["ok"], release_detail, release_artifacts)
    elif args.include_package or args.refresh_site or args.require_package_artifacts:
        add_check(checks, "release_update_manifest", False, f"No packaged Cortex release found under {output_root}.")
        add_check(checks, "release_artifacts", False, f"No packaged Cortex release found under {output_root}.")
    else:
        add_check(checks, "release_update_manifest", True, f"No packaged Cortex release found under {output_root}; release manifest validation skipped for local readiness.")
        add_check(checks, "release_artifacts", True, f"No packaged Cortex release found under {output_root}; package artifact verification skipped for local readiness.")

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
