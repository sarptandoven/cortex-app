from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


MemoryKind = Literal["claim", "decision", "event", "preference", "observation", "action", "question", "summary", "style", "negative", "procedure"]
MemoryLayer = Literal["semantic", "episodic", "style", "decision", "preference", "negative", "procedural"]


class CaptureRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=200_000)
    source: str = Field(default="macos", max_length=80)
    source_url: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    user_id: str = "local"
    # Phase-2 sync: a client-supplied stable capture id (idempotency key) + pinned timestamp so a
    # re-pushed capture upserts (ON CONFLICT DO UPDATE) instead of duplicating.
    capture_id_override: str | None = Field(default=None, max_length=80)
    captured_at: str | None = Field(default=None, max_length=40)


class CaptureResponse(BaseModel):
    capture_id: str
    summary: str
    memories: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    graph: dict[str, Any]


# --- Phase-2 local→hosted push sync ---------------------------------------------------------------

class CaptureChangeItem(BaseModel):
    seq: int
    client_capture_id: str
    content: str
    source: str
    source_url: str | None = None
    title: str | None = None
    captured_at: str


class CaptureChangePage(BaseModel):
    """Local outbound feed: captures newer than a monotonic rowid cursor, WITH content."""
    items: list[CaptureChangeItem]
    next_seq: int
    has_more: bool


class SyncIngestItem(BaseModel):
    client_capture_id: str = Field(..., min_length=1, max_length=80)
    content: str = Field(..., min_length=1, max_length=200_000)
    source: str = Field(default="macos", max_length=80)
    source_url: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    captured_at: str | None = Field(default=None, max_length=40)


class SyncIngestRequest(BaseModel):
    device_id: str = Field(default="", max_length=80)
    cursor: str = Field(default="", max_length=160)
    items: list[SyncIngestItem] = Field(default_factory=list, max_length=500)


class SyncIngestItemResult(BaseModel):
    client_capture_id: str
    capture_id: str
    status: str


class SyncIngestResponse(BaseModel):
    applied: int
    cursor: str
    results: list[SyncIngestItemResult]


class SourceImportRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=200)
    source_hint: str = Field(default="", max_length=80)
    processing: Literal["sync", "async"] = "async"
    max_records: int = Field(default=1000, ge=1, le=5000)
    offset: int = Field(default=0, ge=0)
    user_id: str = "local"
    # A file the user explicitly imports (e.g. their ChatGPT/Claude export) is trusted, so its
    # content is usable immediately instead of sitting per-conversation in Review. Set false to
    # route imported captures through Review.
    auto_approve: bool = True


class SourceAnalyzeRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=200)
    source_hint: str = Field(default="", max_length=80)
    max_records: int = Field(default=500, ge=1, le=500)


class SourceImportResponse(BaseModel):
    import_id: str
    status: str
    records_found: int
    records_available: int = 0
    offset: int = 0
    has_more: bool = False
    next_offset: int | None = None
    queued: int
    saved: int
    failed: int
    skipped: int = 0
    sources: list[dict[str, Any]]
    records: list[dict[str, Any]]
    errors: list[dict[str, Any]]


class SourceImportDeleteResponse(BaseModel):
    import_id: str
    deleted: bool
    status: str
    deleted_captures: int
    deleted_memories: int
    deleted_tasks: int
    deleted_edges: int


class SourceAnalyzeResponse(BaseModel):
    records_found: int
    sources: list[dict[str, Any]]
    sample: list[dict[str, Any]]
    supported_sources: list[dict[str, Any]]


class SourceAccountRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=80)
    account_label: str = Field(default="", max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    connection_type: str = Field(default="manual", max_length=40)
    status: str = Field(default="available", max_length=40)
    auth_state: str = Field(default="not_configured", max_length=40)
    policy: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    last_error: str | None = Field(default=None, max_length=500)


class SourceAccountResponse(BaseModel):
    id: str
    user_id: str
    source: str
    account_label: str
    account_identifier: str | None = None
    connection_type: str
    status: str
    auth_state: str
    policy: dict[str, Any]
    metadata: dict[str, Any]
    last_sync_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str
    disconnected_at: str | None = None
    retention: dict[str, Any] = Field(default_factory=dict)


class SourceAccountListResponse(BaseModel):
    results: list[SourceAccountResponse]


class SourceAccountSyncRecord(BaseModel):
    content: str = Field(..., min_length=1, max_length=200_000)
    title: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=500)
    external_id: str | None = Field(default=None, max_length=240)
    captured_at: str | None = Field(default=None, max_length=80)
    metadata: dict[str, Any] | None = None


