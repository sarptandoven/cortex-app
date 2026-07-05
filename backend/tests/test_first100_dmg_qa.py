from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import scripts.run_first100_dmg_qa as dmg_qa


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class First100DmgQaTests(unittest.TestCase):
    def test_release_dir_manifest_selects_dmg_and_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "outputs" / "Cortex-0.1.0-1"
            release_dir.mkdir(parents=True)
            dmg = release_dir / "Cortex-0.1.0-1.dmg"
            dmg.write_bytes(b"dmg")
            digest = sha256(dmg)
            (release_dir / "latest.json").write_text(
                json.dumps(
                    {
                        "version": "0.1.0",
                        "build": "1",
                        "artifacts": [
                            {
                                "kind": "dmg",
                                "filename": dmg.name,
                                "size_bytes": dmg.stat().st_size,
                                "sha256": digest,
                            }
                        ],
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(dmg_qa.find_default_dmg(release_dir), dmg)
            expected_hash, expected_source = dmg_qa.expected_sha256_for(dmg, release_dir=release_dir)

            self.assertEqual(expected_hash, digest)
            self.assertEqual(expected_source, str(release_dir / "latest.json"))

    def test_release_dir_can_fall_back_to_any_dmg_when_manifest_has_no_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            release_dir = Path(tmp) / "release"
            release_dir.mkdir()
            dmg = release_dir / "Cortex-0.1.0-1.dmg"
            dmg.write_bytes(b"dmg")
            (release_dir / "latest.json").write_text('{"version":"0.1.0"}\n', encoding="utf-8")

            self.assertEqual(dmg_qa.find_default_dmg(release_dir), dmg)


if __name__ == "__main__":
    unittest.main()
