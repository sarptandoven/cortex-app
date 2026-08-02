from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Callable
from urllib.parse import urlencode, urlparse
from urllib.request import Request

from ..http_security import open_same_origin
from ._redaction import connector_error_payload


GITHUB_SOURCE = "github"
CONNECTOR_VERSION = "2026-07-01"
DEFAULT_API_BASE_URL = "https://api.github.com"
MAX_REPOSITORIES = 25

# OAuth Device Authorization Flow (RFC 8628). GitHub's device flow needs ONLY the public client ID —
# no client secret — so it's the secretless, broker-free "Sign in with GitHub" path for a distributed
# desktop app. Default read scopes cover the user's profile + their repos' issues/PRs (Cortex only
# ever reads). Override the scope via CORTEX_GITHUB_OAUTH_SCOPE if you want to narrow it.
GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
GITHUB_DEVICE_TOKEN_URL = "https://github.com/login/oauth/access_token"
GITHUB_DEVICE_GRANT_TYPE = "urn:ietf:params:oauth:grant-type:device_code"
DEFAULT_DEVICE_SCOPE = "read:user repo"

# Poll statuses the caller (app) drives its polling loop on.
DEVICE_POLL_PENDING = "authorization_pending"
DEVICE_POLL_SLOW_DOWN = "slow_down"
DEVICE_POLL_EXPIRED = "expired_token"
DEVICE_POLL_DENIED = "access_denied"
DEVICE_POLL_OK = "ok"


RequestJSON = Callable[[str, dict[str, str]], Any]


