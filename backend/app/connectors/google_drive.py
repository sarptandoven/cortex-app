from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from html import unescape
import json
import re
from typing import Any, Callable
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from ..config import APP_BRAND
from ._redaction import connector_error_payload


GOOGLE_DRIVE_SOURCE = "google-drive"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://www.googleapis.com/drive/v3"
MAX_RECORDS = 200
GOOGLE_DOC_MIME_TYPE = "application/vnd.google-apps.document"
DOWNLOADABLE_TEXT_MIME_TYPES = {
    "text/plain",
    "text/markdown",
    "text/csv",
    "application/json",
}


RequestValue = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class GoogleDriveSyncRecord:
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
class GoogleDriveSync:
    records: list[GoogleDriveSyncRecord]
    records_found: int
    records_returned: int
    skipped_unsupported: int
    high_water_mark: str | None
    cursor_value: str | None
    next_page_token: str | None
    errors: list[dict[str, Any]]
    api_base_url: str
    user_email: str | None
    query: str | None
    mime_types: list[str]

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": GOOGLE_DRIVE_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "skipped_unsupported": self.skipped_unsupported,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_page_token": self.next_page_token,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
            "user_email": self.user_email,
            "query": self.query,
            "mime_types": self.mime_types,
        }


def fetch_google_drive_records(
    *,
    access_token: str,
    query: str | None = None,
    mime_types: list[str] | None = None,
    since: str | None = None,
    page_token: str | None = None,
    max_records: int = 50,
    include_content: bool = True,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_value: RequestValue | None = None,
) -> GoogleDriveSync:
    cleaned_token = str(access_token or "").strip()
    if not cleaned_token:
        raise ValueError("Google Drive access token is required")
    capped_max = max(1, min(int(max_records or 50), MAX_RECORDS))
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    requester = request_value or _request_value
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
    }
    secrets = [cleaned_token, headers.get("Authorization")]
    normalized_mime_types = _normalize_mime_types(mime_types)
    normalized_query = _drive_query(query, normalized_mime_types, since)

    user_email = _fetch_about_email(requester, base_url=base_url, headers=headers)
    records: list[GoogleDriveSyncRecord] = []
    records_found = 0
    skipped_unsupported = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_page_token = str(page_token or "").strip() or None

    while len(records) < capped_max:
        params: dict[str, Any] = {
            "pageSize": min(100, capped_max - len(records)),
            "fields": (
                "nextPageToken,files(id,name,mimeType,createdTime,modifiedTime,webViewLink,"
                "owners(emailAddress,displayName),lastModifyingUser(emailAddress,displayName),trashed,size)"
            ),
            "orderBy": "modifiedTime desc",
            "q": normalized_query,
        }
        if next_page_token:
            params["pageToken"] = next_page_token
        url = f"{base_url}/files?{urlencode(params)}"
        try:
            payload = requester(url, headers)
        except Exception as exc:
            errors.append(connector_error_payload(exc, secrets))
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Google Drive files response was not an object"})
            break
        files = payload.get("files") if isinstance(payload.get("files"), list) else []
        records_found += len(files)
        for item in files:
            if not isinstance(item, dict) or bool(item.get("trashed")):
                continue
            try:
                record = _record_from_file(
                    item,
                    requester=requester,
                    base_url=base_url,
                    headers=headers,
                    include_content=include_content,
                    user_email=user_email,
                )
            except Exception as exc:
                errors.append({"file_id": _text(item.get("id")), **connector_error_payload(exc, secrets)})
                continue
            if record is None:
                skipped_unsupported += 1
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break
        next_page_token = str(payload.get("nextPageToken") or "").strip() or None
        if not next_page_token or len(records) >= capped_max:
            break

    cursor_value = next_page_token or high_water_mark or since
    return GoogleDriveSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        skipped_unsupported=skipped_unsupported,
        high_water_mark=high_water_mark,
        cursor_value=cursor_value,
        next_page_token=next_page_token,
        errors=errors,
        api_base_url=base_url,
        user_email=user_email,
        query=normalized_query,
        mime_types=normalized_mime_types,
    )


