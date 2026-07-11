"""In-process live activity feed for the local-first server.

A tiny, thread-safe ring buffer of "something is happening" events — a memory was learned,
an import started, a source was purged, a connector synced. The macOS app long-polls
``GET /v1/activity`` to render a live, per-item ticker of what the backend is doing right now
(each memory appears as it lands, not a summary that refreshes every few seconds).

This is deliberately EPHEMERAL and process-local. It is NOT the durable ``memory_events``
audit log (that lives in SQLite and is authoritative). Nothing here is persisted, replayed on
restart, or trusted for correctness — it exists only to make the pipeline feel alive. Losing
it (server restart, buffer overflow) costs nothing but a momentarily quiet ticker.

Transport is a bounded long-poll rather than SSE on purpose: the stdlib ThreadingHTTPServer
caps concurrency and a never-closing SSE connection would pin a worker thread. A long-poll
returns each event within milliseconds of publication (a condition variable wakes the waiter),
then closes — friendly to Content-Length responses and trivial to consume from URLSession.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from datetime import datetime, timezone
from typing import Any

# Keep the buffer small: it is a live ticker, not history. A burst import can produce
# thousands of memories; the client paces its own reveal, and anything older than the last
# few hundred events is stale for "what's happening right now" purposes.
_MAX_EVENTS = 512
_MAX_WAIT_SECONDS = 8.0


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


class ActivityHub:
    """Thread-safe, bounded, monotonic-sequence live event buffer."""

    def __init__(self, maxlen: int = _MAX_EVENTS) -> None:
        self._cond = threading.Condition()
        self._events: deque[dict[str, Any]] = deque(maxlen=maxlen)
        self._seq = 0

    def publish(
        self,
        user_id: str,
        kind: str,
        action: str,
        *,
        title: str = "",
        detail: str = "",
        source: str = "",
        object_id: str | None = None,
        count: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> int:
        """Append an event and wake any long-poll waiters. Never raises for bad input —
        callers publish best-effort from hot paths and must not be able to break a save."""
        with self._cond:
            self._seq += 1
            event: dict[str, Any] = {
                "seq": self._seq,
                "ts": _now_iso(),
                "user_id": str(user_id or ""),
                "kind": str(kind or "info")[:40],
                "action": str(action or "")[:40],
                "title": str(title or "")[:300],
                "detail": str(detail or "")[:300],
                "source": str(source or "")[:120],
            }
            if object_id:
                event["object_id"] = str(object_id)[:120]
            if count is not None:
                try:
                    event["count"] = int(count)
                except (TypeError, ValueError):
                    pass
            if extra:
                for key, value in extra.items():
                    if isinstance(value, (str, int, float, bool)) or value is None:
                        event[str(key)[:40]] = value
            self._events.append(event)
            self._cond.notify_all()
            return self._seq

    def latest_seq(self) -> int:
        with self._cond:
            return self._seq

    def _collect(self, cursor: int, user_id: str | None) -> list[dict[str, Any]]:
        # Events with an empty user_id are process-wide (e.g. server info) and go to everyone.
        return [
            event
            for event in self._events
            if event["seq"] > cursor
            and (user_id is None or event["user_id"] in ("", user_id))
        ]

    def since(self, cursor: int, user_id: str | None = None) -> list[dict[str, Any]]:
        with self._cond:
            return self._collect(cursor, user_id)

    def wait_since(
        self,
        cursor: int,
        user_id: str | None = None,
        timeout: float = 5.0,
    ) -> list[dict[str, Any]]:
        """Return events newer than ``cursor`` immediately if any exist; otherwise block up to
        ``timeout`` seconds for the next publish, then return whatever arrived (possibly empty)."""
        timeout = max(0.0, min(float(timeout or 0.0), _MAX_WAIT_SECONDS))
        deadline = time.monotonic() + timeout
        with self._cond:
            pending = self._collect(cursor, user_id)
            if pending:
                return pending
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return self._collect(cursor, user_id)
                self._cond.wait(timeout=remaining)
                pending = self._collect(cursor, user_id)
                if pending:
                    return pending


# Process-global singleton. The local server is single-process; the hosted server gets a
# per-worker view (acceptable — the live ticker is best-effort and per-instance by nature).
activity_hub = ActivityHub()
