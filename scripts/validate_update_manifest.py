from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from urllib.parse import urlparse, unquote


REQUIRED_TOP_LEVEL = {
    "app",
    "bundle_id",
    "channel",
    "version",
    "build",
    "minimum_macos",
    "released_at",
    "mandatory",
    "release_notes",
    "artifacts",
}
REQUIRED_ARTIFACT = {"kind", "filename", "url", "size_bytes", "sha256"}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_path(root: Path, filename: str, url: str) -> Path:
    parsed = urlparse(url)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    return root / filename


def validate(manifest_path: Path, *, allow_remote_artifacts: bool = False) -> dict:
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    missing = sorted(REQUIRED_TOP_LEVEL - set(payload))
    if missing:
        raise ValueError(f"manifest missing top-level keys: {', '.join(missing)}")
    if payload["app"] != "Cortex":
        raise ValueError("manifest app must be Cortex")
    if payload["bundle_id"] != "com.cortex.doppl":
        raise ValueError("manifest bundle_id must be com.cortex.doppl")
    if not isinstance(payload["release_notes"], list):
        raise ValueError("release_notes must be a list")
    artifacts = payload["artifacts"]
    if not isinstance(artifacts, list) or not artifacts:
        raise ValueError("artifacts must be a non-empty list")

    root = manifest_path.parent
    seen_kinds: set[str] = set()
    for artifact in artifacts:
        missing_artifact = sorted(REQUIRED_ARTIFACT - set(artifact))
        if missing_artifact:
            raise ValueError(f"artifact missing keys: {', '.join(missing_artifact)}")
        kind = str(artifact["kind"])
        if kind not in {"dmg", "zip", "obsidian-plugin"}:
            raise ValueError(f"unsupported artifact kind: {kind}")
        seen_kinds.add(kind)
        url = str(artifact["url"])
        path = artifact_path(root, str(artifact["filename"]), url)
        if not path.exists():
            parsed = urlparse(url)
            if allow_remote_artifacts and parsed.scheme == "https":
                size = int(artifact["size_bytes"])
                digest = str(artifact["sha256"]).lower()
                if size <= 0:
                    raise ValueError(f"size_bytes must be positive for remote artifact: {artifact['filename']}")
                if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                    raise ValueError(f"sha256 must be a 64-character hexadecimal digest: {artifact['filename']}")
                continue
            raise FileNotFoundError(f"artifact not found: {path}")
        size = path.stat().st_size
        if size != int(artifact["size_bytes"]):
            raise ValueError(f"size mismatch for {path.name}: manifest={artifact['size_bytes']} actual={size}")
        digest = sha256(path)
        if digest != str(artifact["sha256"]).lower():
            raise ValueError(f"sha256 mismatch for {path.name}: manifest={artifact['sha256']} actual={digest}")

    if "dmg" not in seen_kinds:
        raise ValueError("manifest must include a dmg artifact")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a Cortex update manifest against local release artifacts.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument(
        "--allow-remote-artifacts",
        action="store_true",
        help="Allow absent artifacts only when their manifest URL uses HTTPS; schema, size, and digest metadata remain required.",
    )
    args = parser.parse_args()
    payload = validate(args.manifest, allow_remote_artifacts=args.allow_remote_artifacts)
    print(json.dumps({"status": "ok", "version": payload["version"], "build": payload["build"], "artifacts": len(payload["artifacts"])}, indent=2))


if __name__ == "__main__":
    main()
