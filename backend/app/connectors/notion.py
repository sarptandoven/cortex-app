from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request

from ..http_security import open_same_origin
from ._redaction import connector_error_payload


NOTION_SOURCE = "notion"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://api.notion.com/v1"
DEFAULT_NOTION_VERSION = "2026-03-11"
MAX_RECORDS = 200
MAX_BLOCKS_PER_PAGE = 80
MAX_BLOCK_TREE_DEPTH = 3


RequestJSON = Callable[[str, dict[str, str], dict[str, Any] | None, str], Any]


@dataclass(frozen=True)
class NotionSyncRecord:
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
class NotionSync:
    records: list[NotionSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_cursor: str | None
    errors: list[dict[str, Any]]
    api_base_url: str
    notion_version: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": NOTION_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_cursor": self.next_cursor,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
            "notion_version": self.notion_version,
        }


def fetch_notion_records(
    *,
    token: str,
    since: str | None = None,
    cursor: str | None = None,
    max_records: int = 50,
    include_content: bool = True,
    api_base_url: str = DEFAULT_API_BASE_URL,
    notion_version: str = DEFAULT_NOTION_VERSION,
    request_json: RequestJSON | None = None,
) -> NotionSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("Notion token is required")
    capped_max = max(1, min(int(max_records or 50), MAX_RECORDS))
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    version = str(notion_version or DEFAULT_NOTION_VERSION).strip() or DEFAULT_NOTION_VERSION
    requester = request_json or _request_json
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {cleaned_token}",
        "Content-Type": "application/json",
        "Notion-Version": version,
        "User-Agent": "Cortex-local-connector",
    }

    records: list[NotionSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_cursor = str(cursor or "").strip() or None

    while len(records) < capped_max:
        body: dict[str, Any] = {
            "page_size": min(100, capped_max - len(records)),
            "filter": {"value": "page", "property": "object"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
        }
        if next_cursor:
            body["start_cursor"] = next_cursor
        try:
            payload = requester(f"{base_url}/search", headers, body, "POST")
        except Exception as exc:
            errors.append(connector_error_payload(exc, _request_secrets(cleaned_token, headers)))
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Notion search response was not an object"})
            break
        if payload.get("object") == "error":
            errors.append(connector_error_payload(_NotionErrorObject(payload), _request_secrets(cleaned_token, headers)))
            break
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        records_found += len(results)
        for page in results:
            if not isinstance(page, dict) or page.get("object") != "page":
                continue
            if bool(page.get("archived")) or bool(page.get("in_trash")):
                continue
            last_edited_time = _text(page.get("last_edited_time"))
            if since and last_edited_time and last_edited_time < since:
                continue
            blocks: list[dict[str, Any]] = []
            if include_content:
                blocks = _fetch_page_blocks(base_url, headers, page, requester, errors, _request_secrets(cleaned_token, headers))
            record = _record_from_page(page, blocks)
            if record is None:
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break
        next_cursor = str(payload.get("next_cursor") or "").strip() or None
        if not payload.get("has_more") or not next_cursor or len(records) >= capped_max:
            break

    return NotionSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=high_water_mark or since,
        next_cursor=next_cursor,
        errors=errors,
        api_base_url=base_url,
        notion_version=version,
    )


def _request_json(url: str, headers: dict[str, str], body: dict[str, Any] | None, method: str) -> Any:
    data = json.dumps(body or {}).encode("utf-8") if body is not None else None
    request = Request(url, data=data, headers=headers, method=method)
    with open_same_origin(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


class _NotionErrorObject(Exception):
    """Notion error-object response ({"object": "error", ...}) shaped for connector_error_payload.

    Carries the numeric "status" field as a `status` attribute so the shared error
    helper categorizes it (401/403 auth, 429 rate_limited, 5xx server, other 4xx
    client); without a status, the "code"-prefixed message drives classification.
    """

    def __init__(self, payload: dict[str, Any]) -> None:
        code = _clean_text(payload.get("code"))
        message = _clean_text(payload.get("message")) or "Notion API returned an error"
        super().__init__(f"{code}: {message}" if code else message)
        try:
            status = int(payload.get("status"))
        except (TypeError, ValueError):
            status = 0
        if status > 0:
            self.status = status


def _fetch_page_blocks(
    base_url: str,
    headers: dict[str, str],
    page: dict[str, Any],
    requester: RequestJSON,
    errors: list[dict[str, Any]],
    secrets: list[str | None],
) -> list[dict[str, Any]]:
    page_id = _text(page.get("id"))
    if not page_id:
        return []
    blocks: list[dict[str, Any]] = []
    _append_child_blocks(
        base_url,
        headers,
        parent_id=page_id,
        requester=requester,
        errors=errors,
        secrets=secrets,
        blocks=blocks,
        depth=0,
        seen_parent_ids=set(),
    )
    return blocks


def _append_child_blocks(
    base_url: str,
    headers: dict[str, str],
    *,
    parent_id: str,
    requester: RequestJSON,
    errors: list[dict[str, Any]],
    secrets: list[str | None],
    blocks: list[dict[str, Any]],
    depth: int,
    seen_parent_ids: set[str],
) -> None:
    if len(blocks) >= MAX_BLOCKS_PER_PAGE or depth > MAX_BLOCK_TREE_DEPTH:
        return
    if parent_id in seen_parent_ids:
        errors.append({"block_id": parent_id, "error": "Notion block tree contained a repeated parent id"})
        return
    seen_parent_ids.add(parent_id)
    cursor: str | None = None
    while len(blocks) < MAX_BLOCKS_PER_PAGE:
        query = {"page_size": str(min(100, MAX_BLOCKS_PER_PAGE - len(blocks)))}
        if cursor:
            query["start_cursor"] = cursor
        url = f"{base_url}/blocks/{parent_id}/children?{urlencode(query)}"
        try:
            payload = requester(url, headers, None, "GET")
        except Exception as exc:
            errors.append({"block_id": parent_id, **connector_error_payload(exc, secrets)})
            break
        if not isinstance(payload, dict):
            errors.append({"block_id": parent_id, "error": "Notion block children response was not an object"})
            break
        if payload.get("object") == "error":
            errors.append({"block_id": parent_id, **connector_error_payload(_NotionErrorObject(payload), secrets)})
            break
        results = payload.get("results") if isinstance(payload.get("results"), list) else []
        for item in results:
            if not isinstance(item, dict):
                continue
            block = {**item, "_cortex_depth": depth}
            blocks.append(block)
            block_id = _text(item.get("id"))
            if bool(item.get("has_children")) and block_id and len(blocks) < MAX_BLOCKS_PER_PAGE:
                _append_child_blocks(
                    base_url,
                    headers,
                    parent_id=block_id,
                    requester=requester,
                    errors=errors,
                    secrets=secrets,
                    blocks=blocks,
                    depth=depth + 1,
                    seen_parent_ids=seen_parent_ids,
                )
            if len(blocks) >= MAX_BLOCKS_PER_PAGE:
                break
        cursor = str(payload.get("next_cursor") or "").strip() or None
        if not payload.get("has_more") or not cursor:
            break
    seen_parent_ids.discard(parent_id)


def _record_from_page(page: dict[str, Any], blocks: list[dict[str, Any]]) -> NotionSyncRecord | None:
    page_id = _text(page.get("id"))
    if not page_id:
        return None
    title = _page_title(page) or "Untitled Notion page"
    source_url = _text(page.get("url")) or f"notion://page/{page_id}"
    created_time = _text(page.get("created_time"))
    last_edited_time = _text(page.get("last_edited_time"))
    archived = bool(page.get("archived"))
    in_trash = bool(page.get("in_trash"))
    parent = page.get("parent") if isinstance(page.get("parent"), dict) else {}
    properties = _properties_summary(page.get("properties"))
    block_lines = [_block_text(block) for block in blocks]
    block_lines = [line for line in block_lines if line]

    lines = [
        "Source: Notion",
        f"Page: {title}",
        f"Page ID: {page_id}",
    ]
    if created_time:
        lines.append(f"Created: {created_time}")
    if last_edited_time:
        lines.append(f"Last edited: {last_edited_time}")
    if archived or in_trash:
        lines.append(f"Archived: {archived or in_trash}")
    if parent:
        lines.append(f"Parent: {_parent_label(parent)}")
    if properties:
        lines.append(f"Properties: {properties}")
    lines.append(f"URL: {source_url}")
    if block_lines:
        lines.extend(["", "Content:", *block_lines])

    return NotionSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Notion: {title}"[:200],
        source_url=source_url[:500],
        external_id=f"notion:page:{page_id}"[:240],
        captured_at=last_edited_time or created_time,
        metadata={
            "connector": NOTION_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "page_id": page_id,
            "title": title,
            "archived": archived,
            "in_trash": in_trash,
            "parent": parent,
            "url": source_url,
            "source_type": "notion_page",
            "source_quality": "canonical",
        },
    )


def _request_secrets(token: str, headers: dict[str, str]) -> list[str | None]:
    return [token, headers.get("Authorization")]


def _page_title(page: dict[str, Any]) -> str:
    properties = page.get("properties") if isinstance(page.get("properties"), dict) else {}
    for value in properties.values():
        if isinstance(value, dict) and value.get("type") == "title":
            return _rich_text(value.get("title"))
    return ""


def _properties_summary(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts: list[str] = []
    for name, prop in value.items():
        if not isinstance(prop, dict) or prop.get("type") == "title":
            continue
        text = _property_value(prop)
        if text:
            parts.append(f"{name}: {text}")
        if len(parts) >= 8:
            break
    return "; ".join(parts)


def _property_value(prop: dict[str, Any]) -> str:
    prop_type = _text(prop.get("type"))
    value = prop.get(prop_type)
    if prop_type in {"rich_text", "title"}:
        return _rich_text(value)
    if prop_type in {"select", "status"} and isinstance(value, dict):
        return _clean_text(value.get("name"))
    if prop_type == "multi_select" and isinstance(value, list):
        return ", ".join(_clean_text(item.get("name")) for item in value if isinstance(item, dict) and _clean_text(item.get("name")))
    if prop_type == "date" and isinstance(value, dict):
        start = _text(value.get("start"))
        end = _text(value.get("end"))
        return f"{start} to {end}" if start and end else start
    if prop_type in {"checkbox", "number", "url", "email", "phone_number"}:
        return _text(value)
    return ""


def _block_text(block: dict[str, Any]) -> str:
    block_type = _text(block.get("type"))
    value = block.get(block_type) if isinstance(block.get(block_type), dict) else {}
    text = _rich_text(value.get("rich_text") if isinstance(value, dict) else None)
    if not text and block_type == "child_page" and isinstance(value, dict):
        text = _clean_text(value.get("title"))
    if not text and block_type == "child_database" and isinstance(value, dict):
        text = _clean_text(value.get("title"))
    if not text and block_type in {"bookmark", "embed", "link_preview"} and isinstance(value, dict):
        text = _clean_text(value.get("url"))
    if not text:
        return ""
    label = block_type.replace("_", " ").title()
    depth = 0
    try:
        depth = max(0, int(block.get("_cortex_depth") or 0))
    except (TypeError, ValueError):
        depth = 0
    prefix = "  " * min(depth, MAX_BLOCK_TREE_DEPTH)
    return f"{prefix}{label}: {text}"


def _rich_text(value: Any) -> str:
    parts: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            text = ((item.get("text") or {}).get("content") if isinstance(item.get("text"), dict) else None) or item.get("plain_text")
            cleaned = _clean_text(text)
            if cleaned:
                parts.append(cleaned)
    return " ".join(parts).strip()


def _parent_label(parent: dict[str, Any]) -> str:
    parent_type = _text(parent.get("type"))
    value = _text(parent.get(parent_type)) if parent_type else ""
    return f"{parent_type}:{value}" if value else parent_type


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
