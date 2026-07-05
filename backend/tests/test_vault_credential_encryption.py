"""Connector-credential encryption at rest (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §4, phase 1).

`credentials.json` is a shared file across tenants in bucket mode and used to hold
OAuth tokens/client secrets as plaintext JSON — the design's "worst offender".
These tests pin: encrypted-on-disk roundtrip, lazy read-migrate-write-back of
legacy plaintext, byte-identical cipher=None behavior, crypto-shred tenant
isolation in a shared vault, and the StoreRegistry keyring injection seam.
"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.keyring import (
    CredentialCipher,
    LocalKekProvider,
    ShreddedKeyError,
    UserKeyring,
)
from backend.app.sharding import ShardRouter, StoreRegistry
from backend.app.vault import CortexVault

KEK_B64 = base64.b64encode(b"\x42" * 32).decode("ascii")
SECRET_TOKEN = "oauth-access-token-do-not-leak-8f31"


class VaultCredentialEncryptionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.keyring = UserKeyring(
            self.root / "keys.sqlite", LocalKekProvider(env={"CORTEX_KEK": KEK_B64})
        )
        self.cipher = CredentialCipher(self.keyring)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def vault(self, *, cipher=None, name: str = "vault") -> CortexVault:
        vault = CortexVault(self.root / name, self.root / f"{name}.sqlite", cipher=cipher)
        vault.ensure()
        return vault

    def write_credential(self, vault: CortexVault, user_id: str, *, token: str = SECRET_TOKEN) -> dict:
        return vault.write_source_credential(
            user_id=user_id,
            source_account_id=f"sa_{user_id}",
            source="github",
            payload={"access_token": token, "refresh_token": f"refresh-{token}"},
        )

    # -------------------------------------------------------------- encrypted

    def test_write_read_roundtrip_is_encrypted_on_disk(self) -> None:
        vault = self.vault(cipher=self.cipher)
        ref = self.write_credential(vault, "user-a")
        self.assertEqual(ref["credential_ref"], "source_credential:sa_user-a")

        raw = vault.credentials_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_TOKEN, raw)
        record = json.loads(raw)["users"]["user-a"]["sa_user-a"]
        self.assertNotIn("payload", record)
        self.assertIn("payload_cxe1", record)
        # Hex-encoded CXE1 envelope (starts with hex of the b"CXE1" magic).
        self.assertTrue(record["payload_cxe1"].startswith(b"CXE1".hex()))

        read = vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        self.assertIsNotNone(read)
        self.assertEqual(read["payload"]["access_token"], SECRET_TOKEN)
        self.assertEqual(read["source"], "github")

    def test_overwrite_keeps_created_at_and_stays_encrypted(self) -> None:
        vault = self.vault(cipher=self.cipher)
        self.write_credential(vault, "user-a", token="first-secret")
        created = json.loads(vault.credentials_path.read_text())["users"]["user-a"]["sa_user-a"]["created_at"]
        self.write_credential(vault, "user-a", token="second-secret")
        raw = vault.credentials_path.read_text(encoding="utf-8")
        self.assertNotIn("first-secret", raw)
        self.assertNotIn("second-secret", raw)
        record = json.loads(raw)["users"]["user-a"]["sa_user-a"]
        self.assertEqual(record["created_at"], created)
        read = vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        self.assertEqual(read["payload"]["access_token"], "second-secret")

    def test_encrypted_credential_unreadable_without_cipher(self) -> None:
        encrypting = self.vault(cipher=self.cipher)
        self.write_credential(encrypting, "user-a")
        plain_view = CortexVault(encrypting.root, encrypting.index_path)  # no cipher
        self.assertIsNone(
            plain_view.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        )

    # -------------------------------------------------- legacy lazy migration

    def test_legacy_plaintext_is_readable_and_rewritten_encrypted_on_first_read(self) -> None:
        legacy_vault = self.vault()  # cipher=None writes today's plaintext format
        self.write_credential(legacy_vault, "user-a")
        self.assertIn(SECRET_TOKEN, legacy_vault.credentials_path.read_text(encoding="utf-8"))

        vault = CortexVault(legacy_vault.root, legacy_vault.index_path, cipher=self.cipher)
        read = vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        self.assertEqual(read["payload"]["access_token"], SECRET_TOKEN)

        # First read migrated the entry: plaintext gone, envelope present.
        raw = vault.credentials_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_TOKEN, raw)
        record = json.loads(raw)["users"]["user-a"]["sa_user-a"]
        self.assertNotIn("payload", record)
        self.assertIn("payload_cxe1", record)

        # And subsequent reads keep working from the encrypted copy.
        again = vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        self.assertEqual(again["payload"]["access_token"], SECRET_TOKEN)

    def test_migration_only_touches_the_read_entry(self) -> None:
        legacy_vault = self.vault()
        self.write_credential(legacy_vault, "user-a")
        self.write_credential(legacy_vault, "user-b", token="b-token-untouched")

        vault = CortexVault(legacy_vault.root, legacy_vault.index_path, cipher=self.cipher)
        vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")

        payload = json.loads(vault.credentials_path.read_text(encoding="utf-8"))
        self.assertIn("payload_cxe1", payload["users"]["user-a"]["sa_user-a"])
        # user-b's entry stays plaintext until *its* first read (lazy, per-entry).
        self.assertEqual(
            payload["users"]["user-b"]["sa_user-b"]["payload"]["access_token"], "b-token-untouched"
        )

    # --------------------------------------------------------- cipher is None

    def test_cipher_none_path_is_byte_identical_to_legacy_behavior(self) -> None:
        vault = self.vault()
        self.write_credential(vault, "user-a")
        record = json.loads(vault.credentials_path.read_text(encoding="utf-8"))["users"]["user-a"]["sa_user-a"]
        self.assertEqual(
            sorted(record),
            ["created_at", "payload", "source", "source_account_id", "updated_at", "user_id"],
        )
        self.assertEqual(record["payload"]["access_token"], SECRET_TOKEN)
        read = vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        self.assertEqual(read["payload"]["access_token"], SECRET_TOKEN)
        self.assertTrue(
            vault.delete_source_credential(user_id="user-a", source_account_id="sa_user-a")
        )

    # ------------------------------------------------------------ crypto-shred

    def test_crypto_shred_unreads_one_tenant_in_shared_bucket_vault(self) -> None:
        vault = self.vault(cipher=self.cipher)  # one shared credentials.json, two tenants
        self.write_credential(vault, "user-a", token="a-secret-token")
        self.write_credential(vault, "user-b", token="b-secret-token")

        self.keyring.crypto_shred("user-a")

        with self.assertRaises(ShreddedKeyError):
            vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        surviving = vault.read_source_credential(user_id="user-b", source_account_id="sa_user-b")
        self.assertEqual(surviving["payload"]["access_token"], "b-secret-token")
        # The shredded user's blob is still on disk but is ciphertext forever.
        raw = vault.credentials_path.read_text(encoding="utf-8")
        self.assertNotIn("a-secret-token", raw)

    # ------------------------------------------------- StoreRegistry injection

    def router(self, mode: str) -> ShardRouter:
        return ShardRouter(
            mode=mode,
            db_path=self.root / "local.sqlite",
            vault_path=self.root / "local.vault",
            shard_root=self.root / "shards",
            shard_count=4,
        )

    def test_store_registry_injects_cipher_in_hosted_modes(self) -> None:
        registry = StoreRegistry(self.router("bucket"), default_user_id="local", keyring=self.keyring)
        store = registry.store_for_user("alice")
        self.assertIsNotNone(store.vault.cipher)

        store.vault.write_source_credential(
            user_id="alice", source_account_id="sa_alice", source="github",
            payload={"access_token": SECRET_TOKEN},
        )
        self.assertNotIn(SECRET_TOKEN, store.vault.credentials_path.read_text(encoding="utf-8"))
        read = store.vault.read_source_credential(user_id="alice", source_account_id="sa_alice")
        self.assertEqual(read["payload"]["access_token"], SECRET_TOKEN)

    def test_store_registry_local_mode_never_encrypts(self) -> None:
        registry = StoreRegistry(self.router("local"), default_user_id="local", keyring=self.keyring)
        self.assertIsNone(registry.store_for_user("alice").vault.cipher)

    def test_store_registry_without_available_kek_never_encrypts(self) -> None:
        disabled = UserKeyring(self.root / "nokek.sqlite", LocalKekProvider(env={}))
        registry = StoreRegistry(self.router("bucket"), default_user_id="local", keyring=disabled)
        self.assertIsNone(registry.store_for_user("alice").vault.cipher)
        no_keyring = StoreRegistry(self.router("user"), default_user_id="local")
        self.assertIsNone(no_keyring.store_for_user("alice").vault.cipher)

    def test_store_registry_keyring_settable_after_construction(self) -> None:
        registry = StoreRegistry(self.router("user"), default_user_id="local")
        before = registry.store_for_user("alice")
        self.assertIsNone(before.vault.cipher)
        registry.keyring = self.keyring  # main.py wiring happens post-construction
        after = registry.store_for_user("alice")
        self.assertIs(after, before)  # same cached store, retrofitted on next access
        self.assertIsNotNone(after.vault.cipher)


if __name__ == "__main__":
    unittest.main()
