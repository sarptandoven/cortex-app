from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen


READWISE_SOURCE = "readwise"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://readwise.io/api/v2"
MAX_RECORDS = 500


RequestJSON = Callable[[str, dict[str, str]], Any]


@dataclass(frozen=True)
class ReadwiseSyncRecord:
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
class ReadwiseSync:
    records: list[ReadwiseSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_page_cursor: str | None
    errors: list[dict[str, Any]]
    api_base_url: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": READWISE_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_page_cursor": self.next_page_cursor,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
        }


def fetch_readwise_records(
    *,
    token: str,
    since: str | None = None,
    page_cursor: str | None = None,
    max_records: int = 100,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> ReadwiseSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("Readwise token is required")
    capped_max = max(1, min(int(max_records or 100), MAX_RECORDS))
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    headers = {
        "Accept": "application/json",
        "Authorization": f"Token {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
    }

    records: list[ReadwiseSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_page_cursor = str(page_cursor or "").strip() or None

    while len(records) < capped_max:
        query: dict[str, str] = {}
        if since and not next_page_cursor:
            query["updatedAfter"] = str(since)
        if next_page_cursor:
            query["pageCursor"] = next_page_cursor
        url = f"{base_url}/export/"
        if query:
            url = f"{url}?{urlencode(query)}"
        try:
            payload = requester(url, headers)
        except Exception as exc:
            errors.append({"error": str(exc)})
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Readwise export response was not an object"})
            break
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for book in results:
            if not isinstance(book, dict):
                continue
            highlights = book.get("highlights") if isinstance(book.get("highlights"), list) else []
            records_found += len(highlights)
            for highlight in highlights:
                if not isinstance(highlight, dict):
                    continue
                record = _record_from_highlight(book, highlight)
                if record is None:
                    continue
                high_water_mark = _max_iso(high_water_mark, record.captured_at)
                records.append(record)
                if len(records) >= capped_max:
                    break
            if len(records) >= capped_max:
                break
        next_page_cursor = str(payload.get("nextPageCursor") or "").strip() or None
        if not next_page_cursor or len(records) >= capped_max:
            break

    return ReadwiseSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=next_page_cursor or high_water_mark or since,
        next_page_cursor=next_page_cursor,
        errors=errors,
        api_base_url=base_url,
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with urlopen(request, timeout=30) as response:  # noqa: S310 - trusted Readwise API URL by default.
        return json.loads(response.read().decode("utf-8"))


def _record_from_highlight(book: dict[str, Any], highlight: dict[str, Any]) -> ReadwiseSyncRecord | None:
    text = _clean_text(highlight.get("text"))
    highlight_id = _text(highlight.get("id")) or _text(highlight.get("highlight_id"))
    if not text or not highlight_id:
        return None
    book_id = _text(book.get("user_book_id") or book.get("id"))
    book_title = _clean_text(book.get("title")) or "Untitled"
    author = _clean_text(book.get("author"))
    category = _clean_text(book.get("category"))
    source = _clean_text(book.get("source"))
    note = _clean_text(highlight.get("note"))
    highlighted_at = _text(highlight.get("highlighted_at"))
    updated_at = _text(highlight.get("updated")) or _text(book.get("updated"))
    captured_at = updated_at or highlighted_at or _text(book.get("last_highlight_at"))
    source_url = _source_url(book, highlight, book_id, highlight_id)
    tags = _tags(highlight.get("tags") or book.get("tags"))
    location = _text(highlight.get("location"))
    location_type = _clean_text(highlight.get("location_type"))

    lines = [
        "Source: Readwise",
        f"Book: {book_title}",
        f"Highlight ID: {highlight_id}",
    ]
    if author:
        lines.append(f"Author: {author}")
    if category:
        lines.append(f"Category: {category}")
    if source:
        lines.append(f"Readwise Source: {source}")
    if highlighted_at:
        lines.append(f"Highlighted: {highlighted_at}")
    if updated_at:
        lines.append(f"Updated: {updated_at}")
    if location:
        suffix = f" ({location_type})" if location_type else ""
        lines.append(f"Location: {location}{suffix}")
    if tags:
        lines.append(f"Tags: {', '.join(tags)}")
    lines.append(f"URL: {source_url}")
    lines.extend(["", "Highlight:", text])
    if note:
        lines.extend(["", "Note:", note])

    return ReadwiseSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Readwise {book_title}: {_title_excerpt(text)}"[:200],
        source_url=source_url[:500],
        external_id=f"readwise:highlight:{highlight_id}"[:240],
        captured_at=captured_at,
        metadata={
            "connector": READWISE_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "book_id": book_id,
            "book_title": book_title,
            "author": author,
            "category": category,
            "readwise_source": source,
            "highlight_id": highlight_id,
            "highlighted_at": highlighted_at,
            "updated_at": updated_at,
            "tags": tags,
            "url": source_url,
            "source_type": "readwise_highlight",
            "source_quality": "canonical",
        },
    )


def _source_url(book: dict[str, Any], highlight: dict[str, Any], book_id: str, highlight_id: str) -> str:
    for value in (highlight.get("url"), book.get("source_url")):
        text = _text(value)
        if text.startswith("http://") or text.startswith("https://"):
            return text
    if book_id:
        return f"readwise://book/{book_id}/highlight/{highlight_id}"
    return f"readwise://highlight/{highlight_id}"


def _tags(value: Any) -> list[str]:
    tags: list[str] = []
    if not isinstance(value, list):
        return tags
    for item in value:
        if isinstance(item, dict):
            text = _clean_text(item.get("name"))
        else:
            text = _clean_text(item)
        if text and text not in tags:
            tags.append(text)
    return tags


def _clean_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", _text(value)).strip()
    return text


def _text(value: Any) -> str:
    return str(value or "").strip()


def _title_excerpt(text: str, limit: int = 80) -> str:
    cleaned = _clean_text(text)
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 1)].rstrip() + "..."


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)
