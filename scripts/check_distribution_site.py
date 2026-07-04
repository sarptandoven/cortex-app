from __future__ import annotations

import argparse
import hashlib
import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlparse


class SiteParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.refs: list[tuple[str, str]] = []
        self.ids: set[str] = set()

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if values.get("id"):
            self.ids.add(values["id"] or "")
        for key in ("href", "src"):
            value = values.get(key)
            if value:
                self.refs.append((key, value))


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_external(ref: str) -> bool:
    parsed = urlparse(ref)
    return parsed.scheme in {"http", "https", "mailto", "tel", "javascript"} or bool(parsed.netloc)


def local_target(site_dir: Path, html_path: Path, ref: str) -> tuple[Path, str | None] | None:
    if is_external(ref):
        return None
    parsed = urlparse(ref)
    if not parsed.path and parsed.fragment:
        return html_path, parsed.fragment
    if not parsed.path:
        return None
    if parsed.path.startswith("/"):
        target = site_dir / parsed.path.lstrip("/")
    else:
        target = html_path.parent / parsed.path
    return target.resolve(), parsed.fragment or None


def parse_html(path: Path) -> SiteParser:
    parser = SiteParser()
    parser.feed(path.read_text(encoding="utf-8"))
    return parser


def validate_html(site_dir: Path) -> list[str]:
    errors: list[str] = []
    parsers: dict[Path, SiteParser] = {}
    for html_path in sorted(site_dir.glob("*.html")):
        parsers[html_path.resolve()] = parse_html(html_path)

    for html_path, parser in parsers.items():
        for _, ref in parser.refs:
            target = local_target(site_dir, html_path, ref)
            if target is None:
                continue
            target_path, fragment = target
            if not str(target_path).startswith(str(site_dir.resolve())):
                errors.append(f"{html_path.name}: local reference escapes site directory: {ref}")
                continue
            if not target_path.exists():
                errors.append(f"{html_path.name}: missing local reference: {ref}")
                continue
            if fragment and target_path.suffix == ".html":
                target_parser = parsers.get(target_path) or parse_html(target_path)
                parsers[target_path] = target_parser
                if fragment not in target_parser.ids:
                    errors.append(f"{html_path.name}: missing anchor #{fragment} in {target_path.name}")

    index = site_dir / "index.html"
    index_text = index.read_text(encoding="utf-8") if index.exists() else ""
    required_tokens = [
        "id=\"memoryCanvas\"",
        "class=\"button primary download-link\"",
        "href=\"downloads/latest.json\"",
        "href=\"privacy.html\"",
        "Your private personal memory model for AI.",
    ]
    for token in required_tokens:
        if token not in index_text:
            errors.append(f"index.html: missing required landing-page token: {token}")
    return errors


def validate_manifest(site_dir: Path) -> list[str]:
    errors: list[str] = []
    manifest_path = site_dir / "downloads" / "latest.json"
    if not manifest_path.exists():
        return ["downloads/latest.json is missing"]

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"downloads/latest.json is invalid JSON: {exc}"]

    for key in ("app", "version", "build", "channel", "artifacts"):
        if key not in manifest:
            errors.append(f"downloads/latest.json: missing key {key}")

    artifacts = manifest.get("artifacts", [])
    kinds = {artifact.get("kind") for artifact in artifacts}
    for kind in ("dmg", "zip"):
        if kind not in kinds:
            errors.append(f"downloads/latest.json: missing {kind} artifact")

    for artifact in artifacts:
        filename = artifact.get("filename")
        if not filename:
            errors.append("downloads/latest.json: artifact missing filename")
            continue
        url = artifact.get("url", "")
        if not url:
            errors.append(f"downloads/latest.json: artifact URL missing for {filename}")
        path = site_dir / "downloads" / filename
        # When the artifact is hosted off-site (GitHub Releases / a CDN, i.e. an
        # absolute https:// URL) and is not present on disk, skip the local
        # existence/size/sha256 checks: the bytes live in the Release, not git.
        # If a local copy IS still on disk (the current local-file layout), keep
        # the strict size/sha256 validation so nothing regresses.
        if is_external(url) and not path.exists():
            continue
        if not path.exists():
            errors.append(f"downloads/latest.json: artifact file missing: {filename}")
            continue
        expected_size = artifact.get("size_bytes")
        if expected_size != path.stat().st_size:
            errors.append(f"downloads/latest.json: size mismatch for {filename}")
        expected_hash = artifact.get("sha256")
        if expected_hash and expected_hash != sha256(path):
            errors.append(f"downloads/latest.json: sha256 mismatch for {filename}")

    checksums = list((site_dir / "downloads").glob("*.checksums.txt"))
    if not checksums:
        errors.append("downloads: checksum file is missing")
    return errors


