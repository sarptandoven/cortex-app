from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any


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
        "Ask > AI Handoff",
        "Open `Sources`",
        "Open `Trust`",
        "five-step setup flow",
    ),
    "README.md": (
        "Five-tab product flow",
        "Model, Sources, Review, Ask, Trust",
    ),
    "docs/CAPTURE_SURFACES.md": (
        "Save tab",
        "from Settings",
    ),
    "docs/FIRST_RUN_ONBOARDING.md": (
        "More ->",
        "context/adaptation handoff",
        "focused context, model context, or agent adaptation completes",
        "Import a real first source",
        "Quick memories and clipboard captures",
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
        "import one real user-selected local source",
    ),
    "PUBLISH_MANIFEST.md": (
        "uploaded through Transporter",
    ),
    "macos/Sources/CortexApp.swift": (
        "Review Source Import",
        "Import to Model",
        "Choose Sources to Add to Cortex",
        "Add Recovery Items",
        "Add Clipboard",
        "Add Link",
        "Copy Capture Bookmarklet",
        "Open Capture Page",
        "Quick signal",
        "Capture Inbox",
    ),
    "macos/Sources/OnboardingView.swift": (
        "See source layer",
        "View source connection layer",
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


def mcp_config_errors() -> list[str]:
    errors: list[str] = []
    config = ROOT / "claude_desktop_config.json"
    if not config.exists():
        return errors
    try:
        payload: dict[str, Any] = json.loads(config.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"claude_desktop_config.json: invalid JSON: {exc}"]
    text = json.dumps(payload)
    for phrase in ("mcp_server.py", "github_store", "redis_store", "GITHUB_TOKEN", "REDIS_URL"):
        if phrase in text:
            errors.append(f"claude_desktop_config.json: stale MCP config references {phrase!r}")
    if "scripts/cortex_mcp_stdio.py" not in text and "cortex_mcp_stdio.py" not in text:
        errors.append("claude_desktop_config.json: must use scripts/cortex_mcp_stdio.py")
    return errors


def main() -> None:
    errors = [*phrase_errors(), *manifest_errors(), *mcp_config_errors()]
    payload = {
        "status": "error" if errors else "ok",
        "checks": ["stale-ui-phrases", "direct-release-manifest", "mcp-config"],
        "errors": errors,
    }
    print(json.dumps(payload, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
