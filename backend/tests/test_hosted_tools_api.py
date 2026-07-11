"""Parity tests: the hosted FastAPI plane (main.py, api.signindoppl.com) must expose the SAME
universal-reach tools API the local standalone server exposes (backend/app/standalone_server.py
/v1/tools/schema + /v1/tools/call). The YC diligence gap this closes: the plan sells "universal
reach" (openai/anthropic/openapi schema exports + generic tool dispatch) but those routes used to
exist ONLY on the local server — a hosted caller (any external AI/agent hitting
api.signindoppl.com) had no way to discover or call Cortex tools at all.

These tests spin up BOTH backends against the SAME underlying store (same db/vault paths, real
StoreRegistry — not a hand-rolled fake) so "local vs hosted" comparisons are apples-to-apples:
- GET /v1/tools/schema?format=openai|anthropic|openapi returns byte-identical JSON on both planes
  for the same token/scopes.
- POST /v1/tools/call round-trips a real tool call the same way on both planes, and hosted calls
  record an agent event exactly like local calls (so hosted reads count in the cross-AI headline
  metric — this was the other half of the diligence gap).
- Scope enforcement flows through the same call_tool/_require_tool_access path as /mcp: a
  read-only hosted token can see write tools advertised (schema does not hide them at surface
  "full") but calling one is still rejected — advertisement is never authorization.

Isolation note: this file builds its OWN StoreRegistry on its OWN temp db/vault (like
test_hosted_deletion_isolation.py does) rather than reusing the process-shared main_module.store.
main_module.settings/store are process-wide singletons that other test modules (e.g.
test_fastapi_contract.py) tear down their own tempdir out from under at module-teardown time;
building an independent store here means these tests are correct regardless of what order pytest
collects test files in, and never disturb any other module's store.
"""

from __future__ import annotations

import json
import tempfile
import threading
import unittest
from dataclasses import replace
from pathlib import Path
from urllib import error, request

from fastapi.testclient import TestClient

from backend.app import main as main_module
from backend.app import standalone_server

HOSTED_BASE_URL = "https://api.signindoppl.com"
LOCAL_BASE_URL_TEMPLATE = "http://127.0.0.1:8766"


