from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import re
from typing import Any


REDACTED_CONNECTOR_SECRET = "[REDACTED_CONNECTOR_SECRET]"

_AUTH_CREDENTIAL_RE = re.compile(
    r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:Bearer|Token|Basic)\s+)([^\s,'\"}]+)"
)
_SECRET_FIELD_RE = re.compile(
    r"(?i)((?:access[_-]?token|refresh[_-]?token|client[_-]?id|client[_-]?secret|api[_-]?key|api[_-]?token|token|zotero-api-key)"
    r"[\"']?\s*[:=]\s*[\"']?)([^\s,'\"}]+)"
)


def redact_error_message(
    value: Any,
    secrets: Iterable[Any] = (),
    *,
    placeholder: str = REDACTED_CONNECTOR_SECRET,
) -> str:
    message = str(value)
    secret_values = {
        str(secret).strip()
        for secret in secrets
        if str(secret or "").strip()
    }
    for secret in sorted(secret_values, key=len, reverse=True):
        message = message.replace(secret, placeholder)
    message = _AUTH_CREDENTIAL_RE.sub(lambda match: f"{match.group(1)}{placeholder}", message)
    message = _SECRET_FIELD_RE.sub(lambda match: f"{match.group(1)}{placeholder}", message)
    return message


def connector_error_payload(value: Any, secrets: Iterable[Any] = ()) -> dict[str, Any]:
    payload: dict[str, Any] = {"error": redact_error_message(value, secrets)}
    status_code = _exception_status_code(value)
    if status_code is not None:
        payload["status_code"] = status_code
        payload["category"] = _status_category(status_code)
    else:
        payload["category"] = _message_category(payload["error"])
    retry_after = _retry_after_iso(_exception_header(value, "Retry-After"))
    if retry_after:
        payload["retry_after"] = retry_after
    return {key: val for key, val in payload.items() if val not in (None, "", [], {})}


def classify_error_message(value: Any, *, default: str = "network") -> str:
    return _message_category(str(value or ""), default=default)


def _exception_status_code(value: Any) -> int | None:
    for attr in ("code", "status", "status_code"):
        raw = getattr(value, attr, None)
        try:
            parsed = int(raw)
        except (TypeError, ValueError):
            continue
        if parsed > 0:
            return parsed
    return None


def _exception_header(value: Any, name: str) -> str | None:
    headers = getattr(value, "headers", None) or getattr(value, "hdrs", None)
    if not headers:
        return None
    getter = getattr(headers, "get", None)
    if callable(getter):
        raw = getter(name)
        return str(raw).strip() if raw not in (None, "") else None
    if isinstance(headers, dict):
        raw = headers.get(name) or headers.get(name.lower())
        return str(raw).strip() if raw not in (None, "") else None
    return None


def _retry_after_iso(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if text.isdigit():
        seconds = max(0, min(86400, int(text)))
        return _isoformat_z(datetime.now(timezone.utc) + timedelta(seconds=seconds))
    try:
        parsed = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return _isoformat_z(parsed)


def _isoformat_z(value: datetime) -> str:
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _status_category(status_code: int) -> str:
    if status_code in {401, 403}:
        return "auth"
    if status_code == 429:
        return "rate_limited"
    if 500 <= status_code <= 599:
        return "server"
    if 400 <= status_code <= 499:
        return "client"
    return "network"


def _message_category(message: str, *, default: str = "network") -> str:
    lowered = message.lower()
    if "rate limit" in lowered or "ratelimit" in lowered or "rate_limited" in lowered or "too many requests" in lowered:
        return "rate_limited"
    if (
        "unauthorized" in lowered
        or "forbidden" in lowered
        or "invalid token" in lowered
        or "invalid_auth" in lowered
        or "not_authed" in lowered
        or "token_revoked" in lowered
        or "missing_scope" in lowered
    ):
        return "auth"
    if "server_error" in lowered or "internal server" in lowered or "bad gateway" in lowered:
        return "server"
    if "timeout" in lowered or "timed out" in lowered:
        return "timeout"
    return default
