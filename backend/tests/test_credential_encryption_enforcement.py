"""CORTEX_REQUIRE_ENCRYPTED_CREDENTIALS enforcement (docs/ACCOUNTS_ENCRYPTION_DESIGN.md §4).

Enforcement makes the vault REFUSE to persist a plaintext credential:
- cipher present + enforce on  -> write stores encrypted (never plaintext)
- cipher ABSENT + enforce on   -> write raises (loud refusal, no plaintext write)
- enforce off                  -> today's behavior exactly (plaintext when no cipher)
- local mode (no cipher, off)  -> byte-identical to before

Also pins the StoreRegistry threading seam: from_settings reads
require_encrypted_credentials, and store_for_user propagates enforce_encryption
(and the cipher) onto the shard vault in hosted mode while leaving local mode
untouched.
"""

from __future__ import annotations

import base64
import json
import tempfile
import unittest
from pathlib import Path

from backend.app.config import Settings
from backend.app.keyring import CredentialCipher, LocalKekProvider, UserKeyring
from backend.app.sharding import ShardRouter, StoreRegistry
from backend.app.vault import CortexVault, CredentialEncryptionRequiredError

KEK_B64 = base64.b64encode(b"\x11" * 32).decode("ascii")
SECRET_TOKEN = "oauth-token-must-never-be-plaintext-77"


class CredentialEncryptionEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.keyring = UserKeyring(
            self.root / "keys.sqlite", LocalKekProvider(env={"CORTEX_KEK": KEK_B64})
        )
        self.cipher = CredentialCipher(self.keyring)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _vault(self, *, cipher=None, enforce=False, name: str = "vault") -> CortexVault:
        vault = CortexVault(
            self.root / name, self.root / f"{name}.sqlite",
            cipher=cipher, enforce_encryption=enforce,
        )
        vault.ensure()
        return vault

    def _write(self, vault: CortexVault, user_id: str = "user-a") -> dict:
        return vault.write_source_credential(
            user_id=user_id,
            source_account_id=f"sa_{user_id}",
            source="github",
            payload={"access_token": SECRET_TOKEN},
        )

    # ---------------------------------------------------------- cipher + enforce

    def test_enforce_on_with_cipher_stores_encrypted(self) -> None:
        vault = self._vault(cipher=self.cipher, enforce=True)
        self._write(vault)
        raw = vault.credentials_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_TOKEN, raw)
        record = json.loads(raw)["users"]["user-a"]["sa_user-a"]
        self.assertIn("payload_cxe1", record)
        self.assertNotIn("payload", record)
        read = vault.read_source_credential(user_id="user-a", source_account_id="sa_user-a")
        self.assertEqual(read["payload"]["access_token"], SECRET_TOKEN)

    def test_enforce_on_without_cipher_raises(self) -> None:
        vault = self._vault(cipher=None, enforce=True)
        with self.assertRaises(CredentialEncryptionRequiredError):
            self._write(vault)
        # Nothing plaintext was persisted.
        self.assertFalse(vault.credentials_path.exists() and SECRET_TOKEN in vault.credentials_path.read_text())

    # --------------------------------------------------------------- enforce off

    def test_enforce_off_no_cipher_is_todays_plaintext_behavior(self) -> None:
        vault = self._vault(cipher=None, enforce=False)
        self._write(vault)
        record = json.loads(vault.credentials_path.read_text(encoding="utf-8"))["users"]["user-a"]["sa_user-a"]
        self.assertIn("payload", record)
        self.assertEqual(record["payload"]["access_token"], SECRET_TOKEN)
        self.assertNotIn("payload_cxe1", record)

    def test_enforce_off_with_cipher_still_encrypts(self) -> None:
        vault = self._vault(cipher=self.cipher, enforce=False)
        self._write(vault)
        raw = vault.credentials_path.read_text(encoding="utf-8")
        self.assertNotIn(SECRET_TOKEN, raw)

    # ---------------------------------------------------- StoreRegistry threading

    def _router(self, mode: str) -> ShardRouter:
        return ShardRouter(
            mode=mode,
            db_path=self.root / "local.sqlite",
            vault_path=self.root / "local.vault",
            shard_root=self.root / "shards",
            shard_count=4,
        )

    def _settings(self, *, mode: str, enforce: bool) -> Settings:
        return Settings(
            vault_path=self.root / "local.vault",
            db_path=self.root / "local.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            default_user_id="local",
            shard_mode=mode,
            shard_root=self.root / "shards",
            shard_count=4,
            require_encrypted_credentials=enforce,
        )

    def test_from_settings_reads_enforcement_flag(self) -> None:
        registry = StoreRegistry.from_settings(self._settings(mode="user", enforce=True))
        self.assertTrue(registry.enforce_encryption)
        registry_off = StoreRegistry.from_settings(self._settings(mode="user", enforce=False))
        self.assertFalse(registry_off.enforce_encryption)

    def test_registry_hosted_mode_threads_cipher_and_enforce_to_vault(self) -> None:
        registry = StoreRegistry(
            self._router("bucket"), default_user_id="local",
            keyring=self.keyring, enforce_encryption=True,
        )
        vault = registry.store_for_user("alice").vault
        self.assertIsNotNone(vault.cipher)
        self.assertTrue(vault.enforce_encryption)
        vault.write_source_credential(
            user_id="alice", source_account_id="sa_alice", source="github",
            payload={"access_token": SECRET_TOKEN},
        )
        self.assertNotIn(SECRET_TOKEN, vault.credentials_path.read_text(encoding="utf-8"))

    def test_registry_enforce_without_keyring_refuses_plaintext_write(self) -> None:
        # Enforcement on but NO keyring reached the registry: the vault carries
        # enforce_encryption but no cipher, so a write must raise (never plaintext).
        registry = StoreRegistry(
            self._router("user"), default_user_id="local", enforce_encryption=True,
        )
        vault = registry.store_for_user("alice").vault
        self.assertIsNone(vault.cipher)
        self.assertTrue(vault.enforce_encryption)
        with self.assertRaises(CredentialEncryptionRequiredError):
            vault.write_source_credential(
                user_id="alice", source_account_id="sa_alice", source="github",
                payload={"access_token": SECRET_TOKEN},
            )

    def test_registry_local_mode_never_enforces_or_encrypts(self) -> None:
        registry = StoreRegistry(
            self._router("local"), default_user_id="local",
            keyring=self.keyring, enforce_encryption=True,
        )
        vault = registry.store_for_user("alice").vault
        # Local mode: injection returns early, so neither cipher nor enforcement is set.
        self.assertIsNone(vault.cipher)
        self.assertFalse(vault.enforce_encryption)
        vault.write_source_credential(
            user_id="alice", source_account_id="sa_alice", source="github",
            payload={"access_token": SECRET_TOKEN},
        )
        # Plaintext, exactly as before.
        self.assertIn(SECRET_TOKEN, vault.credentials_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