class HostedToolsApiParityTests(unittest.TestCase):
    """Runs the real standalone HTTP server (local plane) and the FastAPI app via TestClient
    (hosted plane) side by side, pointed at the identical db_path/vault_path so both planes see
    the same store, users, and tokens."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.tmp = tempfile.TemporaryDirectory()
        root = Path(cls.tmp.name)

        # Independent settings/store: same construction main.py and standalone_server.py both use
        # (StoreRegistry.from_settings), just pointed at a tempdir this test class owns outright.
        cls.original_hosted_settings = main_module.settings
        cls.original_hosted_store = main_module.store
        hosted_settings = replace(
            cls.original_hosted_settings,
            db_path=root / "hosted_tools_api.sqlite",
            vault_path=root / "hosted_tools_api.vault",
            api_key="test-token",
            public_base_url=HOSTED_BASE_URL,
        )
        main_module.settings = hosted_settings
        main_module.store = main_module.StoreRegistry.from_settings(hosted_settings)
        cls.hosted_client = TestClient(main_module.app)

        # Standalone server keeps its own module-level store/settings singletons (it is a plain
        # BaseHTTPRequestHandler stack, not FastAPI). Point them at the SAME store just built so
        # both planes share one real CortexStore-backed StoreRegistry, then spin up a real
        # ThreadingHTTPServer for local calls — mirrors test_standalone_server.py's harness, but
        # with the REAL store instead of a hand-rolled FakeStore.
        cls.original_standalone_store = standalone_server.store
        cls.original_standalone_settings = standalone_server.settings
        cls.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.settings = replace(hosted_settings, public_base_url=LOCAL_BASE_URL_TEMPLATE)
        standalone_server.store = main_module.store
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
        cls.local_server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        cls.local_thread = threading.Thread(target=cls.local_server.serve_forever, daemon=True)
        cls.local_thread.start()
        cls.local_base_url = f"http://127.0.0.1:{cls.local_server.server_address[1]}"

    @classmethod
    def tearDownClass(cls) -> None:
        cls.local_server.shutdown()
        cls.local_server.server_close()
        cls.local_thread.join(timeout=2)
        standalone_server.store = cls.original_standalone_store
        standalone_server.settings = cls.original_standalone_settings
        standalone_server.REQUEST_GUARDS = cls.original_guards
        main_module.settings = cls.original_hosted_settings
        main_module.store = cls.original_hosted_store
        cls.tmp.cleanup()

    # -- local-plane helpers (real HTTP against the standalone ThreadingHTTPServer) -----------

    def _local_get(self, path: str, token: str = "test-token"):
        req = request.Request(self.local_base_url + path, headers={"Authorization": f"Bearer {token}"})
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            return exc.code, (json.loads(body) if body else {})

    def _local_post(self, path: str, payload: dict, token: str = "test-token"):
        data = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self.local_base_url + path,
            data=data,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read().decode("utf-8"))
        except error.HTTPError as exc:
            body = exc.read().decode("utf-8")
            exc.close()
            return exc.code, (json.loads(body) if body else {})

    # -- hosted-plane helpers (FastAPI TestClient against main.app) ---------------------------

    def _hosted_get(self, path: str, token: str = "test-token", **kwargs):
        return self.hosted_client.get(path, headers={"Authorization": f"Bearer {token}"}, **kwargs)

    def _hosted_post(self, path: str, payload: dict, token: str = "test-token"):
        return self.hosted_client.post(path, json=payload, headers={"Authorization": f"Bearer {token}"})

    def _scorecard_calls_for_token(self, token_id: str) -> int:
        """Total tool_call events attributed to token_id, per the SAME read-model
        (get_tool_scorecard) the product's tool-scorecard/cross-AI metrics use."""
        scorecard = main_module.store.default_store.get_tool_scorecard(
            main_module.settings.default_user_id, token_id=token_id
        )
        return sum(int(host.get("calls") or 0) for host in scorecard.get("hosts", []))

    # -- schema parity --------------------------------------------------------------------

    def test_schema_parity_admin_token_openai(self) -> None:
        local_status, local_body = self._local_get("/v1/tools/schema?format=openai")
        hosted_response = self._hosted_get("/v1/tools/schema", params={"format": "openai"})

        self.assertEqual(local_status, 200)
        self.assertEqual(hosted_response.status_code, 200)
        # Local advertises against http://127.0.0.1:<port>; hosted advertises against
        # api.signindoppl.com — the schema CONTENTS (tool defs) must be identical regardless of
        # base_url, since openai/anthropic formats carry no server URL at all.
        self.assertEqual(local_body["schema"], hosted_response.json()["schema"])

    def test_schema_parity_admin_token_anthropic(self) -> None:
        local_status, local_body = self._local_get("/v1/tools/schema?format=anthropic")
        hosted_response = self._hosted_get("/v1/tools/schema", params={"format": "anthropic"})

        self.assertEqual(local_status, 200)
        self.assertEqual(hosted_response.status_code, 200)
        self.assertEqual(local_body["schema"], hosted_response.json()["schema"])

    def test_schema_parity_openapi_differs_only_by_server_url(self) -> None:
        local_status, local_body = self._local_get("/v1/tools/schema?format=openapi")
        hosted_response = self._hosted_get("/v1/tools/schema", params={"format": "openapi"})

        self.assertEqual(local_status, 200)
        self.assertEqual(hosted_response.status_code, 200)
        local_schema = local_body["schema"]
        hosted_schema = hosted_response.json()["schema"]

        # The OpenAPI export is server-parameterized: the standalone server derives base_url from
        # the incoming request's Host header (so it self-advertises whatever host/port it is
        # actually reachable on), while main.py passes settings.public_base_url (the hosted
        # plane's fixed public DNS name) — hosted must advertise the HOSTED base, never localhost.
        self.assertEqual(local_schema["servers"], [{"url": self.local_base_url}])
        self.assertEqual(hosted_schema["servers"], [{"url": HOSTED_BASE_URL}])
        self.assertNotIn("127.0.0.1", json.dumps(hosted_schema))

        # Strip the servers block; everything else (paths, operationIds, schemas) must match.
        local_paths = local_schema["paths"]
        hosted_paths = hosted_schema["paths"]
        self.assertEqual(local_paths, hosted_paths)
        self.assertEqual(local_schema["openapi"], hosted_schema["openapi"])
        self.assertEqual(local_schema["info"], hosted_schema["info"])

    def test_schema_parity_read_scoped_token(self) -> None:
        scoped_token = "cxm_hosted_parity_read_token_0001"
        registered = self._hosted_post(
            "/v1/integrations/mcp-token",
            {"token": scoped_token, "label": "Parity read token", "scopes": ["read"]},
        )
        self.assertEqual(registered.status_code, 200)

        local_status, local_body = self._local_get("/v1/tools/schema?format=openai", token=scoped_token)
        hosted_response = self._hosted_get("/v1/tools/schema", params={"format": "openai"}, token=scoped_token)

        self.assertEqual(local_status, 200)
        self.assertEqual(hosted_response.status_code, 200)
        self.assertEqual(local_body["schema"], hosted_response.json()["schema"])
        names = {fn["function"]["name"] for fn in hosted_response.json()["schema"]}
        self.assertNotIn("remember_this", names)
        self.assertNotIn("delete_all_user_data", names)

    def test_schema_unknown_format_returns_422_on_both_planes(self) -> None:
        local_status, _ = self._local_get("/v1/tools/schema?format=graphql")
        hosted_response = self._hosted_get("/v1/tools/schema", params={"format": "graphql"})
        self.assertEqual(local_status, 422)
        self.assertEqual(hosted_response.status_code, 422)

    def test_schema_requires_auth_on_hosted_plane(self) -> None:
        response = self.hosted_client.get("/v1/tools/schema", params={"format": "openai"})
        self.assertEqual(response.status_code, 401)

    # -- tools/call round-trip + scope enforcement -----------------------------------------

    def test_tools_call_round_trips_same_way_on_both_planes(self) -> None:
        phrase = "Hosted tools API parity: the launch date is March 3rd."
        local_status, local_body = self._local_post(
            "/v1/tools/call",
            {"name": "remember_this", "arguments": {"content": phrase, "source": "hosted-parity-test"}},
        )
        self.assertEqual(local_status, 200)
        self.assertEqual(local_body["tool"], "remember_this")

        hosted_response = self._hosted_post(
            "/v1/tools/call",
            {"name": "search_memory", "arguments": {"query": "launch date March 3rd"}},
        )
        self.assertEqual(hosted_response.status_code, 200)
        hosted_body = hosted_response.json()
        self.assertEqual(hosted_body["tool"], "search_memory")
        # The memory written through the LOCAL plane must be visible through the HOSTED plane's
        # tools/call — proof both planes dispatch against the same store/call_tool path.
        result_text = json.dumps(hosted_body["result"])
        self.assertIn("launch date", result_text)

    def test_hosted_tools_call_records_agent_event_for_cross_ai_headline(self) -> None:
        # A labeled token gives get_tool_scorecard a stable token_id to filter on, so this
        # directly proves record_agent_event fired for a HOSTED /v1/tools/call — the same
        # ledger the cross-AI recall headline metric reads (memory: "cross-AI recall metric").
        # Without this, hosted tool use would be invisible to that metric even though the tool
        # call itself succeeded.
        scoped_token = "cxm_hosted_parity_scorecard_token_0003"
        registered = self._hosted_post(
            "/v1/integrations/mcp-token",
            {"token": scoped_token, "label": "Scorecard probe app", "scopes": ["read"]},
        )
        self.assertEqual(registered.status_code, 200)
        token_id = registered.json()["token_id"]

        before_calls = self._scorecard_calls_for_token(token_id)

        response = self.hosted_client.post(
            "/v1/tools/call",
            json={"name": "search_memory", "arguments": {"query": "hosted agent event probe"}},
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(response.status_code, 200)

        after_calls = self._scorecard_calls_for_token(token_id)
        self.assertGreater(after_calls, before_calls)

    def test_hosted_tools_call_named_path_matches_operation_id_dispatch(self) -> None:
        response = self._hosted_post("/v1/tools/list_capabilities", {})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsInstance(payload, dict)

    def test_hosted_tools_call_unknown_tool_returns_422(self) -> None:
        response = self._hosted_post("/v1/tools/call", {"name": "definitely_not_a_real_tool", "arguments": {}})
        self.assertEqual(response.status_code, 422)

    def test_hosted_tools_call_missing_name_returns_422(self) -> None:
        response = self._hosted_post("/v1/tools/call", {"arguments": {}})
        self.assertEqual(response.status_code, 422)

    def test_hosted_tools_call_requires_auth(self) -> None:
        response = self.hosted_client.post("/v1/tools/call", json={"name": "search_memory", "arguments": {}})
        self.assertEqual(response.status_code, 401)

    def test_read_scoped_hosted_token_cannot_call_write_tool_advertisement_is_not_authorization(self) -> None:
        scoped_token = "cxm_hosted_parity_scope_gate_0002"
        registered = self._hosted_post(
            "/v1/integrations/mcp-token",
            {"token": scoped_token, "label": "Parity scope-gate token", "scopes": ["read"]},
        )
        self.assertEqual(registered.status_code, 200)

        # Same call, both planes: a read-only token must be rejected calling a write tool via
        # tools/call exactly like it is rejected via /mcp's tools/call (same _require_tool_access
        # path). This is the core "advertisement != authorization" contract.
        local_status, local_body = self._local_post(
            "/v1/tools/call",
            {"name": "remember_this", "arguments": {"content": "should not be allowed"}},
            token=scoped_token,
        )
        hosted_response = self.hosted_client.post(
            "/v1/tools/call",
            json={"name": "remember_this", "arguments": {"content": "should not be allowed"}},
            headers={"Authorization": f"Bearer {scoped_token}"},
        )

        self.assertEqual(local_status, 403)
        self.assertEqual(hosted_response.status_code, 403)

        # Confirm parity against /mcp's tools/call too: same scope, same tool, same outcome shape
        # (a JSON-RPC error rather than an HTTP error, but the same PermissionError message).
        mcp_response = self.hosted_client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "remember_this", "arguments": {}}},
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(mcp_response.status_code, 200)
        mcp_payload = mcp_response.json()
        self.assertIn("error", mcp_payload)
        self.assertIn("not scoped", mcp_payload["error"]["message"])
        self.assertIn("not scoped", hosted_response.json()["detail"])

    def test_manifest_advertises_tools_api(self) -> None:
        response = self.hosted_client.get("/.well-known/cortex.json")
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["tools_api"]["schema_endpoint"], "/v1/tools/schema")
        self.assertEqual(payload["tools_api"]["call_endpoint"], "/v1/tools/call")


if __name__ == "__main__":
    unittest.main()
