#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from first100_launch_gate import CLEAN_PROFILE_QA_FIELDS, SUPPORT_FIELDS, ROOT, git_output


DEFAULT_OUTPUT_DIR = ROOT / ".context" / "first100_launch_packets"


def load_manifest() -> dict[str, Any]:
    manifest_path = ROOT / "site/downloads/latest.json"
    return json.loads(manifest_path.read_text(encoding="utf-8"))


def artifact_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for artifact in manifest.get("artifacts") or []:
        if isinstance(artifact, dict):
            filename = str(artifact.get("filename") or "")
            sha256 = str(artifact.get("sha256") or "")
            if filename and sha256:
                hashes[filename] = sha256
    return hashes


def release_summary(manifest: dict[str, Any]) -> dict[str, str]:
    provenance = manifest.get("source_provenance") if isinstance(manifest.get("source_provenance"), dict) else {}
    hashes = artifact_hashes(manifest)
    dmg_name = next((name for name in hashes if name.endswith(".dmg")), "")
    zip_name = next((name for name in hashes if name.endswith(".app.zip")), "")
    return {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "git_head": git_output(["rev-parse", "HEAD"]) or "",
        "git_branch": git_output(["branch", "--show-current"]) or "",
        "version": str(manifest.get("version") or ""),
        "build": str(manifest.get("build") or ""),
        "released_at": str(manifest.get("released_at") or ""),
        "manifest_source_commit": str(provenance.get("git_commit") or ""),
        "dmg_name": dmg_name,
        "dmg_sha256": hashes.get(dmg_name, ""),
        "zip_name": zip_name,
        "zip_sha256": hashes.get(zip_name, ""),
    }


def write_field_block(fields: list[str], defaults: dict[str, str]) -> str:
    lines: list[str] = []
    for field in fields:
        value = defaults.get(field, "")
        lines.append(f"{field}: {value}")
    return "\n".join(lines) + "\n"


def support_packet(summary: dict[str, str]) -> str:
    defaults = {
        "Build version, build number, hash": f"{summary['version']} build {summary['build']}; DMG {summary['dmg_sha256']}; ZIP {summary['zip_sha256']}",
        "Tester cohort source": "FILL_ME",
        "First batch size": "FILL_ME",
        "Known limitations sent to testers": "FILL_ME",
    }
    body = write_field_block(SUPPORT_FIELDS, defaults)
    return (
        "# First-100 Support Packet\n\n"
        "Fill every FILL_ME/blank field before inviting testers. Keep this file private.\n\n"
        f"Generated at: {summary['generated_at']}\n"
        f"PR or commit: {summary['git_head']}\n"
        f"Version: {summary['version']}\n"
        f"Build: {summary['build']}\n"
        f"DMG: {summary['dmg_name']} {summary['dmg_sha256']}\n"
        f"ZIP: {summary['zip_name']} {summary['zip_sha256']}\n\n"
        "```text\n"
        f"{body}"
        "```\n\n"
        "After both packets are filled, run the strict launch gate command printed by this generator.\n"
    )


def clean_profile_packet(summary: dict[str, str]) -> str:
    artifact = f"site/downloads/{summary['dmg_name']}" if summary["dmg_name"] else "site/downloads/Cortex-0.1.0-1.dmg"
    defaults = {
        "QA date": summary["generated_at"][:10],
        "Artifact source": artifact,
        "DMG checksum matched": "FILL_ME",
        "Installed on clean macOS 13+ profile": "FILL_ME",
        "Gatekeeper path accepted": "FILL_ME",
        "First-run onboarding completed without developer docs": "FILL_ME",
        "Obsidian/local notes or MCP connected": "FILL_ME",
        "Sync created Review items": "FILL_ME",
        "Review approval worked": "FILL_ME",
        "Ask returned cited answer": "FILL_ME",
        "Backup worked": "FILL_ME",
        "Content-free support bundle export worked": "FILL_ME",
        "Manual update preserved memory folder": "FILL_ME",
        "Manual rollback preserved memory folder": "FILL_ME",
        "Invite copy reviewed for local-beta limitations": "FILL_ME",
    }
    body = write_field_block(CLEAN_PROFILE_QA_FIELDS, defaults)
    return (
        "# First-100 Clean-Profile QA Packet\n\n"
        "Replace FILL_ME with yes/passed/ok/done/verified only after the exact pass is complete. Keep this file private.\n\n"
        f"Generated at: {summary['generated_at']}\n"
        f"PR or commit: {summary['git_head']}\n"
        f"Version: {summary['version']}\n"
        f"Build: {summary['build']}\n"
        f"DMG SHA-256: {summary['dmg_sha256']}\n\n"
        "```text\n"
        f"{body}"
        "```\n\n"
        "After both packets are filled, run the strict launch gate command printed by this generator.\n"
    )


def append_strict_command(packet: str, command: str) -> str:
    return (
        packet
        + "\n"
        + "## Strict Launch Gate\n\n"
        + "Run this exact command from the repository root after both packet files are filled. The first tester invite should not go out until it passes.\n\n"
        + "```bash\n"
        + command
        + "\n"
        + "```\n"
    )


def strict_command(output_dir: Path) -> str:
    support = output_dir / "support-packet.txt"
    qa = output_dir / "clean-profile-qa.txt"
    return (
        "python3 scripts/first100_launch_gate.py "
        f"--support-packet {support} "
        f"--clean-profile-qa {qa} "
        "--require-human-packet --require-clean-profile-qa"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate private first-100 support and clean-profile QA packet templates.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for generated packet templates.")
    args = parser.parse_args()

    output_dir = args.output_dir
    if not output_dir.is_absolute():
        output_dir = ROOT / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    summary = release_summary(manifest)
    support_path = output_dir / "support-packet.txt"
    qa_path = output_dir / "clean-profile-qa.txt"
    command = strict_command(output_dir)
    support_path.write_text(append_strict_command(support_packet(summary), command), encoding="utf-8")
    qa_path.write_text(append_strict_command(clean_profile_packet(summary), command), encoding="utf-8")

    payload = {
        "status": "ok",
        "output_dir": str(output_dir),
        "support_packet": str(support_path),
        "clean_profile_qa": str(qa_path),
        "strict_launch_gate_command": command,
        "release": summary,
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
