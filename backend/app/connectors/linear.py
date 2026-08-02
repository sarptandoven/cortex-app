from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.request import Request

from ..http_security import open_same_origin
from ._redaction import classify_error_message, connector_error_payload, redact_error_message


LINEAR_SOURCE = "linear"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_URL = "https://api.linear.app/graphql"
MAX_RECORDS = 500


RequestJSON = Callable[[str, dict[str, str], dict[str, Any]], Any]


LINEAR_ISSUES_QUERY = """
query CortexLinearIssues($first: Int!, $after: String) {
  issues(first: $first, after: $after, includeArchived: true) {
    pageInfo {
      hasNextPage
      endCursor
    }
    nodes {
      id
      identifier
      title
      description
      url
      priority
      priorityLabel
      createdAt
      updatedAt
      archivedAt
      completedAt
      canceledAt
      state { name type }
      team { key name }
      project { name url }
      assignee { name email }
      creator { name email }
      labels { nodes { name } }
    }
  }
}
"""


@dataclass(frozen=True)
class LinearSyncRecord:
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
class LinearSync:
    records: list[LinearSyncRecord]
    records_found: int
    records_returned: int
    high_water_mark: str | None
    cursor_value: str | None
    next_cursor: str | None
    errors: list[dict[str, Any]]
    api_url: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": LINEAR_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "next_cursor": self.next_cursor,
            "errors": self.errors,
            "api_url": self.api_url,
        }


