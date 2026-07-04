"""Billing webhook (docs/COMPLETE_LAUNCH_INSTRUCTIONS.txt PART 15).

Two layers:

1. Unit: BillingWebhookVerifier signature verify (good/bad/missing), Paddle
   event -> plan mapping for each event type, apply_billing_event resolution +
   idempotency semantics, unknown-user handling.
2. HTTP: the /v1/billing/webhook route (400 on bad sig, 200 + plan flip on a
   valid event, idempotent replay, 404 when billing unconfigured) and
   GET /v1/account/plan reflecting a flip — booted against the real FastAPI app
   in auth-enabled hosted mode with billing configured (same monkeypatch
   discipline as test_auth_http.AuthEnabledTestCase).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.billing import (
    BillingWebhookVerifier,
    PaddleBillingProvider,
    SignatureVerificationError,
    apply_billing_event,
)

WEBHOOK_SECRET = "pdl_ntfset_webhook_secret_value"
PASSWORD = "correct-horse-battery"


def _sign_paddle(secret: str, raw_body: bytes, ts: str = "1700000000") -> str:
    signed = ts.encode("ascii") + b":" + raw_body
    h1 = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"ts={ts};h1={h1}"


def _paddle_event(
    event_type: str,
    *,
    status: str = "active",
    user_id: str | None = "u_alice",
    email: str | None = None,
    event_id: str = "evt_1",
) -> dict[str, Any]:
    data: dict[str, Any] = {"status": status}
    if user_id is not None:
        data["custom_data"] = {"cortex_user_id": user_id}
    if email is not None:
        data["customer"] = {"email": email}
    return {"event_id": event_id, "event_type": event_type, "data": data}


class _FakeStore:
    """Minimal store honoring get_user / set_user_plan / billing ledger."""

    def __init__(self, users: dict[str, str]) -> None:
        # user_id -> plan
        self._plans = dict(users)
        self._processed: set[str] = set()

    def get_user(self, user_id: str | None) -> dict[str, Any] | None:
        if user_id and user_id in self._plans:
            return {"user_id": user_id, "plan": self._plans[user_id]}
        return None

    def set_user_plan(self, user_id: str, plan: str) -> None:
        self._plans[user_id] = plan

    def billing_event_processed(self, event_id: str) -> bool:
        return event_id in self._processed

    def record_billing_event(self, *, event_id: str, **_: Any) -> bool:
        if not event_id:
            return True
        if event_id in self._processed:
            return False
        self._processed.add(event_id)
        return True


# ------------------------------------------------------------------- unit tests

class PaddleSignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = PaddleBillingProvider(WEBHOOK_SECRET)

    def test_good_signature_passes(self) -> None:
        body = json.dumps(_paddle_event("subscription.created")).encode()
        header = _sign_paddle(WEBHOOK_SECRET, body)
        self.provider.verify_signature(raw_body=body, headers={"Paddle-Signature": header})

    def test_bad_signature_rejected(self) -> None:
        body = json.dumps(_paddle_event("subscription.created")).encode()
        header = _sign_paddle("the-wrong-secret", body)
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body, headers={"Paddle-Signature": header})

    def test_tampered_body_rejected(self) -> None:
        body = json.dumps(_paddle_event("subscription.created")).encode()
        header = _sign_paddle(WEBHOOK_SECRET, body)
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body + b" ", headers={"Paddle-Signature": header})

    def test_missing_signature_rejected(self) -> None:
        body = json.dumps(_paddle_event("subscription.created")).encode()
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body, headers={})

    def test_malformed_signature_rejected(self) -> None:
        body = b"{}"
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body, headers={"Paddle-Signature": "garbage"})

    def test_verifier_from_settings_requires_secret(self) -> None:
        class _S:
            billing_provider = "paddle"
            paddle_webhook_secret = ""
        with self.assertRaises(ValueError):
            BillingWebhookVerifier.from_settings(_S())

    def test_verifier_rejects_unknown_provider(self) -> None:
        class _S:
            billing_provider = "stripe"
            paddle_webhook_secret = "x"
        with self.assertRaises(ValueError):
            BillingWebhookVerifier.from_settings(_S())


class PaddleEventMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = PaddleBillingProvider(WEBHOOK_SECRET)

    def _parse(self, event: dict[str, Any]):
        return self.provider.parse_event(event)

    def test_created_active_maps_to_pro(self) -> None:
        parsed = self._parse(_paddle_event("subscription.created", status="active"))
        self.assertEqual(parsed.plan, "pro")
        self.assertEqual(parsed.user_id, "u_alice")

    def test_updated_trialing_maps_to_pro(self) -> None:
        self.assertEqual(self._parse(_paddle_event("subscription.updated", status="trialing")).plan, "pro")

    def test_past_due_keeps_pro(self) -> None:
        self.assertEqual(self._parse(_paddle_event("subscription.past_due")).plan, "pro")

    def test_canceled_maps_to_free(self) -> None:
        self.assertEqual(self._parse(_paddle_event("subscription.canceled")).plan, "free")

    def test_updated_canceled_status_maps_to_free(self) -> None:
        self.assertEqual(self._parse(_paddle_event("subscription.updated", status="canceled")).plan, "free")

    def test_unknown_event_type_maps_to_no_change(self) -> None:
        self.assertIsNone(self._parse(_paddle_event("transaction.completed")).plan)

    def test_email_extracted_when_no_custom_data(self) -> None:
        parsed = self._parse(_paddle_event("subscription.created", user_id=None, email="Buyer@Example.com"))
        self.assertIsNone(parsed.user_id)
        self.assertEqual(parsed.email, "Buyer@Example.com")


class ApplyBillingEventTests(unittest.TestCase):
    def _event(self, event_type: str, **kw: Any):
        return PaddleBillingProvider(WEBHOOK_SECRET).parse_event(_paddle_event(event_type, **kw))

    def test_flip_free_to_pro(self) -> None:
        store = _FakeStore({"u_alice": "free"})
        result = apply_billing_event(store, self._event("subscription.created"))
        self.assertEqual(result, {"user_id": "u_alice", "plan": "pro", "action": "updated"})
        self.assertEqual(store._plans["u_alice"], "pro")

    def test_cancel_back_to_free(self) -> None:
        store = _FakeStore({"u_alice": "pro"})
        result = apply_billing_event(store, self._event("subscription.canceled"))
        self.assertEqual(result["action"], "updated")
        self.assertEqual(store._plans["u_alice"], "free")

    def test_already_on_target_plan_is_unchanged(self) -> None:
        store = _FakeStore({"u_alice": "pro"})
        result = apply_billing_event(store, self._event("subscription.created"))
        self.assertEqual(result["action"], "unchanged")

    def test_no_plan_change_event(self) -> None:
        store = _FakeStore({"u_alice": "free"})
        result = apply_billing_event(store, self._event("transaction.completed"))
        self.assertEqual(result["action"], "no_plan_change")
        self.assertEqual(store._plans["u_alice"], "free")

    def test_unknown_user_handled(self) -> None:
        store = _FakeStore({})
        result = apply_billing_event(store, self._event("subscription.created", user_id="u_ghost"))
        self.assertEqual(result["action"], "unknown_user")

    def test_email_resolver_fallback(self) -> None:
        store = _FakeStore({"u_bob": "free"})
        event = self._event("subscription.created", user_id=None, email="bob@example.com")
        result = apply_billing_event(store, event, email_resolver=lambda e: "u_bob" if e == "bob@example.com" else None)
        self.assertEqual(result["user_id"], "u_bob")
        self.assertEqual(store._plans["u_bob"], "pro")


# ------------------------------------------------------------------- HTTP tests

class BillingHttpTests(unittest.TestCase):
    """Auth-enabled hosted app with billing configured (Paddle)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._original_settings = main_module.settings
        self._original_store = main_module.store
        self._original_runtime = main_module.auth_runtime
        self._kek_prev = os.environ.get("CORTEX_KEK")
        os.environ["CORTEX_KEK"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        hosted = replace(
            self._original_settings,
            db_path=root / "hosted.sqlite",
            vault_path=root / "hosted.vault",
            shard_root=root / "shards",
            shard_mode="user",
            default_user_id="hosted-default",
            require_scoped_api_tokens=True,
            auth_enabled=True,
            accounts_db_path=None,
            auth_email_mode="log",
            auth_rate_limit_per_minute=0,
            auth_access_ttl_seconds=0,
            auth_refresh_idle_ttl_seconds=0,
            auth_refresh_absolute_ttl_seconds=0,
            oidc_google_client_id="",
            oidc_google_client_secret="",
            oidc_github_client_id="",
            oidc_github_client_secret="",
            public_app_url="http://127.0.0.1:8766",
            billing_provider="paddle",
            paddle_webhook_secret=WEBHOOK_SECRET,
        )
        main_module.settings = hosted
        main_module.store = main_module.StoreRegistry.from_settings(hosted)
        main_module.init_auth_runtime()
        self.settings = hosted
        self.runtime = main_module.auth_runtime
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        main_module.settings = self._original_settings
        main_module.store = self._original_store
        main_module.auth_runtime = self._original_runtime
        if self._kek_prev is None:
            os.environ.pop("CORTEX_KEK", None)
        else:
            os.environ["CORTEX_KEK"] = self._kek_prev
        self._tmp.cleanup()

    # ------------------------------------------------------------ helpers
    def _last_flow_token(self, kind: str) -> str:
        for item in reversed(self.runtime.outbox):
            if item["kind"] == kind:
                return item["token"]
        raise AssertionError(f"no {kind} delivery found")

    def _signup_login(self, email: str = "buyer@example.com") -> dict[str, Any]:
        r = self.client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        v = self.client.post("/v1/auth/verify-email", json={"token": self._last_flow_token("email_verify")})
        self.assertEqual(v.status_code, 200, v.text)
        account = v.json()["account"]
        login = self.client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        self.assertEqual(login.status_code, 200, login.text)
        return {"account": account, "login": login.json()}

    def _post_webhook(self, event: dict[str, Any], *, secret: str = WEBHOOK_SECRET):
        body = json.dumps(event).encode()
        header = _sign_paddle(secret, body)
        return self.client.post(
            "/v1/billing/webhook", content=body,
            headers={"Paddle-Signature": header, "Content-Type": "application/json"},
        )

    # ------------------------------------------------------------ tests
    def test_bad_signature_is_400(self) -> None:
        resp = self._post_webhook(_paddle_event("subscription.created"), secret="wrong")
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_webhook_flips_plan_and_account_plan_reflects_it(self) -> None:
        signed = self._signup_login()
        user_id = signed["account"]["user_id"]
        access = signed["login"]["access_token"]
        bearer = {"Authorization": f"Bearer {access}"}

        before = self.client.get("/v1/account/plan", headers=bearer)
        self.assertEqual(before.status_code, 200, before.text)
        self.assertEqual(before.json()["plan"], "free")
        self.assertTrue(before.json()["billing_enabled"])

        resp = self._post_webhook(
            _paddle_event("subscription.created", user_id=user_id, event_id="evt_flip")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "updated")
        self.assertEqual(resp.json()["plan"], "pro")

        after = self.client.get("/v1/account/plan", headers=bearer)
        self.assertEqual(after.json()["plan"], "pro")
        # pro quota is unlimited by default.
        self.assertTrue(after.json()["memory_quota_unlimited"])

    def test_idempotent_replay_is_single_effect(self) -> None:
        signed = self._signup_login(email="dup@example.com")
        user_id = signed["account"]["user_id"]
        event = _paddle_event("subscription.created", user_id=user_id, event_id="evt_dup")
        first = self._post_webhook(event)
        self.assertEqual(first.json()["action"], "updated")
        second = self._post_webhook(event)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["action"], "duplicate")

    def test_unknown_user_is_accepted_no_change(self) -> None:
        resp = self._post_webhook(
            _paddle_event("subscription.created", user_id="u_nobody", event_id="evt_ghost")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "unknown_user")

    def test_email_fallback_resolves_user(self) -> None:
        signed = self._signup_login(email="viaemail@example.com")
        user_id = signed["account"]["user_id"]
        resp = self._post_webhook(
            _paddle_event(
                "subscription.created", user_id=None, email="viaemail@example.com", event_id="evt_email"
            )
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "updated")
        self.assertEqual(resp.json()["user_id"], user_id)