def _request_value(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - trusted Google API URL by default.
        body = response.read().decode("utf-8", errors="replace")
        content_type = response.headers.get("Content-Type", "")
    if "application/json" in content_type:
        return json.loads(body)
    return body


def _fetch_about_email(requester: RequestValue, *, base_url: str, headers: dict[str, str]) -> str | None:
    try:
        payload = requester(f"{base_url}/about?fields=user(emailAddress,displayName)", headers)
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    user = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    email = _text(user.get("emailAddress"))
    return email or None


def _record_from_file(
    file: dict[str, Any],
    *,
    requester: RequestValue,
    base_url: str,
    headers: dict[str, str],
    include_content: bool,
    user_email: str | None,
) -> GoogleDriveSyncRecord | None:
    file_id = _text(file.get("id"))
    name = _text(file.get("name")) or "Untitled"
    mime_type = _text(file.get("mimeType"))
    if not file_id or not _supported_mime_type(mime_type):
        return None
    text = ""
    if include_content:
        text = _fetch_file_text(file_id, mime_type=mime_type, requester=requester, base_url=base_url, headers=headers)
    if not text:
        return None
    modified_at = _normalized_iso(file.get("modifiedTime"))
    created_at = _normalized_iso(file.get("createdTime"))
    captured_at = modified_at or created_at
    source_url = _text(file.get("webViewLink")) or f"https://drive.google.com/file/d/{quote(file_id)}/view"
    owner = _first_person(file.get("owners"))
    last_modifier = file.get("lastModifyingUser") if isinstance(file.get("lastModifyingUser"), dict) else {}
    author_role = "unknown"
    if user_email:
        owner_email = _text(owner.get("emailAddress")).lower()
        modifier_email = _text(last_modifier.get("emailAddress")).lower()
        author_role = "user" if user_email.lower() in {owner_email, modifier_email} else "external"
    lines = [
        "Source: Docs",
        f"File: {name}",
        f"Modified: {modified_at}" if modified_at else "",
        f"URL: {source_url}",
        "",
        "Content:",
        _truncate_body(text),
    ]
    metadata = {
        "connector": GOOGLE_DRIVE_SOURCE,
        "connector_version": CONNECTOR_VERSION,
        "file_id": file_id,
        "document": name,
        "mime_type": mime_type,
        "url": source_url,
        "source_type": "document",
        "source_quality": "canonical",
        "owner_email": _text(owner.get("emailAddress")),
        "owner_name": _text(owner.get("displayName")),
        "last_modifier_email": _text(last_modifier.get("emailAddress")),
        "last_modifier_name": _text(last_modifier.get("displayName")),
        "author_role": author_role,
        "created_at": created_at,
        "modified_at": modified_at,
    }
    if user_email:
        metadata["user_email"] = user_email
    return GoogleDriveSyncRecord(
        content="\n".join(line for line in lines if line is not None).strip(),
        title=f"Google Drive: {name}"[:200],
        source_url=source_url,
        external_id=f"google-drive:file:{file_id}",
        captured_at=captured_at,
        metadata={key: value for key, value in metadata.items() if value not in (None, "", [])},
    )


def _fetch_file_text(
    file_id: str,
    *,
    mime_type: str,
    requester: RequestValue,
    base_url: str,
    headers: dict[str, str],
) -> str:
    if mime_type == GOOGLE_DOC_MIME_TYPE:
        url = f"{base_url}/files/{quote(file_id)}/export?mimeType=text%2Fplain"
    else:
        url = f"{base_url}/files/{quote(file_id)}?alt=media"
    payload = requester(url, {**headers, "Accept": "text/plain"})
    if isinstance(payload, bytes):
        return _clean_text(payload.decode("utf-8", errors="replace"))
    if isinstance(payload, str):
        return _clean_text(_strip_html(payload) if "<" in payload and ">" in payload else payload)
    return ""


def _drive_query(query: str | None, mime_types: list[str], since: str | None) -> str:
    parts = ["trashed = false"]
    if query:
        parts.append(f"({str(query).strip()[:500]})")
    if mime_types:
        mime_filter = " or ".join(f"mimeType = '{mime}'" for mime in mime_types)
        parts.append(f"({mime_filter})")
    since_value = _normalized_iso(since)
    if since_value:
        parts.append(f"modifiedTime > '{since_value}'")
    return " and ".join(parts)


def _normalize_mime_types(value: list[str] | None) -> list[str]:
    candidates = value or [GOOGLE_DOC_MIME_TYPE, "text/plain", "text/markdown", "application/json"]
    result: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        mime = _text(item).lower()[:120]
        if not mime or mime in seen:
            continue
        result.append(mime)
        seen.add(mime)
        if len(result) >= 20:
            break
    return result


def _supported_mime_type(value: str) -> bool:
    mime = _text(value).lower()
    return mime == GOOGLE_DOC_MIME_TYPE or mime in DOWNLOADABLE_TEXT_MIME_TYPES


def _first_person(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return {}


def _strip_html(value: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?</\1>", " ", value)
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return unescape(text)


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
    return f"{text[:limit].rstrip()}\n[truncated by {APP_BRAND} Google Drive connector]"


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def _text(value: Any) -> str:
    return str(value or "").strip()
