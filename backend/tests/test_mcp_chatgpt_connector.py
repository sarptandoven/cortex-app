"""ChatGPT deep-research / company-knowledge connector contract for /mcp.

ChatGPT requires two read-only tools named EXACTLY `search` and `fetch` (compatibility schema),
returned as structuredContent + JSON text, over a streamable-HTTP-compatible transport. These
tests pin that contract on the hosted FastAPI app (TestClient) and assert stdlib-shared parity
(the standalone stdlib server serves the same mcp_tools surface). They also guard that the JSON
path existing stdio-bridge clients use (Claude Desktop / Cursor) stays application/json.
"""
from __future__ import annotations

import contextlib
import dataclasses
import json
import os
import tempfile
import unittest
from pathlib import Path

MODULE_TMP = tempfile.TemporaryDirectory()
# Only set the DB/vault/api-key if this module is the one that binds the shared app import. When
# this file runs alongside another that imported backend.app.main first, we DON'T change global
# behavior (no auto-approve env, no per-user settings mutation): captures are approved explicitly
# via the API so `search` (which never surfaces pending) can find them, keeping the ~2058 suite
# byte-identical when this module shares a process with it.
os.environ.setdefault("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "chatgpt_connector.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "chatgpt_connector.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from fastapi.testclient import TestClient  # noqa: E402

from backend.app import main as main_module  # noqa: E402
from backend.app import mcp_tools  # noqa: E402

app = main_module.app

ADMIN = {"Authorization": "Bearer test-token"}


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


@contextlib.contextmanager
def _tool_surface(surface: str):
    """Swap the module-level settings for one with a different advertised MCP surface. settings is
    a frozen dataclass, so we replace() a copy and restore the original afterwards. The /mcp
    handler reads main_module.settings.mcp_tool_surface at request time, so this takes effect
    for requests made inside the block and is fully reverted on exit."""
    original = main_module.settings
    main_module.settings = dataclasses.replace(original, mcp_tool_surface=surface)
    try:
        yield
    finally:
        main_module.settings = original


class ChatGPTConnectorToolTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def _headers(self, token: str, user: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "X-Cortex-User": user}

    def _register_token(self, token: str, user: str, scopes: list[str], label: str) -> None:
        resp = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": token, "label": label, "scopes": scopes},
            headers={**ADMIN, "X-Cortex-User": user},
        )
        self.assertEqual(resp.status_code, 200, resp.text)

    def _seed_memory(self, user: str, content: str, source_url: str | None = None) -> None:
        body: dict = {"content": content, "source": "note"}
        if source_url:
            body["source_url"] = source_url
        resp = self.client.post("/v1/captures", json=body, headers={**ADMIN, "X-Cortex-User": user})
        self.assertEqual(resp.status_code, 200, resp.text)
        # Approve so it becomes an active memory (search never surfaces pending). Explicit
        # per-user approval, not a global auto-approve env, so the shared suite is unaffected.
        approved = self.client.post("/v1/captures/approve-all", headers={**ADMIN, "X-Cortex-User": user})
        self.assertEqual(approved.status_code, 200, approved.text)

    def _mcp_call(self, token: str, user: str, name: str, arguments: dict, *, call_id: str = "1") -> dict:
        resp = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": call_id,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            },
            headers=self._headers(token, user),
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        return resp.json()

    def test_search_returns_results_with_real_ids(self) -> None:
        user = "connector-search"
        self._register_token("cxm_connector_search_token_0001", user, ["read"], "search read")
        self._seed_memory(user, "Atlas migrated to PostgreSQL because of jsonb support.", "https://notes/atlas")
        payload = self._mcp_call("cxm_connector_search_token_0001", user, "search", {"query": "Atlas PostgreSQL jsonb"})
        self.assertNotIn("error", payload, payload)
        structured = payload["result"]["structuredContent"]
        self.assertIn("results", structured)
        self.assertTrue(structured["results"], "search should surface the seeded memory")
        for item in structured["results"]:
            self.assertIn("id", item)
            self.assertIn("title", item)
            self.assertIn("url", item)
            self.assertTrue(item["id"])
            self.assertTrue(item["title"])
            self.assertTrue(item["url"])
        # source_url is projected as the item url when present.
        self.assertTrue(any(item["url"] == "https://notes/atlas" for item in structured["results"]))

    def test_search_fetch_roundtrip(self) -> None:
        user = "connector-roundtrip"
        self._register_token("cxm_connector_roundtrip_token_01", user, ["read"], "roundtrip read")
        self._seed_memory(user, "We chose Rust for the sync engine to avoid GC pauses.")
        search_payload = self._mcp_call("cxm_connector_roundtrip_token_01", user, "search", {"query": "sync engine language"})
        results = search_payload["result"]["structuredContent"]["results"]
        self.assertTrue(results)
        memory_id = results[0]["id"]

        fetch_payload = self._mcp_call("cxm_connector_roundtrip_token_01", user, "fetch", {"id": memory_id})
        self.assertNotIn("error", fetch_payload, fetch_payload)
        doc = fetch_payload["result"]["structuredContent"]
        self.assertEqual(doc["id"], memory_id)
        for key in ("id", "title", "text", "url", "metadata"):
            self.assertIn(key, doc, key)
        self.assertIn("Rust", doc["text"])
        self.assertIsInstance(doc["metadata"], dict)
        for meta_key in ("layer", "kind", "source", "confidence", "created_at"):
            self.assertIn(meta_key, doc["metadata"], meta_key)
        # url falls back to a cortex:// locator when the memory has no source_url.
        self.assertTrue(doc["url"])

    def test_fetch_unknown_id_is_clean_error_not_500(self) -> None:
        user = "connector-unknown"
        self._register_token("cxm_connector_unknown_token_001", user, ["read"], "unknown read")
        payload = self._mcp_call("cxm_connector_unknown_token_001", user, "fetch", {"id": "mem_does_not_exist"})
        # A clean structured error result, never a JSON-RPC error / 500.
        self.assertNotIn("error", payload, payload)
        doc = payload["result"]["structuredContent"]
        self.assertEqual(doc["id"], "mem_does_not_exist")
        self.assertEqual(doc.get("error"), "not_found")
        self.assertEqual(doc["text"], "")

    def test_both_tools_listed_for_read_scoped_token_under_chatgpt_surface(self) -> None:
        # A hosted deployment fronting ChatGPT advertises the "chatgpt" surface, where a
        # read-scoped connector token sees search + fetch by name.
        user = "connector-list"
        self._register_token("cxm_connector_list_token_00001", user, ["read"], "list read")
        with _tool_surface("chatgpt"):
            resp = self.client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": "list", "method": "tools/list"},
                headers=self._headers("cxm_connector_list_token_00001", user),
            )
        self.assertEqual(resp.status_code, 200, resp.text)
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        self.assertIn("search", names)
        self.assertIn("fetch", names)

    def test_both_tools_require_read_scope(self) -> None:
        user = "connector-noread"
        # A write-only token: no read scope. search/fetch must be denied (advertisement-independent).
        self._register_token("cxm_connector_noread_token_0001", user, ["write"], "noread write")
        for name, args in (("search", {"query": "anything"}), ("fetch", {"id": "x"})):
            payload = self._mcp_call("cxm_connector_noread_token_0001", user, name, args, call_id=name)
            self.assertIn("error", payload, f"{name} without read scope should be denied")
            self.assertNotIn("result", payload)
        # And they should not be advertised to a no-read token, even under the chatgpt surface
        # (scope filtering removes them before surface selection).
        with _tool_surface("chatgpt"):
            resp = self.client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": "list", "method": "tools/list"},
                headers=self._headers("cxm_connector_noread_token_0001", user),
            )
        names = {t["name"] for t in resp.json()["result"]["tools"]}
        self.assertNotIn("search", names)
        self.assertNotIn("fetch", names)

    def test_result_carries_structured_content_and_json_text(self) -> None:
        user = "connector-shape"
        self._register_token("cxm_connector_shape_token_00001", user, ["read"], "shape read")
        self._seed_memory(user, "The launch checklist lives in the ops runbook.")
        payload = self._mcp_call("cxm_connector_shape_token_00001", user, "search", {"query": "launch checklist"})
        result = payload["result"]
        # structuredContent present...
        self.assertIn("structuredContent", result)
        self.assertIn("results", result["structuredContent"])
        # ...AND the same object JSON-encoded as a text content item (ChatGPT-compat).
        self.assertTrue(result["content"])
        text_item = result["content"][0]
        self.assertEqual(text_item["type"], "text")
        decoded = json.loads(text_item["text"])
        self.assertEqual(decoded, result["structuredContent"])


class ChatGPTConnectorTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def _initialize(self, accept: str | None = None) -> "object":
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "transport"}
        if accept:
            headers["Accept"] = accept
        return self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "init", "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
            headers=headers,
        )

    def test_event_stream_accept_returns_sse_with_response_event(self) -> None:
        headers = {
            "Authorization": "Bearer test-token",
            "X-Cortex-User": "transport",
            "Accept": "application/json, text/event-stream",
        }
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "t", "method": "ping"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.headers["content-type"].startswith("text/event-stream"), resp.headers["content-type"])
        body = resp.text
        self.assertIn("event: message", body)
        self.assertIn("data: ", body)
        # The SSE data line is the exact JSON-RPC response.
        data_line = next(line for line in body.splitlines() if line.startswith("data: "))
        envelope = json.loads(data_line[len("data: "):])
        self.assertEqual(envelope["jsonrpc"], "2.0")
        self.assertEqual(envelope["id"], "t")
        self.assertEqual(envelope["result"], {})

    def test_application_json_accept_stays_application_json(self) -> None:
        headers = {
            "Authorization": "Bearer test-token",
            "X-Cortex-User": "transport",
            "Accept": "application/json",
        }
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "t", "method": "ping"},
            headers=headers,
        )
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.headers["content-type"].startswith("application/json"), resp.headers["content-type"])
        self.assertEqual(resp.json(), {"jsonrpc": "2.0", "id": "t", "result": {}})

    def test_default_no_accept_stays_application_json(self) -> None:
        # The stdio bridge clients POST without forcing text/event-stream; they must get JSON.
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "t", "method": "ping"},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "transport"},
        )
        self.assertTrue(resp.headers["content-type"].startswith("application/json"))

    def test_initialize_returns_session_id_header_for_streamable_client(self) -> None:
        # A ChatGPT streamable-HTTP connector sends Accept: application/json, text/event-stream.
        resp = self._initialize(accept="application/json, text/event-stream")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIn("mcp-session-id", {k.lower() for k in resp.headers.keys()})
        self.assertTrue(resp.headers["mcp-session-id"])
        # advertises the newest protocol revision (SSE-framed for a text/event-stream client).
        data_line = next(line for line in resp.text.splitlines() if line.startswith("data: "))
        self.assertEqual(json.loads(data_line[len("data: "):])["result"]["protocolVersion"], "2025-06-18")

    def test_initialize_json_client_gets_no_session_header(self) -> None:
        # JSON-only stdio-bridge parity: an Accept: application/json initialize is byte-identical
        # to before (no Mcp-Session-Id header added).
        resp = self._initialize(accept="application/json")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertNotIn("mcp-session-id", {k.lower() for k in resp.headers.keys()})
        self.assertTrue(resp.headers["content-type"].startswith("application/json"))
        self.assertEqual(resp.json()["result"]["protocolVersion"], "2025-06-18")

    def test_mcp_protocol_version_header_does_not_400(self) -> None:
        resp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "t", "method": "ping"},
            headers={
                "Authorization": "Bearer test-token",
                "X-Cortex-User": "transport",
                "MCP-Protocol-Version": "2025-06-18",
            },
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["result"], {})


