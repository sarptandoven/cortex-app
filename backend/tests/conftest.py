"""Session-wide test isolation for the Cortex backend test suite.

`backend.app.main` builds a process-singleton store the first time it is imported. Whichever
test module imports it first fixes that store's on-disk location for the whole run. If that
module doesn't set CORTEX_DB_PATH/CORTEX_VAULT_PATH, the store binds to the DEFAULT path
(backend/data/Cortex.vault) — a fixed location that (a) persists across runs, so a second run
sees the first run's data and fails on duplicate detection, and (b) writes into the working
tree. This conftest is imported by pytest before any test module, so setting the env here
guarantees every test session gets a fresh, throwaway, isolated store and never touches a
real/default store.

`setdefault` is used so an explicit CI/developer override still wins.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

_SESSION_ROOT = tempfile.mkdtemp(prefix="cortex-tests-")

os.environ.setdefault("CORTEX_API_KEY", "test-token")
os.environ.setdefault("CORTEX_DB_PATH", str(Path(_SESSION_ROOT) / "cortex.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(_SESSION_ROOT) / "cortex.vault"))
