from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, Field


MemoryKind = Literal["claim", "decision", "event", "preference", "observation", "action", "question", "summary"]


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


class SearchResponse(BaseModel):
    query: str
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
    top_topics: list[dict[str, Any]]
    top_entities: list[dict[str, Any]]


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
    redact_sensitive_context: bool


class SettingsUpdateRequest(BaseModel):
    review_new_captures: bool | None = None
    allow_pending_in_context: bool | None = None
    context_pack_limit: int | None = None
    allow_agent_reads: bool | None = None
    allow_agent_writes: bool | None = None
    allow_agent_exports: bool | None = None
    redact_sensitive_context: bool | None = None


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
    vault: dict[str, Any] | None = None


class BackupResponse(BaseModel):
    backup_path: str
    size_bytes: int
    created_at: str


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
    vector_model: str | None = None


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
