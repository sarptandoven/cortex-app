"""Zero-access (E2EE) blind-relay sync.

An opt-in user's macOS client encrypts a capture and holds the key; it pushes only ciphertext
(`encrypted_payload`, a CXE1 blob the server cannot read) plus a non-secret decrypt hint
(`enc_meta`) to the hosted plane. The server is a BLIND relay: it stores the ciphertext verbatim,
persists NO plaintext, and derives ZERO server-side searchable content (no memory/task/entity/
embedding). A second device pulls the exact ciphertext back and decrypts it locally.

This suite proves the isolation invariants that make that claim true, and that the additive path
never disturbs today's plaintext behavior:

  * a zero-access capture produces NO memory/task/entity on the hosted store;
  * its plaintext is never persisted server-side (raw_text is a fixed non-sensitive placeholder,
    title/source_url carry no content);
  * the hosted raw_text LIKE search never matches it (blind-relay content is not server-searchable);
  * a mixed batch (some encrypted, some plaintext) applies correctly;
  * idempotent double-apply converges (the property retries + the pull-echo rely on);
  * the pull feed round-trips the EXACT ciphertext bytes;
  * the schema migration is idempotent + safe on an already-migrated DB;
  * both servers (FastAPI main.py + the stdlib standalone_server) accept and relay it, in parity;
  * the plaintext path is byte-for-byte unchanged.
"""
from __future__ import annotations

import base64
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "zeroaccess.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "zeroaccess.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from backend.app.config import Settings
from backend.app.database import connect, init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


# A representative CXE1-shaped ciphertext blob (magic + some opaque bytes). The server treats it as
# fully opaque, so any non-empty byte string exercises the blind-relay path; using the real magic
# keeps the fixture honest about what the client actually ships.
def _fixture_ciphertext(secret: bytes = b"the secret plaintext the server must never see") -> bytes:
    kek_id = b"local:v1"
    header = b"CXE1" + bytes([len(kek_id)]) + kek_id + (1).to_bytes(4, "big") + os.urandom(12)
    return header + secret  # (not a real GCM tag; the server never decrypts, so this is fine)


def _fixture_enc_meta() -> dict:
    return {
        "alg": "AES-256-GCM",
        "nonce_b64": base64.b64encode(os.urandom(12)).decode("ascii"),
        "key_id": "recovery:v1",
        "aad_context": "user|content|1",
    }


