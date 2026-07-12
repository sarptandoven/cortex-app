"""The offline `cortex-decrypt` tool must decrypt real keyring output byte-for-byte.

This is the load-bearing guarantee behind "a memory you own, readable even if Cortex
dies": scripts/cortex_decrypt.py re-implements the CXE1 format from the published spec
(docs/CXE1_WIRE_FORMAT.md) rather than importing the app, so these tests pin that the
two can never silently drift. If keyring.py changes the envelope, this fails loudly.
"""
from __future__ import annotations

import base64
import importlib.util
import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.keyring import LocalKekProvider, UserKeyring

# Import the standalone script by path (it lives in scripts/, not a package).
_TOOL_PATH = Path(__file__).resolve().parents[2] / "scripts" / "cortex_decrypt.py"
_spec = importlib.util.spec_from_file_location("cortex_decrypt", _TOOL_PATH)
cortex_decrypt = importlib.util.module_from_spec(_spec)
assert _spec and _spec.loader
_spec.loader.exec_module(cortex_decrypt)


def _make_keyring(tmp: Path) -> tuple[UserKeyring, bytes, str]:
    kek = bytes(range(32))  # deterministic 32-byte KEK
    kek_b64 = base64.b64encode(kek).decode()
    provider = LocalKekProvider(env={"CORTEX_KEK": kek_b64, "CORTEX_KEK_VERSION": "1"})
    keyring = UserKeyring(tmp / "keyring.sqlite", provider)
    return keyring, kek, kek_b64


def _wrapped_dek(keyring_db: Path, user_id: str, dek_version: int) -> bytes:
    conn = sqlite3.connect(keyring_db)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT wrapped_dek FROM user_key_wraps "
            "WHERE user_id = ? AND dek_version = ? AND wrap_type = 'service'",
            (user_id, dek_version),
        ).fetchone()
    finally:
        conn.close()
    assert row is not None
    return bytes(row["wrapped_dek"])


class CortexDecryptRoundTripTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.keyring, self.kek, self.kek_b64 = _make_keyring(self.tmp)

    def test_offline_tool_decrypts_keyring_output_via_kek_and_wrap(self) -> None:
        secret = b'{"token": "cxr_super_secret_value", "scope": "sync"}'
        blob = self.keyring.encrypt_blob("alice", "credentials", secret)
        self.assertTrue(cortex_decrypt.is_cxe1(blob))
        wrapped = _wrapped_dek(self.keyring.db_path, "alice", 1)

        recovered = cortex_decrypt.decrypt_blob(
            blob, user_id="alice", purpose="credentials", kek=self.kek, wrapped_dek=wrapped
        )
        self.assertEqual(recovered, secret)

    def test_offline_tool_decrypts_with_raw_dek(self) -> None:
        # A user who exported their raw DEK can decrypt without touching the KEK.
        blob = self.keyring.encrypt_blob("alice", "content", b"a memory in my own words")
        wrapped = _wrapped_dek(self.keyring.db_path, "alice", 1)
        dek = cortex_decrypt.unwrap_dek(self.kek, "alice", 1, "local:v1", wrapped)
        recovered = cortex_decrypt.decrypt_blob(
            blob, user_id="alice", purpose="content", dek=dek
        )
        self.assertEqual(recovered, b"a memory in my own words")

    def test_wrong_user_fails_authentication(self) -> None:
        blob = self.keyring.encrypt_blob("alice", "credentials", b"secret")
        wrapped = _wrapped_dek(self.keyring.db_path, "alice", 1)
        with self.assertRaises(cortex_decrypt.DecryptError):
            cortex_decrypt.decrypt_blob(
                blob, user_id="mallory", purpose="credentials", kek=self.kek, wrapped_dek=wrapped
            )

    def test_wrong_purpose_fails_authentication(self) -> None:
        blob = self.keyring.encrypt_blob("alice", "credentials", b"secret")
        wrapped = _wrapped_dek(self.keyring.db_path, "alice", 1)
        with self.assertRaises(cortex_decrypt.DecryptError):
            cortex_decrypt.decrypt_blob(
                blob, user_id="alice", purpose="content", kek=self.kek, wrapped_dek=wrapped
            )

    def test_legacy_plaintext_returned_unchanged(self) -> None:
        # Lazy read-migrate: a pre-encryption plaintext blob passes through untouched.
        plain = b"legacy plaintext memory"
        self.assertEqual(
            cortex_decrypt.decrypt_blob(plain, user_id="alice", purpose="content", dek=bytes(32)),
            plain,
        )

    def test_from_keyring_cli_end_to_end(self) -> None:
        secret = b"end to end via the CLI path"
        blob = self.keyring.encrypt_blob("alice", "credentials", secret)
        blob_path = self.tmp / "secret.cxe1"
        blob_path.write_bytes(blob)
        out_path = self.tmp / "out.bin"
        kek_file = self.tmp / "kek.b64"
        kek_file.write_text(self.kek_b64)

        rc = cortex_decrypt.main([
            "from-keyring",
            "--keyring", str(self.keyring.db_path),
            "--kek-file", str(kek_file),
            "--user", "alice",
            "--purpose", "credentials",
            "--in", str(blob_path),
            "--out", str(out_path),
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(out_path.read_bytes(), secret)

    def test_multiple_purposes_and_users_isolated(self) -> None:
        # Each (user, purpose) is cryptographically isolated by the AAD.
        self.keyring.encrypt_blob("bob", "vault", b"bob vault")
        alice_blob = self.keyring.encrypt_blob("alice", "backup", b"alice backup")
        alice_wrap = _wrapped_dek(self.keyring.db_path, "alice", 1)
        got = cortex_decrypt.decrypt_blob(
            alice_blob, user_id="alice", purpose="backup", kek=self.kek, wrapped_dek=alice_wrap
        )
        self.assertEqual(got, b"alice backup")


if __name__ == "__main__":
    unittest.main()
