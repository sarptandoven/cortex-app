from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.delivery import build_delivery_payload, deliver_webhook, is_safe_webhook_url


class SSRFGuardTests(unittest.TestCase):
    def test_rejects_internal_and_insecure(self):
        for url in (
            "http://127.0.0.1/hook",
            "https://127.0.0.1/hook",
            "http://10.0.0.5/hook",
            "https://169.254.169.254/latest/meta-data",
            "http://8.8.8.8/hook",          # public but not https
            "ftp://example.com/x",
            "https://user:pass@8.8.8.8/x",   # credentials in URL
            "not a url",
        ):
            ok, _ = is_safe_webhook_url(url)
            self.assertFalse(ok, url)

    def test_allows_public_https(self):
        ok, reason = is_safe_webhook_url("https://8.8.8.8/hook")  # numeric -> deterministic, no DNS
        self.assertTrue(ok, reason)


class DeliverWebhookTests(unittest.TestCase):
    def test_success_and_failure_via_injected_sender(self):
        good = deliver_webhook("https://8.8.8.8/h", {"a": 1}, request_fn=lambda u, b, h: 200)
        self.assertTrue(good["ok"])
        self.assertEqual(good["status"], 200)
        bad = deliver_webhook("https://8.8.8.8/h", {"a": 1}, request_fn=lambda u, b, h: 500)
        self.assertFalse(bad["ok"])

    def test_ssrf_blocked_never_calls_sender(self):
        calls = []
        result = deliver_webhook("http://127.0.0.1/h", {"a": 1}, request_fn=lambda u, b, h: calls.append(u) or 200)
        self.assertFalse(result["ok"])
        self.assertEqual(calls, [])


class DeliveryEvalGateTests(unittest.TestCase):
    def test_full_gate_passes(self):
        from scripts.delivery_eval import run_delivery_eval

        with tempfile.TemporaryDirectory() as tmp:
            summary = run_delivery_eval(Path(tmp) / "cortex.db", Path(tmp) / "vault")
        self.assertEqual(summary["counts"]["failures"], 0, summary["checks"])


if __name__ == "__main__":
    unittest.main()
