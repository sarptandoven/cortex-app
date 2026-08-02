from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .database_maintenance import shared_database_access
from .embeddings import VECTOR_DIMENSIONS
from .sqlite_runtime import sqlite3


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
  version INTEGER PRIMARY KEY,
  applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS captures (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  import_id TEXT,
  source TEXT NOT NULL,
  source_url TEXT,
  source_account_id TEXT,
  author_principal_id TEXT NOT NULL DEFAULT '',
  external_id TEXT,
  title TEXT,
  raw_text TEXT NOT NULL,
  raw_hash TEXT,
  summary TEXT,
  review_status TEXT NOT NULL DEFAULT 'pending',
  approved_at TEXT,
  archived_at TEXT,
  captured_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS import_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source_hint TEXT NOT NULL DEFAULT '',
  processing TEXT NOT NULL DEFAULT 'async',
  paths_json TEXT NOT NULL DEFAULT '[]',
  source_counts_json TEXT NOT NULL DEFAULT '[]',
  records_found INTEGER NOT NULL DEFAULT 0,
  queued INTEGER NOT NULL DEFAULT 0,
  saved INTEGER NOT NULL DEFAULT 0,
  failed INTEGER NOT NULL DEFAULT 0,
  skipped INTEGER NOT NULL DEFAULT 0,
  capture_ids_json TEXT NOT NULL DEFAULT '[]',
  errors_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT,
  deleted_at TEXT
);

