from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import request
from urllib.parse import quote

from backend.app.config import Settings

MODULE_TMP = tempfile.TemporaryDirectory()
os.environ["CORTEX_DB_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.sqlite")
os.environ["CORTEX_VAULT_PATH"] = str(Path(MODULE_TMP.name) / "bootstrap.vault")
os.environ["CORTEX_API_KEY"] = "test-token"

from backend.app import standalone_server
from backend.app.database import connect, init_db
from backend.app.mcp_tools import READ_TOOLS, TOOLS, call_tool
from backend.app.storage import CortexStore


def tearDownModule() -> None:
    MODULE_TMP.cleanup()


ALEX_ENTITY_ID = "person_alex-rivera"
LINKED_MEMORY_ID = "mem_linked_q3"
LINKED_MEMORY_SOURCE_URL = "https://mail.example.com/thread/778"
DECISION_MEMORY_ID = "mem_decision_scope"
OPEN_TASK_ID = "task_send_summary"
CLOSED_TASK_ID = "task_already_done"


def seed_alex_fixture(db_path: Path, user_id: str) -> None:
    created_at = "2026-06-20T10:00:00Z"
    with connect(db_path) as conn:
        conn.execute(
            "INSERT INTO entities (id, user_id, kind, name, aliases_json, context, first_seen, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                ALEX_ENTITY_ID,
                user_id,
                "person",
                "Alex Rivera",
                json.dumps(["Alex", "Lexi"]),
                "Design partner",
                "2026-06-01T00:00:00Z",
                "2026-06-21T00:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO memories
            (id, capture_id, user_id, kind, layer, content, summary, source, source_url, status, topics_json, entity_ids_json, occurred_at, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                LINKED_MEMORY_ID,
                None,
                user_id,
                "event",
                "episodic",
                "Q3 pricing review moved the renewal deadline to Friday.",
                "",
                "gmail",
                LINKED_MEMORY_SOURCE_URL,
                "active",
                json.dumps(["pricing"]),
                json.dumps([ALEX_ENTITY_ID]),
                "2026-06-19T09:00:00Z",
                "2026-06-19T09:05:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO memories
            (id, capture_id, user_id, kind, layer, content, summary, source, source_url, status, topics_json, entity_ids_json, occurred_at, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                DECISION_MEMORY_ID,
                None,
                user_id,
                "decision",
                "semantic",
                "Decided to keep the rollout scoped to design partners.",
                "",
                "notes",
                "https://notes.example.com/rollout",
                "active",
                json.dumps(["rollout", "pricing"]),
                json.dumps([ALEX_ENTITY_ID]),
                None,
                "2026-06-18T12:00:00Z",
            ),
        )
        for memory_id in (LINKED_MEMORY_ID, DECISION_MEMORY_ID):
            conn.execute(
                "INSERT OR REPLACE INTO memory_entities(memory_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (memory_id, ALEX_ENTITY_ID, user_id, created_at),
            )
        conn.execute(
            """
            INSERT INTO tasks
            (id, capture_id, user_id, kind, content, status, importance, topics_json, entity_ids_json, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                OPEN_TASK_ID,
                None,
                user_id,
                "action",
                "Send the pricing summary deck before Thursday.",
                "open",
                4,
                json.dumps([]),
                json.dumps([ALEX_ENTITY_ID]),
                "2026-06-20T08:00:00Z",
            ),
        )
        conn.execute(
            """
            INSERT INTO tasks
            (id, capture_id, user_id, kind, content, status, importance, topics_json, entity_ids_json, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                CLOSED_TASK_ID,
                None,
                user_id,
                "action",
                "Share the onboarding checklist.",
                "done",
                3,
                json.dumps([]),
                json.dumps([ALEX_ENTITY_ID]),
                "2026-06-15T08:00:00Z",
            ),
        )
        for task_id in (OPEN_TASK_ID, CLOSED_TASK_ID):
            conn.execute(
                "INSERT OR REPLACE INTO task_entities(task_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (task_id, ALEX_ENTITY_ID, user_id, created_at),
            )


class RelationshipContextStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "test-user"
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})
        seed_alex_fixture(self.db_path, self.user_id)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _memory_ids(self, payload: dict) -> set[str]:
        return {item["id"] for item in [*payload["recent_context"], *payload["decisions"]]}

    def test_entity_linked_memory_appears_without_text_match(self) -> None:
        payload = self.store.person_context(self.user_id, "Alex Rivera")

        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["person"]["id"], ALEX_ENTITY_ID)
        self.assertEqual(payload["person"]["name"], "Alex Rivera")
        self.assertIn("Lexi", payload["person"]["aliases"])
        self.assertIn(LINKED_MEMORY_ID, self._memory_ids(payload))
        self.assertNotIn("alex", json.dumps(payload["recent_context"]).lower().replace(ALEX_ENTITY_ID, ""))
        self.assertEqual(payload["last_interaction"], "2026-06-19T09:00:00Z")

        with connect(self.db_path) as conn:
            audit_count = conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE user_id = ? AND object_type = 'entity' AND event_type = 'person_context'",
                (self.user_id,),
            ).fetchone()[0]
        self.assertGreaterEqual(audit_count, 1)

    def test_alias_resolution(self) -> None:
        payload = self.store.person_context(self.user_id, "Lexi")

        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["person"]["id"], ALEX_ENTITY_ID)
        self.assertEqual(payload["person"]["name"], "Alex Rivera")
        self.assertIn(LINKED_MEMORY_ID, self._memory_ids(payload))

    def test_open_commitment_from_task_entities(self) -> None:
        payload = self.store.person_context(self.user_id, "Alex Rivera")

        commitment_ids = [item["id"] for item in payload["open_commitments"]]
        self.assertIn(OPEN_TASK_ID, commitment_ids)
        self.assertNotIn(CLOSED_TASK_ID, commitment_ids)
        commitment = next(item for item in payload["open_commitments"] if item["id"] == OPEN_TASK_ID)
        self.assertEqual(commitment["status"], "open")
        self.assertEqual(commitment["citation"]["id"], OPEN_TASK_ID)
        self.assertEqual(commitment["citation"]["index"], 1)

    def test_decision_memory_is_bucketed_separately(self) -> None:
        payload = self.store.person_context(self.user_id, "Alex Rivera")

        decision_ids = [item["id"] for item in payload["decisions"]]
        self.assertIn(DECISION_MEMORY_ID, decision_ids)
        self.assertNotIn(DECISION_MEMORY_ID, [item["id"] for item in payload["recent_context"]])
        self.assertIn({"topic": "pricing", "count": 2}, payload["top_topics"])

    def test_citation_fields_present(self) -> None:
        payload = self.store.person_context(self.user_id, "Alex Rivera")

        linked = next(item for item in payload["recent_context"] if item["id"] == LINKED_MEMORY_ID)
        citation = linked["citation"]
        self.assertEqual(citation["source_url"], LINKED_MEMORY_SOURCE_URL)
        self.assertEqual(citation["id"], LINKED_MEMORY_ID)
        self.assertEqual(citation["kind"], "event")
        self.assertIsInstance(citation["index"], int)
        self.assertTrue(citation["excerpt"])
        self.assertEqual(citation["captured_at"], "2026-06-19T09:05:00Z")
        self.assertEqual(citation["occurred_at"], "2026-06-19T09:00:00Z")

    def test_unresolved_person_payload(self) -> None:
        payload = self.store.person_context(self.user_id, "Zork Nobody")

        self.assertFalse(payload["resolved"])
        self.assertIsNone(payload["person"]["id"])
        self.assertEqual(payload["person"]["name"], "Zork Nobody")
        self.assertEqual(payload["person"]["aliases"], [])
        self.assertIsNone(payload["last_interaction"])
        self.assertEqual(payload["open_commitments"], [])
        self.assertEqual(payload["decisions"], [])
        self.assertEqual(payload["recent_context"], [])
        self.assertEqual(payload["top_topics"], [])

    def test_mcp_tool_get_relationship_context(self) -> None:
        self.assertIn("get_relationship_context", READ_TOOLS)
        tool = next(tool for tool in TOOLS if tool["name"] == "get_relationship_context")
        self.assertEqual(tool["inputSchema"]["required"], ["name"])

        payload = call_tool(self.store, self.user_id, "get_relationship_context", {"name": "Alex Rivera", "limit": 5})

        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["person"]["id"], ALEX_ENTITY_ID)
        self.assertIn(LINKED_MEMORY_ID, self._memory_ids(payload))

        with connect(self.db_path) as conn:
            reuse_count = conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE user_id = ? AND event_type = 'context_reused'",
                (self.user_id,),
            ).fetchone()[0]
        self.assertGreaterEqual(reuse_count, 1)

    def test_answer_query_person_hook_cites_linked_memory(self) -> None:
        result = self.store.answer_query(self.user_id, "Brief me before I talk to Alex", limit=5)

        citation_ids = [citation["id"] for citation in result["citations"]]
        self.assertIn(LINKED_MEMORY_ID, citation_ids)
        self.assertIn(LINKED_MEMORY_ID, [item["id"] for item in result["results"]])
        self.assertLessEqual(len(result["results"]), 5)
        citation = next(citation for citation in result["citations"] if citation["id"] == LINKED_MEMORY_ID)
        self.assertEqual(citation["source_url"], LINKED_MEMORY_SOURCE_URL)
        self.assertTrue(result["answer"].startswith("Cortex found"))

    def test_answer_query_without_person_stays_deterministic(self) -> None:
        result = self.store.answer_query(self.user_id, "what changed in the release checklist", limit=5)

        self.assertEqual(result["query"], "what changed in the release checklist")
        self.assertIn("citations", result)
        self.assertIn("results", result)

    def test_person_hook_requires_briefing_intent(self) -> None:
        # A capitalized token matching a person entity must NOT reorder generic
        # queries; the implicit hook only fires on person-briefing intent
        # ("talk to", "meeting with", "brief me", ...).
        self.assertIsNone(self.store._query_person_entity(self.user_id, "Alex launch checklist status"))
        self.assertIsNotNone(self.store._query_person_entity(self.user_id, "Brief me before I talk to Alex"))
        self.assertIsNotNone(self.store._query_person_entity(self.user_id, "prepare for my meeting with Alex"))

        generic = self.store.answer_query(self.user_id, "Alex launch checklist status", limit=5)
        self.assertEqual(generic["query"], "Alex launch checklist status")


class RelationshipContextEndpointTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "index.sqlite"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path)
        self.user_id = "local"
        self.store.update_settings(self.user_id, {"allow_pending_in_context": True})
        seed_alex_fixture(self.db_path, self.user_id)
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        standalone_server.store = self.store
        standalone_server.settings = Settings(
            vault_path=Path(self.tmp.name) / "vault",
            db_path=self.db_path,
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
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
        self.tmp.cleanup()

    def get(self, path: str):
        headers = {"Authorization": "Bearer test-token"}
        return request.urlopen(request.Request(self.base_url + path, headers=headers), timeout=5)

    def test_person_context_endpoint_returns_cited_briefing(self) -> None:
        with self.get(f"/v1/people/{quote('Alex Rivera')}/context?limit=5") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertTrue(payload["resolved"])
        self.assertEqual(payload["person"]["id"], ALEX_ENTITY_ID)
        memory_ids = {item["id"] for item in [*payload["recent_context"], *payload["decisions"]]}
        self.assertIn(LINKED_MEMORY_ID, memory_ids)
        linked = next(item for item in payload["recent_context"] if item["id"] == LINKED_MEMORY_ID)
        self.assertEqual(linked["citation"]["source_url"], LINKED_MEMORY_SOURCE_URL)
        self.assertIn(OPEN_TASK_ID, [item["id"] for item in payload["open_commitments"]])

    def test_person_context_endpoint_unresolved_person(self) -> None:
        with self.get("/v1/people/Nobody/context") as response:
            payload = json.loads(response.read().decode("utf-8"))

        self.assertEqual(response.status, 200)
        self.assertFalse(payload["resolved"])
        self.assertIsNone(payload["person"]["id"])
        self.assertEqual(payload["recent_context"], [])


if __name__ == "__main__":
    unittest.main()
