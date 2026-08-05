"""Stripe billing provider (H8) — mirrors test_billing.py's Paddle coverage.

Two layers:

1. Unit: StripeBillingProvider signature verify (good/bad/missing/skew), the
   Stripe event -> plan mapping for each subscription event type, metadata +
   email resolution, and BillingWebhookVerifier.from_settings provider
   selection (paddle vs stripe honored, secret required).
2. HTTP: the shared /v1/billing/webhook route dispatching to Stripe when
   CORTEX_BILLING_PROVIDER=stripe (200 + plan flip on a valid event, 400 on bad
   sig, idempotent replay via the billing_events ledger, unknown-user accepted,
   email fallback) and the route still 404ing when unconfigured — booted against
   the real FastAPI app the same way test_billing.py boots the Paddle path.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.billing import (
    BillingWebhookVerifier,
    StripeBillingProvider,
    SignatureVerificationError,
    apply_billing_event,
)

WEBHOOK_SECRET = "whsec_stripe_test_secret_value"
PASSWORD = "correct-horse-battery"


def _sign_stripe(secret: str, raw_body: bytes, ts: int | None = None) -> str:
    if ts is None:
        ts = int(time.time())
    signed = f"{ts}.".encode("ascii") + raw_body
    v1 = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
    return f"t={ts},v1={v1}"


def _stripe_event(
    event_type: str,
    *,
    status: str = "active",
    user_id: str | None = "u_alice",
    email: str | None = None,
    event_id: str = "evt_stripe_1",
) -> dict[str, Any]:
    obj: dict[str, Any] = {"status": status, "object": "subscription"}
    if user_id is not None:
        obj["metadata"] = {"cortex_user_id": user_id}
    if email is not None:
        obj["customer_email"] = email
    return {"id": event_id, "type": event_type, "data": {"object": obj}}


class _FakeStore:
    """Minimal store honoring get_user / set_user_plan / billing ledger."""

    def __init__(self, users: dict[str, str]) -> None:
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

class StripeSignatureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = StripeBillingProvider(WEBHOOK_SECRET)

    def test_good_signature_passes(self) -> None:
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        header = _sign_stripe(WEBHOOK_SECRET, body)
        self.provider.verify_signature(raw_body=body, headers={"Stripe-Signature": header})

    def test_multiple_v1_any_match_passes(self) -> None:
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        header = _sign_stripe(WEBHOOK_SECRET, body)
        # A rotated-secret style header: a bogus v1 alongside the real one.
        t = header.split(",")[0]
        good_v1 = header.split("v1=")[1]
        multi = f"{t},v1=deadbeef,v1={good_v1}"
        self.provider.verify_signature(raw_body=body, headers={"Stripe-Signature": multi})

    def test_bad_signature_rejected(self) -> None:
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        header = _sign_stripe("the-wrong-secret", body)
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body, headers={"Stripe-Signature": header})

    def test_tampered_body_rejected(self) -> None:
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        header = _sign_stripe(WEBHOOK_SECRET, body)
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body + b" ", headers={"Stripe-Signature": header})

    def test_missing_signature_rejected(self) -> None:
        body = b"{}"
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body, headers={})

    def test_malformed_signature_rejected(self) -> None:
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=b"{}", headers={"Stripe-Signature": "garbage"})

    def test_skewed_timestamp_rejected(self) -> None:
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        stale = int(time.time()) - 10_000  # well beyond the 5-minute tolerance
        header = _sign_stripe(WEBHOOK_SECRET, body, ts=stale)
        with self.assertRaises(SignatureVerificationError):
            self.provider.verify_signature(raw_body=body, headers={"Stripe-Signature": header})

    def test_skew_allowed_when_tolerance_disabled(self) -> None:
        provider = StripeBillingProvider(WEBHOOK_SECRET, tolerance_seconds=0)
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        header = _sign_stripe(WEBHOOK_SECRET, body, ts=int(time.time()) - 10_000)
        provider.verify_signature(raw_body=body, headers={"Stripe-Signature": header})


class StripeEventMappingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = StripeBillingProvider(WEBHOOK_SECRET)

    def _parse(self, event: dict[str, Any]):
        return self.provider.parse_event(event)

    def test_created_active_maps_to_pro(self) -> None:
        parsed = self._parse(_stripe_event("customer.subscription.created", status="active"))
        self.assertEqual(parsed.plan, "pro")
        self.assertEqual(parsed.user_id, "u_alice")
        self.assertEqual(parsed.event_id, "evt_stripe_1")

    def test_updated_trialing_maps_to_pro(self) -> None:
        self.assertEqual(
            self._parse(_stripe_event("customer.subscription.updated", status="trialing")).plan, "pro"
        )

    def test_updated_past_due_keeps_pro(self) -> None:
        self.assertEqual(
            self._parse(_stripe_event("customer.subscription.updated", status="past_due")).plan, "pro"
        )

    def test_updated_canceled_status_maps_to_free(self) -> None:
        self.assertEqual(
            self._parse(_stripe_event("customer.subscription.updated", status="canceled")).plan, "free"
        )

    def test_updated_unpaid_maps_to_free(self) -> None:
        self.assertEqual(
            self._parse(_stripe_event("customer.subscription.updated", status="unpaid")).plan, "free"
        )

    def test_updated_incomplete_expired_maps_to_free(self) -> None:
        self.assertEqual(
            self._parse(_stripe_event("customer.subscription.updated", status="incomplete_expired")).plan,
            "free",
        )

    def test_deleted_maps_to_free(self) -> None:
        self.assertEqual(
            self._parse(_stripe_event("customer.subscription.deleted", status="canceled")).plan, "free"
        )

    def test_unknown_event_type_maps_to_no_change(self) -> None:
        self.assertIsNone(self._parse(_stripe_event("invoice.paid")).plan)

    def test_metadata_user_id_resolved(self) -> None:
        parsed = self._parse(_stripe_event("customer.subscription.created", user_id="u_meta"))
        self.assertEqual(parsed.user_id, "u_meta")

    def test_email_extracted_when_no_metadata(self) -> None:
        parsed = self._parse(
            _stripe_event("customer.subscription.created", user_id=None, email="Buyer@Example.com")
        )
        self.assertIsNone(parsed.user_id)
        self.assertEqual(parsed.email, "Buyer@Example.com")


class StripeApplyBillingEventTests(unittest.TestCase):
    def _event(self, event_type: str, **kw: Any):
        return StripeBillingProvider(WEBHOOK_SECRET).parse_event(_stripe_event(event_type, **kw))

    def test_flip_free_to_pro(self) -> None:
        store = _FakeStore({"u_alice": "free"})
        result = apply_billing_event(store, self._event("customer.subscription.created"))
        self.assertEqual(result, {"user_id": "u_alice", "plan": "pro", "action": "updated"})
        self.assertEqual(store._plans["u_alice"], "pro")

    def test_delete_back_to_free(self) -> None:
        store = _FakeStore({"u_alice": "pro"})
        result = apply_billing_event(store, self._event("customer.subscription.deleted"))
        self.assertEqual(result["action"], "updated")
        self.assertEqual(store._plans["u_alice"], "free")

    def test_unknown_user_handled(self) -> None:
        store = _FakeStore({})
        result = apply_billing_event(
            store, self._event("customer.subscription.created", user_id="u_ghost")
        )
        self.assertEqual(result["action"], "unknown_user")

    def test_email_resolver_fallback(self) -> None:
        store = _FakeStore({"u_bob": "free"})
        event = self._event("customer.subscription.created", user_id=None, email="bob@example.com")
        result = apply_billing_event(
            store, event, email_resolver=lambda e: "u_bob" if e == "bob@example.com" else None
        )
        self.assertEqual(result["user_id"], "u_bob")
        self.assertEqual(store._plans["u_bob"], "pro")


class ProviderSelectionTests(unittest.TestCase):
    def test_stripe_selected_from_settings(self) -> None:
        class _S:
            billing_provider = "stripe"
            stripe_webhook_secret = WEBHOOK_SECRET
            paddle_webhook_secret = ""
        verifier = BillingWebhookVerifier.from_settings(_S())
        self.assertIsInstance(verifier.provider, StripeBillingProvider)

    def test_stripe_requires_secret(self) -> None:
        class _S:
            billing_provider = "stripe"
            stripe_webhook_secret = ""
            paddle_webhook_secret = ""
        with self.assertRaises(ValueError):
            BillingWebhookVerifier.from_settings(_S())

    def test_paddle_still_selectable(self) -> None:
        from backend.app.billing import PaddleBillingProvider

        class _S:
            billing_provider = "paddle"
            paddle_webhook_secret = "pdl_secret"
            stripe_webhook_secret = ""
        verifier = BillingWebhookVerifier.from_settings(_S())
        self.assertIsInstance(verifier.provider, PaddleBillingProvider)


# ------------------------------------------------------------------- HTTP tests

class _StripeHttpBase(unittest.TestCase):
    billing_extra: dict = {}

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
            **self.billing_extra,
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
        header = _sign_stripe(secret, body)
        return self.client.post(
            "/v1/billing/webhook", content=body,
            headers={"Stripe-Signature": header, "Content-Type": "application/json"},
        )


class StripeWebhookHttpTests(_StripeHttpBase):
    billing_extra = {
        "billing_provider": "stripe",
        "stripe_webhook_secret": WEBHOOK_SECRET,
        "paddle_webhook_secret": "",
    }

    def test_billing_enabled_for_stripe(self) -> None:
        self.assertTrue(self.settings.billing_enabled)

    def test_bad_signature_is_400(self) -> None:
        resp = self._post_webhook(_stripe_event("customer.subscription.created"), secret="wrong")
        self.assertEqual(resp.status_code, 400, resp.text)

    def test_webhook_flips_plan_and_account_plan_reflects_it(self) -> None:
        signed = self._signup_login()
        user_id = signed["account"]["user_id"]
        access = signed["login"]["access_token"]
        bearer = {"Authorization": f"Bearer {access}"}

        before = self.client.get("/v1/account/plan", headers=bearer)
        self.assertEqual(before.json()["plan"], "free")
        self.assertTrue(before.json()["billing_enabled"])

        resp = self._post_webhook(
            _stripe_event("customer.subscription.created", user_id=user_id, event_id="evt_flip")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "updated")
        self.assertEqual(resp.json()["plan"], "pro")

        after = self.client.get("/v1/account/plan", headers=bearer)
        self.assertEqual(after.json()["plan"], "pro")
        self.assertTrue(after.json()["memory_quota_unlimited"])

    def test_idempotent_replay_is_single_effect(self) -> None:
        signed = self._signup_login(email="dup@example.com")
        user_id = signed["account"]["user_id"]
        event = _stripe_event("customer.subscription.created", user_id=user_id, event_id="evt_dup")
        first = self._post_webhook(event)
        self.assertEqual(first.json()["action"], "updated")
        second = self._post_webhook(event)
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["action"], "duplicate")

    def test_unknown_user_is_accepted_no_change(self) -> None:
        resp = self._post_webhook(
            _stripe_event("customer.subscription.created", user_id="u_nobody", event_id="evt_ghost")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "unknown_user")

    def test_email_fallback_resolves_user(self) -> None:
        signed = self._signup_login(email="viaemail@example.com")
        user_id = signed["account"]["user_id"]
        resp = self._post_webhook(
            _stripe_event(
                "customer.subscription.created", user_id=None, email="viaemail@example.com",
                event_id="evt_email",
            )
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["action"], "updated")
        self.assertEqual(resp.json()["user_id"], user_id)

    def test_deleted_event_downgrades_to_free(self) -> None:
        signed = self._signup_login(email="downgrade@example.com")
        user_id = signed["account"]["user_id"]
        self._post_webhook(
            _stripe_event("customer.subscription.created", user_id=user_id, event_id="evt_up")
        )
        resp = self._post_webhook(
            _stripe_event("customer.subscription.deleted", user_id=user_id, event_id="evt_del")
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["plan"], "free")


class StripeWebhookDisabledTests(_StripeHttpBase):
    billing_extra = {"billing_provider": "", "stripe_webhook_secret": "", "paddle_webhook_secret": ""}

    def test_webhook_404_when_unconfigured(self) -> None:
        body = json.dumps(_stripe_event("customer.subscription.created")).encode()
        resp = self.client.post(
            "/v1/billing/webhook", content=body,
            headers={"Stripe-Signature": _sign_stripe(WEBHOOK_SECRET, body)},
        )
        self.assertEqual(resp.status_code, 404, resp.text)


if __name__ == "__main__":
    unittest.main()
