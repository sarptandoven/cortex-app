from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from backend.app.delivery import build_delivery_payload, deliver_webhook, is_safe_webhook_url


class SSRFGuardTests(unittest.TestCase):
    def test_rejects_internal_and_insecure(self):
        for url in (
            "http://127.0.0.1/hook",
            "https://127.0.0.1/hook",
            "http://10.0.0.5/hook",
            "https://169.254.169.254/latest/meta-data",
            "https://100.64.0.1/hook",      # shared CGNAT space is not globally routable
            "http://8.8.8.8/hook",          # public but not https
            "ftp://example.com/x",
            "https://user:pass@8.8.8.8/x",   # credentials in URL
            "https://8.8.8.8:not-a-port/x",
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

    def test_sender_connects_to_the_address_that_passed_ssrf_validation(self):
        resolution = [
            (2, 1, 6, "", ("8.8.8.8", 443)),
        ]
        pinned_calls = []

        def fake_post(parsed, address, body, headers, *, timeout):
            pinned_calls.append((parsed.hostname, address, timeout))
            return 204

        with (
            patch("backend.app.delivery.socket.getaddrinfo", return_value=resolution) as resolver,
            patch("backend.app.delivery._post_to_pinned_target", side_effect=fake_post),
        ):
            result = deliver_webhook("https://hooks.example.test/path", {"a": 1})

        self.assertTrue(result["ok"])
        self.assertEqual(resolver.call_count, 1)
        self.assertEqual(pinned_calls, [("hooks.example.test", "8.8.8.8", 15)])


class DeliveryEvalGateTests(unittest.TestCase):
    def test_full_gate_passes(self):
        from scripts.delivery_eval import run_delivery_eval

        with tempfile.TemporaryDirectory() as tmp:
            summary = run_delivery_eval(Path(tmp) / "cortex.db", Path(tmp) / "vault")
        self.assertEqual(summary["counts"]["failures"], 0, summary["checks"])


if __name__ == "__main__":
    unittest.main()
