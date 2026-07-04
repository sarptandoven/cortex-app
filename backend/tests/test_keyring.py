"""Envelope-encryption keyring tests (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §4).

Release-gate coverage: CXE1 roundtrip, cross-user AAD rejection, wrong-purpose
rejection, legacy plaintext sniff, nonce-budget dek_version bump, crypto-shred,
no-KEK clean disable, malformed-KEK errors, and a thread-safety smoke.
"""

from __future__ import annotations

import base64
import sqlite3
import struct
import tempfile
import threading
import unittest
from pathlib import Path

from backend.app.keyring import (
    CXE1_MAGIC,
    CredentialCipher,
    DecryptionError,
    EncryptionUnavailableError,
    KekConfigError,
    KeyringError,
    LocalKekProvider,
    ShreddedKeyError,
    UserKeyring,
    is_encrypted,
)

KEK_B64 = base64.b64encode(bytes(range(32))).decode("ascii")


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def blob_dek_version(blob: bytes) -> int:
    kek_id_len = blob[4]
    (version,) = struct.unpack(">I", blob[5 + kek_id_len : 9 + kek_id_len])
    return version


class KeyringTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "keys.sqlite"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def provider(self, **extra: str) -> LocalKekProvider:
        return LocalKekProvider(env={"CORTEX_KEK": KEK_B64, **extra})

    def keyring(self, **kwargs) -> UserKeyring:
        provider = kwargs.pop("provider", None) or self.provider()
        return UserKeyring(self.db_path, provider, **kwargs)

    def db_rows(self, query: str, *params) -> list[sqlite3.Row]:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        try:
            return conn.execute(query, params).fetchall()
        finally:
            conn.close()

    # ------------------------------------------------------------- envelope

    def test_envelope_roundtrip_and_cxe1_format(self) -> None:
        keyring = self.keyring()
        secret = b'{"access_token": "tok-supersecret-123"}'
        blob = keyring.encrypt_blob("user-a", "credentials", secret)

        self.assertTrue(blob.startswith(CXE1_MAGIC))
        self.assertTrue(is_encrypted(blob))
        self.assertNotIn(b"supersecret", blob)
        kek_id_len = blob[4]
        self.assertEqual(blob[5 : 5 + kek_id_len], b"local:v1")
        self.assertEqual(blob_dek_version(blob), 1)
        # magic + len byte + kek_id + version + nonce + ct + tag
        self.assertEqual(len(blob), 4 + 1 + kek_id_len + 4 + 12 + len(secret) + 16)

        self.assertEqual(keyring.decrypt_blob("user-a", "credentials", blob), secret)

    def test_kek_version_env_changes_kek_id(self) -> None:
        keyring = self.keyring(provider=self.provider(CORTEX_KEK_VERSION="7"))
        blob = keyring.encrypt_blob("user-a", "content", b"x")
        self.assertEqual(keyring.provider.kek_id, "local:v7")
        self.assertIn(b"local:v7", blob)
        self.assertEqual(keyring.decrypt_blob("user-a", "content", blob), b"x")

    def test_kek_from_secret_file(self) -> None:
        kek_file = self.root / "kek.secret"
        kek_file.write_text(KEK_B64 + "\n", encoding="utf-8")
        provider = LocalKekProvider(env={"CORTEX_KEK_FILE": str(kek_file)})
        self.assertTrue(provider.available)
        keyring = self.keyring(provider=provider)
        blob = keyring.encrypt_blob("user-a", "vault", b"data")
        self.assertEqual(keyring.decrypt_blob("user-a", "vault", blob), b"data")

    def test_kek_file_raw_32_bytes(self) -> None:
        kek_file = self.root / "kek.raw"
        kek_file.write_bytes(bytes(range(32)))
        provider = LocalKekProvider(env={"CORTEX_KEK_FILE": str(kek_file)})
        self.assertTrue(provider.available)
        # Same KEK bytes as KEK_B64: blobs interop across the two config forms.
        blob = self.keyring().encrypt_blob("user-a", "backup", b"data")
        self.assertEqual(
            UserKeyring(self.db_path, provider).decrypt_blob("user-a", "backup", blob), b"data"
        )

    # ------------------------------------------------------------ rejection

    def test_cross_user_aad_rejection(self) -> None:
        keyring = self.keyring()
        blob_a = keyring.encrypt_blob("user-a", "credentials", b"a-secret")
        # Provision user-b so the failure is the AEAD authentication (AAD binds
        # user_id), not merely a missing key row.
        keyring.encrypt_blob("user-b", "credentials", b"b-secret")
        with self.assertRaises(DecryptionError):
            keyring.decrypt_blob("user-b", "credentials", blob_a)

    def test_decrypt_for_unknown_user_rejected(self) -> None:
        keyring = self.keyring()
        blob = keyring.encrypt_blob("user-a", "credentials", b"secret")
        with self.assertRaises(KeyringError):
            keyring.decrypt_blob("user-never-seen", "credentials", blob)

    def test_wrong_purpose_rejection(self) -> None:
        keyring = self.keyring()
        blob = keyring.encrypt_blob("user-a", "credentials", b"secret")
        with self.assertRaises(DecryptionError):
            keyring.decrypt_blob("user-a", "content", blob)

    def test_invalid_purpose_rejected(self) -> None:
        keyring = self.keyring()
        with self.assertRaises(ValueError):
            keyring.encrypt_blob("user-a", "not-a-purpose", b"x")
        with self.assertRaises(ValueError):
            keyring.decrypt_blob("user-a", "not-a-purpose", CXE1_MAGIC + b"\x01a" + b"\x00" * 40)

    def test_truncated_blob_rejected(self) -> None:
        keyring = self.keyring()
        blob = keyring.encrypt_blob("user-a", "credentials", b"secret")
        with self.assertRaises(DecryptionError):
            keyring.decrypt_blob("user-a", "credentials", blob[:20])
        # Bit-flip in the ciphertext body fails authentication.
        tampered = bytearray(blob)
        tampered[-1] ^= 0x01
        with self.assertRaises(DecryptionError):
            keyring.decrypt_blob("user-a", "credentials", bytes(tampered))

    # ------------------------------------------------------- legacy plaintext

    def test_legacy_plaintext_sniff_returns_input_unchanged(self) -> None:
        keyring = self.keyring()
        legacy = b'{"token": "plain-old-secret"}'
        self.assertFalse(is_encrypted(legacy))
        self.assertEqual(keyring.decrypt_blob("user-a", "credentials", legacy), legacy)
        # And the sniff works even with no KEK configured at all.
        disabled = UserKeyring(self.root / "other.sqlite", LocalKekProvider(env={}))
        self.assertEqual(disabled.decrypt_blob("user-a", "credentials", legacy), legacy)

    # ----------------------------------------------------------- nonce budget

    def test_dek_version_bumps_when_nonce_budget_exceeded(self) -> None:
        keyring = self.keyring(nonce_budget=2)
        blob1 = keyring.encrypt_blob("user-a", "credentials", b"one")
        blob2 = keyring.encrypt_blob("user-a", "credentials", b"two")
        blob3 = keyring.encrypt_blob("user-a", "credentials", b"three")

        self.assertEqual(blob_dek_version(blob1), 1)
        self.assertEqual(blob_dek_version(blob2), 1)
        self.assertEqual(blob_dek_version(blob3), 2)

        # Old versions stay readable: decrypt selects the wrap row by version.
        self.assertEqual(keyring.decrypt_blob("user-a", "credentials", blob1), b"one")
        self.assertEqual(keyring.decrypt_blob("user-a", "credentials", blob3), b"three")

        rows = self.db_rows("SELECT dek_version, write_count FROM user_keys WHERE user_id = ?", "user-a")
        self.assertEqual((rows[0]["dek_version"], rows[0]["write_count"]), (2, 1))
        wraps = self.db_rows(
            "SELECT dek_version FROM user_key_wraps WHERE user_id = ? ORDER BY dek_version", "user-a"
        )
        self.assertEqual([row["dek_version"] for row in wraps], [1, 2])

    # ----------------------------------------------------------- crypto-shred

    def test_crypto_shred_makes_decrypt_and_encrypt_fail(self) -> None:
        keyring = self.keyring()
        blob_a = keyring.encrypt_blob("user-a", "credentials", b"a-secret")
        blob_b = keyring.encrypt_blob("user-b", "credentials", b"b-secret")

        result = keyring.crypto_shred("user-a")
        self.assertEqual(result["wraps_destroyed"], 1)

        # Decrypt fails immediately even though the DEK was cached seconds ago.
        with self.assertRaises(ShreddedKeyError):
            keyring.decrypt_blob("user-a", "credentials", blob_a)
        # New writes can never silently mint fresh key material.
        with self.assertRaises(ShreddedKeyError):
            keyring.encrypt_blob("user-a", "credentials", b"again")
        # Wrap rows are destroyed, tombstone is recorded.
        self.assertEqual(self.db_rows("SELECT * FROM user_key_wraps WHERE user_id = ?", "user-a"), [])
        rows = self.db_rows("SELECT destroyed_at FROM user_keys WHERE user_id = ?", "user-a")
        self.assertTrue(rows[0]["destroyed_at"])
        # Co-tenant is untouched.
        self.assertEqual(keyring.decrypt_blob("user-b", "credentials", blob_b), b"b-secret")

    def test_crypto_shred_survives_process_restart(self) -> None:
        keyring = self.keyring()
        blob = keyring.encrypt_blob("user-a", "credentials", b"secret")
        keyring.crypto_shred("user-a")
        fresh = self.keyring()  # new instance, empty cache — same DB file
        with self.assertRaises(ShreddedKeyError):
            fresh.decrypt_blob("user-a", "credentials", blob)

    def test_crypto_shred_never_keyed_user_blocks_future_encrypts(self) -> None:
        keyring = self.keyring()
        keyring.crypto_shred("user-ghost")
        with self.assertRaises(ShreddedKeyError):
            keyring.encrypt_blob("user-ghost", "credentials", b"x")

    # ------------------------------------------------------------- no/bad KEK

    def test_no_kek_configured_disables_encryption_cleanly(self) -> None:
        provider = LocalKekProvider(env={})
        self.assertFalse(provider.available)
        keyring = self.keyring(provider=provider)
        self.assertFalse(keyring.available)
        with self.assertRaises(EncryptionUnavailableError):
            keyring.encrypt_blob("user-a", "credentials", b"secret")
        with self.assertRaises(EncryptionUnavailableError):
            keyring.decrypt_blob("user-a", "credentials", CXE1_MAGIC + b"\x01a" + b"\x00" * 40)

    def test_malformed_kek_raises_clear_error(self) -> None:
        with self.assertRaises(KekConfigError):
            LocalKekProvider(env={"CORTEX_KEK": "not-valid-base64!!!"})
        with self.assertRaises(KekConfigError):
            LocalKekProvider(env={"CORTEX_KEK": base64.b64encode(b"short").decode()})
        with self.assertRaises(KekConfigError):
            LocalKekProvider(env={"CORTEX_KEK": KEK_B64, "CORTEX_KEK_VERSION": "zero"})
        with self.assertRaises(KekConfigError):
            LocalKekProvider(env={"CORTEX_KEK_FILE": str(self.root / "missing.kek")})
        bad_file = self.root / "bad.kek"
        bad_file.write_text("definitely not base64 !!!", encoding="utf-8")
        with self.assertRaises(KekConfigError):
            LocalKekProvider(env={"CORTEX_KEK_FILE": str(bad_file)})

    def test_kek_mismatch_on_unwrap_is_a_clear_error(self) -> None:
        blob = self.keyring().encrypt_blob("user-a", "credentials", b"secret")
        rotated = UserKeyring(self.db_path, self.provider(CORTEX_KEK_VERSION="2"))
        with self.assertRaises(KekConfigError):
            rotated.decrypt_blob("user-a", "credentials", blob)

    # ------------------------------------------------------------ cache + TTL

    def test_dek_cache_ttl_expiry_with_injected_clock(self) -> None:
        clock = FakeClock()
        keyring = self.keyring(cache_ttl_seconds=300.0, clock=clock)
        blob = keyring.encrypt_blob("user-a", "credentials", b"secret")
        self.assertEqual(len(keyring._cache), 1)
        clock.advance(301.0)
        # Expired entry is dropped and the DEK is transparently re-unwrapped.
        self.assertEqual(keyring.decrypt_blob("user-a", "credentials", blob), b"secret")
        self.assertEqual(len(keyring._cache), 1)

    def test_dek_cache_is_lru_bounded(self) -> None:
        keyring = self.keyring(cache_size=2)
        for user in ("u1", "u2", "u3"):
            keyring.encrypt_blob(user, "credentials", b"x")
        self.assertLessEqual(len(keyring._cache), 2)

    # ------------------------------------------------------------ concurrency

    def test_thread_safety_smoke(self) -> None:
        keyring = self.keyring()
        users = [f"user-{i}" for i in range(4)]
        errors: list[BaseException] = []
        barrier = threading.Barrier(8)

        def worker(worker_id: int) -> None:
            try:
                barrier.wait(timeout=10)
                for i in range(20):
                    user = users[(worker_id + i) % len(users)]
                    payload = f"secret-{worker_id}-{i}".encode()
                    blob = keyring.encrypt_blob(user, "credentials", payload)
                    if keyring.decrypt_blob(user, "credentials", blob) != payload:
                        raise AssertionError("roundtrip mismatch")
            except BaseException as exc:  # noqa: BLE001 — collected and asserted below
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertEqual(errors, [])
        rows = self.db_rows("SELECT SUM(write_count) AS n FROM user_keys")
        self.assertEqual(int(rows[0]["n"]), 8 * 20)

    # -------------------------------------------------------- cipher adapter

    def test_credential_cipher_adapter(self) -> None:
        keyring = self.keyring()
        cipher = CredentialCipher(keyring)
        blob = cipher.encrypt("user-a", b"payload-bytes")
        self.assertTrue(cipher.is_encrypted(blob))
        self.assertEqual(cipher.decrypt("user-a", blob), b"payload-bytes")
        # Adapter is purpose-bound to 'credentials': a content-domain blob fails.
        content_blob = keyring.encrypt_blob("user-a", "content", b"payload-bytes")
        with self.assertRaises(DecryptionError):
            cipher.decrypt("user-a", content_blob)


if __name__ == "__main__":
    unittest.main()
