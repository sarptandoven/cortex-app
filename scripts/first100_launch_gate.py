#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]

REQUIRED_FILES = [
    "docs/BETA_SUPPORT.md",
    "docs/BETA_OPERATOR_QUICKSTART.md",
    "docs/INSTALLER_AND_UPDATES.md",
    "docs/DISTRIBUTION.md",
    ".github/ISSUE_TEMPLATE/first100_beta_support_case.md",
    ".github/ISSUE_TEMPLATE/first100_batch_go_no_go.md",
    "site/downloads/latest.json",
]

SUPPORT_FIELDS = [
    "Support channel",
    "Primary support owner",
    "Backup support owner",
    "Incident engineer",
    "Case log location",
    "Support artifact storage",
    "Business hours and timezone",
    "Deletion request contact",
    "Tester cohort source",
    "First batch size",
    "Stop/go decision owner",
]


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_output(args: list[str]) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, text=True, capture_output=True, check=False)
    if completed.returncode != 0:
        return ""
    return completed.stdout.strip()


def command_ok(args: list[str]) -> dict[str, Any]:
    completed = subprocess.run(args, cwd=ROOT, text=True, capture_output=True, check=False)
    return {
        "ok": completed.returncode == 0,
        "command": " ".join(args),
        "returncode": completed.returncode,
        "stdout": completed.stdout[-2000:],
        "stderr": completed.stderr[-2000:],
    }


def check_required_files() -> dict[str, Any]:
    missing = [path for path in REQUIRED_FILES if not (ROOT / path).exists()]
    return {"ok": not missing, "missing": missing}


def check_worktree() -> dict[str, Any]:
    raw_status = git_output(["status", "--porcelain", "--untracked-files=no"])
    tracked_dirty = [line for line in raw_status.splitlines() if line.strip()]
    return {
        "ok": not tracked_dirty,
        "git_commit": git_output(["rev-parse", "HEAD"]) or "unknown",
        "git_branch": git_output(["branch", "--show-current"]) or "",
        "dirty": tracked_dirty,
    }


def check_release_manifest() -> dict[str, Any]:
    manifest_path = ROOT / "site/downloads/latest.json"
    if not manifest_path.exists():
        return {"ok": False, "errors": ["site/downloads/latest.json is missing"]}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"ok": False, "errors": [f"latest.json is invalid JSON: {exc}"]}

    errors: list[str] = []
    artifacts: list[dict[str, Any]] = []
    for artifact in manifest.get("artifacts") or []:
        filename = str(artifact.get("filename") or "")
        expected_hash = str(artifact.get("sha256") or "")
        expected_size = int(artifact.get("size_bytes") or 0)
        artifact_path = ROOT / "site/downloads" / filename
        summary: dict[str, Any] = {"filename": filename, "expected_sha256": expected_hash}
        if not artifact_path.exists():
            errors.append(f"artifact missing: {filename}")
            summary["ok"] = False
            artifacts.append(summary)
            continue
        actual_hash = sha256(artifact_path)
        actual_size = artifact_path.stat().st_size
        summary.update({"ok": actual_hash == expected_hash and actual_size == expected_size, "actual_sha256": actual_hash, "actual_size": actual_size})
        artifacts.append(summary)
        if actual_hash != expected_hash:
            errors.append(f"{filename}: sha256 mismatch")
        if actual_size != expected_size:
            errors.append(f"{filename}: size mismatch")

    provenance = manifest.get("source_provenance") if isinstance(manifest.get("source_provenance"), dict) else {}
    if provenance.get("git_dirty") is not False:
        errors.append("latest.json source_provenance.git_dirty is not false")
    if not provenance.get("git_commit"):
        errors.append("latest.json source_provenance.git_commit is missing")

    return {
        "ok": not errors,
        "version": manifest.get("version"),
        "build": manifest.get("build"),
        "released_at": manifest.get("released_at"),
        "source_provenance": provenance,
        "artifacts": artifacts,
        "errors": errors,
    }


def parse_support_packet(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        key = " ".join(key.strip().split())
        if key in SUPPORT_FIELDS:
            values[key] = value.strip()
    return values


def check_support_packet(path: Path | None) -> dict[str, Any]:
    if path is None:
        return {
            "ok": False,
            "provided": False,
            "missing_fields": SUPPORT_FIELDS,
            "detail": "No support packet file was provided. Use --support-packet with filled field values before inviting testers.",
        }
    values = parse_support_packet(path)
    missing = [field for field in SUPPORT_FIELDS if not values.get(field)]
    return {
        "ok": not missing,
        "provided": True,
        "path": str(path),
        "missing_fields": missing,
        "filled_fields": sorted(values),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize first-100 automated and human launch readiness gates.")
    parser.add_argument("--support-packet", type=Path, help="Optional filled support packet with 'Field: value' lines.")
    parser.add_argument("--require-human-packet", action="store_true", help="Fail unless --support-packet is provided and complete.")
    args = parser.parse_args()

    checks = {
        "required_files": check_required_files(),
        "worktree": check_worktree(),
        "release_manifest": check_release_manifest(),
        "docs_current": command_ok(["python3", "scripts/check_docs_current.py"]),
        "distribution_site": command_ok(["python3", "scripts/check_distribution_site.py"]),
        "site_update_manifest": command_ok(["python3", "scripts/validate_update_manifest.py", "site/downloads/latest.json"]),
        "support_packet": check_support_packet(args.support_packet),
    }

    automated_names = ["required_files", "worktree", "release_manifest", "docs_current", "distribution_site", "site_update_manifest"]
    automated_ok = all(checks[name]["ok"] for name in automated_names)
    human_ok = checks["support_packet"]["ok"]
    status = "ok" if automated_ok and human_ok else "needs_human" if automated_ok else "failed"
    if args.require_human_packet and not human_ok:
        status = "failed"

    payload = {
        "status": status,
        "automated_ok": automated_ok,
        "human_packet_ok": human_ok,
        "checks": checks,
        "next_actions": [],
    }
    if not automated_ok:
        payload["next_actions"].append("Fix failed automated checks before inviting testers.")
    if not human_ok:
        payload["next_actions"].append("Fill support ownership, case-log, artifact-storage, cohort, and go/no-go owner fields before inviting testers.")
    if automated_ok and human_ok:
        payload["next_actions"].append("Run the clean-profile install/product pass and record the batch go/no-go issue.")

    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if status in {"ok", "needs_human"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
