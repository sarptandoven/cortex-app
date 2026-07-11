"""Phase-2 hosted->local PULL sync (the second-device half of cross-device sync).

Under test:
  - the sync feed (capture_change_page / GET /v1/sync/captures) now carries each capture's
    review_status, so a second device can mirror a review decision the user already made;
  - /v1/sync/ingest's bounded `review_status` trust passthrough: "approved" -> auto-approve
    (a decision another device already granted), "pending" pins the origin's not-yet-approved
    state, absent -> today's normal review flow, anything else -> validation error;
  - SECURITY: the passthrough must never let a connector bypass a per-source review policy on
    FIRST ingest — when a connected source account for the item's source demands review, the
    LOCAL policy wins and the capture stays pending despite an "approved" claim;
  - idempotent double-apply (the convergence property the pull worker's echo relies on), and
    that a re-apply of unchanged content can never escalate an existing local review decision;
  - standalone-server parity for POST /v1/sync/ingest (the endpoint the desktop pull worker
    actually applies hosted pages to).
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
os.environ.setdefault("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "pull.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "pull.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from backend.app.config import Settings
from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


class PullSyncStoreTests(unittest.TestCase):
    """capture_change_page review_status passthrough + the source_policy_requires_review bound."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        db = root / "pull.sqlite"
        init_db(db)
        self.store = CortexStore(db, root / "vault")
        self.user = "pull-store-user"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save(self, content: str, *, auto_approve: bool) -> dict:
        return self.store.save_capture(
            user_id=self.user, content=content, source="macos", source_url=None, title=None,
            extracted=extract_context(content, "macos"), cite_capture_provenance=True,
            auto_approve=auto_approve,
        )

    def test_feed_includes_review_status(self) -> None:
        # An approved and a pending capture must surface their ACTUAL review decision in the feed
        # (this is what lets a second device mirror an approval instead of re-queuing it).
        self._save("Approved note about databases", auto_approve=True)
        self._save("Pending note about deploys", auto_approve=False)  # default review setting holds it
        page = self.store.capture_change_page(self.user, 0, 100)
        statuses = {item["content"]: item["review_status"] for item in page["items"]}
        self.assertEqual(statuses["Approved note about databases"], "approved")
        self.assertEqual(statuses["Pending note about deploys"], "pending")
        # Additive change: every pre-existing key is still present for the push worker.
        for key in ("seq", "client_capture_id", "content", "source", "source_url", "title", "captured_at"):
            self.assertIn(key, page["items"][0])

    def test_source_policy_requires_review_bound(self) -> None:
        # No connected account for the source -> nothing to gate on.
        self.assertFalse(self.store.source_policy_requires_review(self.user, "macos"))
        # A connected account whose policy demands review gates the passthrough.
        gated = self.store.upsert_source_account(
            self.user, source="obsidian",
            policy={"review_required": True, "allow_ai_context": True},
        )
        self.assertTrue(self.store.source_policy_requires_review(self.user, "obsidian"))
        # A TRUSTED account (review_required explicitly False) does not gate.
        self.store.upsert_source_account(
            self.user, source="readwise", account_label="Trusted Readwise",
            policy={"review_required": False, "allow_ai_context": True},
        )
        self.assertFalse(self.store.source_policy_requires_review(self.user, "readwise"))
        # A disconnected account no longer gates (no active connector to protect).
        self.store.disconnect_source_account(self.user, gated["id"])
        self.assertFalse(self.store.source_policy_requires_review(self.user, "obsidian"))
        # Another user's policy never leaks into this user's gate.
        self.store.upsert_source_account(
            "someone-else", source="linear",
            policy={"review_required": True, "allow_ai_context": True},
        )
        self.assertFalse(self.store.source_policy_requires_review(self.user, "linear"))