class SourceAccountSyncRequest(BaseModel):
    records: list[SourceAccountSyncRecord] = Field(..., min_length=1, max_length=500)
    cursor_name: str = Field(default="default", min_length=1, max_length=120)
    cursor_value: str | None = Field(default=None, max_length=2000)
    high_water_mark: str | None = Field(default=None, max_length=500)
    state: dict[str, Any] | None = None
    processing: Literal["sync", "async"] = "async"
    archive_missing: bool = False
    complete_snapshot: bool = False


class SourceAccountSyncResponse(BaseModel):
    source_account_id: str
    source: str
    status: str
    processing: str
    received: int
    queued: int
    saved: int
    skipped: int
    failed: int
    archived_missing: int = 0
    archive_missing_decision: dict[str, Any] | None = None
    archive_missing_suppressed: bool = False
    capture_ids: list[str]
    records: list[dict[str, Any]]
    errors: list[dict[str, Any]]
    cursor: dict[str, Any]


class ObsidianVaultSyncRequest(BaseModel):
    vault_path: str = Field(..., min_length=1, max_length=2000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=1000, ge=1, le=5000)
    cursor_name: str = Field(default="local-folder", min_length=1, max_length=120)
    # Default to the product's review gate for a newly connected notes folder. Callers that expose
    # an explicit "trust this vault" choice may set this false to make source memories usable
    # immediately.
    review_required: bool = True


class ObsidianVaultSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    scan: dict[str, Any]


class AgentSessionsSyncRequest(BaseModel):
    """Harvest the user's own messages from local coding-agent session logs.

    No directory-override fields on purpose: the API surface must not let a caller point the
    scanner at arbitrary filesystem paths. Directories are the agents' well-known locations.
    """

    agents: list[Literal["claude", "codex", "cursor"]] | None = None
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=200, ge=1, le=500)
    per_session_limit: int = Field(default=25, ge=1, le=200)
    cursor_name: str = Field(default="agent-sessions", min_length=1, max_length=120)
    # Harvested prompts are raw self-explanation, not curated notes: review-gate by default.
    review_required: bool = True


class AgentSessionsSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    scan: dict[str, Any]


class VerifyIntegrityRequest(BaseModel):
    """Phase D: prove continuity by recomputing the event hash-chain head and comparing it to a
    head the caller pinned earlier. Just the expected head — the events themselves are the store's."""

    expected_head: str = Field(..., min_length=1, max_length=128)


class VerifyBeliefProofRequest(BaseModel):
    """M2: verify a self-contained Proof-of-Belief envelope without trusting its source."""

    proof: dict[str, Any]
    expected_head: str | None = Field(default=None, min_length=1, max_length=128)


class VerifyBundleRequest(BaseModel):
    """Verify a signed portable memory bundle without trusting its source."""

    bundle: dict[str, Any]
    expected_signing_key_id: str | None = Field(default=None, min_length=1, max_length=128)


class ImportBundleRequest(BaseModel):
    """M8: verify and import a signed portable memory bundle into the authenticated tenant."""

    bundle: dict[str, Any]
    expected_signing_key_id: str | None = Field(default=None, min_length=1, max_length=128)


class MemoryConsolidationRequest(BaseModel):
    """M4 sleep pass: safe contradiction consolidation plus verified hot-pack warming."""

    hot_requests: list[dict[str, Any]] = Field(default_factory=list, max_length=50)
    auto_resolve_safe: bool = True
    max_conflicts: int = Field(default=2000, ge=1, le=10_000)
    max_hot_packs: int = Field(default=12, ge=0, le=50)


