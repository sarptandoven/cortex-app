from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .embeddings import VECTOR_DIMENSIONS


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
  topics_json TEXT NOT NULL DEFAULT '[]',
  entity_ids_json TEXT NOT NULL DEFAULT '[]',
  occurred_at TEXT,
  captured_at TEXT NOT NULL,
  updated_at TEXT,
  raw_excerpt TEXT,
  FOREIGN KEY(capture_id) REFERENCES captures(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS entities (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  name TEXT NOT NULL,
  aliases_json TEXT NOT NULL DEFAULT '[]',
  context TEXT,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL
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
  created_at TEXT NOT NULL
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

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  memory_id UNINDEXED,
  content,
  summary,
  source,
  topics
);

CREATE INDEX IF NOT EXISTS idx_memories_user_time ON memories(user_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(user_id, kind);
CREATE INDEX IF NOT EXISTS idx_tasks_open ON tasks(user_id, status);
CREATE INDEX IF NOT EXISTS idx_entities_user_name ON entities(user_id, name);
CREATE INDEX IF NOT EXISTS idx_edges_user_source ON graph_edges(user_id, source_id);
CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(user_id, entity_id);
CREATE INDEX IF NOT EXISTS idx_memory_topics_topic ON memory_topics(user_id, topic);
CREATE INDEX IF NOT EXISTS idx_memory_events_object ON memory_events(user_id, object_type, object_id);
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
    "ALTER TABLE memories ADD COLUMN updated_at TEXT",
    "ALTER TABLE memories ADD COLUMN layer TEXT NOT NULL DEFAULT 'semantic'",
    "ALTER TABLE import_sessions ADD COLUMN skipped INTEGER NOT NULL DEFAULT 0",
]

POST_MIGRATION_INDEXES = """
CREATE INDEX IF NOT EXISTS idx_memories_layer ON memories(user_id, layer);
CREATE INDEX IF NOT EXISTS idx_memories_active_recent ON memories(user_id, status, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_active_kind_rank ON memories(user_id, status, kind, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_active_layer_rank ON memories(user_id, status, layer, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_capture_status ON memories(user_id, capture_id, status);
CREATE INDEX IF NOT EXISTS idx_captures_import ON captures(user_id, import_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_review ON captures(user_id, review_status, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_hash ON captures(user_id, raw_hash);
CREATE INDEX IF NOT EXISTS idx_import_sessions_user_created ON import_sessions(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_sessions_user_status ON import_sessions(user_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_import_records_import ON import_records(user_id, import_id, ordinal);
CREATE INDEX IF NOT EXISTS idx_import_records_capture ON import_records(user_id, capture_id);
CREATE INDEX IF NOT EXISTS idx_tasks_open_rank ON tasks(user_id, status, importance DESC, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_edges_user_created ON graph_edges(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_edges_user_target ON graph_edges(user_id, target_id);
CREATE INDEX IF NOT EXISTS idx_events_user_created ON memory_events(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_user_type_event_created ON memory_events(user_id, object_type, event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_api_tokens_active ON api_tokens(audience, revoked_at, user_id);
CREATE INDEX IF NOT EXISTS idx_source_accounts_user_source ON source_accounts(user_id, source, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_source_accounts_identifier ON source_accounts(user_id, source, account_identifier);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sync_cursors_unique ON sync_cursors(user_id, source_account_id, source, cursor_name);
CREATE INDEX IF NOT EXISTS idx_sync_cursors_user_source ON sync_cursors(user_id, source, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_capture_processing_user_status ON capture_processing_state(user_id, extraction_status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_claim ON memory_jobs(status, run_at, priority, created_at);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_user_status ON memory_jobs(user_id, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_memory_jobs_object ON memory_jobs(user_id, object_type, object_id);
"""


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(SCHEMA)
        _apply_lightweight_migrations(conn)
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
