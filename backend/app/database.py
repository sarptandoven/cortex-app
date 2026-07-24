from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

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
]

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
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        _apply_lightweight_migrations(conn)
        _migrate_entities_composite_primary_key(conn)
        conn.executescript(POST_MIGRATION_INDEXES)
        if load_sqlite_vec(conn)[0]:
            conn.executescript(VECTOR_SCHEMA)
        conn.execute("PRAGMA user_version=1")
        conn.commit()
    finally:
        conn.close()


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
    for statement in MIGRATIONS:
        try:
            conn.execute(statement)
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
