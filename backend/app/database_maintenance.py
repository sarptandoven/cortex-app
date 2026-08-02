from __future__ import annotations

import fcntl
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator


class DatabaseMaintenanceBusy(RuntimeError):
    """Another process or connection is using the database."""


class _LocalFence:
    def __init__(self) -> None:
        self.condition = threading.Condition()
        self.shared_count = 0
        self.exclusive = False


_REGISTRY_LOCK = threading.Lock()
_LOCAL_FENCES: dict[str, _LocalFence] = {}
_EXCLUSIVE_OWNERS = threading.local()


def _database_key(path: Path) -> str:
    return str(Path(path).expanduser().resolve())


def _lock_path(path: Path) -> Path:
    database_path = Path(path).expanduser().resolve()
    return database_path.with_name(f".{database_path.name}.maintenance.lock")


def _local_fence(path: Path) -> _LocalFence:
    key = _database_key(path)
    with _REGISTRY_LOCK:
        return _LOCAL_FENCES.setdefault(key, _LocalFence())


def _owned_exclusive_keys() -> set[str]:
    keys = getattr(_EXCLUSIVE_OWNERS, "keys", None)
    if keys is None:
        keys = set()
        _EXCLUSIVE_OWNERS.keys = keys
    return keys


def require_exclusive_database_maintenance(path: Path) -> None:
    """Reject internal bypass connections without an owned exclusive fence."""

    if _database_key(path) not in _owned_exclusive_keys():
        raise DatabaseMaintenanceBusy(
            "maintenance bypass requires the exclusive database fence"
        )


def _open_lock_file(path: Path) -> int:
    lock_path = _lock_path(path)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(
        lock_path,
        os.O_CREAT | os.O_RDWR,
        0o600,
    )
    os.fchmod(descriptor, 0o600)
    return descriptor


@contextmanager
def shared_database_access(path: Path) -> Iterator[None]:
    """Hold the process-wide shared fence for one connection lifetime."""

    fence = _local_fence(path)
    with fence.condition:
        while fence.exclusive:
            fence.condition.wait()
        fence.shared_count += 1
    descriptor: int | None = None
    try:
        descriptor = _open_lock_file(path)
        fcntl.flock(descriptor, fcntl.LOCK_SH)
        yield
    finally:
        if descriptor is not None:
            try:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        with fence.condition:
            fence.shared_count -= 1
            fence.condition.notify_all()


@contextmanager
def exclusive_database_maintenance(path: Path) -> Iterator[None]:
    """Acquire a non-blocking exclusive fence after local users drain."""

    fence = _local_fence(path)
    with fence.condition:
        if fence.exclusive or fence.shared_count:
            raise DatabaseMaintenanceBusy(
                "database has active in-process connections"
            )
        fence.exclusive = True
    descriptor: int | None = None
    acquired = False
    try:
        descriptor = _open_lock_file(path)
        try:
            fcntl.flock(
                descriptor,
                fcntl.LOCK_EX | fcntl.LOCK_NB,
            )
        except BlockingIOError as exc:
            raise DatabaseMaintenanceBusy(
                "database is active in another Cortex process"
            ) from exc
        acquired = True
        _owned_exclusive_keys().add(_database_key(path))
        yield
    finally:
        _owned_exclusive_keys().discard(_database_key(path))
        if descriptor is not None:
            try:
                if acquired:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)
        with fence.condition:
            fence.exclusive = False
            fence.condition.notify_all()


class MaintenanceLockedConnection:
    """Delegate to sqlite while releasing its shared fence on close."""

    __slots__ = ("_connection", "_guard", "_closed")

    def __init__(self, connection: Any, guard: Any) -> None:
        object.__setattr__(self, "_connection", connection)
        object.__setattr__(self, "_guard", guard)
        object.__setattr__(self, "_closed", False)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self.__slots__:
            object.__setattr__(self, name, value)
            return
        setattr(self._connection, name, value)

    def __enter__(self) -> MaintenanceLockedConnection:
        self._connection.__enter__()
        return self

    def __exit__(self, *args: Any) -> Any:
        try:
            return self._connection.__exit__(*args)
        finally:
            self.close()

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._connection.close()
        finally:
            self._guard.__exit__(None, None, None)


def maintenance_locked_connect(
    path: Path,
    connect_factory: Callable[[], Any],
) -> MaintenanceLockedConnection:
    """Open a SQLite connection while owning the shared maintenance fence."""

    guard = shared_database_access(path)
    guard.__enter__()
    try:
        connection = connect_factory()
    except Exception:
        guard.__exit__(*sys.exc_info())
        raise
    return MaintenanceLockedConnection(connection, guard)