def fetch_linear_records(
    *,
    token: str,
    since: str | None = None,
    cursor: str | None = None,
    max_records: int = 100,
    api_url: str = DEFAULT_API_URL,
    request_json: RequestJSON | None = None,
) -> LinearSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("Linear token is required")
    capped_max = max(1, min(int(max_records or 100), MAX_RECORDS))
    requester = request_json or _request_json
    endpoint = str(api_url or DEFAULT_API_URL).strip()
    headers = {
        "Accept": "application/json",
        "Authorization": cleaned_token,
        "Content-Type": "application/json",
        "User-Agent": "Cortex-local-connector",
    }

    records: list[LinearSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    next_cursor = str(cursor or "").strip() or None

    while len(records) < capped_max:
        variables = {"first": min(50, capped_max - len(records)), "after": next_cursor}
        try:
            payload = requester(endpoint, headers, {"query": LINEAR_ISSUES_QUERY, "variables": variables})
        except Exception as exc:
            errors.append(connector_error_payload(exc, [cleaned_token, headers.get("Authorization")]))
            break
        if not isinstance(payload, dict):
            errors.append({"error": "Linear GraphQL response was not an object"})
            break
        if payload.get("errors"):
            errors.extend(_graphql_errors(payload.get("errors"), [cleaned_token, headers.get("Authorization")]))
            break
        issues = ((payload.get("data") or {}).get("issues") or {}) if isinstance(payload.get("data"), dict) else {}
        nodes = issues.get("nodes") if isinstance(issues.get("nodes"), list) else []
        records_found += len(nodes)
        for issue in nodes:
            if not isinstance(issue, dict):
                continue
            record = _record_from_issue(issue)
            if record is None:
                continue
            if since and record.captured_at and record.captured_at < since:
                continue
            high_water_mark = _max_iso(high_water_mark, record.captured_at)
            records.append(record)
            if len(records) >= capped_max:
                break
        page_info = issues.get("pageInfo") if isinstance(issues.get("pageInfo"), dict) else {}
        next_cursor = str(page_info.get("endCursor") or "").strip() or None
        if not page_info.get("hasNextPage") or not next_cursor or len(records) >= capped_max:
            break

    return LinearSync(
        records=records,
        records_found=records_found,
        records_returned=len(records),
        high_water_mark=high_water_mark,
        cursor_value=next_cursor or high_water_mark or since,
        next_cursor=next_cursor,
        errors=errors,
        api_url=endpoint,
    )


def _request_json(url: str, headers: dict[str, str], body: dict[str, Any]) -> Any:
    request = Request(url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    with open_same_origin(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _record_from_issue(issue: dict[str, Any]) -> LinearSyncRecord | None:
    issue_id = _text(issue.get("id"))
    identifier = _text(issue.get("identifier"))
    title = _clean_text(issue.get("title"))
    if not issue_id or not title:
        return None
    updated_at = _text(issue.get("updatedAt"))
    created_at = _text(issue.get("createdAt"))
    source_url = _source_url(issue, identifier, issue_id)
    team = issue.get("team") if isinstance(issue.get("team"), dict) else {}
    state = issue.get("state") if isinstance(issue.get("state"), dict) else {}
    project = issue.get("project") if isinstance(issue.get("project"), dict) else {}
    assignee = issue.get("assignee") if isinstance(issue.get("assignee"), dict) else {}
    creator = issue.get("creator") if isinstance(issue.get("creator"), dict) else {}
    labels = _labels(issue.get("labels"))
    description = _clean_markdown(issue.get("description"))

    lines = [
        "Source: Linear",
        f"Issue: {identifier or issue_id}",
        f"Title: {title}",
    ]
    if team:
        lines.append(f"Team: {_clean_text(team.get('name')) or _text(team.get('key'))}")
    if state:
        lines.append(f"State: {_clean_text(state.get('name'))}")
    if project:
        lines.append(f"Project: {_clean_text(project.get('name'))}")
    if assignee:
        lines.append(f"Assignee: {_clean_text(assignee.get('name'))}")
    if creator:
        lines.append(f"Creator: {_clean_text(creator.get('name'))}")
    priority = _clean_text(issue.get("priorityLabel")) or _text(issue.get("priority"))
    if priority:
        lines.append(f"Priority: {priority}")
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if created_at:
        lines.append(f"Created: {created_at}")
    if updated_at:
        lines.append(f"Updated: {updated_at}")
    for field, label in (("completedAt", "Completed"), ("canceledAt", "Canceled"), ("archivedAt", "Archived")):
        value = _text(issue.get(field))
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"URL: {source_url}")
    if description:
        lines.extend(["", "Description:", description])

    return LinearSyncRecord(
        content="\n".join(lines).strip(),
        title=f"Linear {identifier or issue_id}: {title}"[:200],
        source_url=source_url[:500],
        external_id=f"linear:issue:{issue_id}"[:240],
        captured_at=updated_at or created_at,
        metadata={
            "connector": LINEAR_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "issue_id": issue_id,
            "identifier": identifier,
            "team": _clean_text(team.get("name")) or _text(team.get("key")),
            "team_key": _text(team.get("key")),
            "state": _clean_text(state.get("name")),
            "state_type": _text(state.get("type")),
            "project": _clean_text(project.get("name")),
            "assignee": _clean_text(assignee.get("name")),
            "creator": _clean_text(creator.get("name")),
            "labels": labels,
            "url": source_url,
            "source_type": "linear_issue",
            "source_quality": "canonical",
        },
    )


def _source_url(issue: dict[str, Any], identifier: str, issue_id: str) -> str:
    url = _text(issue.get("url"))
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if identifier:
        return f"linear://issue/{identifier}"
    return f"linear://issue/{issue_id}"


def _labels(value: Any) -> list[str]:
    labels = []
    nodes = value.get("nodes") if isinstance(value, dict) and isinstance(value.get("nodes"), list) else []
    for item in nodes:
        if isinstance(item, dict):
            label = _clean_text(item.get("name"))
            if label and label not in labels:
                labels.append(label)
    return labels


def _graphql_errors(value: Any, secrets: list[str | None] | None = None) -> list[dict[str, Any]]:
    errors = []
    for item in value or []:
        if isinstance(item, dict):
            message = _clean_text(redact_error_message(item.get("message"), secrets or [])) or "Linear GraphQL error"
            errors.append({"error": message, "category": _graphql_error_category(item, message)})
        else:
            message = _clean_text(redact_error_message(item, secrets or [])) or "Linear GraphQL error"
            errors.append({"error": message, "category": classify_error_message(message, default="client")})
    return errors or [{"error": "Linear GraphQL error", "category": "client"}]


def _graphql_error_category(item: dict[str, Any], message: str) -> str:
    extensions = item.get("extensions") if isinstance(item.get("extensions"), dict) else {}
    code = str(extensions.get("code") or "").strip().upper()
    if code in {"AUTHENTICATION_ERROR", "FORBIDDEN", "UNAUTHENTICATED", "ACCESS_DENIED"}:
        return "auth"
    if code in {"RATELIMITED", "RATE_LIMITED"}:
        return "rate_limited"
    if code in {"INTERNAL_SERVER_ERROR", "SERVICE_UNAVAILABLE"}:
        return "server"
    return classify_error_message(message, default="client")


def _clean_markdown(value: Any) -> str:
    text = _text(value)
    text = re.sub(r"```.*?```", " ", text, flags=re.DOTALL)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


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
