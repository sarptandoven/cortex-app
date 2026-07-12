"""Phase-2 Slice 3 — purge/tombstone SYNC PROPAGATION.

Closes the privacy gap where a local forget/purge did NOT reach the hosted copy or other devices.
A deletion must propagate local -> hosted -> other devices, additively and safely.

Under test:
  - Tombstone recording: a LOCAL capture/memory delete (delete_capture / delete_memory /
    purge_source_memories) appends a content-free row to the sync_tombstones monotonic feed;
    idempotent (a re-delete never appends a second row).
  - The deletions feed (capture_tombstone_page / GET /v1/sync/deletions): tombstones with seq >
    cursor, oldest first, ids only, per-user, paginated.
  - Apply (apply_sync_deletions / POST /v1/sync/deletions): deletes the named capture/memory +
    derivatives via the SAME safe primitive; idempotent (deleting an absent id is a no-op success);
    records a local tombstone so the delete propagates ONWARD and can't be resurrected.
  - Anti-resurrection: a pulled capture whose id is tombstoned is SKIPPED by /v1/sync/ingest instead
    of being re-created (status "tombstoned").
  - Convergence: applying a pulled deletion re-emits it on the LOCAL feed (so it re-pushes) but the
    idempotent feed never grows a duplicate and a hosted re-apply is a no-op — the loop terminates.
  - Zero-access parity: an E2EE (ciphertext) capture deletes by id exactly the same way.
  - Per-user isolation: a user can only tombstone/apply-delete its OWN objects.
  - Endpoint parity on BOTH servers (FastAPI + standalone).
"""
from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "del.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "del.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from backend.app.config import Settings
from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


