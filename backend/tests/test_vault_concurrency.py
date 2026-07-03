from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path

from backend.app.vault import CortexVault


class VaultConcurrencyTests(unittest.TestCase):
    """The shipping server is multi-threaded (one PID) and in bucket mode many tenants share one
    vault. These tests guard the two concurrency hazards the round-2 review found: (a) a pid-only
    atomic-write temp path colliding across threads, and (b) unlocked read-modify-write of the
    shared root-level JSON files silently dropping updates."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.vault = CortexVault(root / "vault", root / "cortex.db")
        self.vault.ensure()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_concurrently(self, fn, count: int) -> list[BaseException]:
        errors: list[BaseException] = []
        barrier = threading.Barrier(count)

        def worker(i: int) -> None:
            try:
                barrier.wait()
                fn(i)
            except BaseException as exc:  # noqa: BLE001 - capture to assert later
                errors.append(exc)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(count)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        return errors

    def test_concurrent_write_settings_preserves_all_users(self) -> None:
        count = 24
        errors = self._run_concurrently(
            lambda i: self.vault.write_settings(f"user-{i}", {"n": i}), count
        )
        self.assertEqual(errors, [], f"write_settings raised under concurrency: {errors}")
        payload = json.loads(self.vault.settings_path.read_text(encoding="utf-8"))
        users = payload.get("users") or {}
        self.assertEqual(
            len(users), count, f"lost settings updates: only {len(users)}/{count} users survived"
        )
        for i in range(count):
            self.assertIn(f"user-{i}", users)

    def test_concurrent_write_source_credential_preserves_all(self) -> None:
        count = 24
        errors = self._run_concurrently(
            lambda i: self.vault.write_source_credential(
                user_id="shared-user",
                source_account_id=f"acct-{i}",
                source="github",
                payload={"token": f"secret-{i}"},
            ),
            count,
        )
        self.assertEqual(errors, [], f"write_source_credential raised under concurrency: {errors}")
        creds = json.loads(self.vault.credentials_path.read_text(encoding="utf-8"))
        accounts = (creds.get("users") or {}).get("shared-user") or {}
        self.assertEqual(
            len(accounts), count, f"lost credentials: only {len(accounts)}/{count} survived"
        )

    def test_concurrent_writes_to_same_memory_do_not_corrupt(self) -> None:
        # Repeatedly write the SAME memory id from many threads: pre-fix the shared pid-only temp
        # path collided and os.replace raised FileNotFoundError / installed a half-written file.
        def write(i: int) -> None:
            for _ in range(8):
                self.vault.write_memory(
                    {
                        "id": "mem_hot",
                        "user_id": "u",
                        "kind": "decision",
                        "layer": "decision",
                        "content": f"iteration {i}",
                        "topics": ["concurrency"],
                    }
                )

        errors = self._run_concurrently(write, 16)
        self.assertEqual(errors, [], f"concurrent write_memory raised: {errors}")

        # The durable JSON record must be present and valid (not truncated/corrupt).
        json_matches = list((self.vault.root / "memories").rglob("mem_hot.json"))
        self.assertEqual(len(json_matches), 1)
        record = json.loads(json_matches[0].read_text(encoding="utf-8"))
        self.assertEqual(record["id"], "mem_hot")

        # The Markdown note (rebuild source of truth) must also be present and parseable.
        md_records = self.vault.iter_memory_markdown_records("u")
        self.assertTrue(any(r["id"] == "mem_hot" for r in md_records))

    def test_concurrent_append_events_none_lost(self) -> None:
        count = 32
        errors = self._run_concurrently(
            lambda i: self.vault.append_event({"user_id": f"u{i}", "type": "test", "seq": i}),
            count,
        )
        self.assertEqual(errors, [], f"append_event raised under concurrency: {errors}")
        events = [e for e in self.vault.iter_events() if e.get("type") == "test"]
        self.assertEqual(len(events), count, "lost or corrupted event lines under concurrent append")


if __name__ == "__main__":
    unittest.main()