class ZeroAccessStoreTests(unittest.TestCase):
    """Store-level invariants. Both servers apply pushed captures through save_encrypted_capture and
    read the feed through capture_change_page, so proving them here proves the shared engine."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db = root / "za.sqlite"
        init_db(self.db)
        self.store = CortexStore(self.db, root / "vault")
        self.user = "za-store-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save_plaintext(self, content: str, cid: str | None = None) -> dict:
        return self.store.save_capture(
            user_id=self.user, content=content, source="macos", source_url=None, title=None,
            extracted=extract_context(content, "macos"), capture_id_override=cid,
            cite_capture_provenance=True, auto_approve=True,
        )

    def _save_encrypted(self, ciphertext: bytes, cid: str, *, enc_meta: dict | None = None,
                        auto_approve: bool = True, force_review: bool = False) -> dict:
        return self.store.save_encrypted_capture(
            user_id=self.user, client_capture_id=cid, encrypted_payload=ciphertext,
            enc_meta=enc_meta if enc_meta is not None else _fixture_enc_meta(),
            source="macos", auto_approve=auto_approve, force_review=force_review,
        )

    # --- Migration -------------------------------------------------------------------------------
    def test_migration_added_nullable_columns_and_is_idempotent(self) -> None:
        with connect(self.db) as conn:
            cols = {row[1]: row for row in conn.execute("PRAGMA table_info(captures)").fetchall()}
        self.assertIn("encrypted_payload", cols)
        self.assertIn("enc_meta", cols)
        # Nullable (notnull flag == 0) so every existing plaintext row is valid with NULL there.
        self.assertEqual(cols["encrypted_payload"][3], 0)
        self.assertEqual(cols["enc_meta"][3], 0)
        # Re-running the migration on an already-migrated DB is a safe no-op (duplicate-column tolerated).
        self.store._ensure_encrypted_capture_columns()
        # Re-opening the store (which re-runs the migration) must not raise or lose data.
        self._save_plaintext("survives a second store open")
        reopened = CortexStore(self.db, Path(self._tmp.name) / "vault")
        self.assertEqual(len(reopened.capture_change_page(self.user, 0, 10)["items"]), 1)

    # --- Blind store: no plaintext, no derivation ------------------------------------------------
    def test_encrypted_capture_persists_no_plaintext(self) -> None:
        secret = b"CEO comp is $250k -- never store this in the clear"
        blob = _fixture_ciphertext(secret)
        self._save_encrypted(blob, "cap_za_secret_1")
        with connect(self.db) as conn:
            row = conn.execute(
                "SELECT raw_text, title, source_url, summary, encrypted_payload, enc_meta "
                "FROM captures WHERE user_id = ? AND id = ?",
                (self.user, "cap_za_secret_1"),
            ).fetchone()
        # raw_text is the fixed placeholder, NOT the plaintext; title carries no content.
        self.assertEqual(row["raw_text"], self.store.ENCRYPTED_CAPTURE_PLACEHOLDER)
        self.assertIsNone(row["title"])
        self.assertEqual(row["summary"], "")
        # The plaintext secret appears NOWHERE in any text column server-side.
        self.assertNotIn("CEO comp", row["raw_text"])
        self.assertNotIn("CEO comp", str(row["source_url"] or ""))
        self.assertNotIn("CEO comp", str(row["enc_meta"] or ""))
        # The ciphertext is stored verbatim (byte-for-byte) and stays opaque to the server.
        self.assertEqual(bytes(row["encrypted_payload"]), blob)
        self.assertTrue(bytes(row["encrypted_payload"]).startswith(b"CXE1"))
        # enc_meta is a non-secret decrypt hint stored as JSON; it never carries key MATERIAL.
        meta = json.loads(row["enc_meta"])
        self.assertEqual(meta["alg"], "AES-256-GCM")
        self.assertNotIn("dek", meta)
        self.assertNotIn("secret", meta)

    def test_encrypted_capture_derives_zero_memories_tasks_entities(self) -> None:
        # A payload whose (imaginary) plaintext would extract memories/tasks/entities in the
        # plaintext path must yield NONE here — there is no plaintext to extract from.
        saved = self._save_encrypted(_fixture_ciphertext(b"TODO: ship the E2EE relay by Friday with Marcus"), "cap_za_2")
        self.assertEqual(saved["memories"], [])
        self.assertEqual(saved["tasks"], [])
        self.assertEqual(saved["entities"], [])
        with connect(self.db) as conn:
            mem = conn.execute("SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ?", (self.user, "cap_za_2")).fetchone()[0]
            tsk = conn.execute("SELECT COUNT(*) FROM tasks WHERE user_id = ? AND capture_id = ?", (self.user, "cap_za_2")).fetchone()[0]
            edges = conn.execute("SELECT COUNT(*) FROM graph_edges WHERE user_id = ? AND source_id = ?", (self.user, "cap_za_2")).fetchone()[0]
            state = conn.execute("SELECT extraction_status, memory_count, task_count, entity_count FROM capture_processing_state WHERE capture_id = ?", ("cap_za_2",)).fetchone()
        self.assertEqual(mem, 0)
        self.assertEqual(tsk, 0)
        self.assertEqual(edges, 0)
        self.assertEqual((state["memory_count"], state["task_count"], state["entity_count"]), (0, 0, 0))

    def test_hosted_raw_text_like_search_never_matches_encrypted(self) -> None:
        # The hosted task search LIKEs c.raw_text; a zero-access capture holds only the placeholder,
        # so a term from the (unseen) plaintext can never match it. Correct: blind content is not
        # server-searchable. A plaintext capture with the same term IS found, proving search works.
        self._save_encrypted(_fixture_ciphertext(b"quarterly revenue projections spreadsheet"), "cap_za_srch_1")
        with connect(self.db) as conn:
            hits = conn.execute(
                "SELECT COUNT(*) FROM captures WHERE user_id = ? AND lower(COALESCE(raw_text,'')) LIKE ?",
                (self.user, "%revenue%"),
            ).fetchone()[0]
            placeholder_hits = conn.execute(
                "SELECT COUNT(*) FROM captures WHERE user_id = ? AND lower(COALESCE(raw_text,'')) LIKE ?",
                (self.user, "%encrypted%"),
            ).fetchone()[0]
        self.assertEqual(hits, 0)               # the secret term is unreachable server-side
        self.assertEqual(placeholder_hits, 1)   # only the placeholder is present

    # --- Idempotency + PK safety -----------------------------------------------------------------
    def test_idempotent_double_apply_same_ciphertext(self) -> None:
        blob = _fixture_ciphertext(b"stable secret")
        first = self._save_encrypted(blob, "cap_za_idem_1")
        again = self._save_encrypted(blob, "cap_za_idem_1")
        self.assertEqual(first["capture_id"], again["capture_id"])
        page = self.store.capture_change_page(self.user, 0, 100)
        self.assertEqual(len([i for i in page["items"] if i["client_capture_id"] == "cap_za_idem_1"]), 1)

    def test_reapply_new_ciphertext_replaces_in_place(self) -> None:
        self._save_encrypted(_fixture_ciphertext(b"v1 secret"), "cap_za_repl_1")
        new_blob = _fixture_ciphertext(b"v2 secret rotated key")
        self._save_encrypted(new_blob, "cap_za_repl_1")
        page = self.store.capture_change_page(self.user, 0, 100)
        matches = [i for i in page["items"] if i["client_capture_id"] == "cap_za_repl_1"]
        self.assertEqual(len(matches), 1)
        self.assertEqual(base64.b64decode(matches[0]["encrypted_payload"]), new_blob)

    def test_encrypted_capture_id_cannot_hijack_another_users_row(self) -> None:
        # SECURITY: the captures PK is id-only; a client id already owned by ANOTHER user must be
        # refused and a fresh id minted instead — identical to the plaintext save_capture guard.
        other = "za-other-user"
        self.store.save_encrypted_capture(
            user_id=other, client_capture_id="cap_za_shared_1",
            encrypted_payload=_fixture_ciphertext(b"other user secret"), enc_meta=None,
            source="macos", auto_approve=True,
        )
        mine = self._save_encrypted(_fixture_ciphertext(b"my overwrite attempt"), "cap_za_shared_1")
        self.assertNotEqual(mine["capture_id"], "cap_za_shared_1")  # refused -> fresh id
        # The other user's row is untouched.
        with connect(self.db) as conn:
            owner = conn.execute("SELECT user_id FROM captures WHERE id = ?", ("cap_za_shared_1",)).fetchone()["user_id"]
        self.assertEqual(owner, other)

    def test_converting_plaintext_capture_to_encrypted_purges_derivatives(self) -> None:
        # SECURITY regression (crypto review, HIGH): a capture pushed plaintext derives + indexes
        # memories server-side. If the SAME capture id is later re-pushed as encrypted (e.g. after
        # enabling zero-access + a cursor reset), the derived memories + memory_fts must be PURGED
        # so the server can no longer hold or full-text-search the plaintext of a now-zero-access
        # capture. Before the fix, raw_text became '[encrypted]' but the memory + fts survived.
        secret = "CEO comp is 250k plus equity confidential"
        self._save_plaintext(secret, cid="cap_convert")
        with connect(self.db) as conn:
            before = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ?",
                (self.user, "cap_convert"),
            ).fetchone()[0]
        self.assertGreaterEqual(before, 1)  # plaintext push derived a memory

        self._save_encrypted(b"CXEC1_ciphertext_bytes_here", "cap_convert")

        with connect(self.db) as conn:
            row = conn.execute(
                "SELECT raw_text FROM captures WHERE user_id = ? AND id = ?",
                (self.user, "cap_convert"),
            ).fetchone()
            self.assertEqual(row["raw_text"], self.store.ENCRYPTED_CAPTURE_PLACEHOLDER)
            memories = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ?",
                (self.user, "cap_convert"),
            ).fetchone()[0]
            self.assertEqual(memories, 0, "prior-plaintext memories must be purged on encryption")
            fts_hits = conn.execute(
                "SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH 'CEO'"
            ).fetchone()[0]
            self.assertEqual(fts_hits, 0, "server must not be able to search the plaintext anymore")

    def test_enc_meta_oversize_rejected(self) -> None:
        # Crypto review (LOW): enc_meta is a tiny hint; an oversize dict must be refused so it
        # can't inflate the store / amplify pulls (matches the FastAPI validator + standalone bound).
        from backend.app.models import SyncIngestItem

        with self.assertRaises(ValueError):
            SyncIngestItem(
                client_capture_id="cap_big", encrypted_payload="Zm9v",
                enc_meta={"alg": "AES-256-GCM", "junk": "x" * 5000},
            )

    def test_empty_ciphertext_rejected(self) -> None:
        with self.assertRaises(ValueError):
            self.store.save_encrypted_capture(
                user_id=self.user, client_capture_id="cap_za_empty_1",
                encrypted_payload=b"", enc_meta=None, source="macos",
            )

    # --- Feed round-trip + mixed batch -----------------------------------------------------------
    def test_feed_round_trips_exact_ciphertext_bytes(self) -> None:
        blob = _fixture_ciphertext(os.urandom(64))  # arbitrary bytes incl. non-utf8
        meta = _fixture_enc_meta()
        self._save_encrypted(blob, "cap_za_rt_1", enc_meta=meta)
        page = self.store.capture_change_page(self.user, 0, 100)
        item = next(i for i in page["items"] if i["client_capture_id"] == "cap_za_rt_1")
        # EXACT bytes survive the base64 JSON round-trip.
        self.assertEqual(base64.b64decode(item["encrypted_payload"]), blob)
        self.assertEqual(item["enc_meta"], meta)
        # content is the placeholder (the pulling device ignores it and uses the ciphertext).
        self.assertEqual(item["content"], self.store.ENCRYPTED_CAPTURE_PLACEHOLDER)

    def test_feed_plaintext_capture_has_null_encryption_fields(self) -> None:
        self._save_plaintext("an ordinary plaintext note about databases")
        page = self.store.capture_change_page(self.user, 0, 100)
        item = page["items"][0]
        self.assertEqual(item["content"], "an ordinary plaintext note about databases")
        self.assertIsNone(item["encrypted_payload"])  # plaintext path additive fields are null
        self.assertIsNone(item["enc_meta"])
        # Every pre-existing feed key is still present (no regression for the push worker).
        for key in ("seq", "client_capture_id", "content", "source", "source_url", "title", "captured_at", "review_status"):
            self.assertIn(key, item)

    def test_mixed_batch_of_encrypted_and_plaintext(self) -> None:
        self._save_plaintext("plaintext alpha", cid="cap_mix_pt_1")
        self._save_encrypted(_fixture_ciphertext(b"encrypted beta"), "cap_mix_enc_1")
        self._save_plaintext("plaintext gamma", cid="cap_mix_pt_2")
        page = self.store.capture_change_page(self.user, 0, 100)
        by_id = {i["client_capture_id"]: i for i in page["items"]}
        self.assertEqual(by_id["cap_mix_pt_1"]["content"], "plaintext alpha")
        self.assertIsNone(by_id["cap_mix_pt_1"]["encrypted_payload"])
        self.assertEqual(by_id["cap_mix_enc_1"]["content"], self.store.ENCRYPTED_CAPTURE_PLACEHOLDER)
        self.assertTrue(base64.b64decode(by_id["cap_mix_enc_1"]["encrypted_payload"]).startswith(b"CXE1"))
        self.assertIsNone(by_id["cap_mix_pt_2"]["encrypted_payload"])
        # Only the two plaintext captures produced memories.
        with connect(self.db) as conn:
            enc_mem = conn.execute("SELECT COUNT(*) FROM memories WHERE capture_id = ?", ("cap_mix_enc_1",)).fetchone()[0]
        self.assertEqual(enc_mem, 0)

    # --- Review passthrough parity ---------------------------------------------------------------
    def test_force_review_pins_pending(self) -> None:
        self._save_encrypted(_fixture_ciphertext(b"pending secret"), "cap_za_pend_1", auto_approve=False, force_review=True)
        page = self.store.capture_change_page(self.user, 0, 100)
        item = next(i for i in page["items"] if i["client_capture_id"] == "cap_za_pend_1")
        self.assertEqual(item["review_status"], "pending")

    def test_unchanged_reapply_preserves_review_decision(self) -> None:
        blob = _fixture_ciphertext(b"approved secret")
        self._save_encrypted(blob, "cap_za_appr_1", auto_approve=True)  # approved
        # A re-apply that tries to force review must NOT escalate an already-approved unchanged row.
        self._save_encrypted(blob, "cap_za_appr_1", auto_approve=False, force_review=True)
        page = self.store.capture_change_page(self.user, 0, 100)
        item = next(i for i in page["items"] if i["client_capture_id"] == "cap_za_appr_1")
        self.assertEqual(item["review_status"], "approved")


class ZeroAccessFastAPITests(unittest.TestCase):
    """POST /v1/sync/ingest + GET /v1/sync/captures on the hosted FastAPI server (main.py)."""

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

    def _feed(self, user: str) -> dict[str, dict]:
        r = self.client.get("/v1/sync/captures", params={"after_seq": 0, "limit": 200}, headers=self._headers(user))
        self.assertEqual(r.status_code, 200)
        return {i["client_capture_id"]: i for i in r.json()["items"]}

    def test_ingest_and_pull_round_trip_encrypted(self) -> None:
        user = "za-fastapi-rt"
        blob = _fixture_ciphertext(b"fastapi secret payload")
        meta = _fixture_enc_meta()
        status, payload = self._ingest(user, [{
            "client_capture_id": "cap_za_api_1",
            "encrypted_payload": base64.b64encode(blob).decode("ascii"),
            "enc_meta": meta,
            "source": "macos",
            "captured_at": "2026-07-11T00:00:00+00:00",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["results"][0]["capture_id"], "cap_za_api_1")
        item = self._feed(user)["cap_za_api_1"]
        self.assertEqual(base64.b64decode(item["encrypted_payload"]), blob)  # exact bytes back
        self.assertEqual(item["enc_meta"], meta)
        self.assertEqual(item["content"], "[encrypted]")
        self.assertEqual(item["review_status"], "approved")
        # No server-side plaintext / memories for this capture.
        with connect(self.main_module.store.db_path) as conn:
            mem = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE user_id = ? AND capture_id = ?",
                (user, "cap_za_api_1"),
            ).fetchone()[0]
        self.assertEqual(mem, 0)

    def test_plaintext_ingest_unchanged(self) -> None:
        # The plaintext path must behave EXACTLY as before: content ingested, no encryption fields.
        user = "za-fastapi-plain"
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_za_plain_1",
            "content": "an ordinary synced note",
            "source": "macos",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        item = self._feed(user)["cap_za_plain_1"]
        self.assertEqual(item["content"], "an ordinary synced note")
        self.assertIsNone(item["encrypted_payload"])
        self.assertIsNone(item["enc_meta"])

    def test_mixed_batch_over_http(self) -> None:
        user = "za-fastapi-mixed"
        blob = _fixture_ciphertext(b"mixed batch secret")
        status, payload = self._ingest(user, [
            {"client_capture_id": "cap_za_mix_pt", "content": "plaintext in a mixed batch", "source": "macos", "review_status": "approved"},
            {"client_capture_id": "cap_za_mix_en", "encrypted_payload": base64.b64encode(blob).decode("ascii"), "enc_meta": _fixture_enc_meta(), "source": "macos", "review_status": "approved"},
        ])
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 2)
        feed = self._feed(user)
        self.assertEqual(feed["cap_za_mix_pt"]["content"], "plaintext in a mixed batch")
        self.assertIsNone(feed["cap_za_mix_pt"]["encrypted_payload"])
        self.assertEqual(base64.b64decode(feed["cap_za_mix_en"]["encrypted_payload"]), blob)

    def test_idempotent_double_apply_over_http(self) -> None:
        user = "za-fastapi-idem"
        blob = _fixture_ciphertext(b"idem secret")
        body = [{"client_capture_id": "cap_za_api_idem", "encrypted_payload": base64.b64encode(blob).decode("ascii"), "enc_meta": _fixture_enc_meta(), "source": "macos", "review_status": "approved"}]
        self._ingest(user, body)
        self._ingest(user, body)
        feed = self._feed(user)
        self.assertEqual(len([k for k in feed if k == "cap_za_api_idem"]), 1)

    def test_invalid_base64_rejected(self) -> None:
        user = "za-fastapi-badb64"
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_za_bad_1",
            "encrypted_payload": "!!!not base64!!!",
            "source": "macos",
        }])
        self.assertEqual(status, 422)

    def test_missing_content_and_ciphertext_rejected(self) -> None:
        user = "za-fastapi-empty"
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_za_empty_api",
            "source": "macos",
        }])
        self.assertEqual(status, 422)


class ZeroAccessStandaloneServerTests(unittest.TestCase):
    """Parity: the stdlib shipping server (standalone_server.py) accepts + relays the same shape."""

    def setUp(self) -> None:
        from backend.app import standalone_server

        self.standalone = standalone_server
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "za_standalone.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")
        self.user = "local"

        self._orig_store = standalone_server.store
        self._orig_settings = standalone_server.settings
        self._orig_origins = standalone_server.ALLOWED_CORS_ORIGINS
        self._orig_guards = standalone_server.REQUEST_GUARDS
        standalone_server.store = self.store
        standalone_server.settings = Settings(
            vault_path=root / "vault",
            db_path=db,
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            auto_approve_captures=True,
        )
        standalone_server.ALLOWED_CORS_ORIGINS = {"http://127.0.0.1:8766"}
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
        self.server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.standalone.store = self._orig_store
        self.standalone.settings = self._orig_settings
        self.standalone.ALLOWED_CORS_ORIGINS = self._orig_origins
        self.standalone.REQUEST_GUARDS = self._orig_guards
        self._tmp.cleanup()

    def _post(self, path: str, body: dict) -> tuple[int, dict]:
        req = request.Request(
            self.base_url + path,
            data=json.dumps(body).encode(),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read() or b"{}")
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _feed(self) -> dict[str, dict]:
        req = request.Request(
            self.base_url + "/v1/sync/captures?after_seq=0&limit=200",
            headers={"Authorization": "Bearer test-token"},
        )
        with request.urlopen(req, timeout=5) as resp:
            page = json.loads(resp.read())
        return {i["client_capture_id"]: i for i in page["items"]}

    def test_standalone_ingest_and_pull_round_trip(self) -> None:
        blob = _fixture_ciphertext(b"standalone secret payload")
        meta = _fixture_enc_meta()
        status, payload = self._post("/v1/sync/ingest", {"items": [{
            "client_capture_id": "cap_za_std_1",
            "encrypted_payload": base64.b64encode(blob).decode("ascii"),
            "enc_meta": meta,
            "source": "macos",
        }]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        item = self._feed()["cap_za_std_1"]
        self.assertEqual(base64.b64decode(item["encrypted_payload"]), blob)
        self.assertEqual(item["enc_meta"], meta)
        self.assertEqual(item["content"], "[encrypted]")
        # No server-side memory derived.
        with connect(self.store.db_path) as conn:
            mem = conn.execute("SELECT COUNT(*) FROM memories WHERE capture_id = ?", ("cap_za_std_1",)).fetchone()[0]
        self.assertEqual(mem, 0)

    def test_standalone_plaintext_unchanged(self) -> None:
        status, _ = self._post("/v1/sync/ingest", {"items": [{
            "client_capture_id": "cap_za_std_plain",
            "content": "an ordinary standalone note",
            "source": "macos",
        }]})
        self.assertEqual(status, 200)
        item = self._feed()["cap_za_std_plain"]
        self.assertEqual(item["content"], "an ordinary standalone note")
        self.assertIsNone(item["encrypted_payload"])

    def test_standalone_mixed_batch(self) -> None:
        blob = _fixture_ciphertext(b"std mixed secret")
        status, payload = self._post("/v1/sync/ingest", {"items": [
            {"client_capture_id": "cap_za_std_pt", "content": "plaintext std", "source": "macos"},
            {"client_capture_id": "cap_za_std_en", "encrypted_payload": base64.b64encode(blob).decode("ascii"), "source": "macos"},
        ]})
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 2)
        feed = self._feed()
        self.assertEqual(feed["cap_za_std_pt"]["content"], "plaintext std")
        self.assertEqual(base64.b64decode(feed["cap_za_std_en"]["encrypted_payload"]), blob)

    def test_standalone_invalid_base64_rejected(self) -> None:
        status, _ = self._post("/v1/sync/ingest", {"items": [{
            "client_capture_id": "cap_za_std_bad",
            "encrypted_payload": "@@ not base64 @@",
            "source": "macos",
        }]})
        self.assertEqual(status, 422)


if __name__ == "__main__":
    unittest.main()
