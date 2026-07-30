from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
import base64
import json
import re
from typing import Any, Callable
from urllib.parse import quote, urlencode
from urllib.request import Request

from ..config import APP_BRAND
from ..http_security import open_same_origin
from ._redaction import connector_error_payload


GMAIL_SOURCE = "gmail"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://gmail.googleapis.com/gmail/v1"
MAX_RECORDS = 200


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class GmailSyncRecord:
    content: str
    title: str
    source_url: str
    external_id: str
    captured_at: str | None
    metadata: dict[str, Any]

    def to_source_account_record(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "title": self.title,
            "source_url": self.source_url,
            "external_id": self.external_id,
            "captured_at": self.captured_at,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class GmailSync:
    records: list[GmailSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_page_token: str | None
    errors: list[dict[str, Any]]
    api_base_url: str
    user_email: str | None
    query: str | None
    label_ids: list[str]

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": GMAIL_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_page_token": self.next_page_token,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
            "user_email": self.user_email,
            "query": self.query,
            "label_ids": self.label_ids,
        }


def fetch_gmail_records(
    *,
    access_token: str,
    query: str | None = None,
    label_ids: list[str] | None = None,
    since: str | None = None,
    page_token: str | None = None,
    max_records: int = 50,
    include_body: bool = True,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> GmailSync:
    cleaned_token = str(access_token or "").strip()
    if not cleaned_token:
        raise ValueError("Gmail access token is required")
    capped_max = max(1, min(int(max_records or 50), MAX_RECORDS))
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    requester = request_json or _request_json
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
    }
    secrets = [cleaned_token, headers.get("Authorization")]
    normalized_labels = _normalize_label_ids(label_ids)
    normalized_query = _gmail_query(query, since)

    user_email = _fetch_profile_email(requester, base_url=base_url, headers=headers)
    records: list[GmailSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_page_token = str(page_token or "").strip() or None

    while len(records) < capped_max:
        params: dict[str, Any] = {"maxResults": min(100, capped_max - len(records))}
        if next_page_token:
            params["pageToken"] = next_page_token
        if normalized_query:
            params["q"] = normalized_query
        if normalized_labels:
            params["labelIds"] = normalized_labels
        url = f"{base_url}/users/me/messages?{urlencode(params, doseq=True)}"
        try:
            payload = requester(url, headers)
        except Exception as exc:
            errors.append(connector_error_payload(exc, secrets))
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Gmail messages response was not an object"})
            break
        messages = payload.get("messages") if isinstance(payload.get("messages"), list) else []
        records_found += len(messages)
        for item in messages:
            message_id = str((item or {}).get("id") or "").strip() if isinstance(item, dict) else ""
            if not message_id:
                continue
            detail_url = f"{base_url}/users/me/messages/{quote(message_id)}?format=full"
            try:
                detail = requester(detail_url, headers)
            except Exception as exc:
                errors.append({"message_id": message_id, **connector_error_payload(exc, secrets)})
                continue
            if not isinstance(detail, dict):
                errors.append({"message_id": message_id, "error": "Gmail message detail response was not an object"})
                continue
            record = _record_from_message(detail, user_email=user_email, include_body=include_body)
            if record is None:
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break
        next_page_token = str(payload.get("nextPageToken") or "").strip() or None
        if not next_page_token or len(records) >= capped_max:
            break

    cursor_value = next_page_token or high_water_mark or since
    return GmailSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=cursor_value,
        next_page_token=next_page_token,
        errors=errors,
        api_base_url=base_url,
        user_email=user_email,
        query=normalized_query,
        label_ids=normalized_labels,
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with open_same_origin(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_profile_email(requester: RequestJSON, *, base_url: str, headers: dict[str, str]) -> str | None:
    try:
        payload = requester(f"{base_url}/users/me/profile", headers)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    email = _text(payload.get("emailAddress"))
    return email or None


def _record_from_message(message: dict[str, Any], *, user_email: str | None, include_body: bool) -> GmailSyncRecord | None:
    message_id = _text(message.get("id"))
    if not message_id:
        return None
    thread_id = _text(message.get("threadId"))
    label_ids = [_text(item) for item in (message.get("labelIds") if isinstance(message.get("labelIds"), list) else [])]
    payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    headers = _message_headers(payload)
    subject = headers.get("subject") or "(no subject)"
    from_header = headers.get("from") or ""
    to_header = headers.get("to") or ""
    cc_header = headers.get("cc") or ""
    date_header = headers.get("date") or ""
    captured_at = _iso_from_internal_date(message.get("internalDate")) or _email_date_to_iso(date_header)
    body = _message_body_text(payload) if include_body else ""
    snippet = _clean_text(_text(message.get("snippet")))
    body_text = body or snippet
    if not body_text:
        return None
    source_url = f"https://mail.google.com/mail/u/0/#all/{quote(message_id)}"
    author_role = "unknown"
    if user_email:
        author_role = "user" if _email_header_matches(from_header, user_email) else "external"
    lines = [
        "Source: Email",
        f"Subject: {subject}",
        f"From: {from_header}" if from_header else "From: unknown",
        f"To: {to_header}" if to_header else "To: unknown",
    ]
    if cc_header:
        lines.append(f"Cc: {cc_header}")
    if date_header:
        lines.append(f"Date: {date_header}")
    lines.extend(["", "Body:", _truncate_body(body_text)])
    metadata = {
        "connector": GMAIL_SOURCE,
        "connector_version": CONNECTOR_VERSION,
        "message_id": message_id,
        "thread_id": thread_id,
        "label_ids": [item for item in label_ids if item],
        "subject": subject,
        "from": from_header,
        "to": to_header,
        "cc": cc_header,
        "date": date_header,
        "url": source_url,
        "source_type": "email",
        "source_quality": "canonical",
        "author_role": author_role,
    }
    if user_email:
        metadata["user_email"] = user_email
    return GmailSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Gmail: {subject}"[:200],
        source_url=source_url,
        external_id=f"gmail:message:{message_id}",
        captured_at=captured_at,
        metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
    )


def _message_headers(payload: dict[str, Any]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in payload.get("headers") or []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("name")).strip().lower()
        value = _text(item.get("value")).strip()
        if name and value and name not in headers:
            headers[name] = value
    return headers


def _message_body_text(payload: dict[str, Any]) -> str:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    _append_payload_text(payload, plain_parts=plain_parts, html_parts=html_parts)
    plain_text = _clean_text("\n\n".join(plain_parts))
    if plain_text:
        return plain_text
    return _clean_text(_strip_html("\n\n".join(html_parts)))


def _append_payload_text(payload: dict[str, Any], *, plain_parts: list[str], html_parts: list[str]) -> None:
    filename = _text(payload.get("filename"))
    mime_type = _text(payload.get("mimeType")).lower()
    body = payload.get("body") if isinstance(payload.get("body"), dict) else {}
    data = _text(body.get("data"))
    if data and not filename:
        decoded = _decode_body_data(data)
        if mime_type == "text/plain":
            plain_parts.append(decoded)
        elif mime_type == "text/html":
            html_parts.append(decoded)
    for part in payload.get("parts") or []:
        if isinstance(part, dict):
            _append_payload_text(part, plain_parts=plain_parts, html_parts=html_parts)


def _decode_body_data(value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return ""
    padding = "=" * (-len(cleaned) % 4)
    try:
        return base64.urlsafe_b64decode(f"{cleaned}{padding}").decode("utf-8", errors="replace")
    except Exception:
        return ""


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(text)


def _gmail_query(query: str | None, since: str | None) -> str | None:
    parts = [str(query or "").strip()]
    since_query = _gmail_after_query(since)
    if since_query:
        parts.append(since_query)
    joined = " ".join(part for part in parts if part).strip()
    return joined[:500] or None


def _gmail_after_query(value: str | None) -> str | None:
    parsed = _parse_iso(value)
    if not parsed:
        return None
    return f"after:{parsed.strftime('%Y/%m/%d')}"


def _iso_from_internal_date(value: Any) -> str | None:
    try:
        millis = int(value)
    except (TypeError, ValueError):
        return None
    return datetime.fromtimestamp(millis / 1000, timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _email_date_to_iso(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError, OverflowError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _email_header_matches(header: str, email: str) -> bool:
    target = str(email or "").strip().lower()
    if not target:
        return False
    candidates = [item.lower() for item in re.findall(r"[\w.+-]+@[\w.-]+\.\w+", str(header or ""))]
    return target in candidates


def _normalize_label_ids(value: list[str] | None) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()
    for item in value or []:
        label = _text(item).strip()[:80]
        key = label.lower()
        if label and key not in seen:
            labels.append(label)
            seen.add(key)
        if len(labels) >= 20:
            break
    return labels


def _clean_text(value: str) -> str:
    text = unescape(str(value or ""))
    text = re.sub(r"\r\n?", "\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate_body(value: str, limit: int = 12000) -> str:
    text = _clean_text(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}\n[truncated by {APP_BRAND} Gmail connector]"


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def _text(value: Any) -> str:
    return str(value or "").strip()
