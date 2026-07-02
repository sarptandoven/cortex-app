from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import scripts.first100_launch_gate as launch_gate


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class First100LaunchGateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_root = launch_gate.ROOT

    def tearDown(self) -> None:
        launch_gate.ROOT = self.original_root

    def test_release_input_path_ignores_context_and_site_downloads(self) -> None:
        self.assertTrue(launch_gate.release_input_path("backend/app/sqlite_runtime.py"))
        self.assertTrue(launch_gate.release_input_path("packages/obsidian-cortex-plugin/main.js"))
        self.assertTrue(launch_gate.release_input_path("macos/Sources/CortexApp.swift"))
        self.assertFalse(launch_gate.release_input_path(".context/package-latest/Cortex.dmg"))
        self.assertFalse(launch_gate.release_input_path("site/downloads/Cortex.dmg"))

    def test_worktree_flags_untracked_release_inputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launch_gate.ROOT = root
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            (root / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            (root / "backend" / "app").mkdir(parents=True)
            (root / "backend" / "app" / "sqlite_runtime.py").write_text("# release input\n", encoding="utf-8")
            (root / ".context").mkdir()
            (root / ".context" / "scratch.txt").write_text("ignored\n", encoding="utf-8")
            (root / "site" / "downloads").mkdir(parents=True)
            (root / "site" / "downloads" / "Cortex.dmg").write_text("ignored\n", encoding="utf-8")

            payload = launch_gate.check_worktree()

            self.assertFalse(payload["ok"])
            self.assertEqual(payload["untracked_release_inputs"], ["backend/app/sqlite_runtime.py"])

    def test_release_dir_manifest_validates_local_artifacts_without_site_downloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            launch_gate.ROOT = root
            subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
            (root / "README.md").write_text("fixture\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "init"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            commit = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                check=True,
                capture_output=True,
                text=True,
            ).stdout.strip()
            release_dir = root / "outputs" / "Cortex-0.1.0-1"
            release_dir.mkdir(parents=True)
            artifacts = [
                ("dmg", "Cortex-0.1.0-1.dmg", b"dmg"),
                ("zip", "Cortex-0.1.0-1.app.zip", b"zip"),
            ]
            manifest_artifacts = []
            checksum_lines = []
            for kind, filename, content in artifacts:
                path = release_dir / filename
                path.write_bytes(content)
                digest = sha256(path)
                manifest_artifacts.append(
                    {
                        "kind": kind,
                        "filename": filename,
                        "size_bytes": len(content),
                        "sha256": digest,
                    }
                )
                checksum_lines.append(f"{digest}  {filename}\n")
            (release_dir / "Cortex-0.1.0-1.checksums.txt").write_text("".join(checksum_lines), encoding="utf-8")
            (release_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "build": "1",
                        "released_at": "2026-07-02T00:00:00Z",
                        "source_provenance": {
                            "git_commit": commit,
                            "git_dirty": False,
                        },
                        "artifacts": manifest_artifacts,
                    }
                ),
                encoding="utf-8",
            )

            payload = launch_gate.check_release_manifest(release_dir)

            self.assertTrue(payload["ok"], payload["errors"])
            self.assertEqual([item["filename"] for item in payload["artifacts"]], ["Cortex-0.1.0-1.dmg", "Cortex-0.1.0-1.app.zip"])


if __name__ == "__main__":
    unittest.main()
