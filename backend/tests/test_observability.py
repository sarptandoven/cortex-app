from __future__ import annotations

import os
import unittest

os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app.observability import MetricsRegistry, metrics


class MetricsRegistryTests(unittest.TestCase):
    def test_disabled_registry_is_a_no_op(self) -> None:
        registry = MetricsRegistry(enabled=False)
        registry.incr("http_requests_total", method="GET")
        registry.observe("http_request_duration_ms", 12.0)
        registry.set_gauge("queue_depth", 5)
        self.assertIsNone(registry.log_event("http_request", route="/health"))
        with registry.time_block("blk"):
            pass
        snapshot = registry.snapshot()
        self.assertFalse(snapshot["enabled"])
        self.assertEqual(snapshot["counters"], {})
        self.assertEqual(snapshot["histograms"], {})
        self.assertEqual(snapshot["recent_events"], [])

    def test_counters_gauges_and_labels(self) -> None:
        registry = MetricsRegistry(enabled=True)
        registry.incr("http_requests_total", method="GET", status="200")
        registry.incr("http_requests_total", method="GET", status="200")
        registry.incr("http_requests_total", method="POST", status="500")
        registry.set_gauge("queue_depth", 7)
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counters"]["http_requests_total{method=GET,status=200}"], 2.0)
        self.assertEqual(snapshot["counters"]["http_requests_total{method=POST,status=500}"], 1.0)
        self.assertEqual(snapshot["gauges"]["queue_depth"], 7.0)

    def test_histogram_aggregates_and_avg(self) -> None:
        registry = MetricsRegistry(enabled=True)
        for value in (10.0, 20.0, 30.0):
            registry.observe("http_request_duration_ms", value, method="GET")
        bucket = registry.snapshot()["histograms"]["http_request_duration_ms{method=GET}"]
        self.assertEqual(bucket["count"], 3.0)
        self.assertEqual(bucket["sum"], 60.0)
        self.assertEqual(bucket["min"], 10.0)
        self.assertEqual(bucket["max"], 30.0)
        self.assertEqual(bucket["avg"], 20.0)

    def test_time_block_records_a_duration(self) -> None:
        registry = MetricsRegistry(enabled=True)
        with registry.time_block("work_ms", stage="extract"):
            pass
        histograms = registry.snapshot()["histograms"]
        self.assertIn("work_ms{stage=extract}", histograms)
        self.assertEqual(histograms["work_ms{stage=extract}"]["count"], 1.0)

    def test_event_ring_buffer_is_bounded(self) -> None:
        registry = MetricsRegistry(enabled=True, max_events=3)
        for index in range(10):
            registry.log_event("http_request", i=index)
        events = registry.snapshot()["recent_events"]
        self.assertEqual(len(events), 3)
        self.assertEqual([event["i"] for event in events], [7, 8, 9])

    def test_reset_clears_all_series(self) -> None:
        registry = MetricsRegistry(enabled=True)
        registry.incr("x")
        registry.observe("y", 1.0)
        registry.log_event("z")
        registry.reset()
        snapshot = registry.snapshot()
        self.assertEqual(snapshot["counters"], {})
        self.assertEqual(snapshot["histograms"], {})
        self.assertEqual(snapshot["recent_events"], [])


class MetricsEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(main_module.app)
        self._was_enabled = metrics.enabled

    def tearDown(self) -> None:
        metrics.configure(self._was_enabled)
        metrics.reset()

    def test_metrics_endpoint_requires_admin_token(self) -> None:
        self.assertEqual(self.client.get("/v1/metrics").status_code, 401)
        self.assertEqual(
            self.client.get("/v1/metrics", headers={"Authorization": "Bearer not-the-admin-token"}).status_code,
            403,
        )

    def test_metrics_endpoint_reports_recorded_requests_when_enabled(self) -> None:
        metrics.configure(True)
        metrics.reset()
        # An ordinary request flows through the timing middleware and is recorded.
        self.assertEqual(self.client.get("/health").status_code, 200)

        response = self.client.get("/v1/metrics", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["enabled"])
        self.assertIn("backend_version", payload)
        total_requests = sum(
            value for key, value in payload["counters"].items() if key.startswith("http_requests_total")
        )
        self.assertGreaterEqual(total_requests, 1)
        self.assertTrue(any(event["event"] == "http_request" for event in payload["recent_events"]))

    def test_metrics_endpoint_reports_disabled_without_recording(self) -> None:
        metrics.configure(False)
        metrics.reset()
        self.client.get("/health")
        payload = self.client.get("/v1/metrics", headers={"Authorization": "Bearer test-token"}).json()
        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["counters"], {})


if __name__ == "__main__":
    unittest.main()
