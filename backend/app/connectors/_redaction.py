from __future__ import annotations

from collections.abc import Iterable
import re
from typing import Any


REDACTED_CONNECTOR_SECRET = "[REDACTED_CONNECTOR_SECRET]"

_AUTH_CREDENTIAL_RE = re.compile(
    r"(?i)(authorization[\"']?\s*[:=]\s*[\"']?(?:Bearer|Token|Basic)\s+)([^\s,'\"}]+)"
)
_SECRET_FIELD_RE = re.compile(
    r"(?i)((?:access[_-]?token|api[_-]?key|api[_-]?token|token|zotero-api-key)"
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