class WorkingCanvasNodeRequest(BaseModel):
    session_id: str = Field(..., min_length=1, max_length=120)
    node_id: str = Field(..., min_length=1, max_length=120)
    raw_text: str = Field(..., min_length=1, max_length=200_000)
    label: str = Field(default="", max_length=120)
    summary: str = Field(default="", max_length=500)
    predecessor_node_id: str | None = Field(default=None, max_length=120)


class WorkingCanvasNodeResponse(BaseModel):
    session_id: str
    node_id: str
    label: str
    summary: str
    raw_sha256: str
    raw_bytes: int
    receipt_event_id: str
    predecessor_node_id: str | None = None
    verified: bool
    raw_text: str | None = None


class WorkingCanvasResponse(BaseModel):
    session_id: str
    node_count: int
    visible_count: int
    nodes: list[dict[str, Any]]
    canvas: str
    contract: dict[str, Any]
    elided_node_ids: list[str] | None = None


class GitHubSyncRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    repositories: list[str] = Field(..., min_length=1, max_length=25)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    include_comments: bool = True
    max_comments_per_item: int = Field(default=10, ge=0, le=50)
    cursor_name: str = Field(default="issues", min_length=1, max_length=120)
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class GitHubRepositoryDiscoveryRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=100, ge=1, le=100)
    page: int = Field(default=1, ge=1, le=1000)
    api_base_url: str | None = Field(default=None, max_length=500)


class GitHubRepositoryDiscoveryResponse(BaseModel):
    connector: str
    connector_version: str
    repositories: list[dict[str, Any]]
    repositories_found: int
    repositories_returned: int
    next_page: int | None = None
    errors: list[dict[str, Any]]
    api_base_url: str


class GitHubSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class GoogleOAuthStartRequest(BaseModel):
    source: Literal["gmail", "google-drive"]
    redirect_uri: str | None = Field(default=None, max_length=500)
    state: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=4000)
    client_secret: str | None = Field(default=None, max_length=4000)
    token_endpoint: str | None = Field(default=None, max_length=500)
    code_verifier: str | None = Field(default=None, max_length=256)
    code_challenge: str | None = Field(default=None, max_length=256)
    code_challenge_method: str | None = Field(default=None, max_length=20)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    query: str | None = Field(default=None, max_length=500)
    label_ids: list[str] = Field(default_factory=list, max_length=20)
    mime_types: list[str] = Field(default_factory=list, max_length=20)
    include_body: bool = True
    include_content: bool = True
    scopes: list[str] = Field(default_factory=list, max_length=10)


class GoogleOAuthStartResponse(BaseModel):
    source: str
    provider: str
    authorization_url: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str
    state: str
    scopes: list[str]
    access_type: str


class GoogleOAuthCompleteRequest(BaseModel):
    source: Literal["gmail", "google-drive"]
    code: str = Field(..., min_length=1, max_length=4000)
    redirect_uri: str | None = Field(default=None, max_length=500)
    state: str | None = Field(default=None, max_length=500)
    expected_state: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=4000)
    client_secret: str | None = Field(default=None, max_length=4000)
    token_endpoint: str | None = Field(default=None, max_length=500)
    code_verifier: str | None = Field(default=None, max_length=256)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    query: str | None = Field(default=None, max_length=500)
    label_ids: list[str] = Field(default_factory=list, max_length=20)
    mime_types: list[str] = Field(default_factory=list, max_length=20)
    include_body: bool = True
    include_content: bool = True


class GoogleOAuthCompleteResponse(BaseModel):
    source: str
    provider: str
    source_account: SourceAccountResponse
    credential_ref: str
    scope: str
    scopes: list[str]
    access_token_expires_at: str | None = None
    sync_plan: dict[str, Any]


class ManagedOAuthStartRequest(BaseModel):
    source: Literal["notion", "outlook"]
    redirect_uri: str | None = Field(default=None, max_length=500)
    state: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=4000)
    client_secret: str | None = Field(default=None, max_length=4000)
    token_endpoint: str | None = Field(default=None, max_length=500)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    include_content: bool = True
    api_base_url: str | None = Field(default=None, max_length=500)
    notion_version: str | None = Field(default=None, max_length=80)
    scopes: list[str] = Field(default_factory=list, max_length=10)


