"""Billing webhook handling — Paddle-first, generic, DORMANT until configured.

The account system already carries a per-user ``plan`` field (sharding.py users
table). This module is the piece that flips it on payment. It is config-gated by
``Settings.billing_enabled``: with no provider + secret set, the webhook route
404s and nothing here runs, so a no-billing deployment is byte-identical to
today.

Design notes:
- Provider abstraction (``BillingProvider``) keeps Stripe addable later; only
  Paddle Billing is implemented now.
- Signature verification is HMAC-SHA256 over the RAW request body using the
  provider webhook secret. Paddle Billing sends a ``Paddle-Signature`` header of
  the form ``ts=<unix>;h1=<hex-hmac>`` where the signed payload is
  ``<ts>:<raw-body>``. We recompute and constant-time compare.
- Event → plan transition mapping lives in ``apply_billing_event`` (pure-ish:
  takes a ``store`` with ``set_user_plan``/``get_user`` and an already-verified,
  parsed event dict; returns ``{user_id, plan, action}``). It is idempotent at
  the caller via a processed-event-id ledger (main.py wires that to the control
  store); this module also exposes ``event_id`` extraction so the caller can
  dedupe before applying.
- User resolution: the Cortex ``user_id`` MUST be carried in the checkout's
  ``custom_data`` (documented expectation below); email is a fallback lookup
  hook the caller may provide.

Stdlib only (hmac/hashlib/json) — no new deps, no import of this module at
module scope in any stdlib-plane file.

## Integration expectation (documented contract)

When creating a Paddle checkout for a Cortex user, set the transaction/
subscription ``custom_data`` to include the Cortex user id, e.g.::

    custom_data: { "cortex_user_id": "u_ab12cd..." }

The webhook then maps the subscription event back to that user with zero
email round-trips. If ``custom_data`` is absent, the handler falls back to the
customer email (resolved via a caller-provided ``email_resolver``); if neither
resolves a known user, the event is accepted (HTTP 200) but no plan changes —
an unknown-user event must never fail the webhook (Paddle retries otherwise).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from typing import Any, Callable, Protocol

# Plan names the webhook can assign. Kept tiny on purpose (free vs paid).
PLAN_FREE = "free"
PLAN_PRO = "pro"

# Paddle subscription statuses → the plan a Cortex user should hold. An active
# or trialing subscription grants "pro"; anything terminal (canceled, past due
# after dunning, paused) drops back to "free". past_due keeps "pro" during the
# grace/dunning window — the operator can tighten this later if desired.
_PADDLE_ACTIVE_STATUSES = {"active", "trialing", "past_due"}
_PADDLE_INACTIVE_STATUSES = {"canceled", "cancelled", "paused", "expired"}


class BillingError(Exception):
    """Base class for billing failures."""


class SignatureVerificationError(BillingError):
    """The webhook signature was missing, malformed, or did not match."""


@dataclass(frozen=True)
class BillingEvent:
    """A provider-agnostic, already-verified billing event.

    ``event_id`` powers idempotent replay dedupe at the caller. ``plan`` is the
    plan the mapped user should hold after this event (None = no plan change).
    ``user_id`` / ``email`` are resolution hints; the caller resolves the final
    user via user_id first, then email.
    """

    provider: str
    event_id: str
    event_type: str
    plan: str | None
    user_id: str | None
    email: str | None
    raw: dict[str, Any]


class BillingProvider(Protocol):
    name: str

    def verify_signature(self, *, raw_body: bytes, headers: dict[str, str]) -> None:
        """Raise SignatureVerificationError if the signature is missing/bad."""
        ...

    def parse_event(self, payload: dict[str, Any]) -> BillingEvent:
        """Map a parsed webhook body to a BillingEvent."""
        ...


class PaddleBillingProvider:
    """Paddle Billing (v2) webhook provider.

    Signature header ``Paddle-Signature: ts=<unix>;h1=<hex>`` over
    ``f"{ts}:{raw_body}"`` keyed by the webhook secret (HMAC-SHA256).
    """

    name = "paddle"

    def __init__(self, webhook_secret: str) -> None:
        self._secret = (webhook_secret or "").encode("utf-8")

    # ------------------------------------------------------------ signature
    def verify_signature(self, *, raw_body: bytes, headers: dict[str, str]) -> None:
        if not self._secret:
            raise SignatureVerificationError("billing webhook secret is not configured")
        header = _header_value(headers, "Paddle-Signature")
        if not header:
            raise SignatureVerificationError("missing Paddle-Signature header")
        parts = self._parse_signature_header(header)
        ts = parts.get("ts")
        provided = parts.get("h1")
        if not ts or not provided:
            raise SignatureVerificationError("malformed Paddle-Signature header")
        signed_payload = ts.encode("ascii") + b":" + raw_body
        expected = hmac.new(self._secret, signed_payload, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(expected, provided):
            raise SignatureVerificationError("Paddle-Signature does not match")

    @staticmethod
    def _parse_signature_header(header: str) -> dict[str, str]:
        out: dict[str, str] = {}
        for chunk in header.split(";"):
            key, sep, value = chunk.partition("=")
            if sep:
                out[key.strip()] = value.strip()
        return out

    # ---------------------------------------------------------------- parse
    def parse_event(self, payload: dict[str, Any]) -> BillingEvent:
        event_type = str(payload.get("event_type") or "").strip()
        event_id = str(payload.get("event_id") or payload.get("notification_id") or "").strip()
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        status = str(data.get("status") or "").strip().lower()
        plan = self._plan_for(event_type, status)
        user_id, email = self._resolve_identity(data)
        return BillingEvent(
            provider=self.name,
            event_id=event_id,
            event_type=event_type,
            plan=plan,
            user_id=user_id,
            email=email,
            raw=payload,
        )

    @staticmethod
    def _plan_for(event_type: str, status: str) -> str | None:
        """Map a subscription event + status to the target plan.

        subscription.created/updated -> pro when active/trialing/past_due,
        free when the status is terminal. subscription.canceled -> free.
        subscription.past_due -> pro (dunning grace). Unknown event types map to
        None (no change)."""
        et = event_type.lower()
        if et == "subscription.canceled" or et == "subscription.cancelled":
            return PLAN_FREE
        if et == "subscription.past_due":
            return PLAN_PRO
        if et in {"subscription.created", "subscription.updated", "subscription.activated", "subscription.resumed"}:
            if status in _PADDLE_INACTIVE_STATUSES:
                return PLAN_FREE
            if status in _PADDLE_ACTIVE_STATUSES or not status:
                return PLAN_PRO
            return PLAN_PRO
        return None

    @staticmethod
    def _resolve_identity(data: dict[str, Any]) -> tuple[str | None, str | None]:
        user_id = _extract_custom_user_id(data)
        email = None
        customer = data.get("customer")
        if isinstance(customer, dict):
            email = _clean_str(customer.get("email"))
        if email is None:
            email = _clean_str(data.get("customer_email"))
        return user_id, email


def _extract_custom_user_id(data: dict[str, Any]) -> str | None:
    """Pull the Cortex user id from Paddle custom_data (checkout must set it).
    Accepts a few common key spellings so a small integration typo still works."""
    custom = data.get("custom_data")
    if not isinstance(custom, dict):
        return None
    for key in ("cortex_user_id", "user_id", "userId", "cortexUserId"):
        value = _clean_str(custom.get(key))
        if value:
            return value
    return None


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _header_value(headers: dict[str, str], name: str) -> str:
    """Case-insensitive header lookup (Starlette headers are already
    case-insensitive, but this keeps the module usable with a plain dict)."""
    lowered = name.lower()
    for key, value in headers.items():
        if key.lower() == lowered:
            return str(value or "")
    return ""


class BillingWebhookVerifier:
    """Thin façade over a configured provider: verifies a raw webhook request
    and parses it into a BillingEvent. Built once from settings; raises
    ValueError if the settings do not enable billing (callers gate on
    ``settings.billing_enabled`` first)."""

    def __init__(self, provider: BillingProvider) -> None:
        self.provider = provider

    @classmethod
    def from_settings(cls, settings: Any) -> "BillingWebhookVerifier":
        provider_name = (getattr(settings, "billing_provider", "") or "").strip().lower()
        if provider_name == "paddle":
            secret = getattr(settings, "paddle_webhook_secret", "") or ""
            if not secret:
                raise ValueError("Paddle billing requires CORTEX_PADDLE_WEBHOOK_SECRET")
            return cls(PaddleBillingProvider(secret))
        raise ValueError(f"unsupported or unconfigured billing provider: {provider_name!r}")

    def verify_and_parse(self, *, raw_body: bytes, headers: dict[str, str]) -> BillingEvent:
        self.provider.verify_signature(raw_body=raw_body, headers=headers)
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BillingError("webhook body is not valid JSON") from exc
        if not isinstance(payload, dict):
            raise BillingError("webhook body must be a JSON object")
        return self.provider.parse_event(payload)


def apply_billing_event(
    store: Any,
    event: BillingEvent,
    *,
    email_resolver: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    """Apply a verified billing event to the user's plan. Pure-ish: reads/writes
    only through ``store`` (needs ``get_user(user_id)`` and
    ``set_user_plan(user_id, plan)``).

    Resolution order: explicit user_id (from checkout custom_data) first, then
    the customer email via the optional ``email_resolver``. Returns
    ``{user_id, plan, action}`` where action is one of:
      - "no_plan_change": the event type maps to no plan transition
      - "unknown_user": no user could be resolved (accepted, no effect)
      - "unchanged": the user already holds the target plan (idempotent)
      - "updated": the plan was changed
    """
    if event.plan is None:
        return {"user_id": event.user_id, "plan": None, "action": "no_plan_change"}

    user_id = event.user_id
    user = store.get_user(user_id) if user_id else None
    if user is None and event.email and email_resolver is not None:
        resolved = email_resolver(event.email)
        if resolved:
            user_id = resolved
            user = store.get_user(user_id)

    if user is None:
        return {"user_id": user_id, "plan": event.plan, "action": "unknown_user"}

    current_plan = str(user.get("plan") or "").strip().lower()
    if current_plan == event.plan:
        return {"user_id": user_id, "plan": event.plan, "action": "unchanged"}

    store.set_user_plan(user_id, event.plan)
    return {"user_id": user_id, "plan": event.plan, "action": "updated"}
