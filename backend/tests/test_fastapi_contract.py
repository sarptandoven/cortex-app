from __future__ import annotations

import base64
import os
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "fastapi.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "fastapi.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from fastapi.testclient import TestClient

from backend.app import main as main_module

app = main_module.app


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


class FastAPIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.client = TestClient(app)

    def setUp(self) -> None:
        self._allow_pending_context()

    def _allow_pending_context(self, user: str | None = None) -> None:
        headers = {"Authorization": "Bearer test-token"}
        if user:
            headers["X-Cortex-User"] = user
        response = self.client.put("/v1/settings", json={"allow_pending_in_context": True}, headers=headers)
        self.assertEqual(response.status_code, 200)

    def test_capture_get_invalid_token_returns_unauthorized_page(self) -> None:
        response = self.client.get("/capture", params={"token": "wrong-token", "content": "Remember this."})

        self.assertEqual(response.status_code, 401)
        self.assertIn("Missing or invalid Cortex capture token", response.text)
        self.assertNotIn("wrong-token", response.text)

    def test_capture_post_invalid_token_returns_unauthorized_page(self) -> None:
        response = self.client.post(
            "/capture",
            data={"token": "wrong-token", "content": "Remember this."},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertIn("Missing or invalid Cortex capture token", response.text)
        self.assertNotIn("wrong-token", response.text)

    def test_capture_post_empty_content_returns_validation_status(self) -> None:
        response = self.client.post(
            "/capture",
            data={"token": "test-token", "content": ""},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )

        self.assertEqual(response.status_code, 422)
        self.assertIn("content is required", response.text)

    def test_delete_capture_removes_capture_from_search(self) -> None:
        phrase = "FastAPI delete capture contract phrase"
        created = self.client.post(
            "/v1/captures",
            json={"content": phrase, "source": "fastapi-test"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)
        capture_id = created.json()["capture_id"]

        deleted = self.client.delete(f"/v1/captures/{capture_id}", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"deleted": True})

        search = self.client.get(
            "/v1/search",
            params={"query": phrase},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["results"], [])

    def test_diagnostics_exposes_embedding_provider_contract(self) -> None:
        response = self.client.get("/v1/diagnostics", headers={"Authorization": "Bearer test-token"})

        self.assertEqual(response.status_code, 200)
        embedding = response.json()["embedding"]
        self.assertEqual(embedding["provider"], "hash")
        self.assertEqual(embedding["model"], "cortex-hash-v1")
        self.assertEqual(embedding["dimensions"], 384)
        self.assertEqual(embedding["schema_dimensions"], 384)
        self.assertTrue(embedding["index_compatible"])
        self.assertFalse(embedding["network_required"])

    def test_privacy_lifecycle_report_exposes_delete_and_backup_contract(self) -> None:
        phrase = "FastAPI privacy lifecycle contract phrase"
        created = self.client.post(
            "/v1/captures",
            json={"content": phrase, "source": "fastapi-test"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)
        backup = self.client.post("/v1/backups", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(backup.status_code, 200)

        response = self.client.get("/v1/privacy/lifecycle", headers={"Authorization": "Bearer test-token"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn(payload["status"], {"ok", "needs_attention"})
        self.assertEqual(payload["storage"]["mode"], "local_first")
        self.assertGreaterEqual(payload["record_counts"]["captures"], 1)
        self.assertGreaterEqual(payload["backups"]["count"], 1)
        self.assertEqual(payload["export"]["json_endpoint"], "/v1/export.json")
        self.assertEqual(payload["deletion"]["endpoint"], "/v1/user-data?include_backups=true")
        self.assertIn("api_tokens", payload["deletion"]["covered_sqlite"])
        self.assertIn("credentials", payload["deletion"]["covered_vault"])
        self.assertTrue(payload["deletion"]["restore_preserves_tombstones"])
        self.assertIn("trust_score", payload["ai_access"])
        self.assertIn("events", payload["audit"])

    def test_health_exposes_sharding_contract(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        sharding = payload["sharding"]
        self.assertEqual(sharding["mode"], "local")
        self.assertEqual(sharding["default"]["shard_id"], "local")
        self.assertIn("db_path", sharding["default"])
        hosted_readiness = payload["hosted_readiness"]
        self.assertEqual(hosted_readiness["status"], "ok")
        self.assertFalse(hosted_readiness["hosted_mode"])
        self.assertEqual(hosted_readiness["shard_mode"], "local")
        self.assertFalse(hosted_readiness["require_scoped_api_tokens"])
        self.assertEqual(hosted_readiness["global_token_user_switching"], "allowed_local_compatibility")
        self.assertEqual(hosted_readiness["checks"][0]["name"], "scoped_api_tokens_required")

    def test_ready_requires_scoped_api_tokens_for_hosted_shard_modes(self) -> None:
        original_settings = main_module.settings
        main_module.settings = replace(original_settings, shard_mode="bucket", require_scoped_api_tokens=False)
        try:
            response = self.client.get("/ready")

            self.assertEqual(response.status_code, 503)
            detail = response.json()["detail"]
            self.assertEqual(detail["status"], "needs_configuration")
            hosted_readiness = detail["hosted_readiness"]
            self.assertEqual(hosted_readiness["status"], "blocked")
            self.assertTrue(hosted_readiness["hosted_mode"])
            self.assertEqual(hosted_readiness["shard_mode"], "bucket")
            self.assertFalse(hosted_readiness["require_scoped_api_tokens"])
            self.assertEqual(hosted_readiness["global_token_user_switching"], "blocked")
            self.assertEqual(hosted_readiness["checks"][0]["status"], "blocked")
            self.assertIn("CORTEX_REQUIRE_SCOPED_API_TOKENS=1", hosted_readiness["checks"][0]["detail"])
        finally:
            main_module.settings = original_settings

    def test_hosted_ready_requires_runtime_scoped_token_control_plane(self) -> None:
        original_settings = main_module.settings
        original_store = main_module.store
        temp = tempfile.TemporaryDirectory()
        try:
            root = Path(temp.name)
            hosted_settings = replace(
                original_settings,
                db_path=root / "hosted.sqlite",
                vault_path=root / "hosted.vault",
                shard_root=root / "shards",
                default_user_id="hosted-default",
                shard_mode="bucket",
                require_scoped_api_tokens=True,
                public_base_url="https://api.cortex-hq.com",
                sync_signing_key="sync-signing-key",
                hosted_vector_backend="pgvector",
                worker_mode="external",
                observability_enabled=True,
                embedding_provider="openai",
            )
            main_module.settings = hosted_settings
            main_module.store = main_module.StoreRegistry.from_settings(hosted_settings)
            user = "hosted-ready-contract"

            response = self.client.get("/ready")
            self.assertEqual(response.status_code, 503)
            blocked = {
                check["name"]
                for check in response.json()["detail"]["hosted_readiness"]["checks"]
                if check["status"] == "blocked"
            }
            self.assertEqual(blocked, {"background_worker_queue", "control_plane_scoped_tokens"})

            main_module.store.ensure_api_token(user, "cxa_hosted_ready_api_token_123456789", label="Hosted API", scopes=["read"])
            split_user = "hosted-ready-mcp-only"
            main_module.store.ensure_mcp_token(split_user, "cxm_hosted_ready_split_mcp_token_123456789", label="Hosted MCP", scopes=["read"])
            split_ready = self.client.get("/ready")
            self.assertEqual(split_ready.status_code, 503)
            split_blocked = {
                check["name"]
                for check in split_ready.json()["detail"]["hosted_readiness"]["checks"]
                if check["status"] == "blocked"
            }
            self.assertEqual(split_blocked, {"background_worker_queue", "control_plane_scoped_tokens"})

            main_module.store.ensure_mcp_token(user, "cxm_hosted_ready_mcp_token_123456789", label="Hosted MCP", scopes=["read"])

            ready = self.client.get("/ready")
            self.assertEqual(ready.status_code, 200)
            control = ready.json()["hosted_readiness"]["runtime"]["control_plane"]
            worker_queue = ready.json()["hosted_readiness"]["runtime"]["worker_queue"]
            self.assertEqual(control["active_api_tokens"], 1)
            self.assertEqual(control["active_mcp_tokens"], 2)
            self.assertEqual(control["active_users"], 2)
            self.assertEqual(control["active_ready_users"], 1)
            self.assertEqual(worker_queue["status"], "ok")
            self.assertEqual(worker_queue["ready_user_count"], 1)
        finally:
            temp.cleanup()
            main_module.store = original_store
            main_module.settings = original_settings

    def test_memory_quality_endpoint_exposes_citation_and_review_contract(self) -> None:
        created = self.client.post(
            "/v1/captures",
            json={
                "content": "FastAPI quality report memory should include cited source coverage.",
                "source": "quality-test",
                "source_url": "/tmp/quality-source.md",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)

        response = self.client.get("/v1/memory/quality", headers={"Authorization": "Bearer test-token"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("score", payload)
        self.assertIn("citation_coverage", payload)
        self.assertIn("date_coverage", payload)
        self.assertIn("review_coverage", payload)
        self.assertIn("source_health", payload)
        self.assertTrue(any(source["source"] == "quality-test" for source in payload["source_health"]))

    def test_ask_endpoint_returns_cited_answer_contract(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "ask-contract"}
        phrase = "FastAPI Ask citation contract should quote source-backed memory."
        created = self.client.post(
            "/v1/captures",
            json={
                "content": phrase,
                "source": "ask-test",
                "source_url": "/tmp/ask-source.md",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)
        approved = self.client.post(f"/v1/captures/{created.json()['capture_id']}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)

        response = self.client.get("/v1/ask", params={"query": "Ask citation contract", "limit": 5}, headers=headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["query"], "Ask citation contract")
        self.assertIn("cited", payload["answer"])
        self.assertTrue(payload["citations"])
        self.assertTrue(payload["results"])
        self.assertTrue(payload["citations"][0]["source_url"].startswith("local-file://ask-source.md?path_hash="))
        self.assertTrue(payload["results"][0]["source_url"].startswith("local-file://ask-source.md?path_hash="))
        self.assertIn("Ask citation contract", payload["citations"][0]["excerpt"])

    def test_ask_endpoint_prefers_source_backed_citations_over_uncited_matches(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "ask-source-backed-contract"}
        uncited = self.client.post(
            "/v1/captures",
            json={
                "content": "FastAPI Ask source-backed ranking points at a generic uncited memo.",
                "source": "ask-test",
            },
            headers=headers,
        )
        self.assertEqual(uncited.status_code, 200)
        approved_uncited = self.client.post(f"/v1/captures/{uncited.json()['capture_id']}/approve", headers=headers)
        self.assertEqual(approved_uncited.status_code, 200)

        cited = self.client.post(
            "/v1/captures",
            json={
                "content": "FastAPI Ask source-backed ranking points at the canonical connected source.",
                "source": "github",
                "source_url": "cortex-source://github#service=github&file=issues.json&line=34&excerpt=ask-source-backed",
            },
            headers=headers,
        )
        self.assertEqual(cited.status_code, 200)
        approved_cited = self.client.post(f"/v1/captures/{cited.json()['capture_id']}/approve", headers=headers)
        self.assertEqual(approved_cited.status_code, 200)

        response = self.client.get(
            "/v1/ask",
            params={"query": "FastAPI Ask source-backed ranking", "limit": 2},
            headers=headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["citations"])
        self.assertTrue(all(citation["source_url"] for citation in payload["citations"]))
        self.assertIn("canonical connected source", payload["citations"][0]["excerpt"])
        self.assertNotIn("generic uncited memo", json.dumps(payload["citations"]))
        self.assertTrue(payload["results"][0]["source_url"])

    def test_retrieval_endpoints_support_sector_scope(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "sector-contract"}
        records = [
            (
                "Project Atlas",
                "Release checklist sector contract: Project Atlas runs backend tests and codesign.",
                "service://notes?project=Project%20Atlas",
            ),
            (
                "Project Boreal",
                "Release checklist sector contract: Project Boreal runs web smoke tests and CDN purge.",
                "service://notes?project=Project%20Boreal",
            ),
        ]
        for _, content, source_url in records:
            created = self.client.post(
                "/v1/captures",
                json={"content": content, "source": "notes", "source_url": source_url},
                headers=headers,
            )
            self.assertEqual(created.status_code, 200)
            approved = self.client.post(f"/v1/captures/{created.json()['capture_id']}/approve", headers=headers)
            self.assertEqual(approved.status_code, 200)

        search = self.client.get(
            "/v1/search",
            params={"query": "release checklist sector contract", "sector": "Project Atlas", "limit": 5},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        search_payload = search.json()
        self.assertEqual(search_payload["sector"], "Project Atlas")
        self.assertIn("retrieval", search_payload)
        self.assertEqual(search_payload["retrieval"]["candidate_limit"], 50)
        self.assertIn("fts", search_payload["retrieval"]["mode_counts"])
        self.assertIn("embedding_provider", search_payload["retrieval"])
        self.assertIn("degraded_reasons", search_payload["retrieval"])
        search_text = json.dumps(search_payload)
        self.assertIn("Project Atlas runs backend tests", search_text)
        self.assertNotIn("Project Boreal runs web smoke tests", search_text)

        ask = self.client.get(
            "/v1/ask",
            params={"query": "release checklist sector contract", "sector": "Project Atlas", "limit": 5},
            headers=headers,
        )
        self.assertEqual(ask.status_code, 200)
        ask_text = json.dumps(ask.json())
        self.assertIn("Project Atlas runs backend tests", ask_text)
        self.assertNotIn("Project Boreal runs web smoke tests", ask_text)

        pack = self.client.get(
            "/v1/context-pack",
            params={"query": "release checklist sector contract", "sector": "Project Atlas", "limit": 5},
            headers=headers,
        )
        self.assertEqual(pack.status_code, 200)
        self.assertIn("Sector: Project Atlas", pack.text)
        self.assertIn("Project Atlas runs backend tests", pack.text)
        self.assertNotIn("Project Boreal runs web smoke tests", pack.text)

    def test_ask_endpoint_redacts_local_paths_inside_service_citation_parameters(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "ask-service-locator-contract"}
        phrase = "FastAPI Ask service locator privacy should quote source-backed memory."
        source_url = (
            "obsidian://open?vault=Work"
            "&path=%2FUsers%2Fvamika%2FDocuments%2FFastAPI%20Private%2FAsk%20Source.md"
            "#ref=/Users/vamika/Documents/FastAPI Private/Ask Notes.md&line=9"
        )
        created = self.client.post(
            "/v1/captures",
            json={
                "content": phrase,
                "source": "ask-service-test",
                "source_url": source_url,
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)
        approved = self.client.post(f"/v1/captures/{created.json()['capture_id']}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)

        response = self.client.get("/v1/ask", params={"query": "Ask service locator privacy", "limit": 5}, headers=headers)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        payload_text = json.dumps(payload)
        self.assertNotIn("/Users/vamika", payload_text)
        self.assertNotIn("%2FUsers%2Fvamika", payload_text)
        self.assertIn("obsidian://open", payload["citations"][0]["source_url"])
        self.assertIn("vault=Work", payload["citations"][0]["source_url"])
        self.assertIn("path=local-file://Ask%20Source.md", payload["citations"][0]["source_url"])
        self.assertIn("ref=local-file://Ask%20Notes.md", payload["citations"][0]["source_url"])
        self.assertIn("line=9", payload["citations"][0]["source_url"])

    def test_ask_endpoint_returns_open_task_citation_contract(self) -> None:
        user_id = "ask-task-contract"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user_id}
        saved = main_module.store.save_capture(
            user_id=user_id,
            content="Planning notes: follow up with Mira about the import undo copy before beta.",
            source="notion",
            source_url="notion://page/import-undo",
            title="Beta planning",
            extracted={
                "_timestamp": "2026-06-29T13:00:00Z",
                "summary": "Beta planning task.",
                "records": [],
                "tasks": [
                    {
                        "id": "task_fastapi_import_undo",
                        "kind": "action",
                        "content": "Follow up with Mira about the import undo copy before beta.",
                        "status": "open",
                        "importance": 4,
                        "topics": ["import", "beta"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )
        self.assertTrue(main_module.store.approve_capture(user_id, saved["capture_id"]))

        response = self.client.get(
            "/v1/ask",
            params={"query": "What open loops are there about import undo?", "limit": 5},
            headers=headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["citations"])
        self.assertTrue(payload["results"])
        self.assertEqual(payload["citations"][0]["result_type"], "task")
        self.assertEqual(payload["citations"][0]["layer"], "task")
        self.assertEqual(payload["citations"][0]["status"], "open")
        self.assertTrue(payload["citations"][0]["source_url"].startswith("notion://page/import-undo"))
        self.assertIn("line=1", payload["citations"][0]["source_url"])
        self.assertIn("excerpt=", payload["citations"][0]["source_url"])
        self.assertEqual(payload["results"][0]["result_type"], "task")

    def test_ask_endpoint_adds_line_locator_to_local_task_citation(self) -> None:
        user_id = "ask-local-task-citation-contract"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user_id}
        saved = main_module.store.save_capture(
            user_id=user_id,
            content=(
                "# Beta Smoke Notes\n\n"
                "Decision: Project Taipei should keep cited local memory stable.\n"
                "Action: follow up with Mira about the beta invite checklist for local task citation.\n"
            ),
            source="obsidian",
            source_url="file:///Users/example/Obsidian/Beta%20Smoke%20Notes.md",
            title="Beta Smoke Notes",
            extracted={
                "_timestamp": "2026-06-29T13:00:00Z",
                "summary": "Beta planning task.",
                "records": [],
                "tasks": [
                    {
                        "id": "task_fastapi_local_task_citation",
                        "kind": "action",
                        "content": "Action: follow up with Mira about the beta invite checklist for local task citation.",
                        "status": "open",
                        "importance": 4,
                        "topics": ["beta"],
                        "entity_ids": [],
                    }
                ],
                "entities": [],
            },
        )
        self.assertTrue(main_module.store.approve_capture(user_id, saved["capture_id"]))

        response = self.client.get(
            "/v1/ask",
            params={"query": "follow up Mira beta invite checklist local task citation", "limit": 5},
            headers=headers,
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        task_citation = next(citation for citation in payload["citations"] if citation["result_type"] == "task")
        self.assertTrue(task_citation["source_url"].startswith("local-file://Beta%20Smoke%20Notes.md"))
        self.assertIn("line=4", task_citation["source_url"])
        self.assertIn("excerpt=", task_citation["source_url"])
        self.assertNotIn("/Users/example", json.dumps(payload))

    def test_scoped_api_token_prevents_user_header_impersonation_when_required(self) -> None:
        scoped_token = "cxa_fastapi_contract_token_123456789"
        registered = self.client.post(
            "/v1/integrations/api-token",
            json={"token": scoped_token, "label": "Alice REST client", "scopes": ["read", "write"]},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
        )
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json()["audience"], "api")
        self.assertEqual(registered.json()["user_id"], "alice")

        original_settings = main_module.settings
        main_module.settings = replace(original_settings, require_scoped_api_tokens=True)
        try:
            global_with_user = self.client.get(
                "/v1/stats",
                headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
            )
            self.assertEqual(global_with_user.status_code, 403)

            scoped = self.client.get(
                "/v1/stats",
                headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": "alice"},
            )
            self.assertEqual(scoped.status_code, 200)

            export_blocked = self.client.get(
                "/v1/context-pack",
                params={"query": "anything"},
                headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": "alice"},
            )
            self.assertEqual(export_blocked.status_code, 403)
            self.assertIn("export scope", export_blocked.json()["detail"])

            destructive_blocked = self.client.delete(
                "/v1/user-data",
                params={"include_backups": "false"},
                headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": "alice"},
            )
            self.assertEqual(destructive_blocked.status_code, 403)
            self.assertIn("destructive scope", destructive_blocked.json()["detail"])

            mismatched = self.client.get(
                "/v1/stats",
                headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": "bob"},
            )
            self.assertEqual(mismatched.status_code, 403)
        finally:
            main_module.settings = original_settings

    def test_scoped_api_tokens_obey_trust_controls(self) -> None:
        user = "scoped-rest-trust"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}
        tokens = {
            "write": "cxa_fastapi_trust_write_123456789",
            "export": "cxa_fastapi_trust_export_123456789",
            "maintenance": "cxa_fastapi_trust_maint_123456789",
            "destructive": "cxa_fastapi_trust_delete_123456789",
        }
        registrations = {
            "write": ["read", "write"],
            "export": ["read", "export"],
            "maintenance": ["read", "maintenance"],
            "destructive": ["read", "destructive"],
        }
        for label, scopes in registrations.items():
            response = self.client.post(
                "/v1/integrations/api-token",
                json={"token": tokens[label], "label": f"{label} REST client", "scopes": scopes},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)

        self.client.put("/v1/settings", json={"allow_agent_writes": False}, headers=headers)
        write_blocked = self.client.post(
            "/v1/captures",
            json={"content": "rest write trust gate memory.", "source": "fastapi-test"},
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(write_blocked.status_code, 403)
        self.assertIn("writes are disabled", write_blocked.json()["detail"])

        export_blocked = self.client.get(
            "/v1/context-pack",
            params={"query": "anything"},
            headers={"Authorization": f"Bearer {tokens['export']}", "X-Cortex-User": user},
        )
        self.assertEqual(export_blocked.status_code, 403)
        self.assertIn("exports are disabled", export_blocked.json()["detail"])

        maintenance_blocked = self.client.post(
            "/v1/maintenance/rebuild-vectors",
            headers={"Authorization": f"Bearer {tokens['maintenance']}", "X-Cortex-User": user},
        )
        self.assertEqual(maintenance_blocked.status_code, 403)
        self.assertIn("maintenance actions are disabled", maintenance_blocked.json()["detail"])

        created = self.client.post(
            "/v1/captures",
            json={"content": "rest destructive trust gate memory.", "source": "fastapi-test"},
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)
        destructive_blocked = self.client.delete(
            f"/v1/captures/{created.json()['capture_id']}",
            headers={"Authorization": f"Bearer {tokens['destructive']}", "X-Cortex-User": user},
        )
        self.assertEqual(destructive_blocked.status_code, 403)
        self.assertIn("destructive actions are disabled", destructive_blocked.json()["detail"])

        self.client.put("/v1/settings", json={"allow_agent_writes": True}, headers=headers)
        write_allowed = self.client.post(
            "/v1/captures",
            json={"content": "rest write trust gate allowed memory.", "source": "fastapi-test"},
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(write_allowed.status_code, 200)

        settings_escalation_blocked = self.client.put(
            "/v1/settings",
            json={"allow_agent_exports": True},
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(settings_escalation_blocked.status_code, 403)
        self.assertIn("maintenance scope", settings_escalation_blocked.json()["detail"])

        source_account_blocked = self.client.post(
            "/v1/source-accounts",
            json={"source": "gmail", "account_label": "Scoped Gmail"},
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(source_account_blocked.status_code, 403)
        self.assertIn("maintenance scope", source_account_blocked.json()["detail"])

        source_disconnect_blocked = self.client.post(
            "/v1/source-accounts/sacct_write_blocked/disconnect",
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(source_disconnect_blocked.status_code, 403)
        self.assertIn("maintenance scope", source_disconnect_blocked.json()["detail"])

        sync_cursor_blocked = self.client.post(
            "/v1/sync-cursors",
            json={"source": "gmail", "cursor_name": "messages"},
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(sync_cursor_blocked.status_code, 403)
        self.assertIn("maintenance scope", sync_cursor_blocked.json()["detail"])

        source_sync_blocked = self.client.post(
            "/v1/sources/sync-due",
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(source_sync_blocked.status_code, 403)
        self.assertIn("maintenance scope", source_sync_blocked.json()["detail"])

        self.client.put("/v1/settings", json={"allow_agent_maintenance": True}, headers=headers)
        source_account_allowed = self.client.post(
            "/v1/source-accounts",
            json={"source": "gmail", "account_label": "Scoped Gmail"},
            headers={"Authorization": f"Bearer {tokens['maintenance']}", "X-Cortex-User": user},
        )
        self.assertEqual(source_account_allowed.status_code, 200)

        sync_cursor_allowed = self.client.post(
            "/v1/sync-cursors",
            json={"source": "gmail", "cursor_name": "messages"},
            headers={"Authorization": f"Bearer {tokens['maintenance']}", "X-Cortex-User": user},
        )
        self.assertEqual(sync_cursor_allowed.status_code, 200)

        source_sync_allowed = self.client.post(
            "/v1/sources/sync-due",
            headers={"Authorization": f"Bearer {tokens['maintenance']}", "X-Cortex-User": user},
        )
        self.assertEqual(source_sync_allowed.status_code, 200)

        source_disconnect_allowed = self.client.post(
            f"/v1/source-accounts/{source_account_allowed.json()['id']}/disconnect",
            headers={"Authorization": f"Bearer {tokens['maintenance']}", "X-Cortex-User": user},
        )
        self.assertEqual(source_disconnect_allowed.status_code, 200)
        self.assertEqual(source_disconnect_allowed.json()["retention"]["disconnect_action"], "pause_sync")

    def test_scoped_capture_query_token_obeys_trust_controls(self) -> None:
        user = "scoped-capture-query-trust"
        scoped_token = "cxa_fastapi_query_capture_123456789"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}
        registered = self.client.post(
            "/v1/integrations/api-token",
            json={"token": scoped_token, "label": "Capture query token", "scopes": ["write"]},
            headers=headers,
        )
        self.assertEqual(registered.status_code, 200)
        disabled = self.client.put("/v1/settings", json={"allow_agent_writes": False}, headers=headers)
        self.assertEqual(disabled.status_code, 200)

        original_settings = main_module.settings
        main_module.settings = replace(original_settings, require_scoped_api_tokens=True)
        try:
            blocked = self.client.get(
                "/capture",
                params={"token": scoped_token, "content": "query capture trust gate memory."},
            )
            self.assertEqual(blocked.status_code, 403)
            self.assertIn("writes are disabled", blocked.text)
        finally:
            main_module.settings = original_settings

        enabled = self.client.put("/v1/settings", json={"allow_agent_writes": True}, headers=headers)
        self.assertEqual(enabled.status_code, 200)

        main_module.settings = replace(original_settings, require_scoped_api_tokens=True)
        try:
            allowed = self.client.get(
                "/capture",
                params={"token": scoped_token, "content": "query capture trust gate memory."},
            )
            self.assertEqual(allowed.status_code, 200)
            self.assertIn("Saved", allowed.text)
        finally:
            main_module.settings = original_settings

    def test_global_token_cannot_select_user_in_sharded_mode(self) -> None:
        original_settings = main_module.settings
        main_module.settings = replace(original_settings, shard_mode="user", require_scoped_api_tokens=False)
        try:
            rest = self.client.get(
                "/v1/stats",
                headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
            )
            self.assertEqual(rest.status_code, 403)
            self.assertIn("sharded mode", rest.json()["detail"])

            mcp = self.client.post(
                "/mcp",
                json={"jsonrpc": "2.0", "id": 91, "method": "tools/list", "params": {}},
                headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
            )
            self.assertEqual(mcp.status_code, 403)
            self.assertIn("sharded mode", mcp.json()["detail"])

            default_user = self.client.get(
                "/v1/stats",
                headers={"Authorization": "Bearer test-token", "X-Cortex-User": "local"},
            )
            self.assertEqual(default_user.status_code, 200)
        finally:
            main_module.settings = original_settings

    def test_integration_tokens_can_be_listed_and_revoked(self) -> None:
        scoped_token = "cxa_fastapi_revoke_token_123456789"
        registered = self.client.post(
            "/v1/integrations/api-token",
            json={"token": scoped_token, "label": "Revocable REST client", "scopes": ["read", "maintenance"]},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
        )
        self.assertEqual(registered.status_code, 200)
        token_id = registered.json()["token_id"]

        listed = self.client.get(
            "/v1/integrations/tokens",
            params={"audience": "api"},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
        )
        self.assertEqual(listed.status_code, 200)
        token_ids = [token["token_id"] for token in listed.json()["results"]]
        self.assertIn(token_id, token_ids)
        self.assertNotIn("token_hash", listed.json()["results"][0])

        before_revoke = self.client.get(
            "/v1/stats",
            headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": "alice"},
        )
        self.assertEqual(before_revoke.status_code, 200)

        revoked = self.client.delete(
            f"/v1/integrations/tokens/{token_id}",
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
        )
        self.assertEqual(revoked.status_code, 200)
        self.assertTrue(revoked.json()["revoked"])
        self.assertIsNotNone(revoked.json()["revoked_at"])

        after_revoke = self.client.get(
            "/v1/stats",
            headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": "alice"},
        )
        self.assertEqual(after_revoke.status_code, 401)

        listed_active = self.client.get(
            "/v1/integrations/tokens",
            params={"audience": "api"},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
        )
        self.assertNotIn(token_id, [token["token_id"] for token in listed_active.json()["results"]])

        listed_revoked = self.client.get(
            "/v1/integrations/tokens",
            params={"audience": "api", "include_revoked": "true"},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "alice"},
        )
        revoked_tokens = {token["token_id"]: token for token in listed_revoked.json()["results"]}
        self.assertEqual(revoked_tokens[token_id]["revoked_at"], revoked.json()["revoked_at"])

    def test_source_account_and_sync_cursor_contract(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "source-contract"}

        catalog = self.client.get("/v1/source-accounts/catalog", headers=headers)
        self.assertEqual(catalog.status_code, 200)
        catalog_ids = {item["id"] for item in catalog.json()["results"]}
        self.assertIn("gmail", catalog_ids)
        self.assertIn("notion", catalog_ids)

        account_response = self.client.post(
            "/v1/source-accounts",
            json={
                "source": "Gmail",
                "account_label": "Contract Gmail",
                "account_identifier": "contract@example.com",
                "connection_type": "oauth",
                "status": "connected",
                "auth_state": "healthy",
                "policy": {"sync": "incremental"},
                "metadata": {"tenant": "contract"},
            },
            headers=headers,
        )
        self.assertEqual(account_response.status_code, 200)
        account = account_response.json()
        self.assertEqual(account["source"], "gmail")
        self.assertEqual(account["policy"]["sync"], "incremental")
        self.assertEqual(account["retention"]["disconnect_action"], "pause_sync")
        self.assertIn("memories", account["retention"]["disconnect_retains"])
        self.assertIn("local_credentials", account["retention"]["disconnect_retains"])

        cursor_response = self.client.post(
            "/v1/sync-cursors",
            json={
                "source": "gmail",
                "source_account_id": account["id"],
                "cursor_name": "messages",
                "cursor_value": "cursor-1",
                "high_water_mark": "2026-06-29T12:00:00Z",
                "state": {"batch": 1},
            },
            headers=headers,
        )
        self.assertEqual(cursor_response.status_code, 200)
        cursor = cursor_response.json()
        self.assertEqual(cursor["source_account_id"], account["id"])
        self.assertEqual(cursor["state"]["batch"], 1)

        sync_payload = {
            "processing": "sync",
            "cursor_name": "messages",
            "cursor_value": "cursor-2",
            "high_water_mark": "2026-06-29T13:00:00Z",
            "state": {"batch": 2},
            "records": [
                {
                    "content": "I decided Project Atlas uses connected source sync for Gmail records.",
                    "title": "Atlas Gmail",
                    "external_id": "gmail-msg-1",
                    "captured_at": "2026-06-29T13:00:00Z",
                }
            ],
        }
        sync_response = self.client.post(
            f"/v1/source-accounts/{account['id']}/sync",
            json=sync_payload,
            headers=headers,
        )
        self.assertEqual(sync_response.status_code, 200)
        synced = sync_response.json()
        self.assertEqual(synced["source"], "gmail")
        self.assertEqual(synced["status"], "complete")
        self.assertEqual(synced["processing"], "sync")
        self.assertEqual(synced["received"], 1)
        self.assertEqual(synced["saved"], 1)
        self.assertEqual(synced["queued"], 0)
        self.assertEqual(synced["skipped"], 0)
        self.assertTrue(synced["records"][0]["source_url"].startswith(f"source-account://gmail/{account['id']}/gmail-msg-1"))
        self.assertEqual(synced["cursor"]["cursor_value"], "cursor-2")
        self.assertEqual(synced["cursor"]["state"]["last_batch_saved"], 1)
        self.assertEqual(synced["archived_missing"], 0)
        capture_id = synced["capture_ids"][0]
        approved = self.client.post(f"/v1/captures/{capture_id}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        found = self.client.get(
            "/v1/search",
            params={"query": "Project Atlas connected source sync"},
            headers=headers,
        )
        self.assertTrue(found.json()["results"])

        duplicate_sync = self.client.post(
            f"/v1/source-accounts/{account['id']}/sync",
            json=sync_payload,
            headers=headers,
        )
        self.assertEqual(duplicate_sync.status_code, 200)
        duplicate_payload = duplicate_sync.json()
        self.assertEqual(duplicate_payload["status"], "complete")
        self.assertEqual(duplicate_payload["saved"], 0)
        self.assertEqual(duplicate_payload["skipped"], 1)
        self.assertEqual(duplicate_payload["records"][0]["status"], "duplicate")

        unsafe_archive = self.client.post(
            f"/v1/source-accounts/{account['id']}/sync",
            json={
                "processing": "sync",
                "cursor_name": "messages",
                "cursor_value": "cursor-unsafe",
                "archive_missing": True,
                "records": [
                    {
                        "content": "Partial Gmail page should not archive Project Atlas memory.",
                        "title": "Atlas Gmail partial",
                        "external_id": "gmail-msg-partial",
                        "captured_at": "2026-06-29T13:03:00Z",
                    }
                ],
            },
            headers=headers,
        )
        self.assertEqual(unsafe_archive.status_code, 422)
        self.assertIn("complete_snapshot", unsafe_archive.json()["detail"])
        still_found = self.client.get(
            "/v1/search",
            params={"query": "Project Atlas connected source sync"},
            headers=headers,
        )
        self.assertTrue(still_found.json()["results"])

        full_snapshot = self.client.post(
            f"/v1/source-accounts/{account['id']}/sync",
            json={
                "processing": "sync",
                "cursor_name": "messages",
                "cursor_value": "cursor-3",
                "archive_missing": True,
                "complete_snapshot": True,
                "records": [
                    {
                        "content": "I decided Project Atlas now keeps only the current Gmail source snapshot.",
                        "title": "Atlas Gmail current",
                        "external_id": "gmail-msg-2",
                        "captured_at": "2026-06-29T13:05:00Z",
                    }
                ],
            },
            headers=headers,
        )
        self.assertEqual(full_snapshot.status_code, 200)
        full_snapshot_payload = full_snapshot.json()
        self.assertEqual(full_snapshot_payload["archived_missing"], 1)
        self.assertEqual(full_snapshot_payload["cursor"]["state"]["last_batch_archived_missing"], 1)
        found_after_archive = self.client.get(
            "/v1/search",
            params={"query": "Project Atlas connected source sync"},
            headers=headers,
        )
        self.assertEqual(found_after_archive.json()["results"], [])

        missing_account = self.client.post(
            "/v1/sync-cursors",
            json={"source": "gmail", "source_account_id": "sacct_missing", "cursor_name": "messages"},
            headers=headers,
        )
        self.assertEqual(missing_account.status_code, 422)

        missing_sync_account = self.client.post(
            "/v1/source-accounts/sacct_missing/sync",
            json=sync_payload,
            headers=headers,
        )
        self.assertEqual(missing_sync_account.status_code, 422)

        listed_accounts = self.client.get("/v1/source-accounts", headers=headers)
        self.assertEqual(listed_accounts.status_code, 200)
        self.assertEqual(listed_accounts.json()["results"][0]["id"], account["id"])
        self.assertIsNotNone(listed_accounts.json()["results"][0]["last_sync_at"])

        listed_cursors = self.client.get(
            "/v1/sync-cursors",
            params={"source_account_id": account["id"]},
            headers=headers,
        )
        self.assertEqual(listed_cursors.status_code, 200)
        self.assertEqual([item["id"] for item in listed_cursors.json()["results"]], [cursor["id"]])

        readiness_response = self.client.get("/v1/sources/readiness", headers=headers)
        self.assertEqual(readiness_response.status_code, 200)
        readiness = readiness_response.json()
        self.assertIn("generated_at", readiness)
        self.assertIn("summary", readiness)
        self.assertIn("sources", readiness)
        self.assertTrue(readiness["recommendations"])
        gmail_readiness = next(item for item in readiness["sources"] if item["source"] == "gmail")
        self.assertEqual(gmail_readiness["accounts"], 1)
        self.assertEqual(gmail_readiness["cursors"], 1)
        self.assertIn(gmail_readiness["status"], {"connected", "synced", "needs_review", "needs_attention"})

        disconnected = self.client.post(f"/v1/source-accounts/{account['id']}/disconnect", headers=headers)
        self.assertEqual(disconnected.status_code, 200)
        self.assertEqual(disconnected.json()["status"], "disconnected")
        self.assertEqual(disconnected.json()["retention"]["disconnect_action"], "pause_sync")
        self.assertIn("memories", disconnected.json()["retention"]["disconnect_retains"])

        active_after_disconnect = self.client.get("/v1/source-accounts", headers=headers)
        self.assertEqual(active_after_disconnect.status_code, 200)
        self.assertEqual(active_after_disconnect.json()["results"], [])

        all_accounts = self.client.get(
            "/v1/source-accounts",
            params={"include_disconnected": "true"},
            headers=headers,
        )
        self.assertEqual(all_accounts.status_code, 200)
        self.assertEqual(all_accounts.json()["results"][0]["id"], account["id"])
        self.assertEqual(all_accounts.json()["results"][0]["retention"]["delete_action"], "delete_user_data")

        legacy_disconnect = self.client.delete(f"/v1/source-accounts/{account['id']}", headers=headers)
        self.assertEqual(legacy_disconnect.status_code, 200)
        self.assertEqual(legacy_disconnect.json()["retention"]["disconnect_action"], "pause_sync")

        missing_delete = self.client.post("/v1/source-accounts/sacct_missing/disconnect", headers=headers)
        self.assertEqual(missing_delete.status_code, 404)

    def test_obsidian_connector_endpoint_scans_vault_and_skips_duplicates(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "obsidian-endpoint-contract"}
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Endpoint Vault"
            vault.mkdir()
            (vault / "Project Atlas.md").write_text(
                "# Project Atlas\n\nI decided the FastAPI Obsidian connector should scan local vault notes.",
                encoding="utf-8",
            )

            response = self.client.post(
                "/v1/connectors/obsidian/sync",
                json={"vault_path": str(vault), "processing": "sync", "max_records": 10},
                headers=headers,
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["source"], "obsidian")
            self.assertEqual(payload["status"], "complete")
            self.assertEqual(payload["saved"], 1)
            self.assertEqual(payload["scan"]["records_found"], 1)
            self.assertEqual(payload["source_account"]["source"], "obsidian")
            encoded_payload = json.dumps(payload)
            self.assertNotIn(str(vault), encoded_payload)
            self.assertNotIn("file:///", encoded_payload)
            self.assertTrue(payload["records"][0]["source_url"].startswith("local-file://Project%20Atlas.md"))
            self.assertTrue(payload["scan"]["vault_path"].startswith("local-file://Endpoint%20Vault"))
            self.assertTrue(payload["source_account"]["metadata"]["vault_path"].startswith("local-file://Endpoint%20Vault"))
            self.assertEqual(payload["records"][0]["title"], "Project Atlas")

            accounts = self.client.get(
                "/v1/source-accounts",
                headers=headers,
            )
            self.assertEqual(accounts.status_code, 200)
            encoded_accounts = json.dumps(accounts.json())
            self.assertNotIn(str(vault), encoded_accounts)
            self.assertNotIn("file:///", encoded_accounts)
            self.assertTrue(accounts.json()["results"][0]["metadata"]["vault_path"].startswith("local-file://Endpoint%20Vault"))

            duplicate = self.client.post(
                "/v1/connectors/obsidian/sync",
                json={"vault_path": str(vault), "processing": "sync", "max_records": 10},
                headers=headers,
            )
            self.assertEqual(duplicate.status_code, 200)
            duplicate_payload = duplicate.json()
            self.assertEqual(duplicate_payload["saved"], 0)
            self.assertEqual(duplicate_payload["skipped"], 1)
            self.assertEqual(duplicate_payload["records"][0]["status"], "duplicate")

    def test_github_connector_endpoint_syncs_issues_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "github-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str]):
            self.assertIn("/repos/doppl-tech/cortex-app/issues", url)
            self.assertEqual(request_headers["Authorization"], "Bearer ghp_test")
            return [
                {
                    "number": 88,
                    "title": "Endpoint sync should cite GitHub",
                    "state": "open",
                    "html_url": "https://github.com/doppl-tech/cortex-app/issues/88",
                    "created_at": "2026-06-30T09:00:00Z",
                    "updated_at": "2026-06-30T10:00:00Z",
                    "user": {"login": "sarp"},
                    "labels": [{"name": "first-100"}],
                    "body": "We decided the FastAPI GitHub connector should preserve GitHub issue URLs.",
                }
            ]

        with patch("backend.app.connectors.github._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/github/sync",
                json={
                    "token": "ghp_test",
                    "repositories": ["doppl-tech/cortex-app"],
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "github")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://github.com/doppl-tech/cortex-app/issues/88")
        self.assertEqual(payload["source_account"]["source"], "github")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("ghp_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI GitHub connector preserve issue URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://github.com/doppl-tech/cortex-app/issues/88"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_slack_connector_endpoint_syncs_messages_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "slack-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str]):
            self.assertIn("/conversations.history", url)
            self.assertIn("channel=C123ABC", url)
            self.assertEqual(request_headers["Authorization"], "Bearer xoxb_test")
            return {
                "ok": True,
                "messages": [
                    {
                        "type": "message",
                        "user": "U123",
                        "text": "We decided the FastAPI Slack connector should preserve Slack message URLs.",
                        "ts": "1782739200.000100",
                    }
                ],
            }

        with patch("backend.app.connectors.slack._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/slack/sync",
                json={
                    "token": "xoxb_test",
                    "channels": ["C123ABC|general"],
                    "workspace_url": "https://doppl.slack.com",
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "slack")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://doppl.slack.com/archives/C123ABC/p1782739200000100")
        self.assertEqual(payload["source_account"]["source"], "slack")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("xoxb_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Slack connector preserve message URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://doppl.slack.com/archives/C123ABC/p1782739200000100"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_readwise_connector_endpoint_syncs_highlights_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "readwise-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str]):
            self.assertIn("/export/", url)
            self.assertEqual(request_headers["Authorization"], "Token readwise_test")
            return {
                "results": [
                    {
                        "user_book_id": 111,
                        "title": "Retrieval Systems",
                        "author": "A. Researcher",
                        "source_url": "https://readwise.io/bookreview/111",
                        "highlights": [
                            {
                                "id": 222,
                                "text": "We decided the FastAPI Readwise connector should preserve highlight URLs.",
                                "updated": "2026-06-30T10:00:00Z",
                            }
                        ],
                    }
                ]
            }

        with patch("backend.app.connectors.readwise._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/readwise/sync",
                json={
                    "token": "readwise_test",
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "readwise")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://readwise.io/bookreview/111")
        self.assertEqual(payload["source_account"]["source"], "readwise")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("readwise_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Readwise connector preserve highlight URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://readwise.io/bookreview/111"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_linear_connector_endpoint_syncs_issues_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "linear-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str], body: dict):
            self.assertEqual(request_headers["Authorization"], "lin_api_test")
            self.assertIn("issues", body["query"])
            return {
                "data": {
                    "issues": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "lin-1",
                                "identifier": "COR-42",
                                "title": "Endpoint sync should cite Linear",
                                "description": "We decided the FastAPI Linear connector should preserve issue URLs.",
                                "url": "https://linear.app/doppl/issue/COR-42/endpoint-sync-should-cite-linear",
                                "createdAt": "2026-06-30T09:00:00Z",
                                "updatedAt": "2026-06-30T10:00:00Z",
                            }
                        ],
                    }
                }
            }

        with patch("backend.app.connectors.linear._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/linear/sync",
                json={
                    "token": "lin_api_test",
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "linear")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://linear.app/doppl/issue/COR-42/endpoint-sync-should-cite-linear")
        self.assertEqual(payload["source_account"]["source"], "linear")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("lin_api_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Linear connector preserve issue URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://linear.app/doppl/issue/COR-42/endpoint-sync-should-cite-linear"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_calendar_connector_endpoint_syncs_events_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "calendar-endpoint-contract"}
        with tempfile.TemporaryDirectory() as tmp:
            private_path = Path(tmp) / "Endpoint Private Calendar.ics"
            private_path.write_text(
                """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:event-fastapi@example.com
DTSTAMP:20260630T100000Z
DTSTART:20260701T160000Z
SUMMARY:Endpoint sync should cite Calendar
DESCRIPTION:We decided the FastAPI Calendar connector should preserve generated source-account citations.
END:VEVENT
END:VCALENDAR
""",
                encoding="utf-8",
            )
            response = self.client.post(
                "/v1/connectors/calendar/sync",
                json={
                    "ics_path": str(private_path),
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )
            private_path_text = str(private_path)

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "calendar")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertTrue(payload["records"][0]["source_url"].startswith("source-account://calendar/"))
        self.assertEqual(payload["source_account"]["source"], "calendar")
        self.assertEqual(payload["source_account"]["connection_type"], "local-file")
        self.assertNotIn(private_path_text, json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Calendar connector generated source-account citations"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("source-account://calendar/"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])
        self.assertNotIn(private_path_text, json.dumps(search.json()))

    def test_raindrop_connector_endpoint_syncs_bookmarks_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "raindrop-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str]):
            self.assertIn("/raindrops/0", url)
            self.assertEqual(request_headers["Authorization"], "Bearer raindrop_test")
            return {
                "result": True,
                "items": [
                    {
                        "_id": 123,
                        "title": "Endpoint sync should cite Raindrop",
                        "link": "https://example.com/raindrop-endpoint",
                        "excerpt": "We decided the FastAPI Raindrop connector should preserve bookmark URLs.",
                        "lastUpdate": "2026-06-30T10:00:00Z",
                    }
                ],
            }

        with patch("backend.app.connectors.raindrop._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/raindrop/sync",
                json={
                    "token": "raindrop_test",
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "raindrop")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://example.com/raindrop-endpoint")
        self.assertEqual(payload["source_account"]["source"], "raindrop")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("raindrop_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Raindrop connector preserve bookmark URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://example.com/raindrop-endpoint"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_zotero_connector_endpoint_syncs_items_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "zotero-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str]):
            self.assertIn("/users/0/items", url)
            self.assertEqual(request_headers["Zotero-API-Version"], "3")
            self.assertNotIn("Zotero-API-Key", request_headers)
            return [
                {
                    "key": "ZTFAST1",
                    "version": 42,
                    "data": {
                        "key": "ZTFAST1",
                        "itemType": "annotation",
                        "parentItem": "ZTPARENT",
                        "annotationText": "We decided the FastAPI Zotero connector should preserve item URLs.",
                        "annotationComment": "Useful for research recall.",
                        "dateModified": "2026-06-30T10:00:00Z",
                    },
                }
            ]

        with patch("backend.app.connectors.zotero._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/zotero/sync",
                json={
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "zotero")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "zotero://select/library/items/ZTFAST1")
        self.assertEqual(payload["source_account"]["source"], "zotero")
        self.assertEqual(payload["source_account"]["connection_type"], "local-api")
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Zotero connector preserve item URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("zotero://select/library/items/ZTFAST1"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_jira_connector_endpoint_syncs_issues_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "jira-endpoint-contract"}
        expected_auth = base64.b64encode(b"sarp@example.com:jira_api_test").decode("ascii")

        def fake_request(url: str, request_headers: dict[str, str], body: dict):
            self.assertEqual(url, "https://doppl.atlassian.net/rest/api/3/search/jql")
            self.assertEqual(request_headers["Authorization"], f"Basic {expected_auth}")
            self.assertEqual(body["jql"], "project = COR ORDER BY updated DESC")
            return {
                "isLast": True,
                "issues": [
                    {
                        "id": "10042",
                        "key": "COR-42",
                        "fields": {
                            "summary": "Endpoint sync should cite Jira",
                            "description": "We decided the FastAPI Jira connector should preserve issue URLs.",
                            "created": "2026-06-30T09:00:00.000+0000",
                            "updated": "2026-06-30T10:00:00.000+0000",
                        },
                    }
                ],
            }

        with patch("backend.app.connectors.jira._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/jira/sync",
                json={
                    "email": "sarp@example.com",
                    "api_token": "jira_api_test",
                    "site_url": "https://doppl.atlassian.net",
                    "jql": "project = COR ORDER BY updated DESC",
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "jira")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://doppl.atlassian.net/browse/COR-42")
        self.assertEqual(payload["source_account"]["source"], "jira")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("jira_api_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Jira connector preserve issue URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://doppl.atlassian.net/browse/COR-42"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_notion_connector_endpoint_syncs_pages_with_citations(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "notion-endpoint-contract"}

        def fake_request(url: str, request_headers: dict[str, str], body: dict | None, method: str):
            self.assertEqual(request_headers["Authorization"], "Bearer notion_test")
            if method == "POST":
                return {
                    "has_more": False,
                    "results": [
                        {
                            "object": "page",
                            "id": "page-1",
                            "created_time": "2026-06-30T09:00:00Z",
                            "last_edited_time": "2026-06-30T10:00:00Z",
                            "url": "https://www.notion.so/doppl/page-1",
                            "properties": {"Name": {"type": "title", "title": [{"plain_text": "Endpoint sync should cite Notion"}]}},
                        }
                    ],
                }
            return {
                "has_more": False,
                "results": [
                    {
                        "type": "paragraph",
                        "paragraph": {"rich_text": [{"plain_text": "We decided the FastAPI Notion connector should preserve page URLs."}]},
                    }
                ],
            }

        with patch("backend.app.connectors.notion._request_json", side_effect=fake_request):
            response = self.client.post(
                "/v1/connectors/notion/sync",
                json={
                    "token": "notion_test",
                    "processing": "sync",
                    "max_records": 25,
                },
                headers=headers,
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["source"], "notion")
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["saved"], 1)
        self.assertEqual(payload["records"][0]["source_url"], "https://www.notion.so/doppl/page-1")
        self.assertEqual(payload["source_account"]["source"], "notion")
        self.assertEqual(payload["source_account"]["connection_type"], "api-token")
        self.assertNotIn("notion_test", json.dumps(payload))
        approved = self.client.post(f"/v1/captures/{payload['capture_ids'][0]}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)
        search = self.client.get(
            "/v1/search",
            params={"query": "FastAPI Notion connector preserve page URLs"},
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])
        self.assertTrue(search.json()["results"][0]["source_url"].startswith("https://www.notion.so/doppl/page-1"))
        self.assertIn("line=", search.json()["results"][0]["source_url"])
        self.assertIn("excerpt=", search.json()["results"][0]["source_url"])

    def test_rebuild_vectors_endpoint_exposes_queue_contract(self) -> None:
        response = self.client.post("/v1/maintenance/rebuild-vectors", headers={"Authorization": "Bearer test-token"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIn("queued", payload)
        self.assertIn("skipped", payload)
        self.assertIn("checked", payload)
        self.assertIn("vector_available", payload)
        self.assertIn("vector_indexed_memories", payload)
        self.assertIn("embedding", payload)

    def test_personal_profile_endpoint_exposes_adaptation_contract(self) -> None:
        created = self.client.post(
            "/v1/captures",
            json={
                "content": "FastAPI profile: I prefer concise technical answers. We decided Project Atlas uses local-first memory.",
                "source": "fastapi-profile-test",
            },
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)
        capture_id = created.json()["capture_id"]
        approved = self.client.post(f"/v1/captures/{capture_id}/approve", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(approved.status_code, 200)

        response = self.client.get("/v1/personal-profile", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["name"], "Cortex Personal Adaptation Profile")
        self.assertIn("coverage", payload)
        self.assertIn("sections", payload)
        self.assertIn("markdown", payload)

        markdown = self.client.get(
            "/v1/personal-profile",
            params={"format": "markdown", "query": "Project Atlas"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(markdown.status_code, 200)
        self.assertIn("# Cortex Personal Adaptation Profile", markdown.text)
        self.assertIn("Project Atlas", markdown.text)

        adaptation = self.client.get(
            "/v1/agent-adaptation",
            params={"target": "Claude", "query": "Project Atlas"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(adaptation.status_code, 200)
        adaptation_payload = adaptation.json()
        self.assertEqual(adaptation_payload["name"], "Cortex Agent Adaptation Layer")
        self.assertEqual(adaptation_payload["target"], "Claude")
        self.assertIn("operating_principles", adaptation_payload)
        self.assertTrue(adaptation_payload["rules"])
        self.assertTrue(adaptation_payload["evidence"])

        adaptation_markdown = self.client.get(
            "/v1/agent-adaptation",
            params={"format": "markdown", "target": "Cursor", "query": "Project Atlas"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(adaptation_markdown.status_code, 200)
        self.assertIn("# Cortex Agent Adaptation Layer", adaptation_markdown.text)
        self.assertIn("Operating Principles", adaptation_markdown.text)

    def test_source_import_endpoint_queues_export_records(self) -> None:
        export_dir = Path(MODULE_TMP.name) / "chatgpt-import-contract"
        export_dir.mkdir(exist_ok=True)
        payload = [
            {
                "title": "Importer contract",
                "mapping": {
                    "a": {
                        "message": {
                            "author": {"role": "user"},
                            "create_time": 1_700_000_001,
                            "content": {"parts": ["Importer contract should remember Project Kestrel."]},
                        }
                    }
                },
            }
        ]
        (export_dir / "conversations.json").write_text(json.dumps(payload), encoding="utf-8")

        analysis = self.client.post(
            "/v1/imports/analyze",
            json={"paths": [str(export_dir)], "max_records": 10},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(analysis.status_code, 200)
        self.assertEqual(analysis.json()["records_found"], 1)

        imported = self.client.post(
            "/v1/imports",
            json={"paths": [str(export_dir)], "processing": "async", "max_records": 10},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(imported.status_code, 200)
        import_payload = imported.json()
        import_id = import_payload["import_id"]
        self.assertEqual(import_payload["queued"], 1)
        self.assertEqual(import_payload["skipped"], 0)

        history = self.client.get("/v1/imports", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(history.status_code, 200)
        self.assertTrue(any(item["import_id"] == import_id for item in history.json()["results"]))
        self.assertTrue(any(item["skipped"] == 0 for item in history.json()["results"]))

        detail = self.client.get(f"/v1/imports/{import_id}", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(detail.status_code, 200)
        self.assertEqual(detail.json()["records_found"], 1)
        self.assertEqual(len(detail.json()["records"]), 1)

        ran = self.client.post("/v1/maintenance/jobs/run?limit=5", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(ran.status_code, 200)
        self.assertGreaterEqual(ran.json()["processed"], 1)

        search = self.client.get(
            "/v1/search",
            params={"query": "Project Kestrel"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])

        deleted = self.client.delete(f"/v1/imports/{import_id}", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(deleted.status_code, 200)
        self.assertTrue(deleted.json()["deleted"])
        self.assertGreaterEqual(deleted.json()["deleted_captures"], 1)

        search_after_delete = self.client.get(
            "/v1/search",
            params={"query": "Project Kestrel"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search_after_delete.status_code, 200)
        self.assertFalse(search_after_delete.json()["results"])

    def test_source_account_catalog_exposes_live_connector_readiness_metadata(self) -> None:
        response = self.client.get("/v1/source-accounts/catalog", headers={"Authorization": "Bearer test-token"})

        self.assertEqual(response.status_code, 200)
        catalog = {item["id"]: item for item in response.json()["results"]}
        expected = {
            "chatgpt": ("export-only", [], "direct connector"),
            "apple-mail": ("import-ready", [], "direct local integration"),
            "gmail": ("live-planned", ["gmail.readonly"], "account sign-in"),
            "notion": ("token-ready", ["read_content"], "read-only token sync"),
            "slack": ("token-ready", ["channels:history", "groups:history", "channels:read", "groups:read"], "read-only token sync"),
            "github": ("token-ready", ["repo:read"], "read-only token sync"),
            "readwise": ("token-ready", ["read"], "read-only token sync"),
            "raindrop": ("token-ready", ["read"], "read-only token sync"),
            "calendar": ("import-ready", [], "native local sync"),
            "linear": ("token-ready", ["read"], "read-only token sync"),
            "jira": ("token-ready", ["read:jira-work"], "read-only token sync"),
            "zotero": ("import-ready", ["read"], "native local sync"),
            "obsidian": ("import-ready", [], "native local sync"),
        }

        for source_id, (readiness_status, scopes, first_100_note) in expected.items():
            self.assertIn(source_id, catalog)
            entry = catalog[source_id]
            self.assertEqual(entry["readiness_status"], readiness_status)
            self.assertEqual(entry["scopes"], scopes)
            self.assertIsInstance(entry["permissions_required"], list)
            self.assertTrue(entry["permissions_required"])
            self.assertIn(first_100_note, entry["first_100_note"])
            self.assertIn(entry["beta_status"], {"ready", "planned", "advanced-fallback", "needs-connector"})
            self.assertIn(entry["primary_beta_path"], {"native-local-connector", "native-token-connector", "account-sign-in-planned", "advanced-fallback-only", "direct-connector-needed"})

        self.assertTrue(any("account sign-in planned" in item for item in catalog["gmail"]["permissions_required"]))
        self.assertTrue(any("account consent for gmail.readonly" in item for item in catalog["gmail"]["permissions_required"]))
        self.assertTrue(any("local app access" in item for item in catalog["obsidian"]["permissions_required"]))
        self.assertFalse(catalog["gmail"]["primary_beta"])
        self.assertEqual(catalog["gmail"]["beta_status"], "planned")
        self.assertFalse(catalog["gmail"]["show_in_primary_ui"])
        self.assertFalse(catalog["notion"]["primary_beta"])
        self.assertEqual(catalog["notion"]["beta_status"], "ready")
        self.assertFalse(catalog["notion"]["show_in_primary_ui"])
        self.assertFalse(catalog["github"]["primary_beta"])
        self.assertEqual(catalog["github"]["beta_status"], "ready")
        self.assertFalse(catalog["github"]["show_in_primary_ui"])
        self.assertFalse(catalog["slack"]["primary_beta"])
        self.assertEqual(catalog["slack"]["beta_status"], "ready")
        self.assertFalse(catalog["slack"]["show_in_primary_ui"])
        self.assertFalse(catalog["readwise"]["primary_beta"])
        self.assertEqual(catalog["readwise"]["beta_status"], "ready")
        self.assertFalse(catalog["readwise"]["show_in_primary_ui"])
        self.assertFalse(catalog["linear"]["primary_beta"])
        self.assertEqual(catalog["linear"]["beta_status"], "ready")
        self.assertFalse(catalog["linear"]["show_in_primary_ui"])
        self.assertFalse(catalog["jira"]["primary_beta"])
        self.assertEqual(catalog["jira"]["beta_status"], "ready")
        self.assertFalse(catalog["jira"]["show_in_primary_ui"])
        self.assertFalse(catalog["raindrop"]["primary_beta"])
        self.assertEqual(catalog["raindrop"]["beta_status"], "ready")
        self.assertFalse(catalog["raindrop"]["show_in_primary_ui"])
        self.assertFalse(catalog["calendar"]["primary_beta"])
        self.assertEqual(catalog["calendar"]["beta_status"], "ready")
        self.assertFalse(catalog["calendar"]["show_in_primary_ui"])
        self.assertFalse(catalog["zotero"]["primary_beta"])
        self.assertEqual(catalog["zotero"]["beta_status"], "ready")
        self.assertFalse(catalog["zotero"]["show_in_primary_ui"])
        self.assertTrue(catalog["obsidian"]["primary_beta"])
        self.assertEqual(catalog["obsidian"]["beta_status"], "ready")
        self.assertTrue(catalog["obsidian"]["show_in_primary_ui"])
        display_text = "\n".join(
            str(value)
            for entry in catalog.values()
            for value in [
                entry.get("name"),
                entry.get("notes"),
                entry.get("first_100_note"),
                entry.get("import_label"),
                *(entry.get("permissions_required") or []),
            ]
            if value
        ).lower()
        for manual_intake_term in (
            "takeout",
            "selected export",
            "manual import",
            "file upload",
            "files or folders",
            "selected files",
            "choose file",
            "choose folder",
            "upload",
            "user-selected",
        ):
            self.assertNotIn(manual_intake_term, display_text)

    def test_scoped_mcp_token_can_use_mcp_but_not_rest(self) -> None:
        scoped_token = "cxm_fastapi_contract_token_123456789"
        registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": scoped_token, "label": "Unit test MCP", "scopes": ["read"]},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json()["scopes"], ["read"])

        rest = self.client.get("/v1/search", params={"query": "anything"}, headers={"Authorization": f"Bearer {scoped_token}"})
        self.assertEqual(rest.status_code, 401)

        mcp = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(mcp.status_code, 200)
        tool_names = {tool["name"] for tool in mcp.json()["result"]["tools"]}
        self.assertIn("search_memory", tool_names)
        self.assertIn("list_source_connectors", tool_names)
        self.assertNotIn("connect_source_account", tool_names)
        self.assertNotIn("sync_source_records", tool_names)
        self.assertNotIn("approve_memory_capture", tool_names)
        self.assertNotIn("get_daily_review", tool_names)
        self.assertNotIn("get_memory_inbox", tool_names)
        self.assertNotIn("delete_all_user_data", tool_names)

        for tool_name in ("get_daily_review", "get_memory_inbox"):
            blocked = self.client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": f"blocked-{tool_name}",
                    "method": "tools/call",
                    "params": {"name": tool_name, "arguments": {}},
                },
                headers={"Authorization": f"Bearer {scoped_token}"},
            )
            self.assertEqual(blocked.status_code, 200)
            blocked_payload = blocked.json()
            self.assertNotIn("result", blocked_payload)
            self.assertEqual(blocked_payload["error"]["code"], -32000)
            self.assertIn("not scoped", blocked_payload["error"]["message"])

    def test_mcp_token_registration_uses_user_scoped_token_ids(self) -> None:
        alice = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": "cxm_fastapi_alice_token_123456789", "label": "Desktop MCP", "scopes": ["read"]},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "mcp-token-alice"},
        )
        bob = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": "cxm_fastapi_bob_token_123456789", "label": "Desktop MCP", "scopes": ["read"]},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "mcp-token-bob"},
        )

        self.assertEqual(alice.status_code, 200)
        self.assertEqual(bob.status_code, 200)
        self.assertEqual(alice.json()["user_id"], "mcp-token-alice")
        self.assertEqual(bob.json()["user_id"], "mcp-token-bob")
        self.assertNotEqual(alice.json()["token_id"], bob.json()["token_id"])

    def test_mcp_tool_calls_return_structured_content_for_retrieval_and_catalog(self) -> None:
        user = "mcp-structured-content-contract"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}
        self._allow_pending_context(user)
        phrase = "Structured MCP retrieval should preserve Cortex citation payloads for Project Signal."
        created = self.client.post(
            "/v1/captures",
            json={
                "content": phrase,
                "source": "github",
                "source_url": "cortex-source://github#service=github&file=issues.json&line=31&excerpt=project-signal",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)

        search = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "structured-search",
                "method": "tools/call",
                "params": {"name": "search_memory", "arguments": {"query": "Project Signal citation payloads", "top_k": 3}},
            },
            headers=headers,
        )
        self.assertEqual(search.status_code, 200)
        search_result = search.json()["result"]
        self.assertIn("content", search_result)
        self.assertIn("structuredContent", search_result)
        self.assertEqual(json.loads(search_result["content"][0]["text"]), search_result["structuredContent"])
        self.assertEqual(search_result["structuredContent"]["results"][0]["source"], "github")
        self.assertIn("line=31", search_result["structuredContent"]["results"][0]["source_url"])
        self.assertIn("retrieval", search_result["structuredContent"])
        self.assertIn("used_modes", search_result["structuredContent"]["retrieval"])
        self.assertIn("embedding_provider", search_result["structuredContent"]["retrieval"])

        catalog = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "structured-catalog",
                "method": "tools/call",
                "params": {"name": "list_source_connectors", "arguments": {}},
            },
            headers=headers,
        )
        self.assertEqual(catalog.status_code, 200)
        catalog_result = catalog.json()["result"]
        self.assertIn("structuredContent", catalog_result)
        github = next(item for item in catalog_result["structuredContent"]["results"] if item["id"] == "github")
        self.assertTrue(github["service_baseline"]["records_supported"])
        self.assertFalse(github["service_baseline"]["primary_ui"])

    def test_read_only_mcp_search_suppresses_edited_obsidian_note_pending_review(self) -> None:
        user = "mcp-obsidian-review-contract"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}
        read_token = "cxm_fastapi_obsidian_review_read_123456789"
        settings = self.client.put(
            "/v1/settings",
            json={"allow_pending_in_context": False},
            headers=headers,
        )
        self.assertEqual(settings.status_code, 200)
        registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": read_token, "label": "Obsidian Review Read MCP", "scopes": ["read"]},
            headers=headers,
        )
        self.assertEqual(registered.status_code, 200)

        def mcp_search(query: str) -> list[dict[str, object]]:
            response = self.client.post(
                "/mcp",
                json={
                    "jsonrpc": "2.0",
                    "id": f"search-{query}",
                    "method": "tools/call",
                    "params": {"name": "search_memory", "arguments": {"query": query, "top_k": 5}},
                },
                headers={"Authorization": f"Bearer {read_token}", "X-Cortex-User": user},
            )
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertNotIn("error", payload)
            search_payload = json.loads(payload["result"]["content"][0]["text"])
            self.assertIn("retrieval", search_payload)
            return search_payload["results"]

        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp) / "Review Vault"
            vault.mkdir()
            note = vault / "Project Taipei.md"
            note.write_text(
                "# Project Taipei\n\nDecision: MCP Obsidian approved gate should expose initial review marker.",
                encoding="utf-8",
            )

            first = self.client.post(
                "/v1/connectors/obsidian/sync",
                json={"vault_path": str(vault), "processing": "sync", "max_records": 10},
                headers=headers,
            )
            self.assertEqual(first.status_code, 200)
            first_payload = first.json()
            self.assertEqual(first_payload["saved"], 1)
            capture_id = first_payload["records"][0]["capture_id"]
            approved = self.client.post(f"/v1/captures/{capture_id}/approve", headers=headers)
            self.assertEqual(approved.status_code, 200)
            self.assertTrue(mcp_search("initial review marker"))

            note.write_text(
                "# Project Taipei\n\nDecision: MCP Obsidian edited gate should stay hidden while pending review.",
                encoding="utf-8",
            )
            changed = self.client.post(
                "/v1/connectors/obsidian/sync",
                json={"vault_path": str(vault), "processing": "sync", "max_records": 10},
                headers=headers,
            )
            self.assertEqual(changed.status_code, 200)
            changed_payload = changed.json()
            self.assertEqual(changed_payload["saved"], 1)
            self.assertEqual(changed_payload["records"][0]["status"], "updated")
            self.assertEqual(changed_payload["records"][0]["capture_id"], capture_id)

            inbox = self.client.get("/v1/inbox", headers=headers)
            self.assertEqual(inbox.status_code, 200)
            self.assertIn(capture_id, [item["id"] for item in inbox.json()["results"]])
            self.assertEqual(mcp_search("edited gate hidden pending review"), [])

            approved_again = self.client.post(f"/v1/captures/{capture_id}/approve", headers=headers)
            self.assertEqual(approved_again.status_code, 200)
            self.assertTrue(mcp_search("edited gate hidden pending review"))

    def test_scoped_mcp_token_blocks_unscoped_tool_even_when_setting_enabled(self) -> None:
        scoped_token = "cxm_fastapi_read_only_token_123456789"
        created = self.client.post(
            "/v1/captures",
            json={"content": "Scoped token delete attempt phrase.", "source": "fastapi-test"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)
        memory_id = created.json()["memories"][0]["id"]
        self.client.put(
            "/v1/settings",
            json={"allow_agent_writes": True, "allow_agent_destructive_actions": True},
            headers={"Authorization": "Bearer test-token"},
        )
        registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": scoped_token, "label": "Read-only MCP", "scopes": ["read"]},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(registered.status_code, 200)

        mcp = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "forget_memory", "arguments": {"id": memory_id}},
            },
            headers={"Authorization": f"Bearer {scoped_token}"},
        )

        self.assertEqual(mcp.status_code, 200)
        self.assertIn("not scoped", mcp.json()["error"]["message"])
        still_present = self.client.get(
            "/v1/search",
            params={"query": "Scoped token delete attempt"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertTrue(still_present.json()["results"])

    def test_mcp_token_registration_defaults_to_read_only_scope(self) -> None:
        user = "mcp-default-read-only-contract"
        scoped_token = "cxm_fastapi_default_read_token_123456789"
        self.client.put(
            "/v1/settings",
            json={"allow_agent_writes": True},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": user},
        )
        registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": scoped_token, "label": "Default read MCP"},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": user},
        )
        self.assertEqual(registered.status_code, 200)
        self.assertEqual(registered.json()["scopes"], ["read"])

        blocked = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "default-read-blocked",
                "method": "tools/call",
                "params": {
                    "name": "connect_source_account",
                    "arguments": {"source": "slack", "account_label": "Default Read Slack"},
                },
            },
            headers={"Authorization": f"Bearer {scoped_token}", "X-Cortex-User": user},
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("not scoped", blocked.json()["error"]["message"])

    def test_scoped_mcp_token_can_register_and_sync_connected_source_records(self) -> None:
        user = "mcp-connected-source-contract"
        self._allow_pending_context(user)
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user}
        read_token = "cxm_fastapi_source_read_123456789"
        write_token = "cxm_fastapi_source_write_123456789"

        read_registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": read_token, "label": "Read MCP", "scopes": ["read"]},
            headers=headers,
        )
        self.assertEqual(read_registered.status_code, 200)

        tools = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "tools", "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {read_token}", "X-Cortex-User": user},
        )
        self.assertEqual(tools.status_code, 200)
        tool_names = {tool["name"] for tool in tools.json()["result"]["tools"]}
        self.assertIn("list_source_connectors", tool_names)
        self.assertNotIn("connect_source_account", tool_names)
        self.assertNotIn("sync_source_records", tool_names)
        self.assertNotIn("sync_connected_sources", tool_names)
        self.assertNotIn("approve_memory_capture", tool_names)

        blocked = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "blocked-source-sync",
                "method": "tools/call",
                "params": {
                    "name": "connect_source_account",
                    "arguments": {"source": "slack", "account_label": "Read-only Slack"},
                },
            },
            headers={"Authorization": f"Bearer {read_token}", "X-Cortex-User": user},
        )
        self.assertEqual(blocked.status_code, 200)
        self.assertIn("not scoped", blocked.json()["error"]["message"])

        write_registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": write_token, "label": "Write MCP", "scopes": ["read", "write"]},
            headers=headers,
        )
        self.assertEqual(write_registered.status_code, 200)

        write_tools = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "write-tools", "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {write_token}", "X-Cortex-User": user},
        )
        self.assertEqual(write_tools.status_code, 200)
        write_tool_names = {tool["name"] for tool in write_tools.json()["result"]["tools"]}
        self.assertIn("list_source_connectors", write_tool_names)
        self.assertIn("connect_source_account", write_tool_names)
        self.assertIn("sync_source_records", write_tool_names)
        self.assertNotIn("sync_connected_sources", write_tool_names)
        self.assertIn("approve_memory_capture", write_tool_names)
        self.assertNotIn("delete_all_user_data", write_tool_names)

        maintenance_token = "cxm_fastapi_source_maintenance_123456789"
        enabled_maintenance = self.client.put(
            "/v1/settings",
            json={"allow_agent_maintenance": True},
            headers=headers,
        )
        self.assertEqual(enabled_maintenance.status_code, 200)
        maintenance_registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": maintenance_token, "label": "Maintenance MCP", "scopes": ["maintenance"]},
            headers=headers,
        )
        self.assertEqual(maintenance_registered.status_code, 200)
        maintenance_tools = self.client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": "maintenance-tools", "method": "tools/list", "params": {}},
            headers={"Authorization": f"Bearer {maintenance_token}", "X-Cortex-User": user},
        )
        self.assertEqual(maintenance_tools.status_code, 200)
        maintenance_tool_names = {tool["name"] for tool in maintenance_tools.json()["result"]["tools"]}
        self.assertIn("sync_connected_sources", maintenance_tool_names)
        self.assertNotIn("sync_source_records", maintenance_tool_names)
        synced_due = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "sync-connected-sources",
                "method": "tools/call",
                "params": {"name": "sync_connected_sources", "arguments": {"limit": 5}},
            },
            headers={"Authorization": f"Bearer {maintenance_token}", "X-Cortex-User": user},
        )
        self.assertEqual(synced_due.status_code, 200)
        synced_due_payload = json.loads(synced_due.json()["result"]["content"][0]["text"])
        self.assertEqual(synced_due_payload["scheduled_source_syncs"]["scheduled"], 0)
        self.assertEqual(synced_due_payload["processed"], 0)

        connected = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "connect-source",
                "method": "tools/call",
                "params": {
                    "name": "connect_source_account",
                    "arguments": {
                        "source": "slack",
                        "account_label": "Demo Slack",
                        "account_identifier": "workspace-demo",
                        "connection_type": "mcp",
                    },
                },
            },
            headers={"Authorization": f"Bearer {write_token}", "X-Cortex-User": user},
        )
        self.assertEqual(connected.status_code, 200)
        connected_payload = json.loads(connected.json()["result"]["content"][0]["text"])
        account = connected_payload["account"]
        self.assertEqual(account["source"], "slack")

        synced = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "sync-source",
                "method": "tools/call",
                "params": {
                    "name": "sync_source_records",
                    "arguments": {
                        "source_account_id": account["id"],
                        "records": [
                            {
                                "content": "I decided Slack should feed Cortex with cited product decisions for Project Orion.",
                                "title": "Project Orion decision",
                                "external_id": "thread-123",
                                "captured_at": "2026-06-30T10:15:00Z",
                            }
                        ],
                        "cursor_name": "threads",
                        "cursor_value": "cursor-2",
                        "processing": "sync",
                    },
                },
            },
            headers={"Authorization": f"Bearer {write_token}", "X-Cortex-User": user},
        )
        self.assertEqual(synced.status_code, 200)
        synced_payload = json.loads(synced.json()["result"]["content"][0]["text"])
        self.assertEqual(synced_payload["saved"], 1)
        self.assertTrue(synced_payload["records"][0]["source_url"].startswith(f"source-account://slack/{account['id']}/thread-123"))
        capture_id = synced_payload["capture_ids"][0]

        found = self.client.get(
            "/v1/search",
            params={"query": "Project Orion cited product decisions"},
            headers=headers,
        )
        self.assertEqual(found.status_code, 200)
        self.assertEqual(found.json()["results"], [])

        approved = self.client.post(f"/v1/captures/{capture_id}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)

        found = self.client.get(
            "/v1/search",
            params={"query": "Project Orion cited product decisions"},
            headers=headers,
        )
        self.assertEqual(found.status_code, 200)
        results = found.json()["results"]
        self.assertTrue(results)
        self.assertEqual(results[0]["source"], "slack")
        self.assertTrue(results[0]["source_url"].startswith(f"source-account://slack/{account['id']}/thread-123"))

        unsafe_archive = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "sync-source-unsafe-archive",
                "method": "tools/call",
                "params": {
                    "name": "sync_source_records",
                    "arguments": {
                        "source_account_id": account["id"],
                        "records": [
                            {
                                "content": "Partial Slack page should not archive Project Orion memory.",
                                "title": "Project Orion partial",
                                "external_id": "thread-partial",
                                "captured_at": "2026-06-30T10:25:00Z",
                            }
                        ],
                        "cursor_name": "threads",
                        "cursor_value": "cursor-unsafe",
                        "processing": "sync",
                        "archive_missing": True,
                    },
                },
            },
            headers={"Authorization": f"Bearer {write_token}", "X-Cortex-User": user},
        )
        self.assertEqual(unsafe_archive.status_code, 200)
        self.assertIn("complete_snapshot", unsafe_archive.json()["error"]["message"])
        still_found = self.client.get(
            "/v1/search",
            params={"query": "Project Orion cited product decisions"},
            headers=headers,
        )
        self.assertTrue(still_found.json()["results"])

        full_snapshot = self.client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": "sync-source-full-snapshot",
                "method": "tools/call",
                "params": {
                    "name": "sync_source_records",
                    "arguments": {
                        "source_account_id": account["id"],
                        "records": [
                            {
                                "content": "I decided Slack now keeps only the latest Project Orion source snapshot.",
                                "title": "Project Orion latest",
                                "external_id": "thread-456",
                                "captured_at": "2026-06-30T10:30:00Z",
                            }
                        ],
                        "cursor_name": "threads",
                        "cursor_value": "cursor-3",
                        "processing": "sync",
                        "archive_missing": True,
                        "complete_snapshot": True,
                    },
                },
            },
            headers={"Authorization": f"Bearer {write_token}", "X-Cortex-User": user},
        )
        self.assertEqual(full_snapshot.status_code, 200)
        full_snapshot_payload = json.loads(full_snapshot.json()["result"]["content"][0]["text"])
        self.assertEqual(full_snapshot_payload["archived_missing"], 1)
        found_after_archive = self.client.get(
            "/v1/search",
            params={"query": "Project Orion cited product decisions"},
            headers=headers,
        )
        self.assertEqual(found_after_archive.json()["results"], [])

    def test_source_policies_round_trip_and_filter_search(self) -> None:
        self._allow_pending_context("source-policy-contract")
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "source-policy-contract"}
        created = self.client.post(
            "/v1/captures",
            json={
                "content": "FastAPI Source Policy Echo should disappear when source policy excludes it.",
                "source": "gmail",
                "source_url": "gmail://message/echo",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)

        before = self.client.get("/v1/search", params={"query": "Source Policy Echo"}, headers=headers)
        self.assertEqual(before.status_code, 200)
        self.assertTrue(before.json()["results"])

        updated = self.client.put(
            "/v1/settings",
            json={"source_policies": {"gmail": {"mode": "excluded"}}},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["source_policies"]["gmail"]["mode"], "excluded")

        after = self.client.get("/v1/search", params={"query": "Source Policy Echo"}, headers=headers)
        self.assertEqual(after.status_code, 200)
        self.assertEqual(after.json()["results"], [])

        cleared = self.client.put(
            "/v1/settings",
            json={"source_policies": {"gmail": {"mode": "default"}}},
            headers=headers,
        )
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.json()["source_policies"], {})

    def test_identity_aliases_settings_round_trip(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "identity-contract"}

        updated = self.client.put(
            "/v1/settings",
            json={"identity_aliases": ["sarpt", "sarpt@example.com", "sarpt"]},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["identity_aliases"], ["sarpt", "sarpt@example.com"])

        fetched = self.client.get("/v1/settings", headers=headers)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(fetched.json()["identity_aliases"], ["sarpt", "sarpt@example.com"])

    def test_sync_capture_uses_identity_aliases_for_personal_memory_gating(self) -> None:
        self._allow_pending_context("identity-capture-contract")
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "identity-capture-contract"}
        updated = self.client.put(
            "/v1/settings",
            json={"identity_aliases": ["sarpt"]},
            headers=headers,
        )
        self.assertEqual(updated.status_code, 200)

        created = self.client.post(
            "/v1/captures",
            json={
                "source": "slack",
                "content": (
                    "Source: Slack\n"
                    "Channel: general\n\n"
                    "--- Messages ---\n"
                    "2026-06-29T12:40:00+00:00 sarpt: I prefer API Alias Capture answers with direct citations.\n"
                    "2026-06-29T12:41:00+00:00 dana: I prefer API Alias Capture answers with long public launch rituals.\n"
                ),
            },
            headers=headers,
        )

        self.assertEqual(created.status_code, 200)
        memories = created.json()["memories"]
        memory_text = "\n".join(memory["content"] for memory in memories)
        self.assertIn("direct citations", memory_text)
        self.assertNotIn("long public launch rituals", memory_text)
        self.assertTrue(any(memory["kind"] == "preference" for memory in memories))

        self.assertTrue(
            self.client.get(
                "/v1/search",
                params={"query": "API Alias Capture direct citations"},
                headers=headers,
            ).json()["results"]
        )
        self.assertEqual(
            self.client.get(
                "/v1/search",
                params={"query": "API Alias Capture long public launch rituals"},
                headers=headers,
            ).json()["results"],
            [],
        )

    def test_sync_changes_contract_is_cursorable_and_redacted(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "sync-contract"}
        phrase = "FastAPI Sync Feed Raw Phrase"
        created = self.client.post(
            "/v1/captures",
            json={
                "content": f"We decided {phrase} must not appear in the sync change feed.",
                "source": "fastapi-sync",
            },
            headers=headers,
        )
        self.assertEqual(created.status_code, 200)
        capture_id = created.json()["capture_id"]
        approved = self.client.post(f"/v1/captures/{capture_id}/approve", headers=headers)
        self.assertEqual(approved.status_code, 200)

        feed_response = self.client.get("/v1/sync/changes", params={"limit": 1}, headers=headers)
        self.assertEqual(feed_response.status_code, 200)
        feed = feed_response.json()
        self.assertEqual(feed["sync_contract"], 1)
        self.assertFalse(feed["content_included"])
        self.assertTrue(feed["changes"])
        self.assertTrue(feed["has_more"])
        self.assertIn("shard", feed)
        self.assertNotIn(phrase, json.dumps(feed))

        next_response = self.client.get(
            "/v1/sync/changes",
            params={"after": feed["next_cursor"], "limit": 50},
            headers=headers,
        )
        self.assertEqual(next_response.status_code, 200)
        next_feed = next_response.json()
        self.assertNotEqual(next_feed["next_cursor"], feed["next_cursor"])
        self.assertNotIn(phrase, json.dumps(next_feed))

        invalid_response = self.client.get(
            "/v1/sync/changes",
            params={"after": "evt_missing"},
            headers=headers,
        )
        self.assertEqual(invalid_response.status_code, 200)
        self.assertEqual(invalid_response.json()["warnings"], ["cursor_not_found"])
        self.assertEqual(invalid_response.json()["changes"], [])

    def test_sync_device_registry_contract_and_signed_feed(self) -> None:
        original_settings = main_module.settings
        main_module.settings = replace(original_settings, sync_signing_key="contract-signing-key")
        try:
            headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "sync-device-contract"}
            registered = self.client.post(
                "/v1/sync/devices",
                json={
                    "device_name": "Contract Mac",
                    "platform": "macOS",
                    "capabilities": ["manifest", "upload"],
                },
                headers=headers,
            )
            self.assertEqual(registered.status_code, 200)
            device = registered.json()
            self.assertTrue(device["id"].startswith("sdev_"))
            self.assertEqual(device["platform"], "macos")
            self.assertIn("device_key", device)
            self.assertEqual(len(device["fingerprint"]), 16)

            listed = self.client.get("/v1/sync/devices", headers=headers)
            self.assertEqual(listed.status_code, 200)
            listed_device = listed.json()["results"][0]
            self.assertEqual(listed_device["id"], device["id"])
            self.assertIsNone(listed_device.get("device_key"))

            feed_response = self.client.get(
                "/v1/sync/changes",
                params={"device_id": device["id"], "limit": 10},
                headers=headers,
            )
            self.assertEqual(feed_response.status_code, 200)
            feed = feed_response.json()
            self.assertEqual(feed["device"]["id"], device["id"])
            self.assertEqual(feed["counts"]["sync_devices"], 1)
            self.assertTrue(feed["signature"]["configured"])
            self.assertEqual(feed["signature"]["device_id"], device["id"])
            self.assertTrue(feed["signature"]["payload_hash"].startswith("sha256:"))
            self.assertTrue(feed["signature"]["value"].startswith("hmac-sha256:"))
            self.assertNotIn("device_key", json.dumps(feed))

            receipt_response = self.client.post(
                f"/v1/sync/devices/{device['id']}/receipts",
                json={
                    "cursor": feed["next_cursor"],
                    "status": "uploaded",
                    "manifest_hash": feed["signature"]["payload_hash"],
                    "remote_ref": "local-sync://contract/upload-1",
                    "stats": {"changes": len(feed["changes"])},
                },
                headers=headers,
            )
            self.assertEqual(receipt_response.status_code, 200)
            receipt = receipt_response.json()
            self.assertTrue(receipt["id"].startswith("srec_"))
            self.assertEqual(receipt["device_id"], device["id"])
            self.assertEqual(receipt["cursor"], feed["next_cursor"])
            self.assertEqual(receipt["status"], "uploaded")
            self.assertEqual(receipt["stats"]["changes"], len(feed["changes"]))

            listed_receipts = self.client.get(f"/v1/sync/devices/{device['id']}/receipts", headers=headers)
            self.assertEqual(listed_receipts.status_code, 200)
            self.assertEqual([item["id"] for item in listed_receipts.json()["results"]], [receipt["id"]])

            revoked = self.client.delete(f"/v1/sync/devices/{device['id']}", headers=headers)
            self.assertEqual(revoked.status_code, 200)
            self.assertEqual(revoked.json()["id"], device["id"])
            self.assertIsNotNone(revoked.json()["revoked_at"])

            active = self.client.get("/v1/sync/devices", headers=headers)
            self.assertEqual(active.status_code, 200)
            self.assertEqual(active.json()["results"], [])

            all_devices = self.client.get("/v1/sync/devices", params={"include_revoked": "true"}, headers=headers)
            self.assertEqual(all_devices.status_code, 200)
            self.assertEqual(all_devices.json()["results"][0]["id"], device["id"])

            revoked_feed = self.client.get(
                "/v1/sync/changes",
                params={"device_id": device["id"]},
                headers=headers,
            )
            self.assertEqual(revoked_feed.status_code, 200)
            self.assertIn("device_revoked", revoked_feed.json()["warnings"])
            self.assertFalse(revoked_feed.json()["signature"]["configured"])
        finally:
            main_module.settings = original_settings

    def test_delete_user_data_removes_current_user_records(self) -> None:
        phrase = "FastAPI delete all user data contract phrase"
        created = self.client.post(
            "/v1/captures",
            json={"content": phrase, "source": "fastapi-test"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)

        deleted = self.client.delete(
            "/v1/user-data",
            params={"include_backups": "false"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(deleted.json()["include_backups"])

        search = self.client.get(
            "/v1/search",
            params={"query": phrase},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()["results"], [])

    def test_restore_latest_backup_restores_search_contract(self) -> None:
        phrase = "FastAPI restore latest backup contract phrase"
        created = self.client.post(
            "/v1/captures",
            json={"content": phrase, "source": "fastapi-test"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(created.status_code, 200)

        backup = self.client.post("/v1/backups", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(backup.status_code, 200)

        deleted = self.client.delete(
            "/v1/user-data",
            params={"include_backups": "false"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(deleted.status_code, 200)

        restored = self.client.post("/v1/backups/restore-latest", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(restored.status_code, 200)
        self.assertGreaterEqual(restored.json()["rebuild"]["memories"], 1)

        search = self.client.get(
            "/v1/search",
            params={"query": phrase},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertTrue(search.json()["results"])

    def test_queue_capture_processes_through_job_endpoint(self) -> None:
        phrase = "FastAPI queued capture async job contract phrase"
        queued = self.client.post(
            "/v1/captures/queue",
            json={"content": phrase, "source": "fastapi-async-test"},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(queued.status_code, 202)
        capture_id = queued.json()["capture_id"]
        job_id = queued.json()["jobs"][0]["id"]

        search_before = self.client.get(
            "/v1/search",
            params={"query": phrase},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search_before.status_code, 200)
        self.assertEqual(search_before.json()["results"], [])

        job = self.client.get(f"/v1/jobs/{job_id}", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(job.status_code, 200)
        self.assertEqual(job.json()["status"], "queued")

        ran = self.client.post("/v1/maintenance/jobs/run", params={"limit": 1}, headers={"Authorization": "Bearer test-token"})
        self.assertEqual(ran.status_code, 200)
        self.assertEqual(ran.json()["processed"], 1)

        status = self.client.get(f"/v1/captures/{capture_id}/status", headers={"Authorization": "Bearer test-token"})
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["processing"]["extraction_status"], "succeeded")
        self.assertGreaterEqual(status.json()["processing"]["memory_count"], 1)

        search_after = self.client.get(
            "/v1/search",
            params={"query": phrase},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(search_after.status_code, 200)
        self.assertTrue(search_after.json()["results"])

    def test_job_endpoint_schedules_due_obsidian_source_sync(self) -> None:
        user_id = "fastapi-source-sync-user"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user_id}
        with tempfile.TemporaryDirectory() as tmp:
            vault_path = Path(tmp) / "vault"
            note_path = vault_path / "Endpoint Sync.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(
                "# Endpoint Sync\n\n"
                "Decision: FastAPI source sync jobs should queue Obsidian records.\n",
                encoding="utf-8",
            )
            account = main_module.store.upsert_source_account(
                user_id,
                source="obsidian",
                account_label="Endpoint Vault",
                account_identifier="endpoint-vault",
                connection_type="local_folder",
                status="connected",
                auth_state="healthy",
                metadata={
                    "vault_path": str(vault_path),
                    "sync_interval_seconds": 60,
                    "next_sync_due_at": "2000-01-01T00:00:00Z",
                },
            )

            ran = self.client.post("/v1/maintenance/jobs/run", params={"limit": 1}, headers=headers)

        self.assertEqual(ran.status_code, 200)
        payload = ran.json()
        self.assertEqual(payload["scheduled_source_syncs"]["scheduled"], 1)
        self.assertEqual(payload["scheduled_source_syncs"]["jobs"][0]["object_id"], account["id"])
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(payload["jobs"][0]["job_type"], "source_account_sync")
        self.assertEqual(payload["jobs"][0]["status"], "succeeded")
        self.assertEqual(payload["jobs"][0]["result"]["queued"], 1)

    def test_source_sync_due_endpoint_runs_only_source_account_jobs(self) -> None:
        user_id = "fastapi-source-sync-only-user"
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": user_id}
        queued_capture = main_module.store.enqueue_capture(
            user_id=user_id,
            content="This unrelated queued capture should wait for the general worker.",
            source="fastapi-test",
            source_url=None,
            title="Queued capture",
        )
        with tempfile.TemporaryDirectory() as tmp:
            vault_path = Path(tmp) / "vault"
            note_path = vault_path / "Endpoint Source Only.md"
            note_path.parent.mkdir(parents=True)
            note_path.write_text(
                "# Endpoint Source Only\n\n"
                "Decision: source-only sync should not drain unrelated capture jobs.\n",
                encoding="utf-8",
            )
            account = main_module.store.upsert_source_account(
                user_id,
                source="obsidian",
                account_label="Source Only Vault",
                account_identifier="source-only-vault",
                connection_type="local_folder",
                status="connected",
                auth_state="healthy",
                metadata={
                    "vault_path": str(vault_path),
                    "sync_interval_seconds": 60,
                    "next_sync_due_at": "2000-01-01T00:00:00Z",
                },
            )

            ran = self.client.post("/v1/sources/sync-due", params={"limit": 1}, headers=headers)

        self.assertEqual(ran.status_code, 200)
        payload = ran.json()
        self.assertEqual(payload["scheduled_source_syncs"]["scheduled"], 1)
        self.assertEqual(payload["scheduled_source_syncs"]["jobs"][0]["object_id"], account["id"])
        self.assertEqual(payload["processed"], 1)
        self.assertEqual(payload["jobs"][0]["job_type"], "source_account_sync")
        self.assertEqual(payload["jobs"][0]["status"], "succeeded")
        queued_extract_jobs = main_module.store.list_jobs(user_id, status="queued", job_type="extract_capture", limit=10)
        self.assertIn(queued_capture["capture_id"], [job["object_id"] for job in queued_extract_jobs])

    def test_job_health_endpoint_reports_queue_state(self) -> None:
        headers = {"Authorization": "Bearer test-token", "X-Cortex-User": "queue-health-contract"}
        phrase = "FastAPI queue health endpoint should report pending work."
        queued = self.client.post(
            "/v1/captures/queue",
            json={"content": phrase, "source": "fastapi-async-test"},
            headers=headers,
        )
        self.assertEqual(queued.status_code, 202)

        health = self.client.get("/v1/jobs/health", headers=headers)

        self.assertEqual(health.status_code, 200)
        payload = health.json()
        self.assertEqual(payload["status"], "attention")
        self.assertEqual(payload["counts"]["queued"], 1)
        self.assertEqual(payload["due_queued"], 1)
        self.assertEqual(payload["recent_failures"], [])
        self.assertEqual(payload["stale_running"], [])

    def test_scoped_mcp_token_cannot_call_queue_rest_endpoint(self) -> None:
        scoped_token = "cxm_fastapi_queue_blocked_token_123456789"
        registered = self.client.post(
            "/v1/integrations/mcp-token",
            json={"token": scoped_token, "label": "Queue blocked MCP", "scopes": ["read"]},
            headers={"Authorization": "Bearer test-token"},
        )
        self.assertEqual(registered.status_code, 200)

        queued = self.client.post(
            "/v1/captures/queue",
            json={"content": "Scoped MCP token should not call REST queue.", "source": "fastapi-async-test"},
            headers={"Authorization": f"Bearer {scoped_token}"},
        )
        self.assertEqual(queued.status_code, 401)


if __name__ == "__main__":
    unittest.main()
