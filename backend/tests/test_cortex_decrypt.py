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


class CXEC1ZeroAccessTests(unittest.TestCase):
    """The offline tool must decrypt zero-access (client-key) CXEC1 blobs — the proof that
    a user with their recovery code owns their zero-access data even if Cortex dies. We
    build the blob exactly as the macOS CortexE2EE seal produces it (AES-256-GCM, 12-byte
    nonce, AAD = capture_id, wire = magic || nonce || ciphertext || tag)."""

    def _seal(self, key: bytes, capture_id: str, plaintext: bytes) -> bytes:
        import os as _os
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = _os.urandom(12)
        ct = AESGCM(key).encrypt(nonce, plaintext, capture_id.encode("utf-8"))
        return cortex_decrypt.CXEC1_MAGIC + nonce + ct

    def test_round_trip_with_sync_key(self) -> None:
        key = bytes(range(32, 64))
        payload = b'{"content":"my zero-access memory","title":null,"source":"macos"}'
        blob = self._seal(key, "cap_zero1", payload)
        self.assertTrue(cortex_decrypt.is_cxec1(blob))
        got = cortex_decrypt.decrypt_cxec1(blob, key, "cap_zero1")
        self.assertEqual(got, payload)

    def test_wrong_capture_id_fails(self) -> None:
        key = bytes(range(32, 64))
        blob = self._seal(key, "cap_zero1", b"secret")
        with self.assertRaises(cortex_decrypt.DecryptError):
            cortex_decrypt.decrypt_cxec1(blob, key, "cap_OTHER")

    def test_wrong_key_fails(self) -> None:
        blob = self._seal(bytes(range(32, 64)), "cap_zero1", b"secret")
        with self.assertRaises(cortex_decrypt.DecryptError):
            cortex_decrypt.decrypt_cxec1(blob, bytes(32), "cap_zero1")

    def _base32_encode(self, data: bytes) -> str:
        # MSB-first Crockford Base32, mirroring CortexE2EE.base32Encode.
        alphabet = cortex_decrypt._CROCKFORD
        out = []
        buffer = 0
        bits = 0
        for byte in data:
            buffer = (buffer << 8) | byte
            bits += 8
            while bits >= 5:
                bits -= 5
                out.append(alphabet[(buffer >> bits) & 0x1F])
        if bits > 0:
            out.append(alphabet[(buffer << (5 - bits)) & 0x1F])
        return "".join(out)

    def _recovery_code(self, key: bytes) -> str:
        payload = key + bytes([cortex_decrypt._crc8_atm(key)])
        symbols = self._base32_encode(payload)
        return "-".join(symbols[i : i + 4] for i in range(0, len(symbols), 4))

    def test_recovery_code_decodes_to_key(self) -> None:
        key = bytes((i * 7 + 3) & 0xFF for i in range(32))
        code = self._recovery_code(key)
        self.assertEqual(cortex_decrypt.key_from_recovery_code(code), key)
        # Lowercase + Crockford aliases (O->0, I/L->1) + missing hyphens all still decode.
        munged = code.lower().replace("-", "").replace("0", "o").replace("1", "l")
        self.assertEqual(cortex_decrypt.key_from_recovery_code(munged), key)

    def test_recovery_code_checksum_rejects_typo(self) -> None:
        key = bytes(range(32))
        code = list(self._recovery_code(key))
        # Flip one symbol to a different valid Crockford symbol.
        i = next(idx for idx, ch in enumerate(code) if ch != "-")
        code[i] = "Z" if code[i] != "Z" else "Y"
        with self.assertRaises(cortex_decrypt.DecryptError):
            cortex_decrypt.key_from_recovery_code("".join(code))

    def test_decrypt_zero_access_with_recovery_code(self) -> None:
        key = bytes((i * 11 + 5) & 0xFF for i in range(32))
        code = self._recovery_code(key)
        blob = self._seal(key, "cap_rec", b'{"content":"recovered from my code alone"}')
        got = cortex_decrypt.decrypt_cxec1(
            blob, cortex_decrypt.key_from_recovery_code(code), "cap_rec"
        )
        self.assertEqual(got, b'{"content":"recovered from my code alone"}')

    def test_cli_cxec1_end_to_end(self) -> None:
        tmp = Path(tempfile.mkdtemp())
        key = bytes(range(32, 64))
        blob = self._seal(key, "cap_cli", b"cli zero-access plaintext")
        (tmp / "z.cxec1").write_bytes(blob)
        out = tmp / "z.out"
        rc = cortex_decrypt.main([
            "cxec1", "--key", base64.b64encode(key).decode(),
            "--capture-id", "cap_cli", "--in", str(tmp / "z.cxec1"), "--out", str(out),
        ])
        self.assertEqual(rc, 0)
        self.assertEqual(out.read_bytes(), b"cli zero-access plaintext")


if __name__ == "__main__":
    unittest.main()