class DeletionFeedStoreTests(unittest.TestCase):
    """Tombstone recording + the deletions feed at the store layer."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "del.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")
        self.user = "del-store-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, content: str, cid: str | None = None) -> dict:
        return self.store.save_capture(
            user_id=self.user, content=content, source="macos", source_url=None, title=None,
            extracted=extract_context(content, "macos"), capture_id_override=cid,
            cite_capture_provenance=True, auto_approve=True,
        )

    def test_delete_capture_records_a_tombstone(self) -> None:
        saved = self._save("Alpha capture to forget", cid="cap_del_1")
        # No tombstones before the delete; the capture is in the create feed.
        self.assertEqual(self.store.capture_tombstone_page(self.user, 0, 100)["items"], [])
        self.assertTrue(self.store.delete_capture(self.user, saved["capture_id"]))
        page = self.store.capture_tombstone_page(self.user, 0, 100)
        self.assertEqual(len(page["items"]), 1)
        self.assertEqual(page["items"][0]["object_type"], "capture")
        self.assertEqual(page["items"][0]["object_id"], "cap_del_1")
        self.assertGreater(page["items"][0]["seq"], 0)  # monotonic seq
        # The feed carries NO content — ids only.
        self.assertEqual(set(page["items"][0].keys()), {"seq", "object_type", "object_id"})

    def test_delete_memory_records_a_tombstone(self) -> None:
        saved = self._save("Bob prefers dark mode", cid="cap_mem_1")
        # Fetch a memory id derived from the capture, directly from the store.
        with_conn_ids = self._memory_ids_for_capture(saved["capture_id"])
        self.assertTrue(with_conn_ids, "capture should have produced at least one memory")
        memory_id = with_conn_ids[0]
        self.assertTrue(self.store.delete_memory(self.user, memory_id))
        tombstones = self.store.capture_tombstone_page(self.user, 0, 100)["items"]
        self.assertIn(("memory", memory_id), [(t["object_type"], t["object_id"]) for t in tombstones])

    def _memory_ids_for_capture(self, capture_id: str) -> list[str]:
        from backend.app.storage import connect
        with connect(self.store.db_path) as conn:
            return [r["id"] for r in conn.execute(
                "SELECT id FROM memories WHERE user_id = ? AND capture_id = ?", (self.user, capture_id)
            ).fetchall()]

    def test_forgotten_memory_not_resurrected_on_capture_reprocess(self) -> None:
        # Regression (deletion review, HIGH): forgetting a memory whose parent capture is retained
        # must STICK — re-processing the capture recomputes the same deterministic memory id, and
        # without the tombstone guard it would silently reappear (and re-sync to every device).
        saved = self._save("My blood type is O negative", cid="cap_resurrect")
        mem_ids = self._memory_ids_for_capture("cap_resurrect")
        self.assertTrue(mem_ids)
        forgotten = mem_ids[0]
        self.assertTrue(self.store.delete_memory(self.user, forgotten))
        self.assertNotIn(forgotten, self._memory_ids_for_capture("cap_resurrect"))
        # Re-process the SAME capture (same id + content) — the derivation must honor the forget.
        self._save("My blood type is O negative", cid="cap_resurrect")
        self.assertNotIn(
            forgotten, self._memory_ids_for_capture("cap_resurrect"),
            "a forgotten memory must not be resurrected when its capture is re-processed",
        )

    def test_mid_batch_deletion_failure_keeps_completed_items_deleted(self) -> None:
        # Regression (deletion review, MEDIUM): a vault IO error mid-batch must not roll back the
        # DB rows of already-processed items whose vault files are already gone (a DB that claims a
        # capture alive while the source-of-truth vault lost it = silent permanent loss). Per-item
        # transactions keep DB + vault consistent for every completed item.
        self._save("first to delete", cid="cap_batch_0")
        self._save("second to delete", cid="cap_batch_1")
        real_delete = self.store.vault.delete_capture

        def boom(capture_id: str):
            if capture_id == "cap_batch_1":
                raise OSError("simulated disk failure")
            return real_delete(capture_id)

        self.store.vault.delete_capture = boom  # type: ignore[assignment]
        try:
            with self.assertRaises(OSError):
                self.store.apply_sync_deletions(self.user, [
                    {"object_type": "capture", "object_id": "cap_batch_0"},
                    {"object_type": "capture", "object_id": "cap_batch_1"},
                ])
        finally:
            self.store.vault.delete_capture = real_delete  # type: ignore[assignment]
        # cap_batch_0 committed fully (DB row gone AND a durable tombstone); cap_batch_1 untouched.
        self.assertEqual(self._memory_ids_for_capture("cap_batch_0"), [])
        tombstoned = {(t["object_type"], t["object_id"]) for t in self.store.capture_tombstone_page(self.user, 0, 100)["items"]}
        self.assertIn(("capture", "cap_batch_0"), tombstoned)
        self.assertNotIn(("capture", "cap_batch_1"), tombstoned)

    def test_recording_is_idempotent_no_duplicate_rows(self) -> None:
        saved = self._save("delete me twice", cid="cap_idem_del_1")
        self.assertTrue(self.store.delete_capture(self.user, saved["capture_id"]))
        first = self.store.capture_tombstone_page(self.user, 0, 100)
        # A second delete of the SAME id (already absent) must be a no-op and must NOT append a row.
        self.assertFalse(self.store.delete_capture(self.user, saved["capture_id"]))
        # Re-apply via the deletion apply path (echo) — still idempotent.
        self.store.apply_sync_deletions(self.user, [{"object_type": "capture", "object_id": "cap_idem_del_1"}])
        second = self.store.capture_tombstone_page(self.user, 0, 100)
        self.assertEqual(len(first["items"]), 1)
        self.assertEqual(len(second["items"]), 1)  # STILL one row — no growth, feed converges
        self.assertEqual(first["items"][0]["seq"], second["items"][0]["seq"])

    def test_purge_source_records_tombstones_for_each_capture(self) -> None:
        self._save("garbage import one", cid="cap_purge_1")
        self._save("garbage import two", cid="cap_purge_2")
        self.store.purge_source_memories(self.user, "macos")
        ids = {t["object_id"] for t in self.store.capture_tombstone_page(self.user, 0, 100)["items"]}
        self.assertIn("cap_purge_1", ids)
        self.assertIn("cap_purge_2", ids)

    def test_feed_pagination_and_cursor_resume(self) -> None:
        for i in range(5):
            saved = self._save(f"note {i}", cid=f"cap_page_{i}")
            self.store.delete_capture(self.user, saved["capture_id"])
        first = self.store.capture_tombstone_page(self.user, 0, 2)
        self.assertEqual(len(first["items"]), 2)
        self.assertTrue(first["has_more"])
        resumed = self.store.capture_tombstone_page(self.user, first["next_seq"], 100)
        self.assertEqual(len(resumed["items"]), 3)
        self.assertFalse(resumed["has_more"])
        # Caught up -> empty, cursor holds.
        caught_up = self.store.capture_tombstone_page(self.user, resumed["next_seq"], 100)
        self.assertEqual(caught_up["items"], [])
        self.assertEqual(caught_up["next_seq"], resumed["next_seq"])

    def test_feed_is_per_user_isolated(self) -> None:
        saved = self._save("mine to forget", cid="cap_iso_1")
        self.store.delete_capture(self.user, saved["capture_id"])
        # A different user sees NONE of this user's tombstones.
        self.assertEqual(self.store.capture_tombstone_page("other-user", 0, 100)["items"], [])

    def test_apply_deletes_via_the_safe_primitive_and_removes_derivatives(self) -> None:
        saved = self._save("Decision: adopt tombstone sync", cid="cap_apply_1")
        mem_ids = self._memory_ids_for_capture(saved["capture_id"])
        self.assertTrue(mem_ids)
        outcome = self.store.apply_sync_deletions(self.user, [{"object_type": "capture", "object_id": "cap_apply_1"}])
        self.assertEqual(outcome["applied"], 1)
        self.assertEqual(outcome["results"][0]["status"], "applied")
        # The capture and its derived memories are gone.
        self.assertEqual(self.store.capture_change_page(self.user, 0, 100)["items"], [])
        self.assertEqual(self._memory_ids_for_capture("cap_apply_1"), [])

    def test_apply_absent_id_is_a_noop_success_and_still_tombstones(self) -> None:
        # Deleting an id this store never held must SUCCEED (idempotent) and still record a tombstone
        # so the delete propagates onward + the anti-resurrection guard holds.
        outcome = self.store.apply_sync_deletions(self.user, [{"object_type": "capture", "object_id": "cap_never_here"}])
        self.assertEqual(outcome["applied"], 1)
        self.assertEqual(outcome["results"][0]["status"], "applied")
        self.assertTrue(self.store.is_tombstoned(self.user, "capture", "cap_never_here"))

    def test_apply_is_per_user_scoped(self) -> None:
        # User A holds a capture; user B applying a delete for that id must NOT touch A's capture.
        a_saved = self.store.save_capture(
            user_id="user-A", content="Alice private capture", source="macos", source_url=None,
            title=None, extracted=extract_context("Alice private capture", "macos"),
            capture_id_override="cap_shared_id", cite_capture_provenance=True, auto_approve=True,
        )
        self.assertEqual(a_saved["capture_id"], "cap_shared_id")
        self.store.apply_sync_deletions("user-B", [{"object_type": "capture", "object_id": "cap_shared_id"}])
        # A's capture is untouched (B could only tombstone under user-B).
        page_a = self.store.capture_change_page("user-A", 0, 100)
        self.assertEqual(len(page_a["items"]), 1)
        self.assertEqual(page_a["items"][0]["content"], "Alice private capture")
        self.assertFalse(self.store.is_tombstoned("user-A", "capture", "cap_shared_id"))
        self.assertTrue(self.store.is_tombstoned("user-B", "capture", "cap_shared_id"))

    def test_convergence_applied_pull_re_emits_once_then_terminates(self) -> None:
        # The echo argument: applying a PULLED deletion writes a LOCAL tombstone, which the push
        # worker would re-emit to hosted. Model the round-trip: the local feed emits it exactly ONCE
        # (no duplicate), and re-applying it (the hosted no-op) never grows the feed -> terminates.
        self.store.apply_sync_deletions(self.user, [{"object_type": "capture", "object_id": "cap_conv_1"}])
        after_apply = self.store.capture_tombstone_page(self.user, 0, 100)
        self.assertEqual(len(after_apply["items"]), 1)  # re-emitted exactly once on the local feed
        # Simulate the echo coming back around (re-apply) N times: feed length is stable.
        for _ in range(3):
            self.store.apply_sync_deletions(self.user, [{"object_type": "capture", "object_id": "cap_conv_1"}])
        self.assertEqual(len(self.store.capture_tombstone_page(self.user, 0, 100)["items"]), 1)

    def test_zero_access_capture_deletes_by_id_the_same_way(self) -> None:
        # A zero-access (E2EE) capture holds only ciphertext server-side, but it deletes by id exactly
        # like a plaintext one — the deletion is content-free, so there is no plaintext concern.
        saved = self.store.save_encrypted_capture(
            user_id=self.user, client_capture_id="cap_enc_del_1",
            encrypted_payload=b"CXEC1-ciphertext-bytes", enc_meta={"alg": "AES-256-GCM"},
            source="macos", captured_at="2026-07-11T00:00:00+00:00", auto_approve=True,
        )
        self.assertEqual(saved["capture_id"], "cap_enc_del_1")
        outcome = self.store.apply_sync_deletions(self.user, [{"object_type": "capture", "object_id": "cap_enc_del_1"}])
        self.assertEqual(outcome["applied"], 1)
        self.assertEqual(self.store.capture_change_page(self.user, 0, 100)["items"], [])
        self.assertTrue(self.store.is_tombstoned(self.user, "capture", "cap_enc_del_1"))


class DeletionFastAPITests(unittest.TestCase):
    """GET/POST /v1/sync/deletions + the anti-resurrection guard on /v1/sync/ingest (hosted FastAPI)."""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient
        from backend.app import main as main_module
        cls.main_module = main_module
        cls.client = TestClient(main_module.app)

    def _headers(self, user: str) -> dict[str, str]:
        return {"Authorization": "Bearer test-token", "X-Cortex-User": user}

    def _ingest(self, user: str, items: list[dict]) -> tuple[int, dict]:
        r = self.client.post("/v1/sync/ingest", json={"items": items}, headers=self._headers(user))
        return r.status_code, (r.json() if r.content else {})

    def _deletions_feed(self, user: str) -> list[dict]:
        r = self.client.get("/v1/sync/deletions", params={"after_seq": 0, "limit": 200}, headers=self._headers(user))
        self.assertEqual(r.status_code, 200)
        return r.json()["items"]

    def _apply_deletions(self, user: str, items: list[dict]) -> tuple[int, dict]:
        r = self.client.post("/v1/sync/deletions", json={"items": items}, headers=self._headers(user))
        return r.status_code, (r.json() if r.content else {})

    def _feed_ids(self, user: str) -> set[str]:
        r = self.client.get("/v1/sync/captures", params={"after_seq": 0, "limit": 200}, headers=self._headers(user))
        self.assertEqual(r.status_code, 200)
        return {item["client_capture_id"] for item in r.json()["items"]}

    def test_ingest_then_apply_deletion_round_trip(self) -> None:
        user = "del-fastapi-roundtrip"
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_ft_1", "content": "hosted capture to forget",
            "source": "macos", "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertIn("cap_ft_1", self._feed_ids(user))
        status, payload = self._apply_deletions(user, [{"object_type": "capture", "object_id": "cap_ft_1"}])
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        self.assertNotIn("cap_ft_1", self._feed_ids(user))
        # The deletion now shows on the outbound feed so it can reach other devices.
        self.assertIn("cap_ft_1", {t["object_id"] for t in self._deletions_feed(user)})

    def test_apply_absent_id_is_noop_success(self) -> None:
        user = "del-fastapi-absent"
        status, payload = self._apply_deletions(user, [{"object_type": "capture", "object_id": "cap_ft_absent"}])
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["results"][0]["status"], "applied")

    def test_apply_rejects_bad_object_type(self) -> None:
        user = "del-fastapi-badtype"
        status, _ = self._apply_deletions(user, [{"object_type": "task", "object_id": "x"}])
        self.assertEqual(status, 422)

    def test_ingest_skips_a_tombstoned_capture_no_resurrection(self) -> None:
        # Anti-resurrection: once forgotten, a re-pushed create for the same id is SKIPPED, not
        # re-created — a "deleted" memory can never come back via an out-of-order create page.
        user = "del-fastapi-resurrect"
        self._ingest(user, [{"client_capture_id": "cap_ft_res", "content": "will be forgotten", "source": "macos", "review_status": "approved"}])
        self._apply_deletions(user, [{"object_type": "capture", "object_id": "cap_ft_res"}])
        self.assertNotIn("cap_ft_res", self._feed_ids(user))
        status, payload = self._ingest(user, [{"client_capture_id": "cap_ft_res", "content": "will be forgotten", "source": "macos", "review_status": "approved"}])
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0]["status"], "tombstoned")
        self.assertNotIn("cap_ft_res", self._feed_ids(user))  # stayed gone

    def test_deletions_feed_pagination(self) -> None:
        user = "del-fastapi-page"
        for i in range(3):
            self._apply_deletions(user, [{"object_type": "capture", "object_id": f"cap_ft_page_{i}"}])
        r = self.client.get("/v1/sync/deletions", params={"after_seq": 0, "limit": 2}, headers=self._headers(user))
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertTrue(body["has_more"])


class DeletionStandaloneParityTests(unittest.TestCase):
    """GET/POST /v1/sync/deletions on the shipping standalone server (the LOCAL endpoint the desktop
    push/pull workers call), backed by a REAL store — same feed + apply + anti-resurrection contract."""

    def setUp(self) -> None:
        from backend.app import standalone_server
        self.standalone_server = standalone_server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        init_db(root / "index.sqlite")
        self.real_store = CortexStore(root / "index.sqlite", root / "vault")
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        self.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.store = self.real_store
        standalone_server.settings = Settings(
            vault_path=root / "vault", db_path=root / "index.sqlite",
            api_key="test-token", public_base_url="http://127.0.0.1:8766",
        )
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
        self.server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"
        self.user = self.standalone_server.settings.default_user_id

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.standalone_server.store = self.original_store
        self.standalone_server.settings = self.original_settings
        self.standalone_server.REQUEST_GUARDS = self.original_guards
        self.tmp.cleanup()

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        req = request.Request(
            self.base_url + path, data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"}, method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _get(self, path: str) -> tuple[int, dict]:
        req = request.Request(self.base_url + path, headers={"Authorization": "Bearer test-token"})
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read() or b"{}")
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _capture_ids(self) -> set[str]:
        status, body = self._get("/v1/sync/captures?after_seq=0&limit=200")
        self.assertEqual(status, 200)
        return {item["client_capture_id"] for item in body["items"]}

    def test_standalone_apply_deletion_removes_capture(self) -> None:
        status, _ = self._post("/v1/sync/ingest", {"items": [{
            "client_capture_id": "cap_sa_del_1", "content": "local capture to forget",
            "source": "macos", "review_status": "approved",
        }]})
        self.assertEqual(status, 200)
        self.assertIn("cap_sa_del_1", self._capture_ids())
        status, payload = self._post("/v1/sync/deletions", {"items": [{"object_type": "capture", "object_id": "cap_sa_del_1"}]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        self.assertNotIn("cap_sa_del_1", self._capture_ids())

    def test_standalone_deletions_feed_carries_local_forget(self) -> None:
        # A LOCAL forget through the store surfaces on the standalone deletions feed for the push
        # worker to send up to hosted.
        saved = self.real_store.save_capture(
            user_id=self.user, content="local forget me", source="macos", source_url=None, title=None,
            extracted=extract_context("local forget me", "macos"), capture_id_override="cap_sa_local_1",
            cite_capture_provenance=True, auto_approve=True,
        )
        self.real_store.delete_capture(self.user, saved["capture_id"])
        status, body = self._get("/v1/sync/deletions?after_seq=0&limit=200")
        self.assertEqual(status, 200)
        self.assertIn("cap_sa_local_1", {t["object_id"] for t in body["items"]})

    def test_standalone_ingest_skips_tombstoned_capture(self) -> None:
        self._post("/v1/sync/deletions", {"items": [{"object_type": "capture", "object_id": "cap_sa_res_1"}]})
        status, payload = self._post("/v1/sync/ingest", {"items": [{
            "client_capture_id": "cap_sa_res_1", "content": "should not resurrect", "source": "macos",
        }]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["results"][0]["status"], "tombstoned")
        self.assertNotIn("cap_sa_res_1", self._capture_ids())

    def test_standalone_apply_rejects_bad_object_type(self) -> None:
        status, _ = self._post("/v1/sync/deletions", {"items": [{"object_type": "widget", "object_id": "x"}]})
        self.assertEqual(status, 422)

    def test_standalone_apply_absent_id_is_noop_success(self) -> None:
        status, payload = self._post("/v1/sync/deletions", {"items": [{"object_type": "capture", "object_id": "cap_sa_absent"}]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)


if __name__ == "__main__":
    unittest.main()
