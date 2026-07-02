from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import scripts.prepare_first100_launch_packets as packets


class PrepareFirst100LaunchPacketsTests(unittest.TestCase):
    def test_strict_command_includes_release_dir_for_local_dmg(self) -> None:
        output_dir = Path(".context/first100_launch_packets")
        release_dir = Path("outputs/Cortex-0.1.0-1")

        command = packets.strict_command(output_dir, release_dir)

        self.assertIn("--release-dir outputs/Cortex-0.1.0-1", command)
        self.assertIn("--support-packet .context/first100_launch_packets/support-packet.txt", command)
        self.assertIn("--require-human-packet --require-clean-profile-qa", command)

    def test_clean_profile_packet_uses_local_release_artifact_path(self) -> None:
        summary = {
            "generated_at": "2026-07-02T00:00:00Z",
            "git_head": "abc123",
            "manifest_path": "/tmp/outputs/Cortex-0.1.0-1/latest.json",
            "version": "0.1.0",
            "build": "1",
            "dmg_path": "/tmp/outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.dmg",
            "dmg_sha256": "dmg-hash",
        }

        packet = packets.clean_profile_packet(summary)

        self.assertIn("Manifest: /tmp/outputs/Cortex-0.1.0-1/latest.json", packet)
        self.assertIn("Artifact source: /tmp/outputs/Cortex-0.1.0-1/Cortex-0.1.0-1.dmg", packet)
        self.assertNotIn("Artifact source: site/downloads/", packet)

    def test_support_packet_includes_every_manifest_artifact_hash(self) -> None:
        summary = {
            "generated_at": "2026-07-02T00:00:00Z",
            "git_head": "abc123",
            "manifest_path": "/tmp/outputs/Cortex-0.1.0-1/latest.json",
            "version": "0.1.0",
            "build": "1",
            "artifact_hash_summary": "Cortex-0.1.0-1.dmg dmg-hash; Cortex-0.1.0-1.app.zip zip-hash; cortex-memory-0.1.0.zip plugin-hash",
            "dmg_name": "Cortex-0.1.0-1.dmg",
            "dmg_sha256": "dmg-hash",
            "zip_name": "Cortex-0.1.0-1.app.zip",
            "zip_sha256": "zip-hash",
        }

        packet = packets.support_packet(summary)

        self.assertIn("Cortex-0.1.0-1.dmg dmg-hash", packet)
        self.assertIn("Cortex-0.1.0-1.app.zip zip-hash", packet)
        self.assertIn("cortex-memory-0.1.0.zip plugin-hash", packet)

    def test_cli_accepts_local_release_dir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            release_dir = root / "outputs" / "Cortex-0.1.0-1"
            output_dir = root / "packets"
            release_dir.mkdir(parents=True)
            (release_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "build": "1",
                        "released_at": "2026-07-02T00:00:00Z",
                        "source_provenance": {"git_commit": "abc123", "git_dirty": False},
                        "artifacts": [
                            {
                                "kind": "dmg",
                                "filename": "Cortex-0.1.0-1.dmg",
                                "size_bytes": 3,
                                "sha256": "dmg-hash",
                            },
                            {
                                "kind": "zip",
                                "filename": "Cortex-0.1.0-1.app.zip",
                                "size_bytes": 3,
                                "sha256": "zip-hash",
                            },
                        ],
                    }
                ),
                encoding="utf-8",
            )

            completed = subprocess.run(
                [
                    "python3",
                    "scripts/prepare_first100_launch_packets.py",
                    "--release-dir",
                    str(release_dir),
                    "--output-dir",
                    str(output_dir),
                ],
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(completed.returncode, 0, completed.stderr)
            payload = json.loads(completed.stdout)
            self.assertEqual(payload["status"], "ok")
            self.assertIn(f"--release-dir {release_dir.resolve()}", payload["strict_launch_gate_command"])
            self.assertIn(
                f"Artifact source: {release_dir.resolve() / 'Cortex-0.1.0-1.dmg'}",
                (output_dir / "clean-profile-qa.txt").read_text(encoding="utf-8"),
            )


if __name__ == "__main__":
    unittest.main()