CREATE TABLE IF NOT EXISTS import_records (
  id TEXT PRIMARY KEY,
  import_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL,
  source TEXT NOT NULL,
  title TEXT NOT NULL,
  source_url TEXT,
  content_hash TEXT NOT NULL,
  chars INTEGER NOT NULL DEFAULT 0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'preview',
  capture_id TEXT,
  job_id TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(import_id) REFERENCES import_sessions(id) ON DELETE CASCADE,
  FOREIGN KEY(capture_id) REFERENCES captures(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  capture_id TEXT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  layer TEXT NOT NULL DEFAULT 'semantic',
  content TEXT NOT NULL,
  summary TEXT,
  source TEXT NOT NULL,
  source_url TEXT,
  confidence TEXT NOT NULL DEFAULT 'confirmed',
  importance INTEGER NOT NULL DEFAULT 3,
  status TEXT NOT NULL DEFAULT 'active',
  sector TEXT NOT NULL DEFAULT '',
  source_type TEXT NOT NULL DEFAULT '',
  provenance_json TEXT NOT NULL DEFAULT '{}',
  topics_json TEXT NOT NULL DEFAULT '[]',
  entity_ids_json TEXT NOT NULL DEFAULT '[]',
  occurred_at TEXT,
  valid_from TEXT,
  valid_to TEXT,
  superseded_by TEXT,
  superseded_at TEXT,
  captured_at TEXT NOT NULL,
  recorded_at TEXT,
  updated_at TEXT,
  raw_excerpt TEXT,
  occurrences INTEGER NOT NULL DEFAULT 1,
  author_class TEXT NOT NULL DEFAULT 'unknown',
  author_principal_id TEXT NOT NULL DEFAULT '',
  trust_score REAL NOT NULL DEFAULT 0.5,
  FOREIGN KEY(capture_id) REFERENCES captures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  context TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  PRIMARY KEY(user_id, id)
);

CREATE TABLE IF NOT EXISTS tasks (
  id TEXT PRIMARY KEY,
  capture_id TEXT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  content TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open',
  importance INTEGER NOT NULL DEFAULT 3,
  topics_json TEXT NOT NULL DEFAULT '[]',
  entity_ids_json TEXT NOT NULL DEFAULT '[]',
  captured_at TEXT NOT NULL,
  FOREIGN KEY(capture_id) REFERENCES captures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS graph_edges (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  target_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  evidence_id TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_entities (
  memory_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(memory_id, entity_id),
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_topics (
  memory_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(memory_id, topic),
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_relations (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source_memory_id TEXT NOT NULL,
  target_memory_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  weight REAL NOT NULL DEFAULT 1.0,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  FOREIGN KEY(source_memory_id) REFERENCES memories(id) ON DELETE CASCADE,
  FOREIGN KEY(target_memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_entities (
  task_id TEXT NOT NULL,
  entity_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, entity_id),
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS task_topics (
  task_id TEXT NOT NULL,
  topic TEXT NOT NULL,
  user_id TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(task_id, topic),
  FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_events (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  object_id TEXT NOT NULL,
  object_type TEXT NOT NULL,
  event_type TEXT NOT NULL,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  fingerprint_sha256 TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_event_revisions (
  user_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS user_settings (
  user_id TEXT NOT NULL,
  key TEXT NOT NULL,
  value_json TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, key)
);

CREATE TABLE IF NOT EXISTS api_tokens (
  token_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  label TEXT NOT NULL,
  audience TEXT NOT NULL,
  token_salt TEXT NOT NULL,
  token_hash TEXT NOT NULL,
  scopes_json TEXT NOT NULL DEFAULT '[]',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_used_at TEXT,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS source_accounts (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source TEXT NOT NULL,
  account_label TEXT NOT NULL,
  account_identifier TEXT,
  connection_type TEXT NOT NULL DEFAULT 'manual',
  status TEXT NOT NULL DEFAULT 'available',
  auth_state TEXT NOT NULL DEFAULT 'not_configured',
  policy_json TEXT NOT NULL DEFAULT '{}',
  metadata_json TEXT NOT NULL DEFAULT '{}',
  last_sync_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  disconnected_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_cursors (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  source_account_id TEXT,
  source TEXT NOT NULL,
  cursor_name TEXT NOT NULL,
  cursor_value TEXT,
  high_water_mark TEXT,
  state_json TEXT NOT NULL DEFAULT '{}',
  last_started_at TEXT,
  last_completed_at TEXT,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(source_account_id) REFERENCES source_accounts(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sync_devices (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  device_name TEXT NOT NULL,
  platform TEXT NOT NULL DEFAULT 'unknown',
  device_key_hash TEXT NOT NULL,
  public_key TEXT,
  capabilities_json TEXT NOT NULL DEFAULT '[]',
  first_cursor TEXT,
  last_cursor TEXT,
  last_seen_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS sync_receipts (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  device_id TEXT NOT NULL,
  cursor TEXT NOT NULL,
  status TEXT NOT NULL,
  manifest_hash TEXT,
  remote_ref TEXT,
  error TEXT,
  stats_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(device_id) REFERENCES sync_devices(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS capture_processing_state (
  capture_id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  ingest_status TEXT NOT NULL DEFAULT 'queued',
  extraction_status TEXT NOT NULL DEFAULT 'queued',
  embedding_status TEXT NOT NULL DEFAULT 'pending',
  memory_count INTEGER NOT NULL DEFAULT 0,
  task_count INTEGER NOT NULL DEFAULT 0,
  entity_count INTEGER NOT NULL DEFAULT 0,
  last_job_id TEXT,
  last_error TEXT,
  queued_at TEXT NOT NULL,
  started_at TEXT,
  completed_at TEXT,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(capture_id) REFERENCES captures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS memory_jobs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  job_type TEXT NOT NULL,
  object_type TEXT NOT NULL,
  object_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  priority INTEGER NOT NULL DEFAULT 100,
  run_at TEXT NOT NULL,
  attempts INTEGER NOT NULL DEFAULT 0,
  max_attempts INTEGER NOT NULL DEFAULT 3,
  locked_by TEXT,
  locked_until TEXT,
  unique_key TEXT NOT NULL UNIQUE,
  payload_json TEXT NOT NULL DEFAULT '{}',
  result_json TEXT NOT NULL DEFAULT '{}',
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  completed_at TEXT
);

CREATE TABLE IF NOT EXISTS agent_sessions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  token_id TEXT,
  host_label TEXT,
  status TEXT NOT NULL DEFAULT 'active',
  goal TEXT,
  parent_session_id TEXT,
  metadata_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  last_checkpoint_at TEXT,
  updated_at TEXT NOT NULL,
  closed_at TEXT
);

CREATE TABLE IF NOT EXISTS context_packs (
  pack_sha TEXT NOT NULL,
  user_id TEXT NOT NULL,
  session_id TEXT,
  task TEXT NOT NULL DEFAULT '',
  intent TEXT NOT NULL DEFAULT '',
  surface TEXT NOT NULL DEFAULT '',
  engine_version INTEGER NOT NULL,
  resolution_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, pack_sha)
);

CREATE TABLE IF NOT EXISTS memory_corpus_revisions (
  user_id TEXT PRIMARY KEY,
  revision INTEGER NOT NULL DEFAULT 0,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_consolidation_runs (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  source_revision_before INTEGER NOT NULL,
  source_revision_after INTEGER,
  conflicts_detected INTEGER NOT NULL DEFAULT 0,
  conflicts_auto_resolved INTEGER NOT NULL DEFAULT 0,
  conflicts_review_required INTEGER NOT NULL DEFAULT 0,
  packs_warmed INTEGER NOT NULL DEFAULT 0,
  metrics_json TEXT NOT NULL DEFAULT '{}',
  started_at TEXT NOT NULL,
  completed_at TEXT,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS memory_consolidation_decisions (
  run_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  field TEXT NOT NULL,
  current_memory_id TEXT NOT NULL,
  stale_memory_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  reason TEXT NOT NULL,
  proof_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  PRIMARY KEY(run_id, field, current_memory_id, stale_memory_id)
);

CREATE TABLE IF NOT EXISTS hot_context_packs (
  user_id TEXT NOT NULL,
  cache_key TEXT NOT NULL,
  source_revision INTEGER NOT NULL,
  pack_sha TEXT NOT NULL,
  request_json TEXT NOT NULL,
  engine_version INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  hit_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_hit_at TEXT,
  PRIMARY KEY(user_id, cache_key)
);

CREATE TABLE IF NOT EXISTS working_canvas_nodes (
  user_id TEXT NOT NULL,
  session_id TEXT NOT NULL,
  node_id TEXT NOT NULL,
  label TEXT NOT NULL,
  summary TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  raw_bytes INTEGER NOT NULL,
  receipt_event_id TEXT NOT NULL,
  predecessor_node_id TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, session_id, node_id)
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  memory_id UNINDEXED,
  content,
  summary,
  source,
  topics
);

CREATE VIRTUAL TABLE IF NOT EXISTS belief_snapshot_fts USING fts5(
  event_id UNINDEXED,
  user_id UNINDEXED,
  memory_id UNINDEXED,
  content,
  topics,
  recorded_at UNINDEXED
);

CREATE TABLE IF NOT EXISTS shared_memory_principals (
  id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  label TEXT NOT NULL,
  trust_score REAL NOT NULL,
  status TEXT NOT NULL DEFAULT 'active',
  key_version INTEGER NOT NULL DEFAULT 1,
  key_fingerprint TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  revoked_at TEXT,
  PRIMARY KEY(user_id, id)
);

CREATE TABLE IF NOT EXISTS shared_memory_nonces (
  user_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  nonce TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, principal_id, nonce)
);

CREATE TABLE IF NOT EXISTS shared_memory_writes (
  id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  principal_id TEXT NOT NULL,
  nonce TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  payload_sha256 TEXT NOT NULL,
  signature TEXT NOT NULL,
  previous_hash TEXT NOT NULL,
  fingerprint_sha256 TEXT NOT NULL,
  chain_hash TEXT NOT NULL,
  capture_id TEXT,
  memory_ids_json TEXT NOT NULL DEFAULT '[]',
  disposition TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, id),
  UNIQUE(user_id, principal_id, nonce)
);

CREATE INDEX IF NOT EXISTS idx_memories_user_time ON memories(user_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(user_id, kind);
CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_entities_user_name ON entities(user_id, name);
CREATE INDEX IF NOT EXISTS idx_edges_user_source ON graph_edges(user_id, source_id);
CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(user_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_memory_topics_topic ON memory_topics(user_id, topic);
CREATE INDEX IF NOT EXISTS idx_memory_events_object ON memory_events(user_id, object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_shared_principals_status ON shared_memory_principals(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_shared_writes_principal ON shared_memory_writes(user_id, principal_id, created_at, id);

-- Pairwise digital-twin evaluation artifacts are intentionally separate from
-- memory_events. Runs are immutable replay bundles; child rows make their
-- candidates, judgments, resolutions, and rankings independently auditable.
CREATE TABLE IF NOT EXISTS twin_eval_runs (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  artifact_digest TEXT NOT NULL,
  seed_json TEXT NOT NULL,
  profile_fingerprint TEXT NOT NULL,
  spec_json TEXT NOT NULL,
  manifest_json TEXT NOT NULL,
  report_json TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY(user_id, run_id),
  UNIQUE(user_id, artifact_digest)
);

CREATE TABLE IF NOT EXISTS twin_eval_profile_artifacts (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  artifact_schema_version TEXT NOT NULL,
  profile_fingerprint TEXT NOT NULL,
  scope_digest TEXT NOT NULL,
  artifact_digest TEXT NOT NULL,
  artifact_ciphertext BLOB NOT NULL,
  expires_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id),
  UNIQUE(user_id, artifact_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS twin_eval_report_artifacts (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  artifact_id TEXT NOT NULL,
  artifact_schema_version TEXT NOT NULL,
  artifact_digest TEXT NOT NULL,
  report_digest TEXT NOT NULL,
  artifact_ciphertext BLOB NOT NULL,
  created_at TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id),
  UNIQUE(user_id, artifact_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS twin_eval_candidates (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  candidate_id TEXT NOT NULL,
  prompt_id TEXT NOT NULL,
  system_id TEXT NOT NULL,
  candidate_json TEXT NOT NULL,
  candidate_digest TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id, candidate_id),
  UNIQUE(user_id, run_id, prompt_id, system_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS twin_eval_comparisons (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  comparison_id TEXT NOT NULL,
  logical_comparison_id TEXT NOT NULL,
  left_candidate_id TEXT NOT NULL,
  right_candidate_id TEXT NOT NULL,
  comparison_json TEXT NOT NULL,
  comparison_digest TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id, comparison_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT,
  FOREIGN KEY(user_id, run_id, left_candidate_id)
    REFERENCES twin_eval_candidates(user_id, run_id, candidate_id) ON DELETE RESTRICT,
  FOREIGN KEY(user_id, run_id, right_candidate_id)
    REFERENCES twin_eval_candidates(user_id, run_id, candidate_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS twin_eval_resolved_comparisons (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  logical_comparison_id TEXT NOT NULL,
  resolved_json TEXT NOT NULL,
  resolved_digest TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id, logical_comparison_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS twin_eval_rankings (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  system_id TEXT NOT NULL,
  rating_json TEXT NOT NULL,
  rating_digest TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id, system_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS twin_eval_ranking_manifests (
  user_id TEXT NOT NULL,
  run_id TEXT NOT NULL,
  ranking_json TEXT NOT NULL,
  ranking_digest TEXT NOT NULL,
  PRIMARY KEY(user_id, run_id),
  FOREIGN KEY(user_id, run_id) REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);

-- Pairwise execution requests contain private profile and prompt material, so
-- only their encrypted CXE1 envelope is stored. This table is also the atomic
-- receipt-consumption and idempotency boundary; no paid worker is enabled yet.
CREATE TABLE IF NOT EXISTS twin_eval_execution_requests (
  user_id TEXT NOT NULL,
  evaluation_id TEXT NOT NULL,
  receipt_id TEXT NOT NULL,
  binding_key_id TEXT NOT NULL,
  idempotency_digest TEXT NOT NULL,
  request_binding TEXT NOT NULL,
  config_digest TEXT NOT NULL,
  artifact_digest TEXT NOT NULL,
  request_ciphertext BLOB,
  consent_version TEXT NOT NULL,
  receipt_consumed_at TEXT NOT NULL,
  request_expires_at TEXT NOT NULL,
  content_deleted_at TEXT,
  status TEXT NOT NULL DEFAULT 'prepared'
    CHECK(status IN (
      'prepared', 'queued', 'running', 'cancel_requested',
      'cancelled', 'succeeded', 'failed'
    )),
  attempt_count INTEGER NOT NULL DEFAULT 0
    CHECK(attempt_count BETWEEN 0 AND 1),
  provider_calls_reserved INTEGER NOT NULL DEFAULT 0
    CHECK(provider_calls_reserved >= 0),
  provider_calls_dispatched INTEGER NOT NULL DEFAULT 0
    CHECK(
      provider_calls_dispatched >= 0
      AND provider_calls_dispatched <= provider_calls_reserved
    ),
  remote_outcome_unknown INTEGER NOT NULL DEFAULT 0
    CHECK(remote_outcome_unknown IN (0, 1)),
  lease_generation INTEGER NOT NULL DEFAULT 0
    CHECK(lease_generation >= 0),
  lease_owner TEXT,
  lease_token_digest TEXT,
  lease_expires_at TEXT,
  execution_deadline_at TEXT,
  queued_at TEXT,
  started_at TEXT,
  last_heartbeat_at TEXT,
  completed_at TEXT,
  cancel_requested_at TEXT,
  result_run_id TEXT,
  result_artifact_digest TEXT,
  completion_binding TEXT,
  error_code TEXT CHECK(
    error_code IS NULL OR (
      length(error_code) BETWEEN 1 AND 80
      AND error_code NOT GLOB '*[^a-z0-9_]*'
    )
  ),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY(user_id, evaluation_id),
  UNIQUE(user_id, receipt_id),
  UNIQUE(user_id, idempotency_digest),
  FOREIGN KEY(user_id, result_run_id)
    REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_twin_eval_execution_status
  ON twin_eval_execution_requests(user_id, status, created_at);

-- Dispatch authorization is deliberately separate from request submission.
-- The singleton runtime row is an operational kill switch and epoch. Every
-- user grant is bound to that exact epoch so any config or enablement change
-- invalidates previously captured consent. Provider execution remains absent.
CREATE TABLE IF NOT EXISTS twin_eval_dispatch_runtime (
  singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
  config_digest TEXT NOT NULL,
  config_epoch INTEGER NOT NULL CHECK(config_epoch >= 1),
  dispatch_enabled INTEGER NOT NULL DEFAULT 0
    CHECK(dispatch_enabled IN (0, 1)),
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS twin_eval_dispatch_consents (
  user_id TEXT PRIMARY KEY,
  scope TEXT NOT NULL,
  consent_version TEXT NOT NULL,
  config_digest TEXT NOT NULL,
  config_epoch INTEGER NOT NULL CHECK(config_epoch >= 1),
  revision INTEGER NOT NULL CHECK(revision >= 1),
  granted_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  CHECK(expires_at > granted_at),
  CHECK(revoked_at IS NULL OR revoked_at >= granted_at)
);
CREATE INDEX IF NOT EXISTS idx_twin_eval_dispatch_consent_expiry
  ON twin_eval_dispatch_consents(expires_at, revoked_at);

CREATE INDEX IF NOT EXISTS idx_twin_eval_comparisons_logical
  ON twin_eval_comparisons(user_id, run_id, logical_comparison_id);
"""

VECTOR_SCHEMA = f"""
CREATE TABLE IF NOT EXISTS memory_vec_map (
  vec_rowid INTEGER PRIMARY KEY AUTOINCREMENT,
  memory_id TEXT NOT NULL UNIQUE,
  user_id TEXT NOT NULL,
  embedding_model TEXT NOT NULL,
  text_hash TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  FOREIGN KEY(memory_id) REFERENCES memories(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_memory_vec_map_user ON memory_vec_map(user_id, memory_id);

CREATE TABLE IF NOT EXISTS vec_index_meta (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  dimensions INTEGER NOT NULL,
  model TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
  embedding float[{VECTOR_DIMENSIONS}]
);
"""

MIGRATIONS = [
    "ALTER TABLE captures ADD COLUMN raw_hash TEXT",
    "ALTER TABLE captures ADD COLUMN review_status TEXT NOT NULL DEFAULT 'pending'",
    "ALTER TABLE captures ADD COLUMN approved_at TEXT",
    "ALTER TABLE captures ADD COLUMN archived_at TEXT",
    "ALTER TABLE captures ADD COLUMN import_id TEXT",
    "ALTER TABLE captures ADD COLUMN source_account_id TEXT",
    "ALTER TABLE captures ADD COLUMN author_principal_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE captures ADD COLUMN external_id TEXT",
    "ALTER TABLE memories ADD COLUMN updated_at TEXT",
    "ALTER TABLE memories ADD COLUMN layer TEXT NOT NULL DEFAULT 'semantic'",
    "ALTER TABLE memories ADD COLUMN sector TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE memories ADD COLUMN source_type TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE memories ADD COLUMN provenance_json TEXT NOT NULL DEFAULT '{}'",
    "ALTER TABLE memories ADD COLUMN valid_from TEXT",
    "ALTER TABLE memories ADD COLUMN valid_to TEXT",
    "ALTER TABLE memories ADD COLUMN superseded_by TEXT",
    "ALTER TABLE memories ADD COLUMN superseded_at TEXT",
    "ALTER TABLE memories ADD COLUMN recorded_at TEXT",
    "ALTER TABLE memories ADD COLUMN occurrences INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE memories ADD COLUMN author_class TEXT NOT NULL DEFAULT 'unknown'",
    "ALTER TABLE memories ADD COLUMN author_principal_id TEXT NOT NULL DEFAULT ''",
    "ALTER TABLE memories ADD COLUMN trust_score REAL NOT NULL DEFAULT 0.5",
    "ALTER TABLE memory_events ADD COLUMN fingerprint_sha256 TEXT",
    "ALTER TABLE import_sessions ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 1)",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN provider_calls_reserved INTEGER NOT NULL DEFAULT 0 CHECK(provider_calls_reserved >= 0)",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN provider_calls_dispatched INTEGER NOT NULL DEFAULT 0 CHECK(provider_calls_dispatched >= 0 AND provider_calls_dispatched <= provider_calls_reserved)",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN remote_outcome_unknown INTEGER NOT NULL DEFAULT 0 CHECK(remote_outcome_unknown IN (0, 1))",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN lease_generation INTEGER NOT NULL DEFAULT 0 CHECK(lease_generation >= 0)",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN lease_owner TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN lease_token_digest TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN lease_expires_at TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN execution_deadline_at TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN queued_at TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN started_at TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN last_heartbeat_at TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN completed_at TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN result_artifact_digest TEXT",
    "ALTER TABLE twin_eval_execution_requests ADD COLUMN completion_binding TEXT",
]

TWIN_EVAL_EXECUTION_CHECKPOINT_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS twin_eval_execution_call_checkpoints (
  user_id TEXT NOT NULL,
  evaluation_id TEXT NOT NULL,
  call_id TEXT NOT NULL,
  call_kind TEXT NOT NULL CHECK(call_kind IN ('candidate', 'judge')),
  call_ordinal INTEGER NOT NULL CHECK(call_ordinal >= 0),
  binding_key_id TEXT NOT NULL,
  coordinate_binding TEXT NOT NULL,
  payload_binding TEXT NOT NULL,
  adapter_binding TEXT NOT NULL,
  checkpoint_binding TEXT NOT NULL,
  checkpoint_ciphertext BLOB NOT NULL,
  request_artifact_digest TEXT NOT NULL,
  config_digest TEXT NOT NULL,
  consent_config_epoch INTEGER NOT NULL
    CHECK(consent_config_epoch >= 1),
  consent_revision INTEGER NOT NULL CHECK(consent_revision >= 1),
  lease_generation INTEGER NOT NULL CHECK(lease_generation >= 1),
  lease_token_digest TEXT NOT NULL,
  permit_digest TEXT NOT NULL,
  idempotency_supported INTEGER NOT NULL
    CHECK(idempotency_supported IN (0, 1)),
  state TEXT NOT NULL
    CHECK(state IN ('reserved', 'dispatching', 'outcome_unknown')),
  paid_attempt_count INTEGER NOT NULL
    CHECK(paid_attempt_count BETWEEN 0 AND 1),
  reserved_at TEXT NOT NULL,
  call_deadline_at TEXT NOT NULL,
  consumed_at TEXT,
  consume_binding TEXT,
  outcome_unknown_at TEXT,
  PRIMARY KEY(user_id, evaluation_id, call_id),
  UNIQUE(user_id, evaluation_id, call_ordinal),
  UNIQUE(user_id, evaluation_id, coordinate_binding),
  FOREIGN KEY(user_id, evaluation_id)
    REFERENCES twin_eval_execution_requests(user_id, evaluation_id)
    ON DELETE CASCADE,
  CHECK(
    (
      state = 'reserved'
      AND paid_attempt_count = 0
      AND consumed_at IS NULL
      AND consume_binding IS NULL
      AND outcome_unknown_at IS NULL
    ) OR (
      state = 'dispatching'
      AND paid_attempt_count = 1
      AND consumed_at IS NOT NULL
      AND consumed_at >= reserved_at
      AND consumed_at < call_deadline_at
      AND consume_binding IS NOT NULL
      AND outcome_unknown_at IS NULL
    ) OR (
      state = 'outcome_unknown'
      AND paid_attempt_count = 1
      AND consumed_at IS NOT NULL
      AND consumed_at >= reserved_at
      AND consumed_at < call_deadline_at
      AND consume_binding IS NOT NULL
      AND outcome_unknown_at IS NOT NULL
      AND outcome_unknown_at >= consumed_at
    )
  )
);
"""

TWIN_EVAL_EXECUTION_CHECKPOINT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS idx_twin_eval_execution_call_state
ON twin_eval_execution_call_checkpoints(
  user_id, evaluation_id, state, call_ordinal
);
"""

POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(user_id, layer);
CREATE INDEX IF NOT EXISTS idx_memories_active_recent ON memories(user_id, status, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_active_kind_rank ON memories(user_id, status, kind, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_active_layer_rank ON memories(user_id, status, layer, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_active_sector_rank ON memories(user_id, status, sector, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_validity ON memories(user_id, status, valid_from, valid_to, superseded_by);
CREATE INDEX IF NOT EXISTS idx_memories_transaction_time ON memories(user_id, recorded_at, superseded_at);
CREATE INDEX IF NOT EXISTS idx_memories_capture_status ON memories(user_id, capture_id, status);
CREATE INDEX IF NOT EXISTS idx_memory_relations_source ON memory_relations(user_id, source_memory_id, kind);
CREATE INDEX IF NOT EXISTS idx_memory_relations_target ON memory_relations(user_id, target_memory_id, kind);
CREATE INDEX IF NOT EXISTS idx_captures_import ON captures(user_id, import_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_review ON captures(user_id, review_status, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_hash ON captures(user_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_captures_source_record ON captures(user_id, source_account_id, external_id);
CREATE INDEX IF NOT EXISTS idx_captures_author_principal ON captures(user_id, author_principal_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_author_principal ON memories(user_id, author_principal_id, status, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_sessions_user_created ON import_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_sessions_user_status ON import_sessions(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_records_import ON import_records(user_id, import_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_import_records_capture ON import_records(user_id, capture_id);
CREATE INDEX IF NOT EXISTS idx_twin_eval_execution_claim
ON twin_eval_execution_requests(user_id, status, request_expires_at, created_at);
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_dispatch_runtime_insert
BEFORE INSERT ON twin_eval_dispatch_runtime
WHEN NEW.config_epoch != 1
  OR NEW.dispatch_enabled != 0
  OR EXISTS (SELECT 1 FROM twin_eval_dispatch_runtime)
BEGIN
  SELECT RAISE(ABORT, 'invalid twin dispatch runtime initialization');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_dispatch_runtime_delete
BEFORE DELETE ON twin_eval_dispatch_runtime
BEGIN
  SELECT RAISE(ABORT, 'twin dispatch runtime cannot be deleted');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_dispatch_runtime_epoch
BEFORE UPDATE ON twin_eval_dispatch_runtime
WHEN (
  (
    NEW.config_digest != OLD.config_digest
    OR NEW.dispatch_enabled != OLD.dispatch_enabled
  )
  AND NEW.config_epoch != OLD.config_epoch + 1
) OR (
  NEW.config_digest = OLD.config_digest
  AND NEW.dispatch_enabled = OLD.dispatch_enabled
  AND NEW.config_epoch != OLD.config_epoch
)
BEGIN
  SELECT RAISE(ABORT, 'invalid twin dispatch runtime epoch');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_dispatch_consent_insert
BEFORE INSERT ON twin_eval_dispatch_consents
WHEN NEW.revision != 1
  OR NEW.revoked_at IS NOT NULL
  OR EXISTS (
    SELECT 1 FROM twin_eval_dispatch_consents
    WHERE user_id = NEW.user_id
  )
  OR NOT EXISTS (
    SELECT 1 FROM twin_eval_dispatch_runtime
    WHERE singleton = 1
      AND dispatch_enabled = 1
      AND config_digest = NEW.config_digest
      AND config_epoch = NEW.config_epoch
  )
BEGIN
  SELECT RAISE(ABORT, 'invalid twin dispatch consent config');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_dispatch_consent_update
BEFORE UPDATE ON twin_eval_dispatch_consents
WHEN NEW.revision != OLD.revision + 1
  OR NEW.user_id IS NOT OLD.user_id
  OR NEW.created_at IS NOT OLD.created_at
  OR (
    NEW.revoked_at IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM twin_eval_dispatch_runtime
      WHERE singleton = 1
        AND dispatch_enabled = 1
        AND config_digest = NEW.config_digest
        AND config_epoch = NEW.config_epoch
    )
  )
  OR (
    NEW.revoked_at IS NOT NULL
    AND (
      OLD.revoked_at IS NOT NULL
      OR NEW.scope IS NOT OLD.scope
      OR NEW.consent_version IS NOT OLD.consent_version
      OR NEW.config_digest IS NOT OLD.config_digest
      OR NEW.config_epoch IS NOT OLD.config_epoch
      OR NEW.granted_at IS NOT OLD.granted_at
      OR NEW.expires_at IS NOT OLD.expires_at
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'invalid twin dispatch consent revision');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_execution_result_insert
BEFORE INSERT ON twin_eval_execution_requests
WHEN (
  NEW.status = 'succeeded'
  AND (
    NEW.result_run_id IS NULL
    OR NEW.result_artifact_digest IS NULL
    OR NEW.completion_binding IS NULL
  )
) OR (
  NEW.status != 'succeeded'
  AND (
    NEW.result_run_id IS NOT NULL
    OR NEW.result_artifact_digest IS NOT NULL
    OR NEW.completion_binding IS NOT NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'invalid twin evaluation result state');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_execution_no_open_calls
BEFORE UPDATE OF status ON twin_eval_execution_requests
WHEN NEW.status = 'succeeded'
  AND EXISTS (
    SELECT 1 FROM twin_eval_execution_call_checkpoints
    WHERE user_id = NEW.user_id
      AND evaluation_id = NEW.evaluation_id
      AND state IN ('reserved', 'dispatching', 'outcome_unknown')
  )
BEGIN
  SELECT RAISE(ABORT, 'open twin execution call has no outcome');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_execution_call_transition
BEFORE UPDATE ON twin_eval_execution_call_checkpoints
WHEN
  NEW.user_id != OLD.user_id
  OR NEW.evaluation_id != OLD.evaluation_id
  OR NEW.call_id != OLD.call_id
  OR NEW.call_kind != OLD.call_kind
  OR NEW.call_ordinal != OLD.call_ordinal
  OR NEW.binding_key_id != OLD.binding_key_id
  OR NEW.coordinate_binding != OLD.coordinate_binding
  OR NEW.payload_binding != OLD.payload_binding
  OR NEW.adapter_binding != OLD.adapter_binding
  OR NEW.checkpoint_binding != OLD.checkpoint_binding
  OR NEW.checkpoint_ciphertext != OLD.checkpoint_ciphertext
  OR NEW.request_artifact_digest != OLD.request_artifact_digest
  OR NEW.config_digest != OLD.config_digest
  OR NEW.consent_config_epoch != OLD.consent_config_epoch
  OR NEW.consent_revision != OLD.consent_revision
  OR NEW.lease_generation != OLD.lease_generation
  OR NEW.lease_token_digest != OLD.lease_token_digest
  OR NEW.permit_digest != OLD.permit_digest
  OR NEW.idempotency_supported != OLD.idempotency_supported
  OR NEW.reserved_at != OLD.reserved_at
  OR NEW.call_deadline_at != OLD.call_deadline_at
  OR NOT (
    (
      OLD.state = 'reserved'
      AND NEW.state = 'dispatching'
      AND OLD.paid_attempt_count = 0
      AND NEW.paid_attempt_count = 1
      AND OLD.consumed_at IS NULL
      AND NEW.consumed_at IS NOT NULL
      AND OLD.consume_binding IS NULL
      AND NEW.consume_binding IS NOT NULL
      AND OLD.outcome_unknown_at IS NULL
      AND NEW.outcome_unknown_at IS NULL
    ) OR (
      OLD.state = 'dispatching'
      AND NEW.state = 'outcome_unknown'
      AND NEW.paid_attempt_count = 1
      AND NEW.consumed_at = OLD.consumed_at
      AND NEW.consume_binding = OLD.consume_binding
      AND OLD.outcome_unknown_at IS NULL
      AND NEW.outcome_unknown_at IS NOT NULL
    )
  )
BEGIN
  SELECT RAISE(ABORT, 'invalid twin execution call transition');
END;
CREATE TRIGGER IF NOT EXISTS enforce_twin_eval_execution_result_update
BEFORE UPDATE OF status, result_run_id, result_artifact_digest,
  completion_binding
ON twin_eval_execution_requests
WHEN (
  NEW.status = 'succeeded'
  AND (
    NEW.result_run_id IS NULL
    OR NEW.result_artifact_digest IS NULL
    OR NEW.completion_binding IS NULL
  )
) OR (
  NEW.status != 'succeeded'
  AND (
    NEW.result_run_id IS NOT NULL
    OR NEW.result_artifact_digest IS NOT NULL
    OR NEW.completion_binding IS NOT NULL
  )
)
BEGIN
  SELECT RAISE(ABORT, 'invalid twin evaluation result state');
END;
CREATE INDEX IF NOT EXISTS idx_tasks_open_rank ON tasks(user_id, status, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_edges_user_created ON graph_edges(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_edges_user_target ON graph_edges(user_id, target_id);
CREATE INDEX IF NOT EXISTS idx_events_user_created ON memory_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_type_event_created ON memory_events(user_id, object_type, event_type, created_at DESC);
CREATE TRIGGER IF NOT EXISTS invalidate_memory_event_fingerprint
AFTER UPDATE OF id, user_id, object_id, object_type, event_type, metadata_json, created_at ON memory_events
BEGIN
  UPDATE memory_events SET fingerprint_sha256 = NULL WHERE rowid = NEW.rowid;
END;
CREATE TRIGGER IF NOT EXISTS revise_memory_events_after_insert
AFTER INSERT ON memory_events
BEGIN
  INSERT INTO memory_event_revisions(user_id, revision) VALUES (NEW.user_id, 1)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1;
END;
CREATE TRIGGER IF NOT EXISTS revise_memory_events_after_delete
AFTER DELETE ON memory_events
BEGIN
  INSERT INTO memory_event_revisions(user_id, revision) VALUES (OLD.user_id, 1)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1;
END;
CREATE TRIGGER IF NOT EXISTS revise_memory_events_after_update
AFTER UPDATE OF id, user_id, object_id, object_type, event_type, metadata_json, created_at ON memory_events
BEGIN
  INSERT INTO memory_event_revisions(user_id, revision) VALUES (OLD.user_id, 1)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1;
  INSERT INTO memory_event_revisions(user_id, revision) VALUES (NEW.user_id, 1)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1;
END;
CREATE INDEX IF NOT EXISTS idx_api_tokens_active ON api_tokens(audience, revoked_at, user_id);
CREATE INDEX IF NOT EXISTS idx_source_accounts_user_source ON source_accounts(user_id, source, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_accounts_identifier ON source_accounts(user_id, source, account_identifier);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_cursors_unique ON sync_cursors(user_id, source_account_id, source, cursor_name);
CREATE INDEX IF NOT EXISTS idx_sync_cursors_user_source ON sync_cursors(user_id, source, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_devices_user_updated ON sync_devices(user_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_sync_devices_key_hash ON sync_devices(user_id, device_key_hash);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_receipts_device_cursor ON sync_receipts(user_id, device_id, cursor);
CREATE INDEX IF NOT EXISTS idx_sync_receipts_device_updated ON sync_receipts(user_id, device_id, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_processing_user_status ON capture_processing_state(user_id, extraction_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_claim ON memory_jobs(status, run_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_user_status ON memory_jobs(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_object ON memory_jobs(user_id, object_type, object_id);
CREATE INDEX IF NOT EXISTS idx_memories_author_class ON memories(user_id, author_class);
CREATE INDEX IF NOT EXISTS idx_agent_sessions_user_status ON agent_sessions(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_packs_user_created ON context_packs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_context_packs_session ON context_packs(user_id, session_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_consolidation_runs_user_created ON memory_consolidation_runs(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_consolidation_decisions_user_run ON memory_consolidation_decisions(user_id, run_id);
CREATE INDEX IF NOT EXISTS idx_hot_context_packs_user_status ON hot_context_packs(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_working_canvas_session ON working_canvas_nodes(user_id, session_id, updated_at DESC);

CREATE TRIGGER IF NOT EXISTS revise_context_corpus_memory_insert
AFTER INSERT ON memories
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_memory_delete
AFTER DELETE ON memories
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (OLD.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_memory_update
AFTER UPDATE ON memories
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (OLD.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_task_insert
AFTER INSERT ON tasks
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_task_delete
AFTER DELETE ON tasks
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (OLD.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_task_update
AFTER UPDATE ON tasks
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (OLD.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_capture_update
AFTER UPDATE OF review_status, approved_at, archived_at ON captures
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_settings_insert
AFTER INSERT ON user_settings
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_settings_update
AFTER UPDATE ON user_settings
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (NEW.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;
CREATE TRIGGER IF NOT EXISTS revise_context_corpus_settings_delete
AFTER DELETE ON user_settings
BEGIN
  INSERT INTO memory_corpus_revisions(user_id, revision, updated_at) VALUES (OLD.user_id, 1, CURRENT_TIMESTAMP)
  ON CONFLICT(user_id) DO UPDATE SET revision = revision + 1, updated_at = CURRENT_TIMESTAMP;
END;

CREATE TABLE IF NOT EXISTS oauth_pending (
  state TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  flow TEXT NOT NULL,
  payload_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_oauth_pending_expiry ON oauth_pending(expires_at);

"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with shared_database_access(path):
        conn = sqlite3.connect(path)
        try:
            conn.executescript(SCHEMA)
            _migrate_twin_eval_execution_request_shape(conn)
            _apply_lightweight_migrations(conn)
            _migrate_entities_composite_primary_key(conn)
            conn.execute("SAVEPOINT twin_checkpoint_migration")
            try:
                _recover_interrupted_twin_eval_checkpoint_migration(
                    conn
                )
                conn.execute(
                    TWIN_EVAL_EXECUTION_CHECKPOINT_TABLE_SQL
                )
                conn.execute(
                    TWIN_EVAL_EXECUTION_CHECKPOINT_INDEX_SQL
                )
                _migrate_twin_eval_execution_checkpoint_shape(conn)
                conn.execute(
                    "RELEASE SAVEPOINT twin_checkpoint_migration"
                )
            except Exception:
                conn.execute(
                    "ROLLBACK TO SAVEPOINT twin_checkpoint_migration"
                )
                conn.execute(
                    "RELEASE SAVEPOINT twin_checkpoint_migration"
                )
                raise
            conn.executescript(POST_MIGRATION_INDEXES)
            if load_sqlite_vec(conn)[0]:
                conn.executescript(VECTOR_SCHEMA)
            conn.execute("PRAGMA user_version=1")
            conn.commit()
        finally:
            conn.close()


def _migrate_twin_eval_execution_request_shape(
    conn: sqlite3.Connection,
) -> None:
    """Replace the pre-control-plane prototype table without trusting it."""

    info = conn.execute(
        "PRAGMA table_info(twin_eval_execution_requests)"
    ).fetchall()
    if not info:
        return
    columns = {str(row[1]): row for row in info}
    control_plane_columns = {
        "user_id",
        "evaluation_id",
        "receipt_id",
        "binding_key_id",
        "idempotency_digest",
        "request_binding",
        "config_digest",
        "artifact_digest",
        "request_ciphertext",
        "consent_version",
        "receipt_consumed_at",
        "request_expires_at",
        "content_deleted_at",
        "status",
        "cancel_requested_at",
        "result_run_id",
        "error_code",
        "created_at",
        "updated_at",
    }
    ciphertext_is_nullable = (
        "request_ciphertext" in columns
        and int(columns["request_ciphertext"][3]) == 0
    )
    if control_plane_columns.issubset(columns) and ciphertext_is_nullable:
        return
    legacy_core = {
        "user_id",
        "evaluation_id",
        "receipt_id",
        "idempotency_digest",
        "config_digest",
        "artifact_digest",
        "consent_version",
        "status",
        "created_at",
        "updated_at",
    }
    if not legacy_core.issubset(columns):
        raise sqlite3.OperationalError(
            "unsupported twin evaluation execution table shape"
        )

    request_binding_source = (
        "request_binding"
        if "request_binding" in columns
        else "request_digest"
        if "request_digest" in columns
        else "artifact_digest"
    )
    conn.execute("DROP INDEX IF EXISTS idx_twin_eval_execution_status")
    conn.execute("DROP INDEX IF EXISTS idx_twin_eval_execution_claim")
    conn.execute(
        "ALTER TABLE twin_eval_execution_requests "
        "RENAME TO twin_eval_execution_requests_legacy_shape"
    )
    conn.execute(
        """
        CREATE TABLE twin_eval_execution_requests (
          user_id TEXT NOT NULL,
          evaluation_id TEXT NOT NULL,
          receipt_id TEXT NOT NULL,
          binding_key_id TEXT NOT NULL,
          idempotency_digest TEXT NOT NULL,
          request_binding TEXT NOT NULL,
          config_digest TEXT NOT NULL,
          artifact_digest TEXT NOT NULL,
          request_ciphertext BLOB,
          consent_version TEXT NOT NULL,
          receipt_consumed_at TEXT NOT NULL,
          request_expires_at TEXT NOT NULL,
          content_deleted_at TEXT,
          status TEXT NOT NULL DEFAULT 'prepared'
            CHECK(status IN (
              'prepared', 'queued', 'running', 'cancel_requested',
              'cancelled', 'succeeded', 'failed'
            )),
          attempt_count INTEGER NOT NULL DEFAULT 0
            CHECK(attempt_count BETWEEN 0 AND 1),
          provider_calls_reserved INTEGER NOT NULL DEFAULT 0
            CHECK(provider_calls_reserved >= 0),
          provider_calls_dispatched INTEGER NOT NULL DEFAULT 0
            CHECK(
              provider_calls_dispatched >= 0
              AND provider_calls_dispatched <= provider_calls_reserved
            ),
          remote_outcome_unknown INTEGER NOT NULL DEFAULT 0
            CHECK(remote_outcome_unknown IN (0, 1)),
          lease_generation INTEGER NOT NULL DEFAULT 0
            CHECK(lease_generation >= 0),
          lease_owner TEXT,
          lease_token_digest TEXT,
          lease_expires_at TEXT,
          execution_deadline_at TEXT,
          queued_at TEXT,
          started_at TEXT,
          last_heartbeat_at TEXT,
          completed_at TEXT,
          cancel_requested_at TEXT,
          result_run_id TEXT,
          result_artifact_digest TEXT,
          completion_binding TEXT,
          error_code TEXT CHECK(
            error_code IS NULL OR (
              length(error_code) BETWEEN 1 AND 80
              AND error_code NOT GLOB '*[^a-z0-9_]*'
            )
          ),
          created_at TEXT NOT NULL,
          updated_at TEXT NOT NULL,
          PRIMARY KEY(user_id, evaluation_id),
          UNIQUE(user_id, receipt_id),
          UNIQUE(user_id, idempotency_digest),
          FOREIGN KEY(user_id, result_run_id)
            REFERENCES twin_eval_runs(user_id, run_id) ON DELETE RESTRICT
        )
        """
    )
    conn.execute(
        f"""
        INSERT INTO twin_eval_execution_requests
        (
          user_id, evaluation_id, receipt_id, binding_key_id,
          idempotency_digest, request_binding, config_digest,
          artifact_digest, request_ciphertext, consent_version,
          receipt_consumed_at, request_expires_at, content_deleted_at,
          status, attempt_count, lease_generation, completed_at,
          cancel_requested_at, created_at, updated_at
        )
        SELECT
          user_id, evaluation_id, receipt_id, 'legacy-unavailable',
          idempotency_digest, {request_binding_source}, config_digest,
          artifact_digest, NULL, consent_version,
          created_at, created_at, updated_at,
          'cancelled', 0, 0, updated_at,
          updated_at, created_at, updated_at
        FROM twin_eval_execution_requests_legacy_shape
        """
    )
    conn.execute("DROP TABLE twin_eval_execution_requests_legacy_shape")
    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_twin_eval_execution_status
        ON twin_eval_execution_requests(user_id, status, created_at)
        """
    )


def _recover_interrupted_twin_eval_checkpoint_migration(
    conn: sqlite3.Connection,
) -> None:
    """Recover the only safe states left by the former non-atomic rebuild."""

    tables = {
        str(row[0])
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name IN (
                'twin_eval_execution_call_checkpoints',
                'twin_eval_execution_call_checkpoints_legacy_shape'
              )
            """
        ).fetchall()
    }
    legacy = "twin_eval_execution_call_checkpoints_legacy_shape"
    current = "twin_eval_execution_call_checkpoints"
    if legacy not in tables:
        return
    conn.execute(
        "DROP TRIGGER IF EXISTS enforce_twin_eval_execution_no_open_calls"
    )
    conn.execute(
        """
        DROP TRIGGER IF EXISTS
          enforce_twin_eval_execution_call_transition
        """
    )
    conn.execute(
        "DROP INDEX IF EXISTS idx_twin_eval_execution_call_state"
    )
    legacy_count = int(
        conn.execute(
            """
            SELECT COUNT(*)
            FROM twin_eval_execution_call_checkpoints_legacy_shape
            """
        ).fetchone()[0]
    )
    current_count = (
        0
        if current not in tables
        else int(
            conn.execute(
                """
                SELECT COUNT(*)
                FROM twin_eval_execution_call_checkpoints
                """
            ).fetchone()[0]
        )
    )
    if legacy_count and current_count:
        raise sqlite3.OperationalError(
            "ambiguous interrupted twin checkpoint migration"
        )
    if legacy_count:
        if current in tables:
            conn.execute(
                "DROP TABLE twin_eval_execution_call_checkpoints"
            )
        conn.execute(
            """
            ALTER TABLE twin_eval_execution_call_checkpoints_legacy_shape
            RENAME TO twin_eval_execution_call_checkpoints
            """
        )
        return
    conn.execute(
        """
        DROP TABLE twin_eval_execution_call_checkpoints_legacy_shape
        """
    )


def _migrate_twin_eval_execution_checkpoint_shape(
    conn: sqlite3.Connection,
) -> None:
    """Add the one-shot dispatch state machine without trusting old state."""

    info = conn.execute(
        "PRAGMA table_info(twin_eval_execution_call_checkpoints)"
    ).fetchall()
    if not info:
        return
    columns = {str(row[1]) for row in info}
    table_sql_row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = 'table'
          AND name = 'twin_eval_execution_call_checkpoints'
        """
    ).fetchone()
    table_sql = "" if table_sql_row is None else str(table_sql_row[0])
    dispatch_columns = {
        "consumed_at",
        "consume_binding",
        "outcome_unknown_at",
    }
    if dispatch_columns.issubset(columns) and "outcome_unknown" in table_sql:
        return
    legacy_columns = {
        "user_id",
        "evaluation_id",
        "call_id",
        "call_kind",
        "call_ordinal",
        "binding_key_id",
        "coordinate_binding",
        "payload_binding",
        "adapter_binding",
        "checkpoint_binding",
        "checkpoint_ciphertext",
        "request_artifact_digest",
        "config_digest",
        "consent_config_epoch",
        "consent_revision",
        "lease_generation",
        "lease_token_digest",
        "permit_digest",
        "idempotency_supported",
        "state",
        "paid_attempt_count",
        "reserved_at",
        "call_deadline_at",
    }
    if not legacy_columns.issubset(columns):
        raise sqlite3.OperationalError(
            "unsupported twin execution checkpoint table shape"
        )
    invalid = conn.execute(
        """
        SELECT 1 FROM twin_eval_execution_call_checkpoints
        WHERE state != 'reserved'
        LIMIT 1
        """
    ).fetchone()
    if invalid is not None:
        raise sqlite3.OperationalError(
            "unsupported legacy twin execution checkpoint state"
        )
    conn.execute(
        "DROP TRIGGER IF EXISTS enforce_twin_eval_execution_no_open_calls"
    )
    conn.execute(
        """
        DROP TRIGGER IF EXISTS
          enforce_twin_eval_execution_call_transition
        """
    )
    conn.execute(
        "DROP INDEX IF EXISTS idx_twin_eval_execution_call_state"
    )
    conn.execute(
        """
        ALTER TABLE twin_eval_execution_call_checkpoints
        RENAME TO twin_eval_execution_call_checkpoints_legacy_shape
        """
    )
    conn.execute(TWIN_EVAL_EXECUTION_CHECKPOINT_TABLE_SQL)
    conn.execute(TWIN_EVAL_EXECUTION_CHECKPOINT_INDEX_SQL)
    conn.execute(
        """
        INSERT INTO twin_eval_execution_call_checkpoints (
          user_id, evaluation_id, call_id, call_kind, call_ordinal,
          binding_key_id, coordinate_binding, payload_binding,
          adapter_binding, checkpoint_binding, checkpoint_ciphertext,
          request_artifact_digest, config_digest, consent_config_epoch,
          consent_revision, lease_generation, lease_token_digest,
          permit_digest, idempotency_supported, state,
          paid_attempt_count, reserved_at, call_deadline_at,
          consumed_at, consume_binding, outcome_unknown_at
        )
        SELECT
          user_id, evaluation_id, call_id, call_kind, call_ordinal,
          binding_key_id, coordinate_binding, payload_binding,
          adapter_binding, checkpoint_binding, checkpoint_ciphertext,
          request_artifact_digest, config_digest, consent_config_epoch,
          consent_revision, lease_generation, lease_token_digest,
          permit_digest, idempotency_supported, 'reserved',
          0, reserved_at, call_deadline_at,
          NULL, NULL, NULL
        FROM twin_eval_execution_call_checkpoints_legacy_shape
        """
    )
    conn.execute(
        "DROP TABLE twin_eval_execution_call_checkpoints_legacy_shape"
    )


def load_sqlite_vec(conn: sqlite3.Connection) -> tuple[bool, str | None]:
    try:
        import sqlite_vec  # type: ignore
    except Exception as exc:
        return False, f"sqlite-vec unavailable: {exc}"

    # Idempotent: connect() loads sqlite-vec at open, and callers (sqlite_vec_status, _vector_ready)
    # re-check per operation. Re-registering the vec0 module on the same connection errors with
    # "error during initialization", which previously disabled vectors mid-transaction. If the
    # extension already answers vec_version(), it's loaded — don't load it again.
    try:
        conn.execute("SELECT vec_version()")
        return True, None
    except sqlite3.Error:
        pass

    if not hasattr(conn, "enable_load_extension"):
        return False, "sqlite-vec unavailable: this Python SQLite build cannot load extensions"

    try:
        conn.enable_load_extension(True)
        sqlite_vec.load(conn)
        conn.enable_load_extension(False)
        return True, None
    except Exception as exc:
        try:
            conn.enable_load_extension(False)
        except Exception:
            pass
        return False, f"sqlite-vec load failed: {exc}"


def sqlite_vec_status(conn: sqlite3.Connection) -> dict[str, str | bool | None]:
    available, reason = load_sqlite_vec(conn)
    version = None
    if available:
        try:
            version = conn.execute("SELECT vec_version()").fetchone()[0]
        except sqlite3.Error as exc:
            available = False
            reason = f"sqlite-vec version check failed: {exc}"
    return {"available": available, "version": version, "reason": reason}


def _apply_lightweight_migrations(conn: sqlite3.Connection) -> None:
    known_columns: dict[str, set[str]] = {}
    for statement in MIGRATIONS:
        # Every migration above is an ADD COLUMN statement. Check the current
        # schema before executing it instead of relying on SQLite's duplicate-
        # column error: newer SQLite versions can fail while rolling back a
        # duplicate constrained column when another CHECK references it.
        parts = statement.split()
        migration_target: tuple[str, str] | None = None
        if (
            len(parts) >= 6
            and parts[0:2] == ["ALTER", "TABLE"]
            and parts[3:5] == ["ADD", "COLUMN"]
        ):
            table_name = parts[2]
            column_name = parts[5]
            migration_target = (table_name, column_name)
            columns = known_columns.setdefault(
                table_name,
                {
                    str(row[1])
                    for row in conn.execute(
                        f'PRAGMA table_info("{table_name}")'
                    ).fetchall()
                },
            )
            if column_name in columns:
                continue
        try:
            conn.execute(statement)
            if migration_target is not None:
                known_columns[table_name].add(column_name)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise
    # M2 Proof-of-Belief: old rows predate explicit transaction time. Preserve them honestly as
    # legacy-derived transaction bounds rather than pretending they were cryptographically sealed
    # when first learned. New writes use a dedicated microsecond-precision recorded_at, while this
    # backfill gives old vaults deterministic point-in-time behavior immediately after migration.
    conn.execute(
        "UPDATE memories SET recorded_at = captured_at "
        "WHERE recorded_at IS NULL OR recorded_at = ''"
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO memory_event_revisions(user_id, revision)
        SELECT user_id, 1 FROM memory_events GROUP BY user_id
        """
    )
    conn.execute(
        """
        UPDATE twin_eval_execution_requests
        SET status = 'cancelled',
            completed_at = COALESCE(completed_at, updated_at),
            cancel_requested_at = COALESCE(
              cancel_requested_at, updated_at
            ),
            lease_owner = NULL,
            lease_token_digest = NULL,
            lease_expires_at = NULL,
            execution_deadline_at = NULL,
            last_heartbeat_at = NULL
        WHERE (
            status = 'queued' AND queued_at IS NULL
          ) OR (
            status IN ('running', 'cancel_requested')
            AND (
              attempt_count != 1
              OR lease_owner IS NULL
              OR lease_token_digest IS NULL
              OR lease_expires_at IS NULL
              OR execution_deadline_at IS NULL
            )
          )
        """
    )
    conn.execute(
        """
        UPDATE twin_eval_execution_requests
        SET completed_at = COALESCE(completed_at, updated_at)
        WHERE status IN ('cancelled', 'succeeded', 'failed')
          AND completed_at IS NULL
        """
    )
    # Never invent transaction time from updated_at: that field may be a trust rescore, rebuild, or
    # metadata edit. Only an actual pre-M2 conflict_resolved receipt is a defensible supersession
    # boundary. Rows without one remain explicitly unknown/legacy-derived.
    conn.execute(
        """
        UPDATE memories
        SET superseded_at = (
          SELECT MIN(e.created_at)
          FROM memory_events AS e
          WHERE e.user_id = memories.user_id
            AND e.event_type = 'conflict_resolved'
            AND json_valid(e.metadata_json)
            AND json_extract(e.metadata_json, '$.stale_id') = memories.id
        )
        WHERE superseded_by IS NOT NULL AND superseded_by != ''
          AND (superseded_at IS NULL OR superseded_at = '')
          AND EXISTS (
            SELECT 1 FROM memory_events AS e
            WHERE e.user_id = memories.user_id
              AND e.event_type = 'conflict_resolved'
              AND json_valid(e.metadata_json)
              AND json_extract(e.metadata_json, '$.stale_id') = memories.id
          )
        """
    )


def _migrate_entities_composite_primary_key(conn: sqlite3.Connection) -> None:
    table_info = conn.execute("PRAGMA table_info(entities)").fetchall()
    if not table_info:
        return

    primary_key_columns = [
        row[1]
        for row in sorted((row for row in table_info if row[5]), key=lambda row: row[5])
    ]
    if primary_key_columns == ["user_id", "id"]:
        return

    legacy_columns = {row[1] for row in table_info}

    def column_or_default(name: str, default: str) -> str:
        return name if name in legacy_columns else default

    first_seen = column_or_default("first_seen", "CURRENT_TIMESTAMP")
    last_seen = (
        "COALESCE(last_seen, first_seen, CURRENT_TIMESTAMP)"
        if {"last_seen", "first_seen"}.issubset(legacy_columns)
        else f"COALESCE({column_or_default('last_seen', first_seen)}, CURRENT_TIMESTAMP)"
    )
    select_columns = [
        "id",
        "user_id",
        "COALESCE(kind, 'person')" if "kind" in legacy_columns else "'person'",
        "COALESCE(name, id)" if "name" in legacy_columns else "id",
        "COALESCE(aliases_json, '[]')" if "aliases_json" in legacy_columns else "'[]'",
        column_or_default("context", "NULL"),
        f"COALESCE({first_seen}, CURRENT_TIMESTAMP)",
        last_seen,
    ]

    conn.execute("SAVEPOINT migrate_entities_primary_key")
    try:
        conn.execute("ALTER TABLE entities RENAME TO entities_legacy_primary_key")
        conn.execute(
            """
            CREATE TABLE entities (
              id TEXT NOT NULL,
              user_id TEXT NOT NULL,
              kind TEXT NOT NULL,
              name TEXT NOT NULL,
              aliases_json TEXT NOT NULL DEFAULT '[]',
              context TEXT,
              first_seen TEXT NOT NULL,
              last_seen TEXT NOT NULL,
              PRIMARY KEY(user_id, id)
            )
            """
        )
        conn.execute(
            f"""
            INSERT INTO entities
            (id, user_id, kind, name, aliases_json, context, first_seen, last_seen)
            SELECT {", ".join(select_columns)}
            FROM entities_legacy_primary_key
            """
        )
        conn.execute("DROP TABLE entities_legacy_primary_key")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_entities_user_name ON entities(user_id, name)")
        conn.execute("RELEASE SAVEPOINT migrate_entities_primary_key")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT migrate_entities_primary_key")
        conn.execute("RELEASE SAVEPOINT migrate_entities_primary_key")
        raise


@contextmanager
def connect(path: Path) -> Iterator[sqlite3.Connection]:
    with shared_database_access(path):
        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=5000")
        load_sqlite_vec(conn)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()
