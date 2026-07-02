from __future__ import annotations

try:
    import pysqlite3 as sqlite3  # type: ignore

    SQLITE_RUNTIME = "pysqlite3"
except Exception:
    import sqlite3  # type: ignore

    SQLITE_RUNTIME = "stdlib"

