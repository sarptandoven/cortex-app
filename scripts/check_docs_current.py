from __future__ import annotations

import json
import re
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
        "pip install -r backend/runtime-requirements.txt",
        "new releases arrive automatically",
        "checks the signed release feed",
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
        "build a paste-ready Markdown context pack",
    ),
    "docs/INSTALLER_AND_UPDATES.md": (
        "More ->",
        "Cortex Settings",
        "import one real user-selected local source",
        "Local-first Cortex beta with installer, update manifest, capture, MCP, and trust controls.",
        "Connect MCP or an Obsidian/local notes vault",
        "Those are appropriate for the public-beta release track, not the local-first MVP package.",
        "The app and DMG are not notarized or stapled.",
        "first-100 release track is explicitly the unnotarized",
        "binaries referenced by the signed update feed",
    ),
    "docs/OPERATIONAL_READINESS.md": (
        "context pack contains stale content",
        "number of users who copied one context pack",
    ),
    "docs/DISTRIBUTION.md": (
        "Cortex gives ChatGPT, Claude, Cursor, and MCP agents your memory",
        "personal operating model for agents",
        "current path is an unnotarized direct",
        "first notarized Release has not been published yet",
        "checks the signed update feed",
    ),
    "docs/PRODUCTION_READINESS.md": (
        "Add app notarization and signed installer",
    ),
    "docs/APPLE_RELEASE.md": (
        "local-first MCP/vault beta",
        "Public beta with local vault, Cortex memory, MCP integrations, and support bundle export.",
        "import one real source",
    ),
    "macos/update-feed.example.json": (
        "Bundled backend, local vault, MCP tools, capture surfaces, and trust controls.",
    ),
    "site/downloads/latest.json": (
        "Complete first-run setup with a local vault, MCP or Obsidian connection",
        "Local-first Cortex beta with bundled backend, capture, MCP, and trust controls.",
    ),
    "site/index.html": (
        "ad-hoc signed for local beta testing",
        "not yet notarized for broad public distribution",
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
        "Connect MCP tools or local notes",
        "Copy MCP Settings",
        "manual MCP setup",
        "local MCP settings",
        "Open Vault",
        "Connect Obsidian Vault",
        "Connect Vault",
        "Connected through MCP",
    ),
    "macos/Sources/ProductFlowTypes.swift": (
        'return "Vault"',
        "Connect an MCP tool",
    ),
    "macos/Sources/ModelTab.swift": (
        "Connect MCP tools",
        "MCP tools or Obsidian",
        "Obsidian vault",
    ),
    "macos/Sources/ConnectionsPrivacySheet.swift": (
        "Memory syncs from MCP tools",
        "local MCP tool",
        "Choose a vault once",
    ),
    "macos/Sources/ReviewTab.swift": (
        "Add a source first",
        "Approved context",
        "open loops",
    ),
    "macos/Sources/OnboardingView.swift": (
        "Cortex first run",
        "First run",
        "first run",
        "Memory layer",
        "memory layer",
        "See source layer",
        "View source connection layer",
        "private local vault",
        "Local vault",
        "Advanced vault",
        "Vault ready",
        "MCP clients",
        "Obsidian vault",
        "Connect vault",
    ),
    "macos/Sources/SourceConnectionComponents.swift": (
        "Obsidian vault",
        "Connect vault",
        "Sync vault",
    ),
}

PRIMARY_UI_FILES: tuple[str, ...] = (
    "macos/Sources/ModelTab.swift",
    "macos/Sources/ReviewTab.swift",
    "macos/Sources/AskTab.swift",
    "macos/Sources/ConnectionsPrivacySheet.swift",
    "macos/Sources/OnboardingView.swift",
    "macos/Sources/SourceConnectionComponents.swift",
)

PRIMARY_UI_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "manual setup",
    "Manual setup",
    "Copy manual setup",
    "manual import",
    "Manual import",
    "Import sources",
    "Import data",
    "Upload",
    "Drop files",
    "memory brief",
    "Memory brief",
    "context pack",
    "Context pack",
    "Copy context",
    "Copy MCP",
    "MCP clients",
    "MCP tools",
    "Obsidian vault",
    "Connect vault",
    "Developer tools",
    "Troubleshooting",
    "Privacy settings",
    "AI tool access",
)

