from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


MemoryKind = Literal["claim", "decision", "event", "preference", "observation", "action", "question", "summary", "style", "negative"]
MemoryLayer = Literal["semantic", "episodic", "style", "decision", "preference", "negative"]


class CaptureRequest(BaseModel):
    content: str = Field(..., min_length=1, max_length=200_000)
    source: str = Field(default="macos", max_length=80)
    source_url: str | None = Field(default=None, max_length=500)
    title: str | None = Field(default=None, max_length=200)
    user_id: str = "local"


class CaptureResponse(BaseModel):
    capture_id: str
    summary: str
    memories: list[dict[str, Any]]
    tasks: list[dict[str, Any]]
    entities: list[dict[str, Any]]
    graph: dict[str, Any]


class SourceImportRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=200)
    source_hint: str = Field(default="", max_length=80)
    processing: Literal["sync", "async"] = "async"
    max_records: int = Field(default=1000, ge=1, le=5000)
    user_id: str = "local"


class SourceAnalyzeRequest(BaseModel):
    paths: list[str] = Field(..., min_length=1, max_length=200)
    source_hint: str = Field(default="", max_length=80)
    max_records: int = Field(default=500, ge=1, le=500)


class SourceImportResponse(BaseModel):
    import_id: str
    status: str
    records_found: int
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


class SourceAccountListResponse(BaseModel):
    results: list[SourceAccountResponse]


class SourceReadinessResponse(BaseModel):
    generated_at: str
    summary: dict[str, int]
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
    pending: int
    failed: int


class SearchResponse(BaseModel):
    query: str
    results: list[dict[str, Any]]


class AskResponse(BaseModel):
    query: str
    answer: str
    citations: list[dict[str, Any]]
    results: list[dict[str, Any]]


class ListResponse(BaseModel):
    results: list[dict[str, Any]]


class GraphResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]


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
    citation_coverage: float
    last_seen: str | None = None
    status: str
    warnings: list[str] = Field(default_factory=list)


class MemoryQualityResponse(BaseModel):
    generated_at: str
    score: int
    status: str
    citation_coverage: float
    review_coverage: float
    layer_coverage: float
    layers_present: list[str]
    totals: dict[str, int]
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
