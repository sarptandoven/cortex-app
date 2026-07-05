from __future__ import annotations

import argparse
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path


def workspace_root(repo_root: Path) -> Path:
    return repo_root.parent.parent if repo_root.parent.name == "work" else repo_root


def latest_release_dir(outputs_dir: Path) -> Path:
    candidates = [path for path in outputs_dir.glob("Cortex-*") if path.is_dir() and (path / "latest.json").exists()]
    if not candidates:
        raise FileNotFoundError(f"No Cortex release with latest.json found in {outputs_dir}")
    return max(candidates, key=lambda path: (path / "latest.json").stat().st_mtime)


def artifact_url(filename: str, base_url: str | None) -> str:
    if base_url:
        return f"{base_url.rstrip('/')}/{filename}"
    return f"downloads/{filename}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare the static Cortex landing page download directory from a packaged release.")
    parser.add_argument("--release-dir", type=Path, help="Directory containing latest.json and release artifacts.")
    parser.add_argument("--site-dir", type=Path, default=Path("site"), help="Static site directory. Default: site")
    parser.add_argument("--artifact-base-url", default="", help="Optional public URL prefix for artifact URLs in site/downloads/latest.json.")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    release_dir = args.release_dir or latest_release_dir(workspace_root(repo_root) / "outputs")
    release_dir = release_dir.resolve()
    site_dir = (repo_root / args.site_dir).resolve() if not args.site_dir.is_absolute() else args.site_dir.resolve()
    downloads_dir = site_dir / "downloads"
    downloads_dir.mkdir(parents=True, exist_ok=True)

    source_manifest_path = release_dir / "latest.json"
    manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    copied_artifacts: list[dict] = []
    for artifact in manifest["artifacts"]:
        filename = artifact["filename"]
        source = release_dir / filename
        if not source.exists():
            raise FileNotFoundError(f"Missing artifact: {source}")
        destination = downloads_dir / filename
        shutil.copy2(source, destination)
        copied = dict(artifact)
        copied["url"] = artifact_url(filename, args.artifact_base_url or None)
        copied_artifacts.append(copied)

    checksums_name = f"Cortex-{manifest['version']}-{manifest['build']}.checksums.txt"
    checksums_source = release_dir / checksums_name
    if checksums_source.exists():
        shutil.copy2(checksums_source, downloads_dir / checksums_name)

    site_manifest = dict(manifest)
    site_manifest["artifacts"] = copied_artifacts
    (downloads_dir / "latest.json").write_text(json.dumps(site_manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    summary = {
        "prepared_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "version": manifest["version"],
        "build": manifest["build"],
        "channel": manifest["channel"],
        "artifacts": [
            {
                "kind": artifact["kind"],
                "filename": artifact["filename"],
                "url": artifact_url(artifact["filename"], args.artifact_base_url or None),
                "size_bytes": artifact["size_bytes"],
                "sha256": artifact["sha256"],
            }
            for artifact in copied_artifacts
        ],
    }
    (downloads_dir / "distribution.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"status": "ok", "site_dir": str(site_dir), "release": f"{manifest['version']}-{manifest['build']}", "artifacts": len(copied_artifacts)}, indent=2))


if __name__ == "__main__":
    main()
