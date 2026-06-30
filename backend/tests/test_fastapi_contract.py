from __future__ import annotations

import os
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

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
        self.assertTrue(payload["deletion"]["restore_preserves_tombstones"])
        self.assertIn("trust_score", payload["ai_access"])
        self.assertIn("events", payload["audit"])

    def test_health_exposes_sharding_contract(self) -> None:
        response = self.client.get("/health")

        self.assertEqual(response.status_code, 200)
        sharding = response.json()["sharding"]
        self.assertEqual(sharding["mode"], "local")
        self.assertEqual(sharding["default"]["shard_id"], "local")
        self.assertIn("db_path", sharding["default"])

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
        self.assertEqual(payload["citations"][0]["source_url"], "/tmp/ask-source.md")
        self.assertIn("Ask citation contract", payload["citations"][0]["excerpt"])

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
        self.assertEqual(payload["citations"][0]["source_url"], "notion://page/import-undo")
        self.assertEqual(payload["results"][0]["result_type"], "task")

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

        sync_cursor_blocked = self.client.post(
            "/v1/sync-cursors",
            json={"source": "gmail", "cursor_name": "messages"},
            headers={"Authorization": f"Bearer {tokens['write']}", "X-Cortex-User": user},
        )
        self.assertEqual(sync_cursor_blocked.status_code, 403)
        self.assertIn("maintenance scope", sync_cursor_blocked.json()["detail"])

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

        missing_account = self.client.post(
            "/v1/sync-cursors",
            json={"source": "gmail", "source_account_id": "sacct_missing", "cursor_name": "messages"},
            headers=headers,
        )
        self.assertEqual(missing_account.status_code, 422)

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

        disconnected = self.client.delete(f"/v1/source-accounts/{account['id']}", headers=headers)
        self.assertEqual(disconnected.status_code, 200)
        self.assertEqual(disconnected.json()["status"], "disconnected")

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

        missing_delete = self.client.delete("/v1/source-accounts/sacct_missing", headers=headers)
        self.assertEqual(missing_delete.status_code, 404)

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
        self.assertIn("tools", mcp.json()["result"])

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

    def test_source_policies_round_trip_and_filter_search(self) -> None:
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