class BillingDisabledTests(unittest.TestCase):
    """Billing unconfigured: the webhook route 404s, /v1/account/plan still
    works and reports billing_enabled False."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self._original_settings = main_module.settings
        self._original_store = main_module.store
        self._original_runtime = main_module.auth_runtime
        self._kek_prev = os.environ.get("CORTEX_KEK")
        os.environ["CORTEX_KEK"] = base64.b64encode(secrets.token_bytes(32)).decode("ascii")
        hosted = replace(
            self._original_settings,
            db_path=root / "hosted.sqlite",
            vault_path=root / "hosted.vault",
            shard_root=root / "shards",
            shard_mode="user",
            default_user_id="hosted-default",
            require_scoped_api_tokens=True,
            auth_enabled=True,
            accounts_db_path=None,
            auth_email_mode="log",
            auth_rate_limit_per_minute=0,
            auth_access_ttl_seconds=0,
            auth_refresh_idle_ttl_seconds=0,
            auth_refresh_absolute_ttl_seconds=0,
            oidc_google_client_id="",
            oidc_google_client_secret="",
            oidc_github_client_id="",
            oidc_github_client_secret="",
            public_app_url="http://127.0.0.1:8766",
            billing_provider="",
            paddle_webhook_secret="",
        )
        main_module.settings = hosted
        main_module.store = main_module.StoreRegistry.from_settings(hosted)
        main_module.init_auth_runtime()
        self.settings = hosted
        self.runtime = main_module.auth_runtime
        self.client = TestClient(main_module.app)

    def tearDown(self) -> None:
        main_module.settings = self._original_settings
        main_module.store = self._original_store
        main_module.auth_runtime = self._original_runtime
        if self._kek_prev is None:
            os.environ.pop("CORTEX_KEK", None)
        else:
            os.environ["CORTEX_KEK"] = self._kek_prev
        self._tmp.cleanup()

    def test_webhook_404_when_unconfigured(self) -> None:
        body = json.dumps(_paddle_event("subscription.created")).encode()
        resp = self.client.post(
            "/v1/billing/webhook", content=body,
            headers={"Paddle-Signature": _sign_paddle(WEBHOOK_SECRET, body)},
        )
        self.assertEqual(resp.status_code, 404, resp.text)

    def test_account_plan_reports_billing_disabled(self) -> None:
        email = "nb@example.com"
        r = self.client.post("/v1/auth/signup", json={"email": email, "password": PASSWORD})
        self.assertEqual(r.status_code, 200, r.text)
        token = None
        for item in reversed(self.runtime.outbox):
            if item["kind"] == "email_verify":
                token = item["token"]
                break
        self.client.post("/v1/auth/verify-email", json={"token": token})
        login = self.client.post("/v1/auth/login", json={"email": email, "password": PASSWORD})
        access = login.json()["access_token"]
        plan = self.client.get("/v1/account/plan", headers={"Authorization": f"Bearer {access}"})
        self.assertEqual(plan.status_code, 200, plan.text)
        self.assertFalse(plan.json()["billing_enabled"])
        self.assertEqual(plan.json()["plan"], "free")


if __name__ == "__main__":
    unittest.main()