class ManagedOAuthStartResponse(BaseModel):
    source: str
    provider: str
    authorization_url: str
    authorization_endpoint: str
    token_endpoint: str
    redirect_uri: str
    state: str
    scopes: list[str]
    access_type: str


class ManagedOAuthCompleteRequest(BaseModel):
    source: Literal["notion", "outlook"]
    code: str = Field(..., min_length=1, max_length=4000)
    redirect_uri: str | None = Field(default=None, max_length=500)
    state: str | None = Field(default=None, max_length=500)
    expected_state: str | None = Field(default=None, max_length=500)
    client_id: str | None = Field(default=None, max_length=4000)
    client_secret: str | None = Field(default=None, max_length=4000)
    token_endpoint: str | None = Field(default=None, max_length=500)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    include_content: bool = True
    api_base_url: str | None = Field(default=None, max_length=500)
    notion_version: str | None = Field(default=None, max_length=80)


class ManagedOAuthCompleteResponse(BaseModel):
    source: str
    provider: str
    source_account: SourceAccountResponse
    credential_ref: str
    scope: str
    scopes: list[str]
    access_token_expires_at: str | None = None
    sync_plan: dict[str, Any]


class GmailSyncRequest(BaseModel):
    access_token: str = Field(..., min_length=1, max_length=4000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    query: str | None = Field(default=None, max_length=500)
    label_ids: list[str] = Field(default_factory=list, max_length=20)
    since: str | None = Field(default=None, max_length=80)
    page_token: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=50, ge=1, le=200)
    cursor_name: str = Field(default="messages", min_length=1, max_length=120)
    include_body: bool = True
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class GmailSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class GoogleDriveSyncRequest(BaseModel):
    access_token: str = Field(..., min_length=1, max_length=4000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    query: str | None = Field(default=None, max_length=500)
    mime_types: list[str] = Field(default_factory=list, max_length=20)
    since: str | None = Field(default=None, max_length=80)
    page_token: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=50, ge=1, le=200)
    cursor_name: str = Field(default="files", min_length=1, max_length=120)
    include_content: bool = True
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class GoogleDriveSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class OutlookSyncRequest(BaseModel):
    access_token: str = Field(..., min_length=1, max_length=4000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    query: str | None = Field(default=None, max_length=1000)
    since: str | None = Field(default=None, max_length=80)
    page_token: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=50, ge=1, le=200)
    cursor_name: str = Field(default="messages", min_length=1, max_length=120)
    include_body: bool = True
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class OutlookSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class SlackSyncRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    channels: list[str] = Field(..., min_length=1, max_length=20)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=200)
    cursor_name: str = Field(default="messages", min_length=1, max_length=120)
    workspace_url: str | None = Field(default=None, max_length=500)
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class SlackChannelDiscoveryRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    limit: int = Field(default=100, ge=1, le=200)
    include_private: bool = True
    cursor: str | None = Field(default=None, max_length=2000)
    api_base_url: str | None = Field(default=None, max_length=500)


class SlackChannelDiscoveryResponse(BaseModel):
    connector: str
    connector_version: str
    channels: list[dict[str, Any]]
    channels_found: int
    channels_returned: int
    next_cursor: str | None = None
    errors: list[dict[str, Any]]
    api_base_url: str
    auth_identity: dict[str, str]


class SlackSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class ReadwiseSyncRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    page_cursor: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    cursor_name: str = Field(default="highlights", min_length=1, max_length=120)
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class ReadwiseSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class CalendarSyncRequest(BaseModel):
    ics_path: str | None = Field(default=None, max_length=1000)
    feed_url: str | None = Field(default=None, max_length=1000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    cursor_name: str = Field(default="events", min_length=1, max_length=120)
    complete_snapshot: bool = False


class CalendarSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class RaindropSyncRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    collection_id: str = Field(default="0", min_length=1, max_length=120)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    page: str | None = Field(default=None, max_length=80)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    cursor_name: str = Field(default="raindrops", min_length=1, max_length=120)
    include_highlights: bool = True
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class RaindropSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class ZoteroSyncRequest(BaseModel):
    token: str | None = Field(default=None, max_length=4000)
    library_type: Literal["user", "group"] = "user"
    library_id: str = Field(default="0", min_length=1, max_length=120)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    cursor: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    cursor_name: str = Field(default="items", min_length=1, max_length=120)
    include_attachments: bool = False
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)


class ZoteroSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class LinearSyncRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    cursor: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    cursor_name: str = Field(default="issues", min_length=1, max_length=120)
    complete_snapshot: bool = False
    api_url: str | None = Field(default=None, max_length=500)


class LinearSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class JiraSyncRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=320)
    api_token: str = Field(..., min_length=1, max_length=4000)
    site_url: str = Field(..., min_length=1, max_length=500)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    jql: str | None = Field(default=None, max_length=2000)
    since: str | None = Field(default=None, max_length=80)
    page_token: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=100, ge=1, le=500)
    cursor_name: str = Field(default="issues", min_length=1, max_length=120)
    complete_snapshot: bool = False


class JiraSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class NotionSyncRequest(BaseModel):
    token: str = Field(..., min_length=1, max_length=4000)
    source_account_id: str | None = Field(default=None, max_length=80)
    account_label: str | None = Field(default=None, max_length=160)
    account_identifier: str | None = Field(default=None, max_length=240)
    since: str | None = Field(default=None, max_length=80)
    cursor: str | None = Field(default=None, max_length=2000)
    processing: Literal["sync", "async"] = "sync"
    max_records: int = Field(default=50, ge=1, le=200)
    cursor_name: str = Field(default="pages", min_length=1, max_length=120)
    include_content: bool = True
    complete_snapshot: bool = False
    api_base_url: str | None = Field(default=None, max_length=500)
    notion_version: str | None = Field(default=None, max_length=80)


class NotionSyncResponse(SourceAccountSyncResponse):
    source_account: SourceAccountResponse
    sync: dict[str, Any]


class SourceReadinessResponse(BaseModel):
    generated_at: str
    summary: dict[str, Any]
    sources: list[dict[str, Any]]
    recommendations: list[str]


class SyncCursorRequest(BaseModel):
    source: str = Field(..., min_length=1, max_length=80)
    cursor_name: str = Field(..., min_length=1, max_length=120)
    cursor_value: str | None = Field(default=None, max_length=2000)
    high_water_mark: str | None = Field(default=None, max_length=500)
    state: dict[str, Any] | None = None
    source_account_id: str | None = Field(default=None, max_length=80)
    last_error: str | None = Field(default=None, max_length=500)
    completed: bool = True


class SyncCursorResponse(BaseModel):
    id: str
    user_id: str
    source_account_id: str | None = None
    source: str
    cursor_name: str
    cursor_value: str | None = None
    high_water_mark: str | None = None
    state: dict[str, Any]
    last_started_at: str | None = None
    last_completed_at: str | None = None
    last_error: str | None = None
    created_at: str
    updated_at: str


class SyncCursorListResponse(BaseModel):
    results: list[SyncCursorResponse]


class SyncDeviceRequest(BaseModel):
    device_name: str = Field(..., min_length=1, max_length=160)
    platform: str = Field(default="unknown", max_length=80)
    device_key: str | None = Field(default=None, max_length=2000)
    public_key: str | None = Field(default=None, max_length=4000)
    capabilities: list[str] = Field(default_factory=list)


class SyncDeviceResponse(BaseModel):
    id: str
    user_id: str
    device_name: str
    platform: str
    fingerprint: str
    public_key: str | None = None
    capabilities: list[str]
    first_cursor: str | None = None
    last_cursor: str | None = None
    last_seen_at: str | None = None
    created_at: str
    updated_at: str
    revoked_at: str | None = None
    device_key: str | None = None


class SyncDeviceListResponse(BaseModel):
    results: list[SyncDeviceResponse]


