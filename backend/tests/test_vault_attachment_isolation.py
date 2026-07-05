from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.vault import CortexVault


class VaultAttachmentDeletionIsolationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = CortexVault(root / "vault", root / "vault-index.sqlite")
        self.vault.ensure()
        self.attachments = self.vault.root / "attachments"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _write_attachment(self, user_id: str, name: str) -> Path:
        user_dir = self.attachments / user_id
        user_dir.mkdir(parents=True, exist_ok=True)
        path = user_dir / name
        path.write_text("attachment-bytes", encoding="utf-8")
        return path

    def test_delete_only_removes_the_target_users_attachments(self) -> None:
        alice_file = self._write_attachment("alice", "a1.bin")
        bob_file = self._write_attachment("bob", "b1.bin")

        counts = self.vault.delete_user_records("alice")

        self.assertFalse(alice_file.exists(), "alice's attachment should be deleted")
        self.assertEqual(counts["attachments"], 1)
        self.assertTrue(bob_file.exists(), "bob's attachment must survive alice's deletion")

    def test_path_traversal_user_id_cannot_escape_attachments_dir(self) -> None:
        # A sibling file outside attachments/ must never be touched even if a user_id tries
        # to traverse out of the attachments directory.
        outside = self.vault.root / "captures" / "keepme.json"
        outside.parent.mkdir(parents=True, exist_ok=True)
        outside.write_text("{}", encoding="utf-8")

        counts = self.vault.delete_user_records("../captures")

        self.assertTrue(outside.exists(), "traversal user_id must not delete files outside attachments/")
        self.assertEqual(counts["attachments"], 0)


if __name__ == "__main__":
    unittest.main()