ARCHITECTURE_STORAGE_DOC_EXCEPTIONS: tuple[str, ...] = (
    "docs/ARCHITECTURE.md",
    "docs/LOCAL_VAULT_FORMAT.md",
    "docs/MEMORY_BACKEND_BLUEPRINT.md",
    "docs/PRODUCTION_READINESS.md",
    "docs/RELIABILITY_HARDENING.md",
    "docs/SOURCE_IMPORTS.md",
    "docs/SQLITE_VEC_BACKEND_PLAN.md",
)

ROOT_COPY_FILES: tuple[str, ...] = (
    "README.md",
    "SETUP.md",
    "backend/README.md",
    "macos/README.md",
)

PRIMARY_PRODUCT_COPY_FILES: tuple[str, ...] = (
    "site/index.html",
    "site/privacy.html",
    "site/app.js",
    "site/downloads/latest.json",
    "macos/update-feed.example.json",
    "macos/package_release.sh",
    *PRIMARY_UI_FILES,
)

PRIMARY_PRODUCT_STALE_PHRASES: tuple[str, ...] = (
    "Trust >",
    "from Trust",
    "Review, Ask, and Trust",
)

SITE_VISIBLE_TRUST_FILES: tuple[str, ...] = (
    "site/index.html",
    "site/privacy.html",
)

SITE_VISIBLE_TRUST_PATTERN = re.compile(r">\s*Trust\s*<")

GENERATED_BETA_METADATA_FILES: tuple[str, ...] = (
    "macos/package_release.sh",
    "macos/update-feed.example.json",
    "site/downloads/latest.json",
    "release-artifacts/direct-mac/latest.json",
)

GENERATED_BETA_METADATA_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "local vault",
    "raw vault data",
)

USER_OPERATOR_DOC_FORBIDDEN_PHRASES: tuple[str, ...] = (
    "Capture, Review, Reuse, Return",
)