class SyncReceiptRequest(BaseModel):
    cursor: str = Field(..., min_length=1, max_length=160)
    status: str = Field(default="accepted", max_length=40)
    manifest_hash: str | None = Field(default=None, max_length=256)
    remote_ref: str | None = Field(default=None, max_length=500)
    error: str | None = Field(default=None, max_length=500)
    stats: dict[str, Any] | None = None


class SyncReceiptResponse(BaseModel):
    id: str
    user_id: str
    device_id: str
    cursor: str
    status: str
    manifest_hash: str | None = None
    remote_ref: str | None = None
    error: str | None = None
    stats: dict[str, Any]
    created_at: str
    updated_at: str


class SyncReceiptListResponse(BaseModel):
    results: list[SyncReceiptResponse]


class SyncChangeFeedResponse(BaseModel):
    generated_at: str
    sync_contract: int
    content_included: bool
    cursor: str
    next_cursor: str
    has_more: bool
    high_watermark: dict[str, Any]
    shard: dict[str, Any] | None = None
    counts: dict[str, int]
    device: dict[str, Any] | None = None
    changes: list[dict[str, Any]]
    warnings: list[str]
    signature: dict[str, Any] | None = None


class QueuedCaptureResponse(BaseModel):
    capture_id: str
    status: str
    summary: str
    jobs: list[dict[str, Any]]
    processing: dict[str, Any]


class JobRunResponse(BaseModel):
    ran_at: str
    processed: int
    jobs: list[dict[str, Any]]
    scheduled_source_syncs: dict[str, Any] | None = None
    pending: int
    failed: int


class SearchResponse(BaseModel):
    query: str
    sector: str | None = None
    associative: bool = False
    filters: dict[str, Any] | None = None
    results: list[dict[str, Any]]
    retrieval: dict[str, Any] | None = None


class AskResponse(BaseModel):
    query: str
    status: str = "cited"
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    known_unknown: bool = False
    confidence_detail: dict[str, Any] = Field(default_factory=dict)
    knowledge_gap: dict[str, Any] | None = None
    filters: dict[str, Any] | None = None
    answer: str
    citations: list[dict[str, Any]]
    conflicts: list[dict[str, Any]] = Field(default_factory=list)
    evidence: dict[str, Any] = Field(default_factory=dict)
    results: list[dict[str, Any]]


class ListResponse(BaseModel):
    results: list[dict[str, Any]]


class GraphResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    analysis: dict[str, Any] | None = None


class StatsResponse(BaseModel):
    captures: int
    pending_captures: int
    memories: int
    decisions: int
    tasks: int
    entities: int
    edges: int
    by_kind: list[dict[str, Any]]
    by_layer: list[dict[str, Any]]
    top_topics: list[dict[str, Any]]
    top_entities: list[dict[str, Any]]


class MemoryQualitySource(BaseModel):
    source: str
    captures: int
    pending: int
    approved: int
    archived: int
    active_memories: int
    cited_memories: int
    uncited_memories: int
    dated_memories: int = 0
    temporal_memories: int = 0
    dated_temporal_memories: int = 0
    undated_temporal_memories: int = 0
    sector_memories: int = 0
    unsectored_memories: int = 0
    source_type_memories: int = 0
    missing_source_type_memories: int = 0
    provenance_complete_memories: int = 0
    weak_provenance_memories: int = 0
    citation_coverage: float
    date_coverage: float = 0.0
    sector_coverage: float = 0.0
    source_type_coverage: float = 0.0
    provenance_coverage: float = 0.0
    last_seen: str | None = None
    status: str
    warnings: list[str] = Field(default_factory=list)


class MemoryQualityResponse(BaseModel):
    generated_at: str
    score: int
    status: str
    citation_coverage: float
    date_coverage: float = 0.0
    review_coverage: float
    layer_coverage: float
    sector_coverage: float = 0.0
    source_type_coverage: float = 0.0
    provenance_coverage: float = 0.0
    vector_coverage: float = 0.0
    relation_coverage: float = 0.0
    relation_integrity: float = 1.0
    layers_present: list[str]
    totals: dict[str, int]
    relation_health: dict[str, Any] = Field(default_factory=dict)
    vector_health: dict[str, Any] = Field(default_factory=dict)
    source_health: list[MemoryQualitySource]
    warnings: list[str]
    recommendations: list[str]


