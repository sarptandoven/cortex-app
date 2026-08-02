from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.ops_readiness_check import (
    check_support_bundle_contract,
    current_git_provenance,
    release_input_path,
    site_match_payload,
    verify_obsidian_plugin_bundle,
)
from scripts.validate_update_manifest import validate as validate_update_manifest


def valid_support_bundle() -> dict:
    return {
        "bundle_schema": 1,
        "privacy": {
            "contains_raw_capture_text": False,
            "contains_memory_content": False,
            "contains_context_pack": False,
            "contains_user_files": False,
        },
        "backend": {"features": ["operational-readiness"]},
        "summary": {"status": "ok", "counts": {"captures": 0}},
    }


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class OpsReadinessSupportBundleTests(unittest.TestCase):
    def test_support_bundle_contract_accepts_content_free_bundle(self) -> None:
        ok, payload = check_support_bundle_contract(valid_support_bundle())

        self.assertTrue(ok)
        self.assertTrue(payload["content_free"])
        self.assertEqual(payload["bundle_schema"], 1)
        self.assertFalse(payload["contains_raw_capture_text"])

    def test_support_bundle_contract_invokes_content_free_validator(self) -> None:
        bundle = valid_support_bundle()
        bundle["captures"] = [{"raw_text": "private text must not ship in support bundles"}]

        with self.assertRaisesRegex(ValueError, "content-free"):
            check_support_bundle_contract(bundle)

    def test_support_bundle_contract_rejects_oauth_refresh_fields(self) -> None:
        bundle = valid_support_bundle()
        bundle["diagnostics"] = {
            "refresh_token": "oauth_refresh_secret_123",
            "client_id": "oauth_client_id_123",
            "client_secret": "oauth_client_secret_123",
        }

        with self.assertRaisesRegex(ValueError, "content-free"):
            check_support_bundle_contract(bundle)

    def test_support_bundle_contract_requires_ops_readiness_feature(self) -> None:
        bundle = copy.deepcopy(valid_support_bundle())
        bundle["backend"]["features"] = []

        ok, payload = check_support_bundle_contract(bundle)

        self.assertFalse(ok)
        self.assertTrue(payload["content_free"])

    def test_obsidian_plugin_bundle_check_accepts_current_packaged_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "packages" / "obsidian-cortex-plugin"
            bundled = root / "macos" / "build" / "Cortex.app" / "Contents" / "Resources" / "obsidian-cortex-plugin"
            source.mkdir(parents=True)
            bundled.mkdir(parents=True)
            files = {
                "manifest.json": '{"id":"cortex-memory","name":"Cortex Memory","version":"0.1.0","minAppVersion":"1.5.0"}\n',
                "versions.json": '{"0.1.0":"1.5.0"}\n',
                "main.js": "console.log('cortex obsidian bridge');\n",
            }
            for filename, text in files.items():
                (source / filename).write_text(text, encoding="utf-8")
                (bundled / filename).write_text(text, encoding="utf-8")

            payload = verify_obsidian_plugin_bundle(root, root / "macos" / "build" / "Cortex.app")

            self.assertTrue(payload["ok"])
            self.assertEqual([item["filename"] for item in payload["files"]], ["manifest.json", "main.js", "versions.json"])

    def test_obsidian_plugin_bundle_check_rejects_stale_packaged_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "packages" / "obsidian-cortex-plugin"
            bundled = root / "macos" / "build" / "Cortex.app" / "Contents" / "Resources" / "obsidian-cortex-plugin"
            source.mkdir(parents=True)
            bundled.mkdir(parents=True)
            common = '{"id":"cortex-memory","name":"Cortex Memory","version":"0.1.0","minAppVersion":"1.5.0"}\n'
            (source / "manifest.json").write_text(common, encoding="utf-8")
            (bundled / "manifest.json").write_text(common, encoding="utf-8")
            (source / "versions.json").write_text('{"0.1.0":"1.5.0"}\n', encoding="utf-8")
            (bundled / "versions.json").write_text('{"0.1.0":"1.5.0"}\n', encoding="utf-8")
            (source / "main.js").write_text("console.log('new');\n", encoding="utf-8")
            (bundled / "main.js").write_text("console.log('old');\n", encoding="utf-8")

            payload = verify_obsidian_plugin_bundle(root, root / "macos" / "build" / "Cortex.app")

            self.assertFalse(payload["ok"])
            self.assertIn("Bundled Obsidian plugin file is stale: main.js", payload["errors"])

    def test_update_manifest_accepts_optional_obsidian_plugin_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = [
                ("dmg", "Cortex-0.1.0-1.dmg", b"dmg"),
                ("zip", "Cortex-0.1.0-1.app.zip", b"zip"),
                ("obsidian-plugin", "cortex-memory-0.1.0.zip", b"obsidian plugin"),
            ]
            manifest_artifacts = []
            for kind, filename, content in artifacts:
                path = root / filename
                path.write_bytes(content)
                manifest_artifacts.append(
                    {
                        "kind": kind,
                        "filename": filename,
                        "url": path.resolve().as_uri(),
                        "size_bytes": len(content),
                        "sha256": sha256(path),
                    }
                )
            manifest = {
                "app": "Cortex",
                "bundle_id": "com.cortex.doppl",
                "channel": "local-beta",
                "version": "0.1.0",
                "build": "1",
                "minimum_macos": "13.0",
                "released_at": "2026-07-02T00:00:00Z",
                "mandatory": False,
                "release_notes": [],
                "artifacts": manifest_artifacts,
            }
            manifest_path = root / "latest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            payload = validate_update_manifest(manifest_path)

            self.assertEqual(payload["version"], "0.1.0")
            self.assertEqual({item["kind"] for item in payload["artifacts"]}, {"dmg", "zip", "obsidian-plugin"})

    def test_update_manifest_allows_explicit_https_release_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            artifacts = [
                {
                    "kind": kind,
                    "filename": filename,
                    "url": f"https://github.com/trace-cortex/releases/download/v1/{filename}",
                    "size_bytes": 123,
                    "sha256": "a" * 64,
                }
                for kind, filename in (
                    ("dmg", "Cortex-1.0.0-1.dmg"),
                    ("zip", "Cortex-1.0.0-1.app.zip"),
                )
            ]
            manifest = {
                "app": "Cortex",
                "bundle_id": "com.cortex.doppl",
                "channel": "stable",
                "version": "1.0.0",
                "build": "1",
                "minimum_macos": "13.0",
                "released_at": "2026-07-28T00:00:00Z",
                "mandatory": False,
                "release_notes": [],
                "artifacts": artifacts,
            }
            manifest_path = root / "latest.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

            with self.assertRaises(FileNotFoundError):
                validate_update_manifest(manifest_path)

            payload = validate_update_manifest(
                manifest_path, allow_remote_artifacts=True
            )

            self.assertEqual(payload["build"], "1")

            manifest["artifacts"][0]["url"] = (
                "http://downloads.example.test/Cortex-1.0.0-1.dmg"
            )
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                validate_update_manifest(manifest_path, allow_remote_artifacts=True)

            manifest["artifacts"][0]["url"] = (
                "https://downloads.example.test/Cortex-1.0.0-1.dmg"
            )
            manifest["artifacts"][0]["sha256"] = "not-a-digest"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaises(ValueError):
                validate_update_manifest(manifest_path, allow_remote_artifacts=True)

    def test_site_match_payload_can_skip_site_for_local_dmg_only_beta(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release_dir = root / "outputs" / "Cortex-0.1.0-1"
            release_dir.mkdir(parents=True)
            (release_dir / "latest.json").write_text('{"version":"0.1.0"}\n', encoding="utf-8")

            payload = site_match_payload(root, release_dir, skip_site=True)

            self.assertTrue(payload["ok"])
            self.assertTrue(payload["skipped"])
            self.assertIn("local-DMG-only", payload["reason"])
            self.assertEqual(payload["release_manifest"], str(release_dir / "latest.json"))

    def test_release_input_path_ignores_local_context_and_site_downloads(self) -> None:
        self.assertTrue(release_input_path("backend/app/sqlite_runtime.py"))
        self.assertTrue(release_input_path("packages/obsidian-cortex-plugin/main.js"))
        self.assertTrue(release_input_path("macos/Sources/CortexApp.swift"))
        self.assertFalse(release_input_path(".context/package-latest/Cortex.dmg"))
        self.assertFalse(release_input_path("site/downloads/Cortex.dmg"))

    def test_git_provenance_flags_untracked_release_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            (root / "backend" / "app").mkdir(parents=True)
            (root / "backend" / "app" / "sqlite_runtime.py").write_text("# release input\n", encoding="utf-8")
            (root / ".context").mkdir()
            (root / ".context" / "scratch.txt").write_text("ignored\n", encoding="utf-8")
            (root / "site" / "downloads").mkdir(parents=True)
            (root / "site" / "downloads" / "Cortex.dmg").write_text("ignored\n", encoding="utf-8")

            provenance = current_git_provenance(root)

            self.assertTrue(provenance["git_dirty"])
            self.assertEqual(provenance["untracked_release_inputs"], ["backend/app/sqlite_runtime.py"])


if __name__ == "__main__":
    unittest.main()