class PullSyncIngestFastAPITests(unittest.TestCase):
    """POST /v1/sync/ingest trust passthrough on the hosted (FastAPI) server."""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        from backend.app import main as main_module

        cls.main_module = main_module
        cls.client = TestClient(main_module.app)

    def _headers(self, user: str) -> dict[str, str]:
        return {"Authorization": "Bearer test-token", "X-Cortex-User": user}

    def _ingest(self, user: str, items: list[dict]) -> tuple[int, dict]:
        response = self.client.post("/v1/sync/ingest", json={"items": items}, headers=self._headers(user))
        return response.status_code, (response.json() if response.content else {})

    def _feed_by_id(self, user: str) -> dict[str, dict]:
        response = self.client.get(
            "/v1/sync/captures", params={"after_seq": 0, "limit": 200}, headers=self._headers(user)
        )
        self.assertEqual(response.status_code, 200)
        return {item["client_capture_id"]: item for item in response.json()["items"]}

    def test_ingest_honors_approved_passthrough(self) -> None:
        # Default review setting would hold this capture pending; the passthrough mirrors the
        # approval the user already granted on the origin device, so it lands approved here.
        user = "pull-fastapi-approved"
        status, payload = self._ingest(user, [{
            "client_capture_id": "cap_pull_appr_1",
            "content": "Decision made on Mac A: ship the pull sync",
            "source": "macos",
            "captured_at": "2026-07-10T00:00:00+00:00",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["results"][0]["capture_id"], "cap_pull_appr_1")
        self.assertEqual(self._feed_by_id(user)["cap_pull_appr_1"]["review_status"], "approved")

    def test_ingest_absent_review_status_keeps_normal_review_flow(self) -> None:
        user = "pull-fastapi-absent"
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_pull_absent_1",
            "content": "Note without any review passthrough",
            "source": "macos",
        }])
        self.assertEqual(status, 200)
        # Default user settings review new captures -> pending, exactly as before this feature.
        self.assertEqual(self._feed_by_id(user)["cap_pull_absent_1"]["review_status"], "pending")

    def test_ingest_pending_passthrough_pins_review(self) -> None:
        # Even when THIS store would auto-approve (review toggle off), "pending" pins the origin
        # device's not-yet-approved state so the capture still shows up in Review here.
        user = "pull-fastapi-pending"
        self.main_module.store.update_settings(user, {"review_new_captures": False})
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_pull_pend_1",
            "content": "Origin device has not approved this yet",
            "source": "macos",
            "review_status": "pending",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(self._feed_by_id(user)["cap_pull_pend_1"]["review_status"], "pending")

    def test_ingest_rejects_out_of_bounds_review_status(self) -> None:
        # The passthrough is bounded to the two mirrorable states; nothing else may ride it.
        user = "pull-fastapi-invalid"
        for bad in ("archived", "auto", "APPROVED!"):
            status, _ = self._ingest(user, [{
                "client_capture_id": "cap_pull_bad_1",
                "content": "attempted status smuggle",
                "review_status": bad,
            }])
            self.assertEqual(status, 422)

    def test_connector_policy_gated_source_stays_pending_despite_passthrough(self) -> None:
        # SECURITY: a connected source account whose policy demands review wins over the
        # passthrough — an "approved" claim must not bypass per-source review on first ingest.
        user = "pull-fastapi-gated"
        self.main_module.store.upsert_source_account(
            user, source="obsidian",
            policy={"review_required": True, "allow_ai_context": True},
        )
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_pull_gated_1",
            "content": "Connector note claiming it was already approved elsewhere",
            "source": "obsidian",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(self._feed_by_id(user)["cap_pull_gated_1"]["review_status"], "pending")

    def test_trusted_source_policy_lets_passthrough_apply(self) -> None:
        # The converse bound: a source the user explicitly trusts (review_required False) does
        # not gate, so the mirrored approval applies.
        user = "pull-fastapi-trusted"
        self.main_module.store.upsert_source_account(
            user, source="readwise",
            policy={"review_required": False, "allow_ai_context": True},
        )
        status, _ = self._ingest(user, [{
            "client_capture_id": "cap_pull_trust_1",
            "content": "Highlight from a trusted source approved on Mac A",
            "source": "readwise",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(self._feed_by_id(user)["cap_pull_trust_1"]["review_status"], "approved")

    def test_ingest_idempotent_double_apply(self) -> None:
        # The convergence property the pull worker relies on: re-applying the same batch (echo,
        # retry after a lost ack) upserts by the stable capture id — no duplicates, stable status.
        user = "pull-fastapi-idem"
        item = {
            "client_capture_id": "cap_pull_idem_1",
            "content": "Same capture applied twice",
            "source": "macos",
            "captured_at": "2026-07-10T01:00:00+00:00",
            "review_status": "approved",
        }
        for _ in range(2):
            status, payload = self._ingest(user, [item])
            self.assertEqual(status, 200)
            self.assertEqual(payload["results"][0]["capture_id"], "cap_pull_idem_1")
        feed = self._feed_by_id(user)
        self.assertEqual(len(feed), 1)
        self.assertEqual(feed["cap_pull_idem_1"]["review_status"], "approved")

    def test_echo_reapply_cannot_escalate_existing_pending(self) -> None:
        # A capture this store already holds PENDING keeps its local decision when the same
        # content is re-applied with an "approved" claim: the passthrough only ever seeds the
        # status of captures the store does not yet hold — it cannot rewrite local review state.
        user = "pull-fastapi-echo"
        item = {
            "client_capture_id": "cap_pull_echo_1",
            "content": "Locally pending capture",
            "source": "macos",
            "captured_at": "2026-07-10T02:00:00+00:00",
        }
        status, _ = self._ingest(user, [item])
        self.assertEqual(status, 200)
        self.assertEqual(self._feed_by_id(user)["cap_pull_echo_1"]["review_status"], "pending")
        status, _ = self._ingest(user, [{**item, "review_status": "approved"}])
        self.assertEqual(status, 200)
        self.assertEqual(self._feed_by_id(user)["cap_pull_echo_1"]["review_status"], "pending")


class PullSyncStandaloneParityTests(unittest.TestCase):
    """POST /v1/sync/ingest on the shipping standalone server (the LOCAL apply endpoint the
    desktop pull worker actually calls), backed by a REAL store — same passthrough contract."""

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
            vault_path=root / "vault",
            db_path=root / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
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

    def _post_ingest(self, items: list[dict]) -> tuple[int, dict]:
        req = request.Request(
            self.base_url + "/v1/sync/ingest",
            data=json.dumps({"items": items}).encode("utf-8"),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read() or b"{}")

    def _feed_by_id(self) -> dict[str, dict]:
        req = request.Request(
            self.base_url + "/v1/sync/captures?after_seq=0&limit=200",
            headers={"Authorization": "Bearer test-token"},
        )
        with request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        return {item["client_capture_id"]: item for item in payload["items"]}

    def test_standalone_ingest_parity(self) -> None:
        # approved passthrough is honored (default review setting would otherwise hold it)...
        status, payload = self._post_ingest([{
            "client_capture_id": "cap_sa_appr_1",
            "content": "Approved on the other device",
            "source": "macos",
            "captured_at": "2026-07-10T03:00:00+00:00",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(payload["applied"], 1)
        self.assertEqual(payload["results"][0]["capture_id"], "cap_sa_appr_1")
        # ...absent keeps the normal review flow...
        status, _ = self._post_ingest([{
            "client_capture_id": "cap_sa_absent_1",
            "content": "No passthrough on this one",
            "source": "macos",
        }])
        self.assertEqual(status, 200)
        feed = self._feed_by_id()
        self.assertEqual(feed["cap_sa_appr_1"]["review_status"], "approved")
        self.assertEqual(feed["cap_sa_absent_1"]["review_status"], "pending")

    def test_standalone_ingest_rejects_out_of_bounds_review_status(self) -> None:
        status, _ = self._post_ingest([{
            "client_capture_id": "cap_sa_bad_1",
            "content": "smuggle attempt",
            "review_status": "archived",
        }])
        self.assertEqual(status, 422)
        self.assertNotIn("cap_sa_bad_1", self._feed_by_id())

    def test_standalone_ingest_idempotent_double_apply(self) -> None:
        item = {
            "client_capture_id": "cap_sa_idem_1",
            "content": "Applied twice via the standalone server",
            "source": "macos",
            "captured_at": "2026-07-10T04:00:00+00:00",
            "review_status": "approved",
        }
        for _ in range(2):
            status, payload = self._post_ingest([item])
            self.assertEqual(status, 200)
            self.assertEqual(payload["results"][0]["capture_id"], "cap_sa_idem_1")
        feed = self._feed_by_id()
        self.assertEqual(len([k for k in feed if k == "cap_sa_idem_1"]), 1)
        self.assertEqual(feed["cap_sa_idem_1"]["review_status"], "approved")

    def test_standalone_policy_gated_source_stays_pending_despite_passthrough(self) -> None:
        # SECURITY parity: the local per-source review policy wins on the standalone server too.
        self.real_store.upsert_source_account(
            self.user, source="obsidian",
            policy={"review_required": True, "allow_ai_context": True},
        )
        status, _ = self._post_ingest([{
            "client_capture_id": "cap_sa_gated_1",
            "content": "Connector note claiming prior approval",
            "source": "obsidian",
            "review_status": "approved",
        }])
        self.assertEqual(status, 200)
        self.assertEqual(self._feed_by_id()["cap_sa_gated_1"]["review_status"], "pending")


if __name__ == "__main__":
    unittest.main()