class ProductLoopResponse(BaseModel):
    generated_at: str
    status: str
    completion: int
    primary_action: dict[str, Any]
    steps: list[dict[str, Any]]
    counts: dict[str, Any]
    last_reused_at: str | None = None
    today: str


class GradeAnswerRequest(BaseModel):
    answer_text: str = Field(..., min_length=1, max_length=20_000)
    session_id: str | None = Field(default=None, max_length=120)
    pack_sha: str | None = Field(default=None, max_length=80)


class WouldIRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    limit: int = Field(default=8, ge=1, le=20)


class DraftAsMeRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2_000)
    medium: str = Field(default="", max_length=60)
    limit: int = Field(default=8, ge=1, le=20)


class GradeTwinPredictionRequest(BaseModel):
    prediction_id: str = Field(..., min_length=1, max_length=120)
    outcome: str = Field(..., min_length=1, max_length=20)
    actual: str = Field(default="", max_length=500)
    answerability: Literal["answerable", "unknown", "unclear"] | None = None


class SharedPrincipalCreateRequest(BaseModel):
    label: str = Field(..., min_length=1, max_length=120)
    kind: Literal["user", "connector", "agent"] = "agent"
    trust_score: float | None = Field(default=None, ge=0, le=1)


class SharedMemoryWriteRequest(BaseModel):
    principal_id: str = Field(..., min_length=1, max_length=120)
    nonce: str = Field(..., min_length=1, max_length=120)
    content: str = Field(..., min_length=1, max_length=200_000)
    signature: str = Field(..., min_length=1, max_length=256)
    source_url: str = Field(default="", max_length=500)
    title: str = Field(default="", max_length=200)
    supersedes_memory_id: str = Field(default="", max_length=120)


class ContextReuseRequest(BaseModel):
    surface: str = Field(default="macos", max_length=80)
    query: str = Field(default="", max_length=500)
    target: str = Field(default="", max_length=80)


class ContextReuseResponse(BaseModel):
    recorded: bool
    event: dict[str, Any]
    product_loop: dict[str, Any]


class SettingsResponse(BaseModel):
    review_new_captures: bool
    allow_pending_in_context: bool
    context_pack_limit: int
    allow_agent_reads: bool
    allow_agent_writes: bool
    allow_agent_exports: bool
    allow_agent_maintenance: bool
    allow_agent_destructive_actions: bool
    redact_sensitive_context: bool
    proactive_alerts_daily_budget: int
    source_policies: dict[str, Any] = Field(default_factory=dict)
    identity_aliases: list[str] = Field(default_factory=list)


class SettingsUpdateRequest(BaseModel):
    review_new_captures: bool | None = None
    allow_pending_in_context: bool | None = None
    context_pack_limit: int | None = None
    allow_agent_reads: bool | None = None
    allow_agent_writes: bool | None = None
    allow_agent_exports: bool | None = None
    allow_agent_maintenance: bool | None = None
    allow_agent_destructive_actions: bool | None = None
    redact_sensitive_context: bool | None = None
    proactive_alerts_daily_budget: int | None = None
    source_policies: dict[str, Any] | None = None
    identity_aliases: list[str] | str | dict[str, Any] | None = None


class DiagnosticsResponse(BaseModel):
    status: str
    quick_check: str
    schema_version: int
    db_path: str
    db_size_bytes: int
    wal_size_bytes: int
    counts: dict[str, Any]
    fts_orphans: int
    inactive_fts_rows: int
    relation_orphans: int
    last_event_at: str | None
    vector: dict[str, Any] | None = None
    embedding: dict[str, Any] | None = None
    vault: dict[str, Any] | None = None


class BackupResponse(BaseModel):
    backup_path: str
    size_bytes: int
    created_at: str
    retention: dict[str, Any] | None = None
    pruned_backups: dict[str, Any] | None = None


class MCPTokenRegistrationRequest(BaseModel):
    token: str = Field(..., min_length=12, max_length=160)
    label: str = Field(default="Local MCP integrations", max_length=120)
    scopes: list[str] | None = None


