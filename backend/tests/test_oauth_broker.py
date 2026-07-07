from __future__ import annotations

import base64
import unittest

from backend.app.oauth_broker import OAuthBrokerRegistry, BrokerError


NOTION_ENV = {
    "CORTEX_BROKER_NOTION_CLIENT_ID": "notion-client-abc",
    "CORTEX_BROKER_NOTION_CLIENT_SECRET": "notion-secret-xyz",
}
GITHUB_ENV = {
    "CORTEX_BROKER_GITHUB_CLIENT_ID": "gh-client-123",
    "CORTEX_BROKER_GITHUB_CLIENT_SECRET": "gh-secret-456",
}
LOOPBACK = "http://127.0.0.1:8766/v1/connectors/oauth/callback"


class _Capture:
    """A stand-in token endpoint that records what the broker sent and returns a canned payload."""
    def __init__(self, payload):
        self.payload = payload
        self.url = None
        self.form = None
        self.headers = None

    def __call__(self, url, form, headers):
        self.url, self.form, self.headers = url, dict(form), dict(headers)
        return self.payload


class OAuthBrokerTests(unittest.TestCase):
    def test_unconfigured_provider_is_absent(self):
        broker = OAuthBrokerRegistry(env={})
        self.assertEqual(broker.configured_providers(), [])
        self.assertFalse(broker.is_configured("notion"))
        with self.assertRaises(BrokerError) as ctx:
            broker.authorization_url("notion", LOOPBACK, "state123")
        self.assertEqual(ctx.exception.status, 503)

    def test_unknown_provider_404(self):
        broker = OAuthBrokerRegistry(env=NOTION_ENV)
        with self.assertRaises(BrokerError) as ctx:
            broker.authorization_url("dropbox", LOOPBACK, "s")
        self.assertEqual(ctx.exception.status, 404)

    def test_notion_authorization_url(self):
        broker = OAuthBrokerRegistry(env=NOTION_ENV)
        url = broker.authorization_url("notion", LOOPBACK, "state123")
        self.assertTrue(url.startswith("https://api.notion.com/v1/oauth/authorize?"))
        self.assertIn("client_id=notion-client-abc", url)
        self.assertIn("owner=user", url)
        self.assertIn("response_type=code", url)
        self.assertIn("state=state123", url)

    def test_redirect_allowlist_blocks_foreign_and_bad_path(self):
        broker = OAuthBrokerRegistry(env=NOTION_ENV)
        for bad in [
            "https://evil.example.com/steal",
            "http://127.0.0.1:8766/not/the/callback",
            "http://10.0.0.5:8766/v1/connectors/oauth/callback",
            "https://127.0.0.1:8766/v1/connectors/oauth/callback",  # https not allowed for loopback here
        ]:
            with self.assertRaises(BrokerError) as ctx:
                broker.authorization_url("notion", bad, "s")
            self.assertEqual(ctx.exception.status, 400, bad)

    def test_redirect_allowlist_permits_google_and_localhost_and_any_port(self):
        broker = OAuthBrokerRegistry(env=NOTION_ENV)
        for good in [
            "http://127.0.0.1:8766/v1/connectors/oauth/callback",
            "http://localhost:8766/v1/connectors/oauth/callback",
            "http://127.0.0.1:53210/v1/connectors/google/oauth/callback",
        ]:
            self.assertIn("authorize", broker.authorization_url("notion", good, "s"))

    def test_notion_exchange_uses_basic_auth_and_normalizes(self):
        cap = _Capture({
            "access_token": "notion-token",
            "workspace_id": "ws-1",
            "workspace_name": "My Space",
            "token_type": "bearer",
        })
        broker = OAuthBrokerRegistry(env=NOTION_ENV, token_request=cap)
        out = broker.exchange("notion", "auth-code-1", LOOPBACK)
        # Basic auth = base64(client_id:client_secret)
        expected = base64.b64encode(b"notion-client-abc:notion-secret-xyz").decode()
        self.assertEqual(cap.headers.get("Authorization"), f"Basic {expected}")
        self.assertEqual(cap.form.get("grant_type"), "authorization_code")
        self.assertEqual(cap.form.get("code"), "auth-code-1")
        # secret must NOT be in the body for basic-auth providers
        self.assertNotIn("client_secret", cap.form)
        self.assertEqual(out["access_token"], "notion-token")
        self.assertEqual(out["workspace_id"], "ws-1")

    def test_github_exchange_posts_credentials_and_pkce(self):
        cap = _Capture({"access_token": "gh-token", "scope": "read:user,repo", "token_type": "bearer"})
        broker = OAuthBrokerRegistry(env=GITHUB_ENV, token_request=cap)
        out = broker.exchange("github", "code-2", LOOPBACK, code_verifier="verifier-xyz")
        # GitHub = creds in the POST body, not Basic auth
        self.assertEqual(cap.form.get("client_id"), "gh-client-123")
        self.assertEqual(cap.form.get("client_secret"), "gh-secret-456")
        self.assertEqual(cap.form.get("code_verifier"), "verifier-xyz")  # github supports PKCE
        self.assertNotIn("Authorization", cap.headers)
        self.assertEqual(out["access_token"], "gh-token")

    def test_exchange_without_access_token_is_502(self):
        cap = _Capture({"error": "bad_verification_code", "error_description": "expired"})
        broker = OAuthBrokerRegistry(env=GITHUB_ENV, token_request=cap)
        with self.assertRaises(BrokerError) as ctx:
            broker.exchange("github", "code-3", LOOPBACK)
        self.assertEqual(ctx.exception.status, 502)

    def test_refresh_uses_refresh_grant_and_computes_expiry(self):
        cap = _Capture({"access_token": "fresh", "expires_in": 3600, "token_type": "bearer"})
        broker = OAuthBrokerRegistry(env=GITHUB_ENV, token_request=cap)
        out = broker.refresh("github", "refresh-abc")
        self.assertEqual(cap.form.get("grant_type"), "refresh_token")
        self.assertEqual(cap.form.get("refresh_token"), "refresh-abc")
        self.assertEqual(out["expires_in"], 3600)
        self.assertIn("access_token_expires_at", out)

    def test_missing_secret_is_503(self):
        broker = OAuthBrokerRegistry(env={"CORTEX_BROKER_NOTION_CLIENT_ID": "id-only"})
        with self.assertRaises(BrokerError) as ctx:
            broker.exchange("notion", "code", LOOPBACK)
        self.assertEqual(ctx.exception.status, 503)


if __name__ == "__main__":
    unittest.main()