class ChatGPTConnectorSharedSurfaceTests(unittest.TestCase):
    """Stdlib-shared parity: both servers advertise search/fetch from the same mcp_tools surface,
    so the standalone stdlib server (which imports the identical TOOLS/tools_for_scopes) exposes
    them too. This asserts the shared module contract without booting the HTTP server."""

    def test_search_and_fetch_registered_read_only(self) -> None:
        by_name = {str(t.get("name") or ""): t for t in mcp_tools.TOOLS}
        self.assertIn("search", by_name)
        self.assertIn("fetch", by_name)
        for name in ("search", "fetch"):
            tool = by_name[name]
            self.assertTrue(tool["annotations"]["readOnlyHint"], name)
            self.assertEqual(tool["outputSchema"]["type"], "object", name)
            self.assertEqual(mcp_tools.tool_required_capabilities(name, scoped=True), ["read"], name)

    def test_read_scoped_chatgpt_surface_includes_search_and_fetch(self) -> None:
        shown = {str(t.get("name") or "") for t in mcp_tools.tools_for_scopes(["read"], surface="chatgpt")}
        self.assertIn("search", shown)
        self.assertIn("fetch", shown)

    def test_core_surface_unchanged_excludes_search_and_fetch(self) -> None:
        # The default Claude/Cursor core surface must NOT gain search/fetch (stdio-bridge parity).
        shown = {str(t.get("name") or "") for t in mcp_tools.tools_for_scopes(["read"], surface="core")}
        self.assertNotIn("search", shown)
        self.assertNotIn("fetch", shown)

    def test_no_read_scope_excludes_search_and_fetch_even_on_chatgpt_surface(self) -> None:
        shown = {str(t.get("name") or "") for t in mcp_tools.tools_for_scopes(["write"], surface="chatgpt")}
        self.assertNotIn("search", shown)
        self.assertNotIn("fetch", shown)


if __name__ == "__main__":
    unittest.main()
