from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import request

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ.setdefault("CORTEX_DB_PATH", str(Path(MODULE_TMP.name) / "headline.sqlite"))
os.environ.setdefault("CORTEX_VAULT_PATH", str(Path(MODULE_TMP.name) / "headline.vault"))
os.environ.setdefault("CORTEX_API_KEY", "test-token")

from backend.app import mcp_tools
from backend.app.config import Settings
from backend.app.database import init_db
from backend.app.storage import CortexStore, connect


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


class CrossAIHeadlineStoreTests(unittest.TestCase):
    """North-star metric (weekly cross-AI recall): served-memory-ID logging on the tool-call
    audit path (record_agent_event) and the cross_ai_recall_headline read-model.

    Honesty invariants under test:
      - distinct_ais counts ONLY per-app token labels; the legacy shared token, the default
        local token, and untokened traffic land in unattributed_calls, never in distinct_ais.
      - events log memory IDs only — never memory content.
      - top_memories drops served IDs that no longer resolve to a memory (never guesses)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db_path = self.root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "headline-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _token(self, token_id: str, label: str) -> dict:
        return {"token_id": token_id, "label": label, "audience": "mcp", "scopes": ["read", "write"]}

    def _read_event(self, token: dict | None = None, result: dict | None = None, tool: str = "search_memory") -> None:
        self.store.record_agent_event(self.user_id, tool, {"query": "x"}, success=True, token=token, result=result)

    def _seed_memory(self, memory_id: str, content: str) -> str:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="note",
            source_url=None,
            title=None,
            extracted={
                "summary": content[:80],
                "records": [
                    {"id": memory_id, "kind": "decision", "layer": "decision", "content": content,
                     "confidence": "confirmed", "importance": 4, "occurred_at": "2026-01-01T00:00:00Z",
                     "topics": ["project"], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )
        # save_capture may re-key records; resolve the ACTUAL stored id by content match.
        for memory in self.store.recent(self.user_id, limit=50):
            if memory["content"] == content:
                return str(memory["id"])
        raise AssertionError(f"seeded memory not found: {content!r}")

    def _event_metadata(self, object_id: str) -> list[dict]:
        with connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = ? ORDER BY rowid",
                (self.user_id, object_id),
            ).fetchall()
        return [self.store._json_or_empty(row["metadata_json"]) for row in rows]

    # -- headline honesty: distinct AIs vs unattributed traffic ------------------------------

    def test_distinct_ais_counts_per_app_tokens_only(self) -> None:
        # Three real per-app tokens...
        self._read_event(self._token("tok_claude", "Claude Desktop"))
        self._read_event(self._token("tok_claude", "Claude Desktop"))
        self._read_event(self._token("tok_cursor", "Cursor"))
        self._read_event(self._token("tok_windsurf", "Windsurf"))
        # ...plus shared/default/untokened noise that must NOT inflate N.
        self._read_event(self._token("tok_shared", "Connected AI tools"))
        self._read_event(self._token("tok_local_mcp", "Local MCP integrations"))
        self._read_event(None)
        # Writes and failed reads are not recalls.
        self.store.record_agent_event(self.user_id, "remember_this", {"content": "x"}, success=True,
                                      token=self._token("tok_claude", "Claude Desktop"))
        self.store.record_agent_event(self.user_id, "search_memory", {"query": "x"}, success=False,
                                      error="boom", token=self._token("tok_cursor", "Cursor"))

        headline = self.store.cross_ai_recall_headline(self.user_id)
        self.assertEqual(headline["distinct_ais"], 3)
        self.assertEqual(headline["total_recalls"], 7)
        self.assertEqual(headline["unattributed_calls"], 3)
        self.assertEqual(
            [client["label"] for client in headline["clients"]],
            ["Claude Desktop", "Cursor", "Windsurf"],
        )
        self.assertEqual(headline["clients"][0]["read_calls"], 2)
        for client in headline["clients"]:
            self.assertTrue(client["last_used_at"])
        unattributed_labels = {source["label"] for source in headline["unattributed_sources"]}
        self.assertEqual(unattributed_labels, {"Connected AI tools", "Local MCP integrations", "(untokened)"})
        self.assertEqual(headline["window_days"], 7)
        self.assertTrue(headline["computed_at"])
        self.assertTrue(headline["caveats"])

    def test_empty_state_returns_zeros_not_errors(self) -> None:
        headline = self.store.cross_ai_recall_headline("nobody-yet")
        self.assertEqual(headline["distinct_ais"], 0)
        self.assertEqual(headline["total_recalls"], 0)
        self.assertEqual(headline["unattributed_calls"], 0)
        self.assertEqual(headline["clients"], [])
        self.assertEqual(headline["top_memories"], [])

    def test_days_window_is_respected(self) -> None:
        self._read_event(self._token("tok_cursor", "Cursor"))
        with connect(self.db_path) as conn:
            conn.execute(
                "UPDATE memory_events SET created_at = datetime('now', '-30 days') WHERE user_id = ?",
                (self.user_id,),
            )
        inside_week = self.store.cross_ai_recall_headline(self.user_id, days=7)
        self.assertEqual(inside_week["distinct_ais"], 0)
        self.assertEqual(inside_week["total_recalls"], 0)
        wide_window = self.store.cross_ai_recall_headline(self.user_id, days=90)
        self.assertEqual(wide_window["distinct_ais"], 1)
        self.assertEqual(wide_window["total_recalls"], 1)
        self.assertEqual(wide_window["window_days"], 90)

    # -- served-memory-ID logging on the audit path ------------------------------------------

    def test_call_tool_read_logs_served_memory_ids_not_content(self) -> None:
        content = "The database is postgres for the launch."
        memory_id = self._seed_memory("mem_db", content)
        value = mcp_tools.call_tool(self.store, self.user_id, "search_memory", {"query": ""})
        self.store.record_agent_event(
            self.user_id, "search_memory", {"query": ""}, success=True,
            token=self._token("tok_claude", "Claude Desktop"), result=value,
        )
        events = self._event_metadata("mcp:search_memory")
        self.assertEqual(len(events), 1)
        served = events[0]["served_memory_ids"]
        self.assertIn(memory_id, served)
        # IDs ONLY — the memory content must never reach the event log.
        self.assertNotIn("postgres", json.dumps(events[0]))

    def test_served_ids_absent_for_writes_and_empty_reads(self) -> None:
        # A write tool returning a payload with ids is NOT a recall...
        self.store.record_agent_event(
            self.user_id, "remember_this", {"content": "x"}, success=True,
            token=self._token("tok_claude", "Claude Desktop"),
            result={"results": [{"result_type": "memory", "id": "mem_write"}]},
        )
        self.assertNotIn("served_memory_ids", self._event_metadata("mcp:remember_this")[0])
        # ...and a read that served nothing logs no served ids (backward compatible).
        self._read_event(self._token("tok_claude", "Claude Desktop"), result={"results": []})
        self.assertNotIn("served_memory_ids", self._event_metadata("mcp:search_memory")[0])

    def test_served_ids_are_bounded_and_deduped(self) -> None:
        oversized = {"results": [{"result_type": "memory", "id": f"mem_{index % 60}"} for index in range(120)]}
        self._read_event(self._token("tok_claude", "Claude Desktop"), result=oversized)
        served = self._event_metadata("mcp:search_memory")[0]["served_memory_ids"]
        self.assertEqual(len(served), self.store.SERVED_MEMORY_IDS_CAP)
        self.assertEqual(len(served), len(set(served)))

    def test_served_ids_harvest_citations_and_cited_memory_ids(self) -> None:
        # assemble_context-style citations ({"memory_id": ...}) and expand_context-style
        # cited_memory_ids lists both count as served.
        self._read_event(
            self._token("tok_claude", "Claude Desktop"),
            result={"citations": [{"memory_id": "mem_ctx"}]},
            tool="get_context",
        )
        self._read_event(
            self._token("tok_claude", "Claude Desktop"),
            result={"entities": [{"cited_memory_ids": ["mem_exp"]}]},
            tool="expand_context",
        )
        self.assertEqual(self._event_metadata("mcp:get_context")[0]["served_memory_ids"], ["mem_ctx"])
        self.assertEqual(self._event_metadata("mcp:expand_context")[0]["served_memory_ids"], ["mem_exp"])

    # -- top memories -------------------------------------------------------------------------

    def test_top_memories_ranks_by_times_served_and_drops_ghosts(self) -> None:
        id_a = self._seed_memory("mem_a", "The database is postgres for the launch.")
        id_b = self._seed_memory("mem_b", "The launch channel is discord.")
        token = self._token("tok_claude", "Claude Desktop")

        def _serve(memory_id: str, times: int) -> None:
            for _ in range(times):
                self._read_event(token, result={"results": [{"result_type": "memory", "id": memory_id}]})

        _serve(id_a, 3)
        _serve(id_b, 1)
        # A ghost id served often must be DROPPED (no memory row -> no title), never invented.
        _serve("mem_ghost_gone", 2)

        top = self.store.cross_ai_recall_headline(self.user_id)["top_memories"]
        self.assertEqual([item["memory_id"] for item in top], [id_a, id_b])
        self.assertEqual(top[0]["times_served"], 3)
        self.assertEqual(top[1]["times_served"], 1)
        for item in top:
            self.assertTrue(item["title_or_summary"])
            self.assertLessEqual(len(item["title_or_summary"]), 80)

    # -- additive: existing scorecard shape untouched ------------------------------------------

    def test_scorecard_shape_unchanged_by_served_id_logging(self) -> None:
        self._seed_memory("mem_db", "The database is postgres for the launch.")
        value = mcp_tools.call_tool(self.store, self.user_id, "search_memory", {"query": ""})
        self.store.record_agent_event(
            self.user_id, "search_memory", {"query": ""}, success=True,
            token=self._token("tok_claude", "Claude Desktop"), result=value,
        )
        scorecard = self.store.get_tool_scorecard(self.user_id)
        host = scorecard["hosts"][0]
        self.assertEqual(host["token_id"], "tok_claude")
        self.assertEqual(host["read_calls"], 1)
        self.assertIn("memory_usage_rate", host)
        self.assertIn("caveats", scorecard)


class CrossAIHeadlineStandaloneEndpointTests(unittest.TestCase):
    """GET /v1/usage/headline on the shipping standalone server, backed by a REAL store."""

    def setUp(self) -> None:
        from backend.app import standalone_server

        self.standalone_server = standalone_server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        init_db(root / "index.sqlite")
        self.real_store = CortexStore(root / "index.sqlite", root / "vault")
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        self.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.store = self.real_store
        standalone_server.settings = Settings(
            vault_path=root / "vault",
            db_path=root / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
        self.server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.standalone_server.store = self.original_store
        self.standalone_server.settings = self.original_settings
        self.standalone_server.REQUEST_GUARDS = self.original_guards
        self.tmp.cleanup()

    def test_usage_headline_route(self) -> None:
        user_id = self.standalone_server.settings.default_user_id
        token = {"token_id": "tok_cursor", "label": "Cursor", "audience": "mcp", "scopes": ["read"]}
        self.real_store.record_agent_event(user_id, "search_memory", {"query": "x"}, success=True, token=token)
        self.real_store.record_agent_event(user_id, "search_memory", {"query": "x"}, success=True, token=None)
        req = request.Request(
            self.base_url + "/v1/usage/headline?days=7",
            headers={"Authorization": "Bearer test-token"},
        )
        with request.urlopen(req, timeout=5) as response:
            self.assertEqual(response.status, 200)
            payload = json.loads(response.read())
        self.assertEqual(payload["distinct_ais"], 1)
        self.assertEqual(payload["total_recalls"], 2)
        self.assertEqual(payload["unattributed_calls"], 1)
        self.assertEqual(payload["clients"][0]["label"], "Cursor")
        self.assertEqual(payload["window_days"], 7)


class CrossAIHeadlineFastAPIEndpointTests(unittest.TestCase):
    """GET /v1/usage/headline on the FastAPI server: same shape, neighboring-route auth."""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        from backend.app import main as main_module

        cls.main_module = main_module
        cls.client = TestClient(main_module.app)

    def test_usage_headline_route(self) -> None:
        user_id = "headline-fastapi-user"
        token = {"token_id": "tok_windsurf", "label": "Windsurf", "audience": "mcp", "scopes": ["read"]}
        self.main_module.store.record_agent_event(user_id, "ask_memory", {"query": "x"}, success=True, token=token)
        response = self.client.get(
            "/v1/usage/headline",
            params={"days": 7},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": user_id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["distinct_ais"], 1)
        self.assertEqual(payload["total_recalls"], 1)
        self.assertEqual(payload["clients"][0]["label"], "Windsurf")
        self.assertEqual(payload["unattributed_calls"], 0)
        self.assertEqual(payload["top_memories"], [])

    def test_usage_headline_requires_auth(self) -> None:
        response = self.client.get("/v1/usage/headline")
        self.assertIn(response.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
