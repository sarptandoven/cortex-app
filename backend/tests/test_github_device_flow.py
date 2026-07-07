from __future__ import annotations

import unittest

from backend.app.connectors.github import (
    GITHUB_DEVICE_CODE_URL,
    GITHUB_DEVICE_GRANT_TYPE,
    GITHUB_DEVICE_TOKEN_URL,
    github_device_poll,
    github_device_start,
)


class _Capture:
    """A stand-in GitHub OAuth endpoint that records the request and returns a canned payload."""

    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.form = None

    def __call__(self, url, form):
        self.url, self.form = url, dict(form)
        return self.payload


class GitHubDeviceStartTests(unittest.TestCase):
    def test_start_sends_client_id_and_scope_and_normalizes(self):
        cap = _Capture({
            "device_code": "dev-123",
            "user_code": "WDJB-MJHT",
            "verification_uri": "https://github.com/login/device",
            "expires_in": 899,
            "interval": 5,
        })
        out = github_device_start("Ov23liwB0zkC2Qac88ig", request=cap)
        self.assertEqual(cap.url, GITHUB_DEVICE_CODE_URL)
        self.assertEqual(cap.form.get("client_id"), "Ov23liwB0zkC2Qac88ig")
        self.assertIn("scope", cap.form)
        self.assertEqual(out["device_code"], "dev-123")
        self.assertEqual(out["user_code"], "WDJB-MJHT")
        self.assertEqual(out["interval"], 5)
        self.assertEqual(out["expires_in"], 899)

    def test_start_defaults_missing_optional_fields(self):
        cap = _Capture({"device_code": "d", "user_code": "u"})
        out = github_device_start("cid", request=cap)
        self.assertEqual(out["verification_uri"], "https://github.com/login/device")
        self.assertEqual(out["expires_in"], 900)
        self.assertGreaterEqual(out["interval"], 1)

    def test_start_requires_client_id(self):
        with self.assertRaises(ValueError):
            github_device_start("   ", request=_Capture({}))

    def test_start_raises_when_github_returns_error(self):
        cap = _Capture({"error": "invalid_client", "error_description": "bad app"})
        with self.assertRaises(ValueError) as ctx:
            github_device_start("cid", request=cap)
        self.assertIn("bad app", str(ctx.exception))


class GitHubDevicePollTests(unittest.TestCase):
    def test_poll_success_returns_token(self):
        cap = _Capture({"access_token": "gho_abc", "scope": "read:user,repo", "token_type": "bearer"})
        out = github_device_poll("cid", "dev-123", request=cap)
        self.assertEqual(cap.url, GITHUB_DEVICE_TOKEN_URL)
        self.assertEqual(cap.form.get("client_id"), "cid")
        self.assertEqual(cap.form.get("device_code"), "dev-123")
        self.assertEqual(cap.form.get("grant_type"), GITHUB_DEVICE_GRANT_TYPE)
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["access_token"], "gho_abc")
        self.assertEqual(out["scope"], "read:user,repo")

    def test_poll_pending_is_surfaced(self):
        out = github_device_poll("cid", "dev", request=_Capture({"error": "authorization_pending"}))
        self.assertEqual(out["status"], "authorization_pending")
        self.assertNotIn("access_token", out)

    def test_poll_slow_down_is_surfaced(self):
        out = github_device_poll("cid", "dev", request=_Capture({"error": "slow_down"}))
        self.assertEqual(out["status"], "slow_down")

    def test_poll_expired_and_denied(self):
        self.assertEqual(
            github_device_poll("cid", "dev", request=_Capture({"error": "expired_token"}))["status"],
            "expired_token",
        )
        self.assertEqual(
            github_device_poll("cid", "dev", request=_Capture({"error": "access_denied"}))["status"],
            "access_denied",
        )

    def test_poll_unknown_error_is_error_status(self):
        out = github_device_poll("cid", "dev", request=_Capture({"error": "boom", "error_description": "kaboom"}))
        self.assertEqual(out["status"], "error")
        self.assertIn("kaboom", out["detail"])

    def test_poll_requires_client_and_device_code(self):
        with self.assertRaises(ValueError):
            github_device_poll("", "dev", request=_Capture({}))
        with self.assertRaises(ValueError):
            github_device_poll("cid", "", request=_Capture({}))


if __name__ == "__main__":
    unittest.main()
