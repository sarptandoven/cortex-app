from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import re
from typing import Any, Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ._redaction import redact_error_message


OUTLOOK_SOURCE = "outlook"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://graph.microsoft.com/v1.0"
MAX_RECORDS = 200


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class OutlookSyncRecord:
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
class OutlookSync:
    records: list[OutlookSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_page_token: str | None
    errors: list[dict[str, Any]]
    api_base_url: str
    user_email: str | None
    query: str | None

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": OUTLOOK_SOURCE,
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
        }


def fetch_outlook_records(
    *,
    access_token: str,
    query: str | None = None,
    since: str | None = None,
    page_token: str | None = None,
    max_records: int = 50,
    include_body: bool = True,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> OutlookSync:
    cleaned_token = str(access_token or "").strip()
    if not cleaned_token:
        raise ValueError("Outlook access token is required")
    capped_max = max(1, min(int(max_records or 50), MAX_RECORDS))
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    requester = request_json or _request_json
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
    }
    secrets = [cleaned_token, headers.get("Authorization")]
    normalized_query = _outlook_filter(query, since)

    user_email = _fetch_profile_email(requester, base_url=base_url, headers=headers)
    records: list[OutlookSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_page_token = str(page_token or "").strip() or None

    while len(records) < capped_max:
        if next_page_token and next_page_token.startswith(("http://", "https://")):
            url = next_page_token
        else:
            params: dict[str, Any] = {
                "$top": min(100, capped_max - len(records)),
                "$orderby": "receivedDateTime desc",
                "$select": (
                    "id,conversationId,internetMessageId,subject,from,toRecipients,ccRecipients,"
                    "receivedDateTime,sentDateTime,bodyPreview,body,webLink,isDraft"
                ),
            }
            if normalized_query:
                params["$filter"] = normalized_query
            if next_page_token:
                params["$skiptoken"] = next_page_token
            url = f"{base_url}/me/messages?{urlencode(params)}"
        try:
            payload = requester(url, headers)
        except Exception as exc:
            errors.append({"error": redact_error_message(exc, secrets)})
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Outlook messages response was not an object"})
            break
        messages = payload.get("value") if isinstance(payload.get("value"), list) else []
        records_found += len(messages)
        for item in messages:
            if not isinstance(item, dict):
                continue
            record = _record_from_message(item, user_email=user_email, include_body=include_body)
            if record is None:
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break
        next_page_token = _next_page_token(payload)
        if not next_page_token or len(records) >= capped_max:
            break

    cursor_value = next_page_token or high_water_mark or since
    return OutlookSync(
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
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - trusted Microsoft Graph URL by default.
        return json.loads(response.read().decode("utf-8"))


def _fetch_profile_email(requester: RequestJSON, *, base_url: str, headers: dict[str, str]) -> str | None:
    try:
        payload = requester(f"{base_url}/me?{urlencode({'$select': 'mail,userPrincipalName,displayName'})}", headers)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    email = _text(payload.get("mail")) or _text(payload.get("userPrincipalName"))
    return email or None


def _record_from_message(message: dict[str, Any], *, user_email: str | None, include_body: bool) -> OutlookSyncRecord | None:
    message_id = _text(message.get("id"))
    if not message_id:
        return None
    subject = _text(message.get("subject")) or "(no subject)"
    conversation_id = _text(message.get("conversationId"))
    internet_message_id = _text(message.get("internetMessageId"))
    from_address = _recipient_address(message.get("from"))
    to_addresses = _recipient_addresses(message.get("toRecipients"))
    cc_addresses = _recipient_addresses(message.get("ccRecipients"))
    received_at = _normalized_iso(message.get("receivedDateTime"))
    sent_at = _normalized_iso(message.get("sentDateTime"))
    captured_at = received_at or sent_at
    body = message.get("body") if isinstance(message.get("body"), dict) else {}
    body_content = _text(body.get("content")) if include_body else ""
    body_type = _text(body.get("contentType")).lower()
    if body_content and (body_type == "html" or ("<" in body_content and ">" in body_content)):
        body_content = _strip_html(body_content)
    body_text = _clean_text(body_content or _text(message.get("bodyPreview")))
    if not body_text:
        return None
    source_url = _text(message.get("webLink")) or f"https://outlook.office.com/mail/deeplink/read/{quote(message_id)}"
    author_role = "unknown"
    if user_email:
        author_role = "user" if from_address.lower() == user_email.lower() else "external"
    lines = [
        "Source: Email",
        f"Subject: {subject}",
        f"From: {from_address}" if from_address else "From: unknown",
        f"To: {', '.join(to_addresses)}" if to_addresses else "To: unknown",
    ]
    if cc_addresses:
        lines.append(f"Cc: {', '.join(cc_addresses)}")
    if received_at:
        lines.append(f"Date: {received_at}")
    lines.extend(["", "Body:", _truncate_body(body_text)])
    metadata = {
        "connector": OUTLOOK_SOURCE,
        "connector_version": CONNECTOR_VERSION,
        "message_id": message_id,
        "conversation_id": conversation_id,
        "internet_message_id": internet_message_id,
        "subject": subject,
        "from": from_address,
        "to": to_addresses,
        "cc": cc_addresses,
        "received_at": received_at,
        "sent_at": sent_at,
        "url": source_url,
        "source_type": "email",
        "source_quality": "canonical",
        "author_role": author_role,
    }
    if user_email:
        metadata["user_email"] = user_email
    return OutlookSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Outlook: {subject}"[:200],
        source_url=source_url,
        external_id=f"outlook:message:{message_id}",
        captured_at=captured_at,
        metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
    )


def _recipient_address(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    email = value.get("emailAddress") if isinstance(value.get("emailAddress"), dict) else {}
    return _text(email.get("address"))


def _recipient_addresses(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    addresses: list[str] = []
    for item in value:
        address = _recipient_address(item)
        if address:
            addresses.append(address)
    return addresses


def _outlook_filter(query: str | None, since: str | None) -> str | None:
    parts = [str(query or "").strip()]
    since_value = _normalized_iso(since)
    if since_value:
        parts.append(f"receivedDateTime gt {since_value}")
    joined = " and ".join(f"({part})" for part in parts if part).strip()
    return joined[:1000] or None


def _next_page_token(payload: dict[str, Any]) -> str | None:
    next_link = _text(payload.get("@odata.nextLink"))
    if next_link:
        return next_link
    return _text(payload.get("@odata.deltaLink")) or None


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(text)


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
    return f"{text[:limit].rstrip()}\n[truncated by Cortex Outlook connector]"


def _normalized_iso(value: Any) -> str | None:
    text = _text(value)
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text[:80]
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def _text(value: Any) -> str:
    return str(value or "").strip()
