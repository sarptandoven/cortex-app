from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def workspace_root(root: Path) -> Path:
    return root.parent.parent if root.parent.name == "work" else root


def default_output_path(root: Path) -> Path:
    timestamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(":", "-")
    return workspace_root(root) / "outputs" / "support-bundles" / f"cortex-support-{timestamp}.json"


def live_bundle(base_url: str, token: str) -> dict:
    request = urllib.request.Request(
        base_url.rstrip("/") + "/v1/support/bundle",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def offline_bundle(db_path: Path | None, vault_path: Path | None, user_id: str) -> dict:
    root = repo_root()
    sys.path.insert(0, str(root))
    from backend.app.config import load_settings
    from backend.app.database import init_db
    from backend.app.storage import CortexStore

    settings = load_settings()
    resolved_db_path = db_path or settings.db_path
    resolved_vault_path = vault_path or settings.vault_path
    init_db(resolved_db_path)
    store = CortexStore(resolved_db_path, resolved_vault_path)
    store.ensure_vault_backfilled(user_id)
    return store.support_bundle(user_id)


OMITTED_FROM_SUPPORT_BUNDLE = "[omitted from support bundle]"
CONTENT_FREE_PRIVACY_FLAGS = {
    "contains_raw_capture_text": False,
    "contains_memory_content": False,
    "contains_context_pack": False,
    "contains_user_files": False,
}
CONTENT_VALUE_KEYS = {
    "captures",
    "content",
    "context_pack",
    "edges",
    "entities",
    "memories",
    "raw_text",
    "tasks",
}
RAW_QUERY_KEYS = {"query", "mcp_query", "raw_query"}
SENSITIVE_VALUE_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9_]{20,}\b"),
    re.compile(r"\b(?:xox[baprs]-[A-Za-z0-9-]{16,})\b"),
    re.compile(
        r"(?i)\b(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|secret|password|passwd|pwd)\s*[:=]\s*['\"]?[^'\"\s,;]{8,}"
    ),
)
SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)^(api[_-]?key|access[_-]?token|auth[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|secret|password|passwd|pwd)$"
)


def validate_content_free_bundle(bundle: dict) -> None:
    privacy = bundle.get("privacy") if isinstance(bundle.get("privacy"), dict) else {}
    for key, expected in CONTENT_FREE_PRIVACY_FLAGS.items():
        if privacy.get(key) is not expected:
            raise ValueError(f"support bundle privacy.{key} must be {expected!r}")

    violations: list[str] = []

    def visit(value, path: str, key: str = "") -> None:
        if key in CONTENT_VALUE_KEYS and value != OMITTED_FROM_SUPPORT_BUNDLE and not isinstance(value, (int, float, bool, type(None))):
            violations.append(path)
        if key in RAW_QUERY_KEYS and value not in (None, OMITTED_FROM_SUPPORT_BUNDLE):
            violations.append(path)
        if SENSITIVE_KEY_PATTERN.match(str(key or "")) and value not in (None, "", OMITTED_FROM_SUPPORT_BUNDLE):
            violations.append(path)
        if isinstance(value, dict):
            for child_key, child_value in value.items():
                child_path = f"{path}.{child_key}" if path else str(child_key)
                visit(child_value, child_path, str(child_key))
        elif isinstance(value, list):
            for index, item in enumerate(value):
                visit(item, f"{path}[{index}]", key)
        elif isinstance(value, str):
            if any(pattern.search(value) for pattern in SENSITIVE_VALUE_PATTERNS):
                violations.append(path)

    visit(bundle, "")
    if violations:
        sample = ", ".join(dict.fromkeys(violations[:8]))
        raise ValueError(f"support bundle is not content-free: {sample}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a sanitized Cortex support bundle.")
    parser.add_argument("--mode", choices=("live", "offline"), default="live", help="live calls the running backend; offline reads the local vault/index directly.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8766")
    parser.add_argument("--token", default="")
    parser.add_argument("--db-path", type=Path)
    parser.add_argument("--vault-path", type=Path)
    parser.add_argument("--user-id", default="local")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    root = repo_root()
    output_path = args.output or default_output_path(root)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.mode == "live" and not args.token:
        raise SystemExit("Pass --token with the local Cortex API token, or use --mode offline.")

    try:
        bundle = live_bundle(args.base_url, args.token) if args.mode == "live" else offline_bundle(args.db_path, args.vault_path, args.user_id)
    except urllib.error.URLError as exc:
        raise SystemExit(f"Unable to fetch live support bundle: {exc}") from exc
    try:
        validate_content_free_bundle(bundle)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    output_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "mode": args.mode,
        "output": str(output_path),
        "bundle_schema": bundle.get("bundle_schema"),
        "support_status": bundle.get("summary", {}).get("status"),
        "content_free": True,
        "contains_raw_capture_text": bundle.get("privacy", {}).get("contains_raw_capture_text"),
    }, indent=2))


if __name__ == "__main__":
    main()