def _github_form_post(url: str, form: dict[str, str]) -> dict[str, Any]:
    """POST an x-www-form-urlencoded body and parse the JSON response (GitHub returns form-encoded
    unless Accept: application/json is set). Stdlib-only."""
    data = urlencode(form).encode("utf-8")
    request = Request(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"},
        method="POST",
    )
    with open_same_origin(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def github_device_start(
    client_id: str,
    scope: str = DEFAULT_DEVICE_SCOPE,
    *,
    request: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Begin the GitHub device flow. Returns the user-facing code + verification URL + poll interval.
    Client ID only — no secret."""
    cleaned = str(client_id or "").strip()
    if not cleaned:
        raise ValueError("GitHub OAuth client ID is not configured")
    requester = request or _github_form_post
    payload = requester(GITHUB_DEVICE_CODE_URL, {"client_id": cleaned, "scope": scope})
    if not isinstance(payload, dict) or not payload.get("device_code") or not payload.get("user_code"):
        detail = payload.get("error_description") if isinstance(payload, dict) else ""
        raise ValueError(f"GitHub did not start the device flow. {detail or ''}".strip())
    return {
        "device_code": payload["device_code"],
        "user_code": payload["user_code"],
        "verification_uri": payload.get("verification_uri") or "https://github.com/login/device",
        "expires_in": int(payload.get("expires_in") or 900),
        "interval": max(1, int(payload.get("interval") or 5)),
    }


def github_device_poll(
    client_id: str,
    device_code: str,
    *,
    request: Callable[[str, dict[str, str]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Poll once for the token. Returns {"status": ...} — one of ok/authorization_pending/slow_down/
    expired_token/access_denied — with "access_token"/"scope" on ok. Client ID only, no secret."""
    cleaned_id = str(client_id or "").strip()
    cleaned_code = str(device_code or "").strip()
    if not cleaned_id or not cleaned_code:
        raise ValueError("GitHub client ID and device code are required")
    requester = request or _github_form_post
    payload = requester(GITHUB_DEVICE_TOKEN_URL, {
        "client_id": cleaned_id,
        "device_code": cleaned_code,
        "grant_type": GITHUB_DEVICE_GRANT_TYPE,
    })
    if not isinstance(payload, dict):
        return {"status": "error", "detail": "Unexpected GitHub response"}
    access_token = str(payload.get("access_token") or "").strip()
    if access_token:
        return {"status": DEVICE_POLL_OK, "access_token": access_token, "scope": payload.get("scope") or ""}
    error = str(payload.get("error") or "").strip()
    if error in {DEVICE_POLL_PENDING, DEVICE_POLL_SLOW_DOWN, DEVICE_POLL_EXPIRED, DEVICE_POLL_DENIED}:
        return {"status": error}
    return {"status": "error", "detail": str(payload.get("error_description") or error or "Token exchange failed")}


@dataclass(frozen=True)
class GitHubSyncRecord:
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
class GitHubSync:
    repositories: list[str]
    records: list[GitHubSyncRecord]
    records_found: int
    records_returned: int
    comments_found: int
    comments_returned: int
    reviews_found: int
    reviews_returned: int
    review_comments_found: int
    review_comments_returned: int
    include_comments: bool
    max_comments_per_item: int
    high_water_mark: str | None
    cursor_value: str | None
    errors: list[dict[str, Any]]
    api_base_url: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": GITHUB_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "repositories": self.repositories,
            "records_found": self.records_found,
            "records_returned": self.records_returned,
            "comments_found": self.comments_found,
            "comments_returned": self.comments_returned,
            "reviews_found": self.reviews_found,
            "reviews_returned": self.reviews_returned,
            "review_comments_found": self.review_comments_found,
            "review_comments_returned": self.review_comments_returned,
            "include_comments": self.include_comments,
            "max_comments_per_item": self.max_comments_per_item,
            "high_water_mark": self.high_water_mark,
            "cursor_value": self.cursor_value,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
        }


@dataclass(frozen=True)
class GitHubRepositoryDiscovery:
    repositories: list[dict[str, Any]]
    repositories_found: int
    repositories_returned: int
    next_page: int | None
    errors: list[dict[str, Any]]
    api_base_url: str

    def to_summary(self) -> dict[str, Any]:
        return {
            "connector": GITHUB_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "repositories": self.repositories,
            "repositories_found": self.repositories_found,
            "repositories_returned": self.repositories_returned,
            "next_page": self.next_page,
            "errors": self.errors,
            "api_base_url": self.api_base_url,
        }


def discover_github_repositories(
    *,
    token: str,
    limit: int = 100,
    page: int = 1,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> GitHubRepositoryDiscovery:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("GitHub token is required")
    capped_limit = max(1, min(int(limit or 100), 100))
    current_page = max(1, int(page or 1))
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    query = {
        "affiliation": "owner,collaborator,organization_member",
        "per_page": str(capped_limit),
        "page": str(current_page),
        "sort": "updated",
        "visibility": "all",
    }
    errors: list[dict[str, Any]] = []
    try:
        payload = requester(f"{base_url}/user/repos?{urlencode(query)}", headers)
    except Exception as exc:
        return GitHubRepositoryDiscovery(
            repositories=[],
            repositories_found=0,
            repositories_returned=0,
            next_page=None,
            errors=[connector_error_payload(exc, [cleaned_token, headers.get("Authorization")])],
            api_base_url=base_url,
        )
    if not isinstance(payload, list):
        errors.append({"error": "GitHub repositories response was not a list"})
        repo_payloads: list[Any] = []
    else:
        repo_payloads = payload

    repositories: list[dict[str, Any]] = []
    for item in repo_payloads:
        if not isinstance(item, dict):
            continue
        full_name = _normalize_repository(item.get("full_name") or item.get("name") or "")
        if not full_name:
            continue
        owner = item.get("owner") if isinstance(item.get("owner"), dict) else {}
        repositories.append(
            {
                "full_name": full_name,
                "name": str(item.get("name") or full_name.split("/")[-1])[:160],
                "owner": str(owner.get("login") or full_name.split("/")[0])[:160],
                "label": full_name,
                "sync_value": full_name,
                "html_url": str(item.get("html_url") or f"https://github.com/{full_name}")[:500],
                "private": bool(item.get("private")),
                "archived": bool(item.get("archived")),
                "fork": bool(item.get("fork")),
                "pushed_at": str(item.get("pushed_at") or "")[:80] or None,
                "updated_at": str(item.get("updated_at") or "")[:80] or None,
                "permissions": _repository_permissions(item.get("permissions")),
            }
        )
    next_page = current_page + 1 if len(repo_payloads) >= capped_limit else None
    return GitHubRepositoryDiscovery(
        repositories=repositories,
        repositories_found=len(repo_payloads),
        repositories_returned=len(repositories),
        next_page=next_page,
        errors=errors,
        api_base_url=base_url,
    )


def fetch_github_records(
    *,
    token: str,
    repositories: list[str],
    since: str | None = None,
    include_comments: bool = True,
    max_comments_per_item: int = 10,
    max_records: int = 100,
    api_base_url: str = DEFAULT_API_BASE_URL,
    request_json: RequestJSON | None = None,
) -> GitHubSync:
    cleaned_token = str(token or "").strip()
    if not cleaned_token:
        raise ValueError("GitHub token is required")
    normalized_repositories = _normalize_repositories(repositories)
    if not normalized_repositories:
        raise ValueError("At least one GitHub repository is required")
    if len(normalized_repositories) > MAX_REPOSITORIES:
        raise ValueError(f"At most {MAX_REPOSITORIES} repositories can be synced at once")
    capped_max = max(1, min(int(max_records or 100), 500))
    requester = request_json or _request_json
    base_url = str(api_base_url or DEFAULT_API_BASE_URL).rstrip("/")
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {cleaned_token}",
        "User-Agent": "Cortex-local-connector",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    records: list[GitHubSyncRecord] = []
    records_found = 0
    errors: list[dict[str, Any]] = []
    high_water_mark: str | None = None
    comments_found = 0
    comments_returned = 0
    reviews_found = 0
    reviews_returned = 0
    review_comments_found = 0
    review_comments_returned = 0
    try:
        capped_comments_per_item = max(0, min(int(max_comments_per_item or 0), 50))
    except (TypeError, ValueError):
        capped_comments_per_item = 10
    comment_enrichment_remaining = min(capped_max, 50) if include_comments and capped_comments_per_item > 0 else 0

    per_page = min(100, capped_max)
    for repository in normalized_repositories:
        page = 1
        while len(records) < capped_max:
            query = {
                "state": "all",
                "sort": "updated",
                "direction": "desc",
                "per_page": str(per_page),
                "page": str(page),
            }
            if since:
                query["since"] = str(since)
            url = f"{base_url}/repos/{repository}/issues?{urlencode(query)}"
            try:
                payload = requester(url, headers)
            except Exception as exc:
                errors.append(
                    {
                        "repository": repository,
                        **connector_error_payload(exc, [cleaned_token, headers.get("Authorization")]),
                    }
                )
                break
            if not isinstance(payload, list):
                errors.append({"repository": repository, "error": "GitHub issues response was not a list"})
                break
            records_found += len(payload)
            for item in payload:
                if not isinstance(item, dict):
                    continue
                comments: list[dict[str, str]] = []
                reviews: list[dict[str, str]] = []
                review_comments: list[dict[str, str]] = []
                if comment_enrichment_remaining > 0:
                    comments, found, returned = _fetch_issue_comments(
                        requester,
                        base_url=base_url,
                        headers=headers,
                        token=cleaned_token,
                        repository=repository,
                        issue=item,
                        limit=capped_comments_per_item,
                        errors=errors,
                    )
                    comments_found += found
                    comments_returned += returned
                    if isinstance(item.get("pull_request"), dict):
                        reviews, found, returned = _fetch_pull_request_reviews(
                            requester,
                            base_url=base_url,
                            headers=headers,
                            token=cleaned_token,
                            repository=repository,
                            pull_request=item,
                            limit=capped_comments_per_item,
                            errors=errors,
                        )
                        reviews_found += found
                        reviews_returned += returned
                        review_comments, found, returned = _fetch_pull_request_review_comments(
                            requester,
                            base_url=base_url,
                            headers=headers,
                            token=cleaned_token,
                            repository=repository,
                            pull_request=item,
                            limit=capped_comments_per_item,
                            errors=errors,
                        )
                        review_comments_found += found
                        review_comments_returned += returned
                    comment_enrichment_remaining -= 1
                record = _record_from_issue(repository, item, comments=comments, reviews=reviews, review_comments=review_comments)
                if record is None:
                    continue
                high_water_mark = _max_iso(high_water_mark, record.captured_at)
                records.append(record)
                if len(records) >= capped_max:
                    break
            if len(payload) < per_page or len(records) >= capped_max:
                break
            page += 1

    return GitHubSync(
        repositories=normalized_repositories,
        records=records,
        records_found=records_found,
        records_returned=len(records),
        comments_found=comments_found,
        comments_returned=comments_returned,
        reviews_found=reviews_found,
        reviews_returned=reviews_returned,
        review_comments_found=review_comments_found,
        review_comments_returned=review_comments_returned,
        include_comments=bool(include_comments),
        max_comments_per_item=capped_comments_per_item,
        high_water_mark=high_water_mark,
        cursor_value=since if errors else high_water_mark or since,
        errors=errors,
        api_base_url=base_url,
    )


def _request_json(url: str, headers: dict[str, str]) -> Any:
    request = Request(url, headers=headers, method="GET")
    with open_same_origin(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_issue_comments(
    requester: RequestJSON,
    *,
    base_url: str,
    headers: dict[str, str],
    token: str,
    repository: str,
    issue: dict[str, Any],
    limit: int,
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], int, int]:
    if limit <= 0:
        return [], 0, 0
    number = issue.get("number")
    if not number:
        return [], 0, 0
    try:
        comment_count = int(issue.get("comments") or 0)
    except (TypeError, ValueError):
        comment_count = 0
    comments_url = str(issue.get("comments_url") or "").strip()
    if comment_count <= 0 and not comments_url:
        return [], 0, 0
    query = {"per_page": str(limit), "page": "1"}
    if comments_url:
        separator = "&" if "?" in comments_url else "?"
        url = f"{comments_url}{separator}{urlencode(query)}"
    else:
        url = f"{base_url}/repos/{repository}/issues/{number}/comments?{urlencode(query)}"
    try:
        payload = requester(url, headers)
    except Exception as exc:
        errors.append(
            {
                "repository": repository,
                "issue": str(number),
                "scope": "comments",
                **connector_error_payload(exc, [token, headers.get("Authorization")]),
            }
        )
        return [], 0, 0
    if not isinstance(payload, list):
        errors.append({"repository": repository, "issue": str(number), "scope": "comments", "error": "GitHub comments response was not a list"})
        return [], 0, 0
    comments: list[dict[str, str]] = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        body = _clean_body(item.get("body"))
        if not body:
            continue
        comments.append(
            {
                "id": _text(item.get("id")),
                "author": _login(item.get("user")) or "unknown",
                "created_at": _text(item.get("created_at")),
                "updated_at": _text(item.get("updated_at")),
                "url": _text(item.get("html_url")),
                "body": body,
            }
        )
    return comments, len(payload), len(comments)


def _fetch_pull_request_reviews(
    requester: RequestJSON,
    *,
    base_url: str,
    headers: dict[str, str],
    token: str,
    repository: str,
    pull_request: dict[str, Any],
    limit: int,
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], int, int]:
    number = pull_request.get("number")
    if limit <= 0 or not number:
        return [], 0, 0
    query = {"per_page": str(limit), "page": "1"}
    url = f"{base_url}/repos/{repository}/pulls/{number}/reviews?{urlencode(query)}"
    try:
        payload = requester(url, headers)
    except Exception as exc:
        errors.append(
            {
                "repository": repository,
                "pull_request": str(number),
                "scope": "pull_request_reviews",
                **connector_error_payload(exc, [token, headers.get("Authorization")]),
            }
        )
        return [], 0, 0
    if not isinstance(payload, list):
        errors.append({"repository": repository, "pull_request": str(number), "scope": "pull_request_reviews", "error": "GitHub pull request reviews response was not a list"})
        return [], 0, 0
    reviews: list[dict[str, str]] = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        body = _clean_body(item.get("body"))
        state = _text(item.get("state"))
        if not body and not state:
            continue
        reviews.append(
            {
                "id": _text(item.get("id")),
                "author": _login(item.get("user")) or "unknown",
                "state": state,
                "submitted_at": _text(item.get("submitted_at")),
                "url": _text(item.get("html_url")),
                "body": body,
            }
        )
    return reviews, len(payload), len(reviews)


def _fetch_pull_request_review_comments(
    requester: RequestJSON,
    *,
    base_url: str,
    headers: dict[str, str],
    token: str,
    repository: str,
    pull_request: dict[str, Any],
    limit: int,
    errors: list[dict[str, Any]],
) -> tuple[list[dict[str, str]], int, int]:
    number = pull_request.get("number")
    if limit <= 0 or not number:
        return [], 0, 0
    query = {"per_page": str(limit), "page": "1"}
    url = f"{base_url}/repos/{repository}/pulls/{number}/comments?{urlencode(query)}"
    try:
        payload = requester(url, headers)
    except Exception as exc:
        errors.append(
            {
                "repository": repository,
                "pull_request": str(number),
                "scope": "pull_request_review_comments",
                **connector_error_payload(exc, [token, headers.get("Authorization")]),
            }
        )
        return [], 0, 0
    if not isinstance(payload, list):
        errors.append({"repository": repository, "pull_request": str(number), "scope": "pull_request_review_comments", "error": "GitHub pull request review comments response was not a list"})
        return [], 0, 0
    comments: list[dict[str, str]] = []
    for item in payload[:limit]:
        if not isinstance(item, dict):
            continue
        body = _clean_body(item.get("body"))
        if not body:
            continue
        comments.append(
            {
                "id": _text(item.get("id")),
                "author": _login(item.get("user")) or "unknown",
                "created_at": _text(item.get("created_at")),
                "updated_at": _text(item.get("updated_at")),
                "url": _text(item.get("html_url")),
                "path": _text(item.get("path")),
                "line": _text(item.get("line")) or _text(item.get("original_line")),
                "body": body,
            }
        )
    return comments, len(payload), len(comments)


def _normalize_repositories(values: list[str]) -> list[str]:
    repositories: list[str] = []
    seen: set[str] = set()
    for value in values or []:
        repository = _normalize_repository(value)
        if not repository or repository.lower() in seen:
            continue
        seen.add(repository.lower())
        repositories.append(repository)
    return repositories


def _normalize_repository(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("http://") or text.startswith("https://"):
        parsed = urlparse(text)
        text = parsed.path.strip("/")
    text = text.removeprefix("github.com/").strip("/")
    parts = [part for part in text.split("/") if part]
    if len(parts) < 2:
        return ""
    owner, repo = parts[0], parts[1]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return ""
    return f"{owner}/{repo}"


def _repository_permissions(value: Any) -> dict[str, bool]:
    if not isinstance(value, dict):
        return {}
    permissions: dict[str, bool] = {}
    for key in ("admin", "maintain", "push", "triage", "pull"):
        if key in value:
            permissions[key] = bool(value.get(key))
    return permissions


def _record_from_issue(
    repository: str,
    item: dict[str, Any],
    *,
    comments: list[dict[str, str]] | None = None,
    reviews: list[dict[str, str]] | None = None,
    review_comments: list[dict[str, str]] | None = None,
) -> GitHubSyncRecord | None:
    number = item.get("number")
    title = str(item.get("title") or "").strip()
    if not number or not title:
        return None
    is_pull_request = isinstance(item.get("pull_request"), dict)
    record_scope = "pull_request" if is_pull_request else "issue"
    type_label = "Pull request" if is_pull_request else "Issue"
    html_url = str(item.get("html_url") or "").strip()
    source_url = html_url or f"https://github.com/{repository}/{'pull' if is_pull_request else 'issues'}/{number}"
    author = _login(item.get("user"))
    labels = _labels(item.get("labels"))
    assignees = [_login(value) for value in item.get("assignees") or [] if _login(value)]
    milestone = item.get("milestone") if isinstance(item.get("milestone"), dict) else {}
    created_at = _text(item.get("created_at"))
    updated_at = _text(item.get("updated_at"))
    closed_at = _text(item.get("closed_at"))
    body = _clean_body(item.get("body"))

    lines = [
        "Source: GitHub",
        f"Repository: {repository}",
        f"Type: {type_label}",
        f"Number: #{number}",
        f"State: {_text(item.get('state')) or 'unknown'}",
        f"Title: {title}",
    ]
    if author:
        lines.append(f"Author: {author}")
    if labels:
        lines.append(f"Labels: {', '.join(labels)}")
    if assignees:
        lines.append(f"Assignees: {', '.join(assignees)}")
    if isinstance(milestone, dict) and milestone.get("title"):
        lines.append(f"Milestone: {milestone['title']}")
    if created_at:
        lines.append(f"Created: {created_at}")
    if updated_at:
        lines.append(f"Updated: {updated_at}")
    if closed_at:
        lines.append(f"Closed: {closed_at}")
    lines.append(f"URL: {source_url}")
    if body:
        lines.extend(["", "Body:", body])
    issue_comments = comments or []
    if issue_comments:
        lines.extend(["", "Comments:"])
        for index, comment in enumerate(issue_comments, start=1):
            comment_header = f"Comment {index}"
            if comment.get("author"):
                comment_header += f" by {comment['author']}"
            if comment.get("updated_at") or comment.get("created_at"):
                comment_header += f" at {comment.get('updated_at') or comment.get('created_at')}"
            lines.extend([comment_header + ":", comment["body"]])
    pr_reviews = reviews or []
    if pr_reviews:
        lines.extend(["", "Pull request reviews:"])
        for index, review in enumerate(pr_reviews, start=1):
            review_header = f"Review {index}"
            if review.get("state"):
                review_header += f" {review['state']}"
            if review.get("author"):
                review_header += f" by {review['author']}"
            if review.get("submitted_at"):
                review_header += f" at {review['submitted_at']}"
            lines.append(review_header + ":")
            if review.get("body"):
                lines.append(review["body"])
    pr_review_comments = review_comments or []
    if pr_review_comments:
        lines.extend(["", "Pull request review comments:"])
        for index, comment in enumerate(pr_review_comments, start=1):
            comment_header = f"Review comment {index}"
            location = _review_comment_location(comment)
            if location:
                comment_header += f" on {location}"
            if comment.get("author"):
                comment_header += f" by {comment['author']}"
            if comment.get("updated_at") or comment.get("created_at"):
                comment_header += f" at {comment.get('updated_at') or comment.get('created_at')}"
            lines.extend([comment_header + ":", comment["body"]])

    return GitHubSyncRecord(
        content="\n".join(lines).strip(),
        title=f"{repository} {type_label} #{number}: {title}"[:200],
        source_url=source_url[:500],
        external_id=f"github:{repository}:{record_scope}:{number}"[:240],
        captured_at=updated_at or created_at,
        metadata={
            "connector": GITHUB_SOURCE,
            "connector_version": CONNECTOR_VERSION,
            "repository": repository,
            "record_scope": record_scope,
            "github_number": str(number),
            "issue": str(number),
            "author": author,
            "state": _text(item.get("state")),
            "labels": labels,
            "comments_returned": len(issue_comments),
            "reviews_returned": len(pr_reviews),
            "review_comments_returned": len(pr_review_comments),
            "url": source_url,
            "source_type": "github_pull_request" if is_pull_request else "github_issue",
            "source_quality": "canonical",
        },
    )


def _review_comment_location(comment: dict[str, str]) -> str:
    path = _text(comment.get("path"))
    line = _text(comment.get("line"))
    if path and line:
        return f"{path}:{line}"
    return path or line


def _login(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("login") or "").strip()
    return ""


def _labels(value: Any) -> list[str]:
    labels: list[str] = []
    for item in value or []:
        if isinstance(item, dict):
            label = str(item.get("name") or "").strip()
        else:
            label = str(item or "").strip()
        if label:
            labels.append(label)
    return labels[:12]


def _clean_body(value: Any) -> str:
    text = str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return text[:20_000]


def _text(value: Any) -> str:
    return str(value or "").strip()


def _max_iso(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right:
        return left
    return max(left, right)
