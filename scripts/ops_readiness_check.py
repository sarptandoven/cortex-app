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

try:
    from scripts.export_support_bundle import validate_content_free_bundle
except ModuleNotFoundError:
    from export_support_bundle import validate_content_free_bundle


REQUIRED_DOCS = (
    "docs/ARCHITECTURE.md",
    "docs/DISTRIBUTION.md",
    "docs/FIRST100_CLEAN_PROFILE_QA.md",
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
    "connections",
    "privacy",
    "review",
    "ask",
    "backup",
    "support bundle",
    "roll back",
)

RELEASE_INPUT_PATH_PREFIXES = (
    ".github/workflows/",
    "backend/",
    "docs/",
    "macos/",
    "packages/",
    "scripts/",
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


def git_output(root: Path, command: list[str]) -> str:
    completed = subprocess.run(["git", *command], cwd=root, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def current_git_provenance(root: Path) -> dict:
    raw_status = git_output(root, ["status", "--porcelain", "--untracked-files=no"])
    source_status = [
        line
        for line in raw_status.splitlines()
        if "site/downloads/" not in line
    ]
    raw_untracked = git_output(root, ["status", "--porcelain", "--untracked-files=all"])
    untracked_release_inputs = sorted(
        path
        for line in raw_untracked.splitlines()
        if line.startswith("?? ")
        for path in [line[3:].strip()]
        if path and release_input_path(path)
    )
    return {
        "git_commit": git_output(root, ["rev-parse", "HEAD"]) or "unknown",
        "git_branch": git_output(root, ["branch", "--show-current"]) or "",
        "git_dirty": bool(source_status or untracked_release_inputs),
        "tracked_dirty_lines": source_status,
        "untracked_release_inputs": untracked_release_inputs,
        "ignored_dirty_paths": ["site/downloads/"],
    }


def release_ignored_path(path: str) -> bool:
    return path.startswith("site/downloads/")


def release_input_path(path: str) -> bool:
    if release_ignored_path(path) or path.startswith(".context/"):
        return False
    return path.startswith(RELEASE_INPUT_PATH_PREFIXES)


def source_changes_since(root: Path, commit: str) -> list[str]:
    if not commit:
        return []
    changed = git_output(root, ["diff", "--name-only", f"{commit}..HEAD"])
    return [path for path in changed.splitlines() if path and not release_ignored_path(path)]


def add_check(checks: list[dict], name: str, ok: bool, detail: str, payload: dict | None = None) -> None:
    checks.append({"name": name, "status": "ok" if ok else "failed", "detail": detail, "payload": payload or {}})


def ast_syntax_check(root: Path) -> dict:
    errors: list[dict] = []
    files = [
        path
        for pattern in ("backend/app/**/*.py", "backend/tests/**/*.py", "scripts/**/*.py")
        for path in root.glob(pattern)
        if "__pycache__" not in path.parts
    ]
    for path in sorted(files):
        try:
            ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except SyntaxError as exc:
            errors.append({"path": str(path.relative_to(root)), "line": exc.lineno, "message": exc.msg})
    return {"ok": not errors, "errors": errors, "files_checked": len(files)}


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


def verify_release_artifacts(
    release_dir: Path,
    strict_beta_metadata: bool,
    *,
    root: Path,
    require_current_provenance: bool,
    allow_stale_package: bool,
) -> dict:
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
    current_provenance = current_git_provenance(root)
    manifest_provenance = manifest.get("source_provenance")

    def provenance_issue(message: str) -> None:
        if require_current_provenance and not allow_stale_package:
            errors.append(message)
        else:
            warnings.append(message)

    if not isinstance(manifest_provenance, dict):
        provenance_issue("latest.json is missing source_provenance metadata.")
        manifest_provenance = {}
    else:
        missing_provenance = [
            key
            for key in ("git_commit", "git_dirty", "built_at")
            if key not in manifest_provenance
        ]
        if missing_provenance:
            provenance_issue(f"source_provenance is missing keys: {', '.join(missing_provenance)}")
        manifest_commit = str(manifest_provenance.get("git_commit", ""))
        if manifest_commit and manifest_commit != current_provenance["git_commit"]:
            changed_source_paths = source_changes_since(root, manifest_commit)
            if changed_source_paths:
                provenance_issue(
                    "Packaged release is stale: "
                    f"manifest git_commit={manifest_commit} current={current_provenance['git_commit']} "
                    f"source changes since package={changed_source_paths[:12]}"
                )
        if manifest_provenance.get("git_dirty") is True:
            provenance_issue("Packaged release was built from a dirty tracked worktree.")
    if require_current_provenance and current_provenance["git_dirty"] and not allow_stale_package:
        errors.append("Current release inputs are dirty or untracked; commit changes before requiring package-artifact freshness.")
    if current_provenance.get("untracked_release_inputs"):
        provenance_issue(
            "Untracked release inputs exist and could be packaged without provenance: "
            f"{current_provenance['untracked_release_inputs'][:12]}"
        )

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
        "current_provenance": current_provenance,
        "manifest_provenance": manifest_provenance,
        "require_current_provenance": require_current_provenance,
        "allow_stale_package": allow_stale_package,
        "errors": errors,
        "warnings": warnings,
        "artifacts": artifact_summaries,
        "handoff": str(handoff_path),
        "checksums": str(checksums_path),
    }


def verify_site_matches_release(root: Path, release_dir: Path) -> dict:
    errors: list[str] = []
    site_downloads = root / "site" / "downloads"
    site_manifest_path = site_downloads / "latest.json"
    release_manifest_path = release_dir / "latest.json"

    if not site_manifest_path.exists():
        return {"ok": False, "errors": ["site/downloads/latest.json is missing."]}
    if not release_manifest_path.exists():
        return {"ok": False, "errors": [f"{release_manifest_path} is missing."]}

    try:
        site_manifest = json.loads(site_manifest_path.read_text(encoding="utf-8"))
        release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "errors": [f"Invalid release/site manifest JSON: {exc}"]}

    for key in ("version", "build", "channel"):
        if site_manifest.get(key) != release_manifest.get(key):
            errors.append(f"Site {key} does not match packaged release: site={site_manifest.get(key)} release={release_manifest.get(key)}")

    release_artifacts = {
        str(artifact.get("filename")): artifact
        for artifact in release_manifest.get("artifacts", [])
        if isinstance(artifact, dict)
    }
    site_artifacts = {
        str(artifact.get("filename")): artifact
        for artifact in site_manifest.get("artifacts", [])
        if isinstance(artifact, dict)
    }
    if set(site_artifacts) != set(release_artifacts):
        errors.append(f"Site artifacts do not match packaged release: site={sorted(site_artifacts)} release={sorted(release_artifacts)}")

    artifact_summaries: list[dict] = []
    for filename, release_artifact in release_artifacts.items():
        site_artifact = site_artifacts.get(filename)
        if not site_artifact:
            continue
        for field in ("kind", "size_bytes", "sha256"):
            if site_artifact.get(field) != release_artifact.get(field):
                errors.append(f"Site artifact {filename} {field} does not match packaged release.")
        site_file = site_downloads / filename
        if not site_file.exists():
            errors.append(f"Site artifact file is missing: {filename}")
            continue
        actual_size = site_file.stat().st_size
        expected_size = int(release_artifact.get("size_bytes") or -1)
        actual_hash = sha256(site_file)
        expected_hash = str(release_artifact.get("sha256", "")).lower()
        if actual_size != expected_size:
            errors.append(f"Site file size mismatch for {filename}: manifest={expected_size} actual={actual_size}")
        if actual_hash != expected_hash:
            errors.append(f"Site file sha256 mismatch for {filename}: manifest={expected_hash} actual={actual_hash}")
        artifact_summaries.append({"filename": filename, "size_bytes": actual_size, "sha256": actual_hash})

    version = str(release_manifest.get("version", ""))
    build = str(release_manifest.get("build", ""))
    checksums_name = f"Cortex-{version}-{build}.checksums.txt"
    site_checksums = site_downloads / checksums_name
    release_checksums = release_dir / checksums_name
    if not site_checksums.exists():
        errors.append(f"Site checksum file is missing: {checksums_name}")
    elif not release_checksums.exists():
        errors.append(f"Packaged checksum file is missing: {checksums_name}")
    elif site_checksums.read_text(encoding="utf-8") != release_checksums.read_text(encoding="utf-8"):
        errors.append(f"Site checksum file does not match packaged release: {checksums_name}")

    return {
        "ok": not errors,
        "errors": errors,
        "site_manifest": str(site_manifest_path),
        "release_manifest": str(release_manifest_path),
        "artifacts": artifact_summaries,
    }


