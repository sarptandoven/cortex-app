"""Regression tests for two information-leak bugs in the standalone stdlib server.

Bug 1 (log_message): the overridden access logger printed the raw request line,
including the query string, so capture tokens (GET /capture?token=cxa_...) and
OAuth callback codes (GET .../oauth/callback?code=...&state=...) leaked to stdout
and therefore to any captured log/console/support bundle. The logger must strip the
query string and log only method + path (+ status).

Bug 2 (tools/call + REST error paths): unexpected exceptions (e.g.
sqlite3.OperationalError / OSError / FileNotFoundError carrying an absolute vault or
db path) had their raw ``str(exc)`` surfaced verbatim in the agent-facing JSON-RPC
error.message and the REST error detail, bypassing the store's local-path/secret
redaction. Only PermissionError/ValueError messages are safe to pass through.
"""

from __future__ import annotations

import io
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

# Bind the process-singleton store to a throwaway location before importing the
# server module (importing it constructs the store at module scope).
_MODULE_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("CORTEX_DB_PATH", str(Path(_MODULE_TMP.name) / "leaks.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(_MODULE_TMP.name) / "leaks.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from backend.app import standalone_server


def tearDownModule() -> None:
    _MODULE_TMP.cleanup()


def _bare_handler() -> standalone_server.CortexRequestHandler:
    """A handler instance whose socket-bound __init__ is bypassed."""
    handler = object.__new__(standalone_server.CortexRequestHandler)
    handler.address_string = lambda: "127.0.0.1"  # type: ignore[method-assign]
    return handler


class LogMessageQueryStrippingTest(unittest.TestCase):
    def _log(self, requestline: str) -> str:
        handler = _bare_handler()
        handler.requestline = requestline
        buffer = io.StringIO()
        with redirect_stdout(buffer):
            # BaseHTTPRequestHandler.log_request calls log_message with a quoted
            # request line and a status code, matching real server behavior.
            handler.log_message('"%s" %s %s', requestline, "200", "-")
        return buffer.getvalue()

    def test_capture_token_is_not_logged(self) -> None:
        secret = "cxa_supersecrettoken1234567890"
        output = self._log(f"GET /capture?token={secret}&title=hi HTTP/1.1")
        self.assertNotIn(secret, output)
        self.assertNotIn("token=", output)
        # The path itself is still logged for observability.
        self.assertIn("/capture", output)
        self.assertIn("GET", output)

    def test_oauth_callback_code_and_state_not_logged(self) -> None:
        line = "GET /v1/connectors/google/oauth/callback?code=AUTHCODE_SECRET&state=STATE_SECRET HTTP/1.1"
        output = self._log(line)
        self.assertNotIn("AUTHCODE_SECRET", output)
        self.assertNotIn("STATE_SECRET", output)
        self.assertNotIn("code=", output)
        self.assertNotIn("state=", output)
        self.assertIn("/v1/connectors/google/oauth/callback", output)

    def test_pathless_query_only_endpoint(self) -> None:
        output = self._log("POST /mcp HTTP/1.1")
        self.assertIn("/mcp", output)
        self.assertIn("POST", output)


class ErrorRedactionTest(unittest.TestCase):
    FAKE_PATH = "/Users/victim/Library/Application Support/Cortex/cortex.vault/cortex.db"

    def test_oserror_path_is_redacted(self) -> None:
        exc = OSError(f"unable to open database file: {self.FAKE_PATH}")
        message = standalone_server.CortexRequestHandler._safe_error_message(exc)
        self.assertNotIn(self.FAKE_PATH, message)
        self.assertNotIn("/Users/victim", message)

    def test_filenotfound_path_is_redacted(self) -> None:
        exc = FileNotFoundError(f"No such file or directory: {self.FAKE_PATH}")
        message = standalone_server.CortexRequestHandler._safe_error_message(exc)
        self.assertNotIn(self.FAKE_PATH, message)

    def test_permission_error_message_preserved(self) -> None:
        exc = PermissionError("Cortex API token is missing the required scope")
        message = standalone_server.CortexRequestHandler._safe_error_message(exc)
        self.assertEqual(message, "Cortex API token is missing the required scope")

    def test_value_error_message_preserved(self) -> None:
        exc = ValueError("content must not be empty")
        message = standalone_server.CortexRequestHandler._safe_error_message(exc)
        self.assertEqual(message, "content must not be empty")

    def test_falls_back_to_generic_when_redaction_unavailable(self) -> None:
        exc = OSError(f"boom {self.FAKE_PATH}")

        class _BrokenRedactor:
            def _redact_text(self, _value: str) -> str:
                raise RuntimeError("redactor exploded")

        class _BrokenStore:
            default_store = _BrokenRedactor()

        with mock.patch.object(standalone_server, "store", _BrokenStore()):
            message = standalone_server.CortexRequestHandler._safe_error_message(exc)
        self.assertNotIn(self.FAKE_PATH, message)
        self.assertEqual(message, "Internal error")


class ToolsCallErrorPathTest(unittest.TestCase):
    """End-to-end style check that the tools/call error path emits a redacted message.

    A store method is stubbed to raise an OSError carrying an absolute path; the
    emitted JSON-RPC error.message must not contain that path.
    """

    FAKE_PATH = "/Users/victim/Library/Application Support/Cortex/cortex.vault/index.sqlite"

    def test_jsonrpc_error_message_is_redacted(self) -> None:
        handler = _bare_handler()

        captured: dict = {}

        def _fake_send_json(payload, status=None):
            captured["payload"] = payload

        handler._send_json = _fake_send_json  # type: ignore[method-assign]
        handler._json_body = lambda: {  # type: ignore[method-assign]
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": "x"}},
        }

        def _boom(*_args, **_kwargs):
            raise OSError(f"disk I/O error opening {self.FAKE_PATH}")

        with mock.patch.object(standalone_server, "call_tool", _boom), mock.patch.object(
            standalone_server.store, "record_agent_event", lambda *a, **k: None
        ):
            handler._handle_mcp({"user_id": "default", "admin": True, "scopes": None})

        payload = captured["payload"]
        self.assertIn("error", payload)
        message = payload["error"]["message"]
        self.assertNotIn(self.FAKE_PATH, message)
        self.assertNotIn("/Users/victim", message)


if __name__ == "__main__":
    unittest.main()
