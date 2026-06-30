from __future__ import annotations

import argparse
import json
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

    output_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "status": "ok",
        "mode": args.mode,
        "output": str(output_path),
        "bundle_schema": bundle.get("bundle_schema"),
        "support_status": bundle.get("summary", {}).get("status"),
        "contains_raw_capture_text": bundle.get("privacy", {}).get("contains_raw_capture_text"),
    }, indent=2))


if __name__ == "__main__":
    main()
