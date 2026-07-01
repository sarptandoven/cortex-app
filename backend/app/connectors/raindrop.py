from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


RAINDROP_SOURCE = "raindrop"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://api.raindrop.io/rest/v1"
MAX_RECORDS = 500
DEFAULT_COLLECTION_ID = "0"


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class RaindropSyncRecord:
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
class RaindropSync:
    records: list[RaindropSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_page: str | None
    errors: list[dict[str, Any]]
    api_base_url: str
    collection_id: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": RAINDROP_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_page": self.next_page,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
            "collection_id": self.collection_id,
        }


def fetch_raindrop_records(
    *,
    token: str,
    collection_id: str | int = DEFAULT_COLLECTION_ID,
    since: str | None = None,
    page: str | int | None = None,
    max_records: int = 100,
    include_highlights: bool = True,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> RaindropSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("Raindrop token is required")
    capped_max = max(1, min(int(max_records or 100), MAX_RECORDS))
    normalized_collection_id = _collection_id(collection_id)
    current_page = _page(page)
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
    }

    records: list[RaindropSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_page: str | None = None
    since_normalized = _text(since)

    while len(records) < capped_max:
        per_page = min(50, capped_max - len(records))
        query = {
            "page": str(current_page),
            "perpage": str(per_page),
            "sort": "-lastUpdate",
        }
        url = f"{base_url}/raindrops/{normalized_collection_id}?{urlencode(query)}"
        try:
            payload = requester(url, headers)
        except Exception as exc:
            errors.append({"error": _safe_error(exc, cleaned_token)})
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Raindrop response was not an object"})
            break
        if payload.get("result") is False:
            error_text = _clean_text(payload.get("error") or payload.get("message")) or "Raindrop API returned an error"
            errors.append({"error": error_text})
            break
        items = payload.get("items") if isinstance(payload.get("items"), list) else []
        records_found += len(items)
        for item in items:
            if not isinstance(item, dict):
                continue
            record = _record_from_item(item, normalized_collection_id, include_highlights=include_highlights)
            if record is None:
                continue
            if since_normalized and record.captured_at and record.captured_at <= since_normalized:
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break
        if len(items) < per_page or len(records) >= capped_max:
            next_page = str(current_page + 1) if len(items) == per_page and len(records) >= capped_max else None
            break
        current_page += 1
        next_page = str(current_page)

    return RaindropSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=next_page or high_water_mark or since_normalized,
        next_page=next_page,
        errors=errors,
        api_base_url=base_url,
        collection_id=normalized_collection_id,
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - trusted Raindrop API URL by default.
        return json.loads(response.read().decode("utf-8"))


def _record_from_item(item: dict[str, Any], collection_id: str, *, include_highlights: bool) -> RaindropSyncRecord | None:
    item_id = _text(item.get("_id") or item.get("id"))
    if not item_id:
        return None
    title = _clean_text(item.get("title")) or _clean_text(item.get("link")) or f"Raindrop {item_id}"
    link = _http_url(item.get("link"))
    source_url = link or f"raindrop://raindrop/{item_id}"
    excerpt = _clean_text(item.get("excerpt"))
    note = _clean_text(item.get("note"))
    domain = _clean_text(item.get("domain"))
    tags = _tags(item.get("tags"))
    collection = item.get("collection") if isinstance(item.get("collection"), dict) else {}
    item_collection_id = _text(collection.get("$id")) or collection_id
    created = _text(item.get("created"))
    updated = _text(item.get("lastUpdate") or item.get("updated"))
    captured_at = updated or created
    important = bool(item.get("important"))
    highlights = _highlights(item.get("highlights")) if include_highlights else []

    lines = [
        "Source: Raindrop",
        f"Bookmark: {title}",
        f"Raindrop ID: {item_id}",
    ]
    if link:
        lines.append(f"Link: {link}")
    if domain:
        lines.append(f"Domain: {domain}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    if important:
        lines.append("Important: true")
    if created:
        lines.append(f"Created: {created}")
    if updated:
        lines.append(f"Updated: {updated}")
    lines.append(f"URL: {source_url}")
    if excerpt:
        lines.extend(["", "Excerpt:", excerpt])
    if note:
        lines.extend(["", "Note:", note])
    for index, highlight in enumerate(highlights, start=1):
        lines.extend(["", f"Highlight {index}:", highlight["text"]])
        if highlight.get("note"):
            lines.append(f"Highlight note: {highlight['note']}")

    return RaindropSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Raindrop: {title}"[:200],
        source_url=source_url[:500],
        external_id=f"raindrop:item:{item_id}"[:240],
        captured_at=captured_at,
        metadata={
            "connector": RAINDROP_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "raindrop_id": item_id,
            "collection_id": item_collection_id,
            "title": title,
            "domain": domain,
            "tags": tags,
            "important": important,
            "created": created,
            "updated": updated,
            "highlight_count": len(highlights),
            "url": source_url,
            "source_type": "raindrop_bookmark",
            "source_quality": "canonical",
        },
    )


def _highlights(value: Any) -> list[dict[str, str]]:
    highlights: list[dict[str, str]] = []
    if not isinstance(value, list):
        return highlights
    for item in value:
        if not isinstance(item, dict):
            continue
        text = _clean_text(item.get("text") or item.get("content"))
        if not text:
            continue
        note = _clean_text(item.get("note"))
        highlights.append({"text": text, "note": note})
    return highlights


def _tags(value: Any) -> list[str]:
    tags: list[str] = []
    if not isinstance(value, list):
        return tags
    for item in value:
        text = _clean_text(item)
        if text and text not in tags:
            tags.append(text)
    return tags


def _collection_id(value: str | int) -> str:
    text = _text(value)
    return text or DEFAULT_COLLECTION_ID


def _page(value: str | int | None) -> int:
    try:
        return max(0, int(str(value or "0").strip() or "0"))
    except ValueError as exc:
        raise ValueError("page must be an integer") from exc


def _http_url(value: Any) -> str:
    text = _text(value)
    if text.startswith("http://") or text.startswith("https://"):
        return text
    return ""


def _clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", _text(value)).strip()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)


def _safe_error(exc: Exception, token: str) -> str:
    message = str(exc)
    if token:
        message = message.replace(token, "[REDACTED_RAINDROP_TOKEN]")
    return message