def site_match_payload(root: Path, release_dir: Path, *, skip_site: bool) -> dict:
    if skip_site:
        return {
            "ok": True,
            "skipped": True,
            "reason": "Static website artifacts are intentionally ignored for a local-DMG-only beta gate.",
            "release_manifest": str(release_dir / "latest.json"),
        }
    return verify_site_matches_release(root, release_dir)


def verify_obsidian_plugin_bundle(root: Path, app_path: Path) -> dict:
    errors: list[str] = []
    required_files = ("manifest.json", "main.js", "versions.json")
    source_dir = root / "packages" / "obsidian-cortex-plugin"
    bundle_dir = app_path / "Contents" / "Resources" / "obsidian-cortex-plugin"
    compared: list[dict] = []

    if not app_path.exists():
        return {"ok": False, "errors": [f"Cortex.app is missing: {app_path}"], "app": str(app_path)}
    if not source_dir.exists():
        return {"ok": False, "errors": [f"Source Obsidian plugin package is missing: {source_dir}"], "app": str(app_path)}
    if not bundle_dir.exists():
        return {"ok": False, "errors": [f"Bundled Obsidian plugin resources are missing: {bundle_dir}"], "app": str(app_path)}

    for filename in required_files:
        source_file = source_dir / filename
        bundle_file = bundle_dir / filename
        if not source_file.exists():
            errors.append(f"Source Obsidian plugin file is missing: {filename}")
            continue
        if not bundle_file.exists():
            errors.append(f"Bundled Obsidian plugin file is missing: {filename}")
            continue
        source_hash = sha256(source_file)
        bundle_hash = sha256(bundle_file)
        if source_hash != bundle_hash:
            errors.append(f"Bundled Obsidian plugin file is stale: {filename}")
        compared.append({"filename": filename, "sha256": bundle_hash, "size_bytes": bundle_file.stat().st_size})

    manifest_path = bundle_dir / "manifest.json"
    versions_path = bundle_dir / "versions.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        versions = json.loads(versions_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        errors.append(f"Bundled Obsidian plugin metadata is invalid: {exc}")
        manifest = {}
        versions = {}
    if manifest:
        for key in ("id", "name", "version", "minAppVersion"):
            if not manifest.get(key):
                errors.append(f"Bundled Obsidian plugin manifest is missing {key}.")
        version = str(manifest.get("version") or "")
        min_app_version = str(manifest.get("minAppVersion") or "")
        if version and isinstance(versions, dict) and versions.get(version) != min_app_version:
            errors.append(f"Bundled Obsidian plugin versions.json does not map {version} to {min_app_version}.")

    return {
        "ok": not errors,
        "errors": errors,
        "app": str(app_path),
        "source_dir": str(source_dir),
        "bundle_dir": str(bundle_dir),
        "files": compared,
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


def check_support_bundle_contract(bundle: dict) -> tuple[bool, dict]:
    validate_content_free_bundle(bundle)
    privacy = bundle.get("privacy", {}) if isinstance(bundle.get("privacy"), dict) else {}
    backend = bundle.get("backend", {}) if isinstance(bundle.get("backend"), dict) else {}
    features = backend.get("features", []) if isinstance(backend.get("features"), list) else []
    return (
        bundle.get("bundle_schema") == 1 and "operational-readiness" in features,
        {
            "bundle_schema": bundle.get("bundle_schema"),
            "summary": bundle.get("summary"),
            "content_free": True,
            "contains_raw_capture_text": privacy.get("contains_raw_capture_text"),
            "contains_memory_content": privacy.get("contains_memory_content"),
            "contains_context_pack": privacy.get("contains_context_pack"),
            "contains_user_files": privacy.get("contains_user_files"),
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Run Cortex local operational readiness checks.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--token", default="")
    parser.add_argument("--skip-tests", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--refresh-site", action="store_true", help="Run prepare_distribution_site.py before validating the static site.")
    parser.add_argument("--include-package", action="store_true", help="Run macos/package_release.sh. This may require macOS disk-image permissions.")
    parser.add_argument("--skip-site", action="store_true", help="Skip static website/download checks for local-DMG-only beta readiness.")
    parser.add_argument("--output-root", default=None, help="Directory containing packaged Cortex-* releases. Defaults to the workspace outputs directory.")
    parser.add_argument("--release-dir", type=Path, help="Specific packaged Cortex release directory to verify.")
    parser.add_argument("--require-package-artifacts", action="store_true", help="Fail unless release artifacts, checksums, beta handoff, and beta-readiness manifest metadata verify.")
    parser.add_argument("--allow-stale-package", action="store_true", help="Warn instead of failing when required package artifacts do not match the current git commit.")
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
    add_check(checks, "docs_current", docs_current_result["ok"], "Beta docs match the current Home/Review/Ask local-first app and release manifest.", docs_current_result)
    smoke_result = run_command(root, [sys.executable, "scripts/backend_beta_smoke.py"], timeout=120)
    add_check(checks, "backend_beta_smoke", smoke_result["ok"], "Backend beta smoke passes with temp data and blocked network sockets.", smoke_result)
    vector_runtime = run_command(root, [sys.executable, "scripts/check_vector_runtime.py"], timeout=60)
    add_check(checks, "source_vector_runtime", vector_runtime["ok"], "Source runtime loads sqlite-vec and creates Cortex vector tables.", vector_runtime)
    connector_baseline = run_command(root, [sys.executable, "scripts/check_connector_baseline.py"], timeout=60)
    add_check(checks, "connector_baseline", connector_baseline["ok"], "10k baseline connectors have real modules, tests, routes, catalog setup, and preserve-on-disconnect semantics.", connector_baseline)

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

    if args.skip_site and args.refresh_site:
        raise SystemExit("--skip-site cannot be combined with --refresh-site.")

    if args.refresh_site or args.include_package:
        prepare_result = run_command(root, [sys.executable, "scripts/prepare_distribution_site.py"], timeout=60)
        add_check(checks, "prepare_distribution_site", prepare_result["ok"], "Static site downloads refreshed from latest package.", prepare_result)

    if args.skip_site:
        add_check(
            checks,
            "distribution_site",
            True,
            "Static site checks skipped for local-DMG-only beta readiness.",
            {"skipped": True, "reason": "local-dmg-only"},
        )
    else:
        site_result = run_command(root, [sys.executable, "scripts/check_distribution_site.py"], timeout=60)
        add_check(checks, "distribution_site", site_result["ok"], "Static site links, artifacts, sizes, and hashes validate.", site_result)

    if args.skip_site:
        add_check(
            checks,
            "site_update_manifest",
            True,
            "Site update feed validation skipped for local-DMG-only beta readiness.",
            {"skipped": True, "reason": "local-dmg-only"},
        )
    else:
        site_manifest_result = run_command(root, [sys.executable, "scripts/validate_update_manifest.py", "site/downloads/latest.json"], timeout=60)
        add_check(checks, "site_update_manifest", site_manifest_result["ok"], "Site update feed validates.", site_manifest_result)

    release_dir = release_dir_arg or latest_release_dir(output_root)
    strict_package_artifacts = args.include_package or args.require_package_artifacts or release_dir_arg is not None
    if release_dir:
        release_manifest_result = run_command(root, [sys.executable, "scripts/validate_update_manifest.py", str(release_dir / "latest.json")], timeout=60)
        add_check(checks, "release_update_manifest", release_manifest_result["ok"], "Latest packaged release update feed validates.", release_manifest_result)
        release_artifacts = verify_release_artifacts(
            release_dir,
            strict_beta_metadata=strict_package_artifacts,
            root=root,
            require_current_provenance=strict_package_artifacts,
            allow_stale_package=args.allow_stale_package,
        )
        if strict_package_artifacts:
            release_detail = "Packaged DMG, ZIP, checksums, latest.json, BETA_HANDOFF, and beta-readiness metadata verify."
        else:
            release_detail = "Packaged DMG, ZIP, checksums, and latest.json verify. Beta handoff/readiness metadata warnings are nonblocking unless --require-package-artifacts or --include-package is used."
        add_check(checks, "release_artifacts", release_artifacts["ok"], release_detail, release_artifacts)
        site_match = site_match_payload(root, release_dir, skip_site=args.skip_site)
        site_match_detail = "Static site downloads match the latest packaged release."
        if args.skip_site:
            site_match_detail = "Static site comparison skipped for local-DMG-only beta readiness."
        add_check(checks, "site_matches_release", site_match["ok"], site_match_detail, site_match)
        package_app = release_dir / "Cortex.app"
        staged_app = root / "macos" / "build" / "release-staging" / "Cortex.app"
        app_for_vector_check = package_app if package_app.exists() else staged_app
        if app_for_vector_check.exists():
            packaged_vector = run_command(root, [sys.executable, "scripts/check_vector_runtime.py", "--app", str(app_for_vector_check)], timeout=60)
            add_check(checks, "packaged_vector_runtime", packaged_vector["ok"], "Packaged app runtime loads sqlite-vec and creates Cortex vector tables.", packaged_vector)
            obsidian_bundle = verify_obsidian_plugin_bundle(root, app_for_vector_check)
            add_check(checks, "packaged_obsidian_plugin", obsidian_bundle["ok"], "Packaged app contains the current repo-owned Obsidian plugin resources.", obsidian_bundle)
        elif strict_package_artifacts:
            add_check(checks, "packaged_vector_runtime", False, "Packaged app runtime could not be checked because no staged Cortex.app was found.")
            add_check(checks, "packaged_obsidian_plugin", False, "Packaged app Obsidian plugin resources could not be checked because no staged Cortex.app was found.")
    elif args.include_package or args.refresh_site or args.require_package_artifacts:
        add_check(checks, "release_update_manifest", False, f"No packaged Cortex release found under {output_root}.")
        add_check(checks, "release_artifacts", False, f"No packaged Cortex release found under {output_root}.")
        add_check(checks, "site_matches_release", False, f"No packaged Cortex release found under {output_root}.")
    else:
        add_check(checks, "release_update_manifest", True, f"No packaged Cortex release found under {output_root}; release manifest validation skipped for local readiness.")
        add_check(checks, "release_artifacts", True, f"No packaged Cortex release found under {output_root}; package artifact verification skipped for local readiness.")

    try:
        bundle = offline_support_bundle(root)
        bundle_ok, bundle_payload = check_support_bundle_contract(bundle)
        add_check(checks, "offline_support_bundle", bundle_ok, "Offline support bundle can be generated without user content.", bundle_payload)
    except Exception as exc:
        add_check(checks, "offline_support_bundle", False, "Offline support bundle failed.", {"error": str(exc)})

    try:
        health = live_get(args.base_url, args.token, "/health")
        reliability = live_get(args.base_url, args.token, "/v1/reliability/report")
        support = live_get(args.base_url, args.token, "/v1/support/bundle")
        try:
            support_ok, support_payload = check_support_bundle_contract(support)
        except ValueError as exc:
            add_check(checks, "live_backend_optional", False, "Running backend support bundle failed content-free validation.", {"error": str(exc), "required": args.require_live})
        else:
            live_ok = (
                health.get("status") == "ok"
                and int(health.get("health_contract") or 0) >= 3
                and reliability.get("health_contract") >= 3
                and support_ok
            )
            live_payload = {"health": health, "reliability_status": reliability.get("status")}
            live_payload.update(support_payload)
            add_check(checks, "live_backend_optional", live_ok, "Running backend exposes health, reliability, and support-bundle contracts.", live_payload)
    except (urllib.error.URLError, TimeoutError) as exc:
        add_check(checks, "live_backend_optional", not args.require_live, "Running backend was not reachable." if not args.require_live else "Running backend is required but unreachable.", {"error": str(exc), "required": args.require_live})

    if args.require_live:
        live_smoke_result = run_command(
            root,
            [
                sys.executable,
                "scripts/first100_live_smoke.py",
                "--base-url",
                args.base_url,
                "--token",
                args.token,
            ],
            timeout=120,
        )
        if args.token:
            live_smoke_result["command"] = live_smoke_result["command"].replace(args.token, "[redacted-token]")
        add_check(
            checks,
            "first100_live_smoke",
            live_smoke_result["ok"],
            "Running packaged app passes isolated MCP/Obsidian -> Review -> Ask smoke and cleans up the smoke user.",
            live_smoke_result,
        )

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
