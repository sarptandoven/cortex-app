"""In-process observability: a metrics registry + structured event ring buffer, gated by
``CORTEX_OBSERVABILITY_ENABLED``.

Design goals:
- **Zero cost when disabled.** Every record call early-returns on a bool check, so the 578
  existing tests and any deployment that leaves the flag off pay nothing.
- **Single-process, dependency-free.** Fits the 10k-tier single/large-instance server without
  pulling in Prometheus/OTel. ``snapshot()`` returns a plain dict that a real exporter (or a
  scrape endpoint) can serialize later without changing any call site.
- **Bounded cardinality.** Labels are meant for low-cardinality dimensions (method, status,
  source, outcome). High-cardinality context (paths with ids, user ids) belongs in the
  structured event ring buffer, which is length-bounded.

The module exposes a process-wide singleton ``metrics``; call ``metrics.configure(enabled)``
once at startup from settings.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any, Deque, Iterator


def _series_key(name: str, labels: dict[str, Any]) -> str:
    if not labels:
        return name
    rendered = ",".join(f"{key}={labels[key]}" for key in sorted(labels))
    return f"{name}{{{rendered}}}"


class MetricsRegistry:
    """Thread-safe counters, gauges, and duration histograms plus a structured event buffer."""

    def __init__(self, *, enabled: bool = False, max_events: int = 500) -> None:
        self._enabled = enabled
        self._max_events = max_events
        self._lock = threading.Lock()
        self._counters: dict[str, float] = {}
        self._gauges: dict[str, float] = {}
        self._histograms: dict[str, dict[str, float]] = {}
        self._events: Deque[dict[str, Any]] = deque(maxlen=max_events)
        self._started_monotonic = time.monotonic()

    @property
    def enabled(self) -> bool:
        return self._enabled

    def configure(self, enabled: bool) -> None:
        with self._lock:
            self._enabled = bool(enabled)

    def incr(self, name: str, value: float = 1.0, **labels: Any) -> None:
        if not self._enabled:
            return
        key = _series_key(name, labels)
        with self._lock:
            self._counters[key] = self._counters.get(key, 0.0) + value

    def set_gauge(self, name: str, value: float, **labels: Any) -> None:
        if not self._enabled:
            return
        key = _series_key(name, labels)
        with self._lock:
            self._gauges[key] = float(value)

    def observe(self, name: str, value: float, **labels: Any) -> None:
        """Record a value (typically a duration in ms) into a summary histogram."""
        if not self._enabled:
            return
        key = _series_key(name, labels)
        amount = float(value)
        with self._lock:
            bucket = self._histograms.get(key)
            if bucket is None:
                self._histograms[key] = {
                    "count": 1.0,
                    "sum": amount,
                    "min": amount,
                    "max": amount,
                }
            else:
                bucket["count"] += 1.0
                bucket["sum"] += amount
                bucket["min"] = min(bucket["min"], amount)
                bucket["max"] = max(bucket["max"], amount)

    def log_event(self, event: str, **fields: Any) -> dict[str, Any] | None:
        """Append a structured event to the bounded ring buffer. Returns the record (or None
        when disabled) so callers can also hand it to a logger if they wish."""
        if not self._enabled:
            return None
        record = {"event": event, "ts": time.time(), **fields}
        with self._lock:
            self._events.append(record)
        return record

    def time_block(self, name: str, **labels: Any) -> "Timer":
        """Context manager that records elapsed wall-clock ms into ``name`` on exit."""
        return Timer(self, name, labels)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            histograms: dict[str, dict[str, float]] = {}
            for key, bucket in self._histograms.items():
                count = bucket["count"] or 1.0
                histograms[key] = {**bucket, "avg": bucket["sum"] / count}
            return {
                "enabled": self._enabled,
                "uptime_seconds": round(time.monotonic() - self._started_monotonic, 3),
                "counters": dict(self._counters),
                "gauges": dict(self._gauges),
                "histograms": histograms,
                "recent_events": list(self._events),
                "recent_event_capacity": self._max_events,
            }

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()
            self._events.clear()


class Timer:
    def __init__(self, registry: MetricsRegistry, name: str, labels: dict[str, Any]) -> None:
        self._registry = registry
        self._name = name
        self._labels = labels
        self._start: float | None = None

    def __enter__(self) -> "Timer":
        if self._registry.enabled:
            self._start = time.perf_counter()
        return self

    def __exit__(self, *exc: object) -> bool:
        if self._start is not None:
            elapsed_ms = (time.perf_counter() - self._start) * 1000.0
            self._registry.observe(self._name, elapsed_ms, **self._labels)
        return False


# Process-wide singleton. Call metrics.configure(settings.observability_enabled) at startup.
metrics = MetricsRegistry()


def route_label(request: Any) -> str:
    """Low-cardinality label for an HTTP request: the matched route template (e.g.
    ``/v1/captures/{capture_id}``) rather than the concrete path, so per-id paths don't
    explode the metric series. Falls back to a coarse bucket if routing metadata is absent."""
    route = None
    try:
        route = request.scope.get("route")
    except Exception:
        route = None
    path = getattr(route, "path", None)
    if isinstance(path, str) and path:
        return path
    raw = getattr(getattr(request, "url", None), "path", "") or "unmatched"
    # Collapse anything unmatched to avoid unbounded cardinality from arbitrary paths.
    return "unmatched" if raw not in {"/health"} else raw


def iter_prometheus_lines(snapshot: dict[str, Any]) -> Iterator[str]:
    """Render a snapshot as Prometheus text-exposition lines (best-effort, for a future scrape
    endpoint). Kept here so the wire format lives next to the registry."""
    for key, value in sorted(snapshot.get("counters", {}).items()):
        yield f"{key} {value}"
    for key, value in sorted(snapshot.get("gauges", {}).items()):
        yield f"{key} {value}"
    for key, bucket in sorted(snapshot.get("histograms", {}).items()):
        yield f"{key}_count {bucket.get('count', 0)}"
        yield f"{key}_sum {bucket.get('sum', 0)}"