REQUIRED_SNIPPETS: dict[str, tuple[str, ...]] = {
    "README.md": (
        "docs/README.md",
        "make setup",
        "examples/README.md",
        "docs/PAIRWISE_TWIN_EVALUATION.md",
    ),
    "backend/README.md": (
        "bundled on-device Model2Vec",
        "cortex-hash-v1",
    ),
    "docs/ARCHITECTURE.md": (
        "bundled Model2Vec embeddings when available",
    ),
    "docs/README.md": (
        "trace-cortex/cortex-app",
        "site/downloads/latest.json",
        "PAIRWISE_TWIN_EVALUATION.md",
    ),
    "site/index.html": (
        "Developer ID signed, notarized by Apple",
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
    errors.extend(primary_ui_errors())
    errors.extend(scoped_stale_copy_errors())
    errors.extend(required_snippet_errors())
    return errors


def required_snippet_errors() -> list[str]:
    errors: list[str] = []
    for relative_path, snippets in REQUIRED_SNIPPETS.items():
        path = ROOT / relative_path
        if not path.exists():
            errors.append(f"{relative_path}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                errors.append(f"{relative_path}: required current-doc reference missing: {snippet!r}")
    return errors


def primary_ui_errors() -> list[str]:
    errors: list[str] = []
    for relative_path in PRIMARY_UI_FILES:
        path = ROOT / relative_path
        if not path.exists():
            errors.append(f"{relative_path}: missing")
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in PRIMARY_UI_FORBIDDEN_PHRASES:
            if phrase in text:
                errors.append(f"{relative_path}: primary UI regressed into forbidden phrase: {phrase!r}")
    return errors


def docs_copy_files() -> tuple[str, ...]:
    paths = list(ROOT_COPY_FILES)
    docs_dir = ROOT / "docs"
    if docs_dir.exists():
        paths.extend(str(path.relative_to(ROOT)) for path in sorted(docs_dir.rglob("*.md")))
    return tuple(dict.fromkeys(paths))


def user_operator_doc_files() -> tuple[str, ...]:
    return tuple(
        relative_path
        for relative_path in docs_copy_files()
        if relative_path not in ARCHITECTURE_STORAGE_DOC_EXCEPTIONS
    )


def primary_product_copy_files() -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            [
                *user_operator_doc_files(),
                *PRIMARY_PRODUCT_COPY_FILES,
            ]
        )
    )


def phrase_present(text: str, phrase: str, *, case_sensitive: bool = True) -> bool:
    if case_sensitive:
        return phrase in text
    return phrase.casefold() in text.casefold()


def stale_phrase_errors(
    relative_paths: tuple[str, ...],
    phrases: tuple[str, ...],
    message: str,
    *,
    case_sensitive: bool = True,
) -> list[str]:
    errors: list[str] = []
    for relative_path in relative_paths:
        path = ROOT / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        for phrase in phrases:
            if phrase_present(text, phrase, case_sensitive=case_sensitive):
                errors.append(f"{relative_path}: {message}: {phrase!r}")
    return errors


def scoped_stale_copy_errors() -> list[str]:
    errors = stale_phrase_errors(
        primary_product_copy_files(),
        PRIMARY_PRODUCT_STALE_PHRASES,
        "old primary-product language still present",
    )
    errors.extend(site_visible_trust_errors())
    errors.extend(
        stale_phrase_errors(
            GENERATED_BETA_METADATA_FILES,
            GENERATED_BETA_METADATA_FORBIDDEN_PHRASES,
            "generated beta metadata still uses stale storage wording",
            case_sensitive=False,
        )
    )
    errors.extend(
        stale_phrase_errors(
            user_operator_doc_files(),
            USER_OPERATOR_DOC_FORBIDDEN_PHRASES,
            "user/operator docs still use the old product-loop phrase",
        )
    )
    return errors


def site_visible_trust_errors() -> list[str]:
    errors: list[str] = []
    for relative_path in SITE_VISIBLE_TRUST_FILES:
        path = ROOT / relative_path
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        if SITE_VISIBLE_TRUST_PATTERN.search(text):
            errors.append(f"{relative_path}: visible site loop/nav label still uses old Trust step")
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
    site_manifest = ROOT / "site" / "downloads" / "latest.json"
    if direct_manifest.exists() and site_manifest.exists():
        errors.extend(artifact_consistency_errors(direct_manifest, site_manifest))
    return errors


def artifact_consistency_errors(direct_manifest: Path, site_manifest: Path) -> list[str]:
    errors: list[str] = []
    try:
        direct_payload = json.loads(direct_manifest.read_text(encoding="utf-8"))
        site_payload = json.loads(site_manifest.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"release manifest comparison failed: invalid JSON: {exc}"]

    direct_artifacts = {
        artifact.get("filename"): artifact
        for artifact in direct_payload.get("artifacts", [])
        if artifact.get("filename")
    }
    site_artifacts = {
        artifact.get("filename"): artifact
        for artifact in site_payload.get("artifacts", [])
        if artifact.get("filename")
    }
    for filename in sorted(set(direct_artifacts) & set(site_artifacts)):
        direct = direct_artifacts[filename]
        site = site_artifacts[filename]
        for key in ("sha256", "size_bytes"):
            if direct.get(key) != site.get(key):
                errors.append(f"{filename}: release-artifacts/direct-mac and site/downloads disagree on {key}")
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
    for phrase in (
        "mcp_server.py",
        "github_store",
        "redis_store",
        "GITHUB_TOKEN",
        "REDIS_URL",
        "CORTEX_API_BASE_URL",
        "CORTEX_MCP_API_KEY",
    ):
        if phrase in text:
            errors.append(f"claude_desktop_config.json: stale MCP config references {phrase!r}")
    if "scripts/cortex_mcp_stdio.py" not in text and "cortex_mcp_stdio.py" not in text:
        errors.append("claude_desktop_config.json: must use scripts/cortex_mcp_stdio.py")
    if "CORTEX_BASE_URL" not in text or "CORTEX_API_KEY" not in text:
        errors.append("claude_desktop_config.json: must set CORTEX_BASE_URL and CORTEX_API_KEY")
    return errors


def main() -> None:
    errors = [*phrase_errors(), *manifest_errors(), *mcp_config_errors()]
    payload = {
        "status": "error" if errors else "ok",
        "checks": [
            "stale-ui-phrases",
            "primary-ui-language",
            "stale-primary-product-copy",
            "site-visible-trust-step",
            "generated-beta-metadata-language",
            "user-operator-loop-language",
            "required-doc-index",
            "direct-release-manifest",
            "mcp-config",
        ],
        "errors": errors,
    }
    print(json.dumps(payload, indent=2))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
