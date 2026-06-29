from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from backend.app.config import Settings

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from backend.app import standalone_server


class FakeStore:
    def __init__(self) -> None:
        self.search_calls: list[tuple[str, str, int, str | None, str | None]] = []
        self.delete_capture_calls: list[tuple[str, str]] = []
        self.delete_backups_calls: list[str] = []
        self.delete_user_data_calls: list[tuple[str, bool]] = []
        self.restore_latest_backup_calls: list[str] = []
        self.agent_events: list[dict] = []
        self.import_analysis_calls: list[tuple[list[str], str, int]] = []
        self.import_sources_calls: list[tuple[str, list[str], str, str, int]] = []
        self.list_imports_calls: list[tuple[str, int, bool]] = []
        self.get_import_calls: list[tuple[str, str]] = []
        self.delete_import_calls: list[tuple[str, str]] = []

    def search(self, user_id: str, query: str, limit: int, kind: str | None = None, layer: str | None = None) -> list[dict]:
        self.search_calls.append((user_id, query, limit, kind, layer))
        return [
            {
                "id": "memory-1",
                "kind": kind or "claim",
                "layer": layer or "semantic",
                "content": "Layer-aware result",
                "source": "unit-test",
            }
        ]

    def delete_capture(self, user_id: str, capture_id: str) -> bool:
        self.delete_capture_calls.append((user_id, capture_id))
        return capture_id == "capture-1"

    def delete_backups(self, user_id: str) -> dict:
        self.delete_backups_calls.append(user_id)
        return {"deleted_at": "2026-01-01T00:00:00Z", "deleted": 2, "bytes_deleted": 128}

    def restore_latest_backup(self, user_id: str) -> dict:
        self.restore_latest_backup_calls.append(user_id)
        return {"restored_at": "2026-01-01T00:00:00Z", "backup_path": "/tmp/backup.zip", "rebuild": {"captures": 1, "memories": 2}}

    def diagnostics(self, user_id: str) -> dict:
        return {
            "status": "ok",
            "quick_check": "ok",
            "schema_version": 1,
            "db_path": "/tmp/index.sqlite",
            "db_size_bytes": 0,
            "wal_size_bytes": 0,
            "counts": {},
            "fts_orphans": 0,
            "inactive_fts_rows": 0,
            "relation_orphans": 0,
            "last_event_at": None,
            "vector": {"available": False},
            "embedding": {
                "provider": "hash",
                "model": "cortex-hash-v1",
                "dimensions": 384,
                "schema_dimensions": 384,
                "index_compatible": True,
                "network_required": False,
                "strict": False,
            },
            "vault": {"record_counts": {}},
        }

    def supported_import_sources(self) -> list[dict]:
        return [{"id": "chatgpt", "name": "ChatGPT", "formats": ["conversations.json"], "status": "native"}]

    def analyze_import_sources(self, paths: list[str], source_hint: str = "", max_records: int = 500) -> dict:
        self.import_analysis_calls.append((paths, source_hint, max_records))
        return {"records_found": 1, "sources": [{"source": "chatgpt", "count": 1}], "sample": [], "supported_sources": self.supported_import_sources()}

    def import_sources(self, *, user_id: str, paths: list[str], source_hint: str = "", processing: str = "async", max_records: int = 1000) -> dict:
        self.import_sources_calls.append((user_id, paths, source_hint, processing, max_records))
        return {
            "import_id": "imp_test",
            "status": "complete",
            "records_found": 1,
            "queued": 1 if processing == "async" else 0,
            "saved": 1 if processing == "sync" else 0,
            "failed": 0,
            "skipped": 0,
            "sources": [{"source": "chatgpt", "count": 1}],
            "records": [],
            "errors": [],
        }

    def list_imports(self, user_id: str, limit: int = 50, include_deleted: bool = True) -> list[dict]:
        self.list_imports_calls.append((user_id, limit, include_deleted))
        return [{"import_id": "imp_test", "status": "complete", "records_found": 1, "queued": 1, "saved": 0, "failed": 0, "skipped": 0, "sources": [{"source": "chatgpt", "count": 1}], "can_delete": True}]

    def get_import(self, user_id: str, import_id: str) -> dict | None:
        self.get_import_calls.append((user_id, import_id))
        if import_id != "imp_test":
            return None
        return {"import_id": import_id, "status": "complete", "records_found": 1, "records": [], "captures": []}

    def delete_import(self, user_id: str, import_id: str) -> dict:
        self.delete_import_calls.append((user_id, import_id))
        if import_id != "imp_test":
            raise FileNotFoundError("Import not found")
        return {"import_id": import_id, "deleted": True, "status": "deleted", "deleted_captures": 1, "deleted_memories": 2, "deleted_tasks": 0, "deleted_edges": 2}

    def delete_user_data(self, user_id: str, *, include_backups: bool = True) -> dict:
        self.delete_user_data_calls.append((user_id, include_backups))
        return {"deleted_at": "2026-01-01T00:00:00Z", "include_backups": include_backups}

    def authenticate_mcp_token(self, token: str) -> dict | None:
        if token != "cxm-standalone-token":
            return None
        return {
            "token_id": "tok_standalone",
            "user_id": "local",
            "label": "Standalone test MCP",
            "audience": "mcp",
            "scopes": ["read"],
            "admin": False,
        }

    def authenticate_api_token(self, token: str, user_id: str | None = None) -> dict | None:
        if token != "cxa-standalone-token":
            return None
        return {
            "token_id": "tok_standalone_api",
            "user_id": "alice",
            "label": "Standalone test API",
            "audience": "api",
            "scopes": ["read"],
            "admin": False,
        }

    def ensure_api_token(self, user_id: str, token: str, *, label: str, scopes) -> dict:
        return {"token_id": "tok_api", "user_id": user_id, "label": label, "audience": "api", "scopes": scopes or [], "updated_at": "2026-01-01T00:00:00Z"}

    def ensure_mcp_token(self, user_id: str, token: str, *, label: str, scopes, token_id: str) -> dict:
        return {"token_id": token_id, "user_id": user_id, "label": label, "audience": "mcp", "scopes": scopes or [], "updated_at": "2026-01-01T00:00:00Z"}

    def require_agent_access(self, user_id: str, capability: str) -> None:
        return None

    def agent_payload(self, user_id: str, value):
        return value

    def record_agent_event(self, user_id: str, tool_name: str, args: dict, *, success: bool, error: str | None = None, token: dict | None = None) -> None:
        self.agent_events.append({"user_id": user_id, "tool": tool_name, "success": success, "error": error, "token": token})


class StandaloneServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        self.original_origins = standalone_server.ALLOWED_CORS_ORIGINS
        self.fake_store = FakeStore()
        standalone_server.store = self.fake_store
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
        standalone_server.ALLOWED_CORS_ORIGINS = {"http://127.0.0.1:8766", "http://localhost:8766"}
        self.server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        standalone_server.store = self.original_store
        standalone_server.settings = self.original_settings
        standalone_server.ALLOWED_CORS_ORIGINS = self.original_origins
        self.tmp.cleanup()

    def get(self, path: str, origin: str | None = None):
        headers = {"Authorization": "Bearer test-token"}
        if origin:
            headers["Origin"] = origin
        return request.urlopen(request.Request(self.base_url + path, headers=headers), timeout=5)

    def delete(self, path: str):
        headers = {"Authorization": "Bearer test-token"}
        return request.urlopen(request.Request(self.base_url + path, headers=headers, method="DELETE"), timeout=5)

    def post(self, path: str):
        headers = {"Authorization": "Bearer test-token"}
        return request.urlopen(request.Request(self.base_url + path, headers=headers, method="POST"), timeout=5)

    def post_json(self, path: str, payload: dict):
        headers = {"Authorization": "Bearer test-token", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8")
        return request.urlopen(request.Request(self.base_url + path, data=data, headers=headers, method="POST"), timeout=5)

    def test_capture_page_does_not_echo_invalid_token(self) -> None:
        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(self.base_url + "/capture?token=wrong-token&content=Remember", timeout=5)

        self.assertEqual(context.exception.code, 401)
        body = context.exception.read().decode("utf-8")
        self.assertIn("Missing or invalid Cortex capture token", body)
        self.assertNotIn("wrong-token", body)

    def test_search_forwards_layer_and_kind_to_store(self) -> None:
        with self.get("/v1/search?query=voice&kind=style&layer=style&limit=7") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["results"][0]["layer"], "style")
        self.assertEqual(self.fake_store.search_calls, [("local", "voice", 7, "style", "style")])

    def test_cors_does_not_allow_arbitrary_origin(self) -> None:
        with self.get("/v1/search?query=voice", origin="https://example.invalid") as response:
            headers = response.headers

        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_cors_echoes_configured_local_origin(self) -> None:
        with self.get("/v1/search?query=voice", origin="http://localhost:8766") as response:
            headers = response.headers

        self.assertEqual(headers.get("Access-Control-Allow-Origin"), "http://localhost:8766")
        self.assertEqual(headers.get("Vary"), "Origin")

    def test_delete_capture_forwards_to_store(self) -> None:
        with self.delete("/v1/captures/capture-1") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload, {"deleted": True})
        self.assertEqual(self.fake_store.delete_capture_calls, [("local", "capture-1")])

    def test_delete_backups_forwards_to_store(self) -> None:
        with self.delete("/v1/backups") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["deleted"], 2)
        self.assertEqual(self.fake_store.delete_backups_calls, ["local"])

    def test_diagnostics_exposes_embedding_provider_contract(self) -> None:
        with self.get("/v1/diagnostics") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["embedding"]["provider"], "hash")
        self.assertEqual(payload["embedding"]["model"], "cortex-hash-v1")
        self.assertEqual(payload["embedding"]["dimensions"], 384)
        self.assertTrue(payload["embedding"]["index_compatible"])

    def test_restore_latest_backup_forwards_to_store(self) -> None:
        with self.post("/v1/backups/restore-latest") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["rebuild"]["captures"], 1)
        self.assertEqual(self.fake_store.restore_latest_backup_calls, ["local"])

    def test_import_sources_routes_validate_and_forward_to_store(self) -> None:
        with self.get("/v1/imports/sources") as response:
            catalog = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(catalog["results"][0]["id"], "chatgpt")

        with self.post_json("/v1/imports/analyze", {"paths": ["/tmp/conversations.json"], "max_records": 5}) as response:
            analysis = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(analysis["records_found"], 1)
        self.assertEqual(self.fake_store.import_analysis_calls, [(["/tmp/conversations.json"], "", 5)])

        with self.post_json("/v1/imports", {"paths": ["/tmp/conversations.json"], "processing": "sync", "source_hint": "chatgpt"}) as response:
            imported = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(imported["saved"], 1)
        self.assertEqual(self.fake_store.import_sources_calls, [("local", ["/tmp/conversations.json"], "chatgpt", "sync", 1000)])

        with self.get("/v1/imports?limit=10&include_deleted=false") as response:
            history = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(history["results"][0]["import_id"], "imp_test")
        self.assertEqual(self.fake_store.list_imports_calls, [("local", 10, False)])

        with self.get("/v1/imports/imp_test") as response:
            detail = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(detail["import_id"], "imp_test")
        self.assertEqual(self.fake_store.get_import_calls, [("local", "imp_test")])

        with self.delete("/v1/imports/imp_test") as response:
            deleted = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(deleted["deleted"])
        self.assertEqual(self.fake_store.delete_import_calls, [("local", "imp_test")])

    def test_import_sources_rejects_invalid_payloads(self) -> None:
        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/imports", {"paths": "/tmp/conversations.json"})
        self.assertEqual(context.exception.code, 422)

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/imports", {"paths": ["/tmp/conversations.json"], "processing": "later"})
        self.assertEqual(context.exception.code, 422)

        with self.assertRaises(error.HTTPError) as context:
            self.post_json("/v1/imports/analyze", {"paths": ["/tmp/conversations.json"], "max_records": "many"})
        self.assertEqual(context.exception.code, 422)

    def test_delete_user_data_forwards_include_backups_flag(self) -> None:
        with self.delete("/v1/user-data?include_backups=false") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertFalse(payload["include_backups"])
        self.assertEqual(self.fake_store.delete_user_data_calls, [("local", False)])

    def test_scoped_mcp_token_can_call_mcp_but_not_rest(self) -> None:
        scoped_headers = {"Authorization": "Bearer cxm-standalone-token"}
        mcp_request = request.Request(
            self.base_url + "/mcp",
            data=json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "search_memory", "arguments": {"query": "voice"}}}).encode("utf-8"),
            headers={**scoped_headers, "Content-Type": "application/json"},
            method="POST",
        )
        with request.urlopen(mcp_request, timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertIn("result", payload)
        self.assertEqual(self.fake_store.search_calls[-1], ("local", "voice", 8, None, None))
        self.assertEqual(self.fake_store.agent_events[-1]["token"]["token_id"], "tok_standalone")

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(request.Request(self.base_url + "/v1/search?query=voice", headers=scoped_headers), timeout=5)
        self.assertEqual(context.exception.code, 401)

    def test_scoped_api_token_prevents_user_header_impersonation_when_required(self) -> None:
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=Path(self.tmp.name) / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            require_scoped_api_tokens=True,
        )

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/stats",
                    headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)

        with request.urlopen(
            request.Request(
                self.base_url + "/v1/search?query=voice",
                headers={"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "alice"},
            ),
            timeout=5,
        ) as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["results"][0]["content"], "Layer-aware result")
        self.assertEqual(self.fake_store.search_calls[-1][0], "alice")

        with self.assertRaises(error.HTTPError) as context:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/search?query=voice",
                    headers={"Authorization": "Bearer cxa-standalone-token", "X-Cortex-User": "bob"},
                ),
                timeout=5,
            )
        self.assertEqual(context.exception.code, 403)


if __name__ == "__main__":
    unittest.main()
