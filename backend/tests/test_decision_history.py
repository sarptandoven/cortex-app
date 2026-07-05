from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.mcp_tools import READ_TOOLS, TOOLS, call_tool
from backend.app.storage import CortexStore


CURRENT_DECISION_ID = "decision_history_current_pricing"
SUPERSEDED_DECISION_ID = "decision_history_old_pricing"
OTHER_SECTOR_DECISION_ID = "decision_history_other_sector"
CURRENT_SOURCE_URL = "https://docs.example.com/pricing-current#line=7"
OLD_SOURCE_URL = "https://docs.example.com/pricing-old#line=3"


def seed_decision_history_fixture(store: CortexStore, user_id: str) -> None:
    store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
    store.save_capture(
        user_id=user_id,
        content="Project Nova pricing decision history fixture.",
        source="docs",
        source_url=CURRENT_SOURCE_URL,
        title="Project Nova current pricing",
        extracted={
            "_timestamp": "2026-06-30T10:00:00Z",
            "summary": "Project Nova current pricing decision.",
            "records": [
                {
                    "id": CURRENT_DECISION_ID,
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Decision: Project Nova pricing stays founder-led until the first design partner renewals finish.",
                    "summary": "Project Nova pricing stays founder-led until design partner renewals finish.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "sector": "Project Nova",
                    "occurred_at": "2026-06-30T10:00:00Z",
                    "topics": ["pricing", "renewals"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    store.save_capture(
        user_id=user_id,
        content="Project Nova old pricing decision history fixture.",
        source="docs",
        source_url=OLD_SOURCE_URL,
        title="Project Nova old pricing",
        extracted={
            "_timestamp": "2026-05-01T10:00:00Z",
            "summary": "Project Nova old pricing decision.",
            "records": [
                {
                    "id": SUPERSEDED_DECISION_ID,
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Decision: Project Nova pricing used a self-serve checkout plan before renewal calls.",
                    "summary": "Project Nova pricing used self-serve checkout before renewal calls.",
                    "confidence": "confirmed",
                    "importance": 4,
                    "sector": "Project Nova",
                    "occurred_at": "2026-05-01T10:00:00Z",
                    "superseded_by": CURRENT_DECISION_ID,
                    "topics": ["pricing", "checkout"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )
    store.save_capture(
        user_id=user_id,
        content="Project Boreal pricing decision history fixture.",
        source="docs",
        source_url="https://docs.example.com/boreal-pricing#line=2",
        title="Project Boreal pricing",
        extracted={
            "_timestamp": "2026-06-29T10:00:00Z",
            "summary": "Project Boreal pricing decision.",
            "records": [
                {
                    "id": OTHER_SECTOR_DECISION_ID,
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Decision: Project Boreal pricing keeps annual enterprise contracts.",
                    "summary": "Project Boreal pricing keeps annual contracts.",
                    "confidence": "confirmed",
                    "importance": 5,
                    "sector": "Project Boreal",
                    "occurred_at": "2026-06-29T10:00:00Z",
                    "topics": ["pricing"],
                    "entity_ids": [],
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )


class DecisionHistoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-test.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=Path(self.tmp.name) / "vault")
        self.user_id = "decision-history-user"
        seed_decision_history_fixture(self.store, self.user_id)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_decision_history_returns_cited_current_and_superseded_decisions(self) -> None:
        payload = self.store.decision_history(self.user_id, "pricing", sector="Project Nova", include_superseded=True)

        self.assertEqual(payload["sector"], "Project Nova")
        current_ids = [item["id"] for item in payload["current_decisions"]]
        superseded_ids = [item["id"] for item in payload["superseded_decisions"]]
        timeline_ids = [item["id"] for item in payload["timeline"]]
        self.assertIn(CURRENT_DECISION_ID, current_ids)
        self.assertIn(SUPERSEDED_DECISION_ID, superseded_ids)
        self.assertNotIn(OTHER_SECTOR_DECISION_ID, timeline_ids)
        self.assertLess(timeline_ids.index(CURRENT_DECISION_ID), timeline_ids.index(SUPERSEDED_DECISION_ID))
        current = next(item for item in payload["current_decisions"] if item["id"] == CURRENT_DECISION_ID)
        old = next(item for item in payload["superseded_decisions"] if item["id"] == SUPERSEDED_DECISION_ID)
        self.assertEqual(current["citation"]["source_url"], CURRENT_SOURCE_URL)
        self.assertEqual(old["superseded_by"], CURRENT_DECISION_ID)
        self.assertEqual(old["citation"]["source_url"], OLD_SOURCE_URL)
        self.assertIn({"topic": "pricing", "count": 2}, payload["top_topics"])
        self.assertEqual(payload["counts"]["current"], 1)
        self.assertEqual(payload["counts"]["superseded"], 1)

        with connect(self.db_path) as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE user_id = ? AND event_type = 'decision_history'",
                (self.user_id,),
            ).fetchone()[0]
        self.assertGreaterEqual(event_count, 1)

    def test_decision_history_can_hide_superseded_decisions(self) -> None:
        payload = self.store.decision_history(self.user_id, "pricing", sector="Project Nova", include_superseded=False)

        self.assertIn(CURRENT_DECISION_ID, [item["id"] for item in payload["current_decisions"]])
        self.assertEqual(payload["superseded_decisions"], [])
        self.assertNotIn(SUPERSEDED_DECISION_ID, [item["id"] for item in payload["timeline"]])

    def test_mcp_get_decision_history_returns_structured_ledger(self) -> None:
        self.assertIn("get_decision_history", READ_TOOLS)
        tool = next(tool for tool in TOOLS if tool["name"] == "get_decision_history")
        self.assertIn("include_superseded", tool["inputSchema"]["properties"])

        payload = call_tool(
            self.store,
            self.user_id,
            "get_decision_history",
            {"query": "pricing", "sector": "Project Nova", "limit": 5},
        )

        self.assertEqual(payload["counts"]["current"], 1)
        self.assertEqual(payload["counts"]["superseded"], 1)
        self.assertIn(CURRENT_DECISION_ID, [item["id"] for item in payload["current_decisions"]])


if __name__ == "__main__":
    unittest.main()
