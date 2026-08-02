from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path


class TwinEvalImportBoundaryTests(unittest.TestCase):
    def test_import_does_not_load_hosted_keyring(self) -> None:
        repository_root = Path(__file__).resolve().parents[2]
        result = subprocess.run(
            [
                sys.executable,
                "-S",
                "-c",
                (
                    "import sys; "
                    "import backend.app.twin_eval; "
                    "assert 'backend.app.keyring' not in sys.modules"
                ),
            ],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            result.returncode,
            0,
            msg=f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
