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

CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  capture_id TEXT,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
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

CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
  memory_id UNINDEXED,
  content,
  summary,
  source,
  topics
);

CREATE INDEX IF NOT EXISTS idx_memories_user_time ON memories(user_id, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_memories_kind ON memories(user_id, kind);
CREATE INDEX IF NOT EXISTS idx_captures_review ON captures(user_id, review_status, captured_at DESC);
CREATE INDEX IF NOT EXISTS idx_captures_hash ON captures(user_id, raw_hash);
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
    "ALTER TABLE memories ADD COLUMN updated_at TEXT",
]


def init_db(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as conn:
        conn.executescript(SCHEMA)
        if load_sqlite_vec(conn)[0]:
            conn.executescript(VECTOR_SCHEMA)
        _apply_lightweight_migrations(conn)
        conn.execute("PRAGMA user_version=1")
        conn.commit()


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