def self_test() -> list[str]:
    """Exercise both manifest branches in a throwaway site dir.

    Proves that (1) a fully local artifact layout still enforces
    existence/size/sha256, and (2) an artifact whose url is an absolute https URL
    with no on-disk file is tolerated (bytes hosted on a Release/CDN).
    """
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        site = Path(tmp) / "site"
        downloads = site / "downloads"
        downloads.mkdir(parents=True)
        # A checksums file must always be referenced.
        (downloads / "Cortex-0.0.0-0.checksums.txt").write_text("stub\n", encoding="utf-8")

        # Case 1: local dmg present, external zip absent -> should be OK.
        dmg = downloads / "Cortex-0.0.0-0.dmg"
        dmg.write_bytes(b"local-dmg-bytes")
        local_manifest = {
            "app": "Cortex",
            "version": "0.0.0",
            "build": "0",
            "channel": "self-test",
            "artifacts": [
                {
                    "kind": "dmg",
                    "filename": "Cortex-0.0.0-0.dmg",
                    "url": "downloads/Cortex-0.0.0-0.dmg",
                    "size_bytes": dmg.stat().st_size,
                    "sha256": sha256(dmg),
                },
                {
                    "kind": "zip",
                    "filename": "Cortex-0.0.0-0.app.zip",
                    "url": "https://github.com/org/repo/releases/download/v0.0.0/Cortex-0.0.0-0.app.zip",
                    "size_bytes": 999999,
                    "sha256": "0" * 64,
                },
            ],
        }
        (downloads / "latest.json").write_text(json.dumps(local_manifest), encoding="utf-8")
        errors = validate_manifest(site)
        if errors:
            failures.append(f"mixed local+external manifest should pass, got: {errors}")

        # Case 2: a local artifact that lies about its size -> must be caught.
        bad_manifest = json.loads(json.dumps(local_manifest))
        bad_manifest["artifacts"][0]["size_bytes"] = 1
        (downloads / "latest.json").write_text(json.dumps(bad_manifest), encoding="utf-8")
        errors = validate_manifest(site)
        if not any("size mismatch" in error for error in errors):
            failures.append(f"local size mismatch should be caught, got: {errors}")

        # Case 3: all artifacts external + absent -> tolerated (Release-hosted).
        external_manifest = json.loads(json.dumps(local_manifest))
        dmg.unlink()
        for artifact in external_manifest["artifacts"]:
            artifact["url"] = f"https://github.com/org/repo/releases/download/v0.0.0/{artifact['filename']}"
        (downloads / "latest.json").write_text(json.dumps(external_manifest), encoding="utf-8")
        errors = validate_manifest(site)
        if errors:
            failures.append(f"all-external manifest should pass, got: {errors}")

        # Case 4: external manifest with no checksums file -> must still fail.
        (downloads / "Cortex-0.0.0-0.checksums.txt").unlink()
        errors = validate_manifest(site)
        if not any("checksum file is missing" in error for error in errors):
            failures.append(f"missing checksums must be caught even for external, got: {errors}")

    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate the Cortex static landing page and release downloads.")
    parser.add_argument("--site-dir", type=Path, default=Path("site"), help="Static site directory. Default: site")
    parser.add_argument("--self-test", action="store_true", help="Run built-in checks for the local vs Release-hosted manifest branches and exit.")
    args = parser.parse_args()

    if args.self_test:
        failures = self_test()
        if failures:
            print(json.dumps({"status": "error", "self_test": "failed", "failures": failures}, indent=2))
            raise SystemExit(1)
        print(json.dumps({"status": "ok", "self_test": "passed", "cases": ["local-file-strict", "local-size-mismatch-caught", "release-url-tolerated", "checksums-required"]}, indent=2))
        return

    site_dir = args.site_dir.resolve()
    errors = []
    if not site_dir.exists():
        errors.append(f"Site directory does not exist: {site_dir}")
    else:
        errors.extend(validate_html(site_dir))
        errors.extend(validate_manifest(site_dir))

    if errors:
        print(json.dumps({"status": "error", "site_dir": str(site_dir), "errors": errors}, indent=2))
        raise SystemExit(1)
    print(json.dumps({"status": "ok", "site_dir": str(site_dir), "checks": ["html-links", "release-manifest", "artifact-hashes"]}, indent=2))


if __name__ == "__main__":
    main()
