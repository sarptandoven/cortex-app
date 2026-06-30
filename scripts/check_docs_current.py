from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PHRASES: dict[str, tuple[str, ...]] = {
    "SETUP.md": (
        "Start Redis",
        "redis_store.py",
        "github_store.py",
        "ingest_batch.py",
        "Omi webhook",
        "Notion MCP",
        "push to GitHub",
    ),
    "docs/CAPTURE_SURFACES.md": (
        "Save tab",
        "from Settings",
    ),
    "docs/FIRST_RUN_ONBOARDING.md": (
        "More ->",
        "context/adaptation handoff",
        "focused context, model context, or agent adaptation completes",
    ),
    "docs/MCP_INTEGRATIONS.md": (
        "Connect tab",
        "first-run Connect step",
        "/absolute/path/to/second-brain",
        "copies a context pack",
    ),
    "docs/INSTALLER_AND_UPDATES.md": (
        "More ->",
        "Cortex Settings",
    ),
    "PUBLISH_MANIFEST.md": (
        "uploaded through Transporter",
    ),
}


def run_command(command: list[str]) -> dict[str, object]:
    completed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True)
    return {
        "command": " ".join(command),
        "returncode": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "ok": completed.returncode == 0,
    }


def phrase_errors() -> list[str]:
    errors: list[str] = []
    for relative_path, phrases in FORBIDDEN_PHRASES.items():
        path = ROOT / relative_path
        if not path.exists():
            errors.append(f"{relative_path}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase in text:
                errors.append(f"{relative_path}: stale phrase still present: {phrase!r}")
    return errors


def manifest_errors() -> list[str]:
    errors: list[str] = []
    direct_manifest = ROOT / "release-artifacts" / "direct-mac" / "latest.json"
    if direct_manifest.exists():
        text = direct_manifest.read_text(encoding="utf-8")
        if "file:///Users/" in text:
            errors.append("release-artifacts/direct-mac/latest.json: contains a developer-machine file URL")
        result = run_command([sys.executable, "scripts/validate_update_manifest.py", str(direct_manifest)])
        if not result["ok"]:
            errors.append(f"release-artifacts/direct-mac/latest.json: validation failed: {result['stderr'] or result['stdout']}")
    return errors


def main() -> None:
    errors = [*phrase_errors(), *manifest_errors()]
    payload = {
        "status": "error" if errors else "ok",
        "checks": ["stale-ui-phrases", "direct-release-manifest"],
        "errors": errors,
    }
    print(json.dumps(payload, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