class MCPTokenRegistrationResponse(BaseModel):
    token_id: str
    user_id: str
    label: str
    audience: str
    scopes: list[str]
    updated_at: str


class UserProvisionRequest(BaseModel):
    user_id: str = Field(..., min_length=1, max_length=160)
    display_name: str = Field(default="", max_length=160)
    plan: str = Field(default="free", max_length=60)
    metadata: dict[str, Any] | None = None
    api_scopes: list[str] | None = None
    mcp_scopes: list[str] | None = None
    allow_existing: bool = False


class ProvisionedTokenModel(BaseModel):
    token: str
    token_id: str | None = None
    scopes: list[str] | None = None


class UserRecordModel(BaseModel):
    user_id: str
    display_name: str = ""
    plan: str = "free"
    status: str = "active"
    metadata: dict[str, Any] = {}
    created_at: str
    updated_at: str


class UserProvisionResponse(BaseModel):
    user: UserRecordModel
    shard: dict[str, Any]
    api_token: ProvisionedTokenModel
    mcp_token: ProvisionedTokenModel


class UserListResponse(BaseModel):
    results: list[UserRecordModel]
    total: int


class UserStatusResponse(BaseModel):
    user: UserRecordModel


class APITokenRegistrationRequest(BaseModel):
    token: str = Field(..., min_length=12, max_length=160)
    label: str = Field(default="REST API client", max_length=120)
    scopes: list[str] | None = None


class APITokenRegistrationResponse(BaseModel):
    token_id: str
    user_id: str
    label: str
    audience: str
    scopes: list[str]
    updated_at: str


class APITokenMetadata(BaseModel):
    token_id: str
    user_id: str
    label: str
    audience: str
    scopes: list[str]
    created_at: str
    updated_at: str
    last_used_at: str | None = None
    revoked_at: str | None = None


class APITokenListResponse(BaseModel):
    results: list[APITokenMetadata]


class APITokenRevokeResponse(APITokenMetadata):
    revoked: bool


class ReliabilityReportResponse(BaseModel):
    status: str
    generated_at: str
    backend_version: str
    health_contract: int
    features: list[str]
    checks: list[dict[str, Any]]
    recommended_actions: list[str]
    latest_backup: dict[str, Any] | None = None
    diagnostics: dict[str, Any]


class DataLifecycleReportResponse(BaseModel):
    generated_at: str
    status: str
    storage: dict[str, Any]
    record_counts: dict[str, int]
    backups: dict[str, Any]
    export: dict[str, Any]
    deletion: dict[str, Any]
    ai_access: dict[str, Any]
    audit: dict[str, Any]
    recommended_actions: list[str]


class SupportBundleResponse(BaseModel):
    bundle_schema: int
    generated_at: str
    privacy: dict[str, Any]
    backend: dict[str, Any]
    runtime: dict[str, Any]
    summary: dict[str, Any]
    health: dict[str, Any]
    diagnostics: dict[str, Any]
    reliability: dict[str, Any]
    trust: dict[str, Any]
    product_loop: dict[str, Any]
    source_readiness: dict[str, Any] | None = None
    recent_events: list[dict[str, Any]]


class RepairStorageResponse(BaseModel):
    repaired_at: str
    backup_path: str
    before: dict[str, Any]
    after: dict[str, Any]
    actions: list[dict[str, Any]]


class MaintenanceResponse(BaseModel):
    indexed_memories: int
    rebuilt_at: str
    vector_available: bool | None = None
    vector_indexed_memories: int | None = None
    vector_queued_memories: int | None = None
    vector_model: str | None = None
    embedding: dict[str, Any] | None = None


class VectorRebuildResponse(BaseModel):
    queued: int
    skipped: int
    checked: int
    rebuilt_at: str
    vector_available: bool
    vector_indexed_memories: int
    vector_model: str | None = None
    embedding: dict[str, Any] | None = None


class VaultRebuildResponse(BaseModel):
    rebuilt_at: str
    vault_path: str
    index_path: str
    captures: int
    memories: int
    tasks: int
    entities: int
    edges: int
    events: int


class MCPRequest(BaseModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] | None = None
