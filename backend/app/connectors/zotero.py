from __future__ import annotations

from dataclasses import dataclass
from html import unescape
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ._redaction import redact_error_message


ZOTERO_SOURCE = "zotero"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "http://localhost:23119/api"
DEFAULT_LIBRARY_TYPE = "user"
DEFAULT_LIBRARY_ID = "0"
MAX_RECORDS = 500
API_VERSION = "3"
ATTACHMENT_ITEM_TYPE = "attachment"


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class ZoteroSyncRecord:
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
class ZoteroSync:
    records: list[ZoteroSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_cursor: str | None
    errors: list[dict[str, Any]]
    api_base_url: str
    library_type: str
    library_id: str
    include_attachments: bool

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": ZOTERO_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_cursor": self.next_cursor,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
            "library_type": self.library_type,
            "library_id": self.library_id,
            "include_attachments": self.include_attachments,
        }


def fetch_zotero_records(
    *,
    token: str | None = None,
    library_type: str = DEFAULT_LIBRARY_TYPE,
    library_id: str = DEFAULT_LIBRARY_ID,
    since: str | None = None,
    cursor: str | None = None,
    max_records: int = 100,
    include_attachments: bool = False,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> ZoteroSync:
    capped_max = max(1, min(int(max_records or 100), MAX_RECORDS))
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    normalized_library_type = _normalize_library_type(library_type)
    normalized_library_id = _normalize_library_id(library_id, normalized_library_type)
    cleaned_token = str(token or "").strip()
    headers = {
        "Accept": "application/json",
        "User-Agent": "Cortex-local-connector",
        "Zotero-API-Version": API_VERSION,
    }
    if cleaned_token:
        headers["Zotero-API-Key"] = cleaned_token

    records: list[ZoteroSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    start = _cursor_start(cursor)
    next_cursor: str | None = None
    since_version = _version_value(since)

    while len(records) < capped_max:
        per_page = min(100, capped_max - len(records))
        query: dict[str, str] = {
            "format": "json",
            "include": "data",
            "sort": "dateModified",
            "direction": "desc",
            "limit": str(per_page),
            "start": str(start),
        }
        if since_version:
            query["since"] = since_version
        url = f"{base_url}/{_library_prefix(normalized_library_type, normalized_library_id)}/items?{urlencode(query)}"
        try:
            payload = requester(url, headers)
        except Exception as exc:
            errors.append({"error": redact_error_message(exc, _request_secrets(cleaned_token, headers))})
            break
        if not isinstance(payload, list):
            errors.append({"error": "Zotero items response was not a list"})
            break
        records_found += len(payload)
        for item in payload:
            if not isinstance(item, dict):
                continue
            record = _record_from_item(
                item,
                library_type=normalized_library_type,
                library_id=normalized_library_id,
                include_attachments=include_attachments,
            )
            if record is None:
                continue
            high_water_mark = _max_version(high_water_mark, _text(record.metadata.get("version")))
            records.append(record)
            if len(records) >= capped_max:
                break
        start += len(payload)
        if len(payload) < per_page:
            next_cursor = None
            break
        if len(records) >= capped_max:
            next_cursor = str(start)
            break

    return ZoteroSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=next_cursor or high_water_mark or since_version,
        next_cursor=next_cursor,
        errors=errors,
        api_base_url=base_url,
        library_type=normalized_library_type,
        library_id=normalized_library_id,
        include_attachments=bool(include_attachments),
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - trusted Zotero local/Web API URL by default.
        return json.loads(response.read().decode("utf-8"))


def _request_secrets(token: str, headers: dict[str, str]) -> list[str | None]:
    return [token, headers.get("Zotero-API-Key")]


def _record_from_item(
    item: dict[str, Any],
    *,
    library_type: str,
    library_id: str,
    include_attachments: bool,
) -> ZoteroSyncRecord | None:
    data = item.get("data") if isinstance(item.get("data"), dict) else {}
    key = _text(item.get("key")) or _text(data.get("key"))
    if not key:
        return None
    item_type = _text(data.get("itemType")) or _text(item.get("itemType")) or "item"
    if item_type == ATTACHMENT_ITEM_TYPE and not include_attachments:
        return None

    title = _title(data, key, item_type)
    version = _text(item.get("version")) or _text(data.get("version"))
    date_added = _text(data.get("dateAdded"))
    date_modified = _text(data.get("dateModified"))
    captured_at = date_modified or date_added
    source_url = _select_url(library_type, library_id, key)
    alternate_url = _alternate_url(item)
    item_url = _http_url(data.get("url"))
    parent_item = _text(data.get("parentItem"))
    tags = _tags(data.get("tags"))
    creators = _creators(data.get("creators"))
    abstract_note = _clean_text(data.get("abstractNote"))
    note_text = _html_to_text(data.get("note"))
    annotation = _annotation_summary(data)

    lines = [
        "Source: Zotero",
        f"Item: {title}",
        f"Item key: {key}",
        f"Item type: {item_type}",
    ]
    if version:
        lines.append(f"Version: {version}")
    if parent_item:
        lines.append(f"Parent item: {parent_item}")
    if creators:
        lines.append(f"Creators: {', '.join(creators)}")
    for field, label in (
        ("date", "Date"),
        ("publicationTitle", "Publication"),
        ("journalAbbreviation", "Journal abbreviation"),
        ("publisher", "Publisher"),
        ("conferenceName", "Conference"),
        ("place", "Place"),
        ("DOI", "DOI"),
        ("ISBN", "ISBN"),
    ):
        value = _clean_text(data.get(field))
        if value:
            lines.append(f"{label}: {value}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    if date_added:
        lines.append(f"Added: {date_added}")
    if date_modified:
        lines.append(f"Modified: {date_modified}")
    lines.append(f"Zotero URL: {source_url}")
    if alternate_url:
        lines.append(f"Alternate URL: {alternate_url}")
    if item_url:
        lines.append(f"Item URL: {item_url}")
    if abstract_note:
        lines.extend(["", "Abstract:", abstract_note])
    if note_text:
        lines.extend(["", "Note:", note_text])
    if annotation:
        lines.extend(["", "Annotation:", annotation])
    if item_type == ATTACHMENT_ITEM_TYPE:
        lines.append("Attachment content was not imported.")

    record_kind = "annotation" if item_type == "annotation" else "note" if item_type == "note" else "item"
    return ZoteroSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Zotero {record_kind}: {title}"[:200],
        source_url=source_url[:500],
        external_id=f"zotero:{library_type}:{library_id}:item:{key}"[:240],
        captured_at=captured_at,
        metadata={
            "connector": ZOTERO_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "library_type": library_type,
            "library_id": library_id,
            "item_key": key,
            "item_type": item_type,
            "record_kind": record_kind,
            "version": version,
            "parent_item": parent_item,
            "tags": tags,
            "creators": creators,
            "url": source_url,
            "alternate_url": alternate_url,
            "item_url": item_url,
            "source_type": f"zotero_{record_kind}",
            "source_quality": "canonical",
            "attachment_content_imported": False,
        },
    )


def _normalize_library_type(value: str) -> str:
    normalized = str(value or DEFAULT_LIBRARY_TYPE).strip().lower()
    if normalized in {"user", "users", "library"}:
        return "user"
    if normalized in {"group", "groups"}:
        return "group"
    raise ValueError("Zotero library_type must be user or group")


def _normalize_library_id(value: str, library_type: str) -> str:
    normalized = str(value or "").strip()
    if not normalized and library_type == "user":
        return DEFAULT_LIBRARY_ID
    if not normalized:
        raise ValueError("Zotero library_id is required for group libraries")
    if not re.fullmatch(r"[0-9A-Za-z_-]+", normalized):
        raise ValueError("Zotero library_id contains unsupported characters")
    return normalized


def _library_prefix(library_type: str, library_id: str) -> str:
    if library_type == "group":
        return f"groups/{library_id}"
    return f"users/{library_id}"


def _cursor_start(value: str | None) -> int:
    text = str(value or "").strip()
    if not text:
        return 0
    try:
        parsed = int(text)
    except ValueError as exc:
        raise ValueError("Zotero cursor must be a start offset integer") from exc
    if parsed < 0:
        raise ValueError("Zotero cursor must be zero or greater")
    return parsed


def _version_value(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not re.fullmatch(r"\d+", text):
        raise ValueError("Zotero since must be a library version integer")
    return text


def _title(data: dict[str, Any], key: str, item_type: str) -> str:
    title = _clean_text(data.get("title"))
    if title:
        return title
    if item_type == "note":
        return _title_excerpt(_html_to_text(data.get("note")) or key)
    if item_type == "annotation":
        return _title_excerpt(_clean_text(data.get("annotationText")) or _clean_text(data.get("annotationComment")) or key)
    return key


def _select_url(library_type: str, library_id: str, key: str) -> str:
    if library_type == "group":
        return f"zotero://select/groups/{library_id}/items/{key}"
    return f"zotero://select/library/items/{key}"


def _alternate_url(item: dict[str, Any]) -> str:
    links = item.get("links") if isinstance(item.get("links"), dict) else {}
    alternate = links.get("alternate") if isinstance(links.get("alternate"), dict) else {}
    return _http_url(alternate.get("href"))


def _http_url(value: Any) -> str:
    text = _text(value)
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def _creators(value: Any) -> list[str]:
    creators: list[str] = []
    if not isinstance(value, list):
        return creators
    for item in value:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"))
        if not name:
            name = " ".join(part for part in [_clean_text(item.get("firstName")), _clean_text(item.get("lastName"))] if part)
        creator_type = _clean_text(item.get("creatorType"))
        label = f"{name} ({creator_type})" if name and creator_type else name
        if label and label not in creators:
            creators.append(label)
    return creators


def _tags(value: Any) -> list[str]:
    tags: list[str] = []
    if not isinstance(value, list):
        return tags
    for item in value:
        text = _clean_text(item.get("tag")) if isinstance(item, dict) else _clean_text(item)
        if text and text not in tags:
            tags.append(text)
    return tags


def _annotation_summary(data: dict[str, Any]) -> str:
    parts: list[str] = []
    for field, label in (
        ("annotationType", "Type"),
        ("annotationText", "Text"),
        ("annotationComment", "Comment"),
        ("annotationPageLabel", "Page"),
        ("annotationSortIndex", "Sort index"),
        ("annotationColor", "Color"),
    ):
        value = _clean_text(data.get(field))
        if value:
            parts.append(f"{label}: {value}")
    return "\n".join(parts)


def _html_to_text(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"(?i)<br\s*/?>", "\n", text)
    text = re.sub(r"(?i)</p\s*>", "\n", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return _clean_text(unescape(text))


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _title_excerpt(text: str, limit: int = 80) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "..."


def _max_version(left: str | None, right: str | None) -> str | None:
    if not left:
        return right or None
    if not right:
        return left
    try:
        return str(max(int(left), int(right)))
    except ValueError:
        return max(left, right)
