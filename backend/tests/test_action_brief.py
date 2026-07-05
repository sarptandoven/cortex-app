from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.mcp_tools import READ_TOOLS, TOOLS, call_tool
from backend.app.storage import CortexStore


class ActionBriefTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "cortex-action-brief.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, vault_path=Path(self.tmp.name) / "vault")
        self.user_id = "action-brief-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        self.store.save_capture(
            user_id=self.user_id,
            content="Project Atlas launch action brief fixture.",
            source="obsidian",
            source_url="local-file://Launch.md#line=4&excerpt=atlas-action-brief",
            title="Project Atlas launch",
            extracted={
                "_timestamp": "2026-07-01T10:00:00Z",
                "summary": "Project Atlas launch fixture.",
                "records": [
                    {
                        "id": "action_brief_context",
                        "kind": "fact",
                        "layer": "semantic",
                        "content": "Project Atlas launch depends on cited beta onboarding notes.",
                        "importance": 4,
                        "sector": "Project Atlas",
                        "topics": ["launch", "onboarding"],
                    },
                    {
                        "id": "action_brief_procedure",
                        "kind": "procedure",
                        "layer": "procedural",
                        "content": "Procedure: Before inviting beta users, verify Ask citations and source sync health.",
                        "importance": 5,
                        "sector": "Project Atlas",
                        "topics": ["launch", "qa"],
                    },
                    {
                        "id": "action_brief_decision",
                        "kind": "decision",
                        "layer": "decision",
                        "content": "Decision: Project Atlas invites should stay founder-reviewed until support load is known.",
                        "importance": 5,
                        "sector": "Project Atlas",
                        "topics": ["launch", "support"],
                    },
                    {
                        "id": "action_brief_preference",
                        "kind": "preference",
                        "layer": "preference",
                        "content": "I prefer launch notes that are concise and cite the source of each claim.",
                        "importance": 4,
                        "sector": "Project Atlas",
                        "topics": ["communication"],
                    },
                    {
                        "id": "action_brief_negative",
                        "kind": "negative",
                        "layer": "negative",
                        "content": "I disliked launch plans that hide known risks from beta users.",
                        "importance": 4,
                        "sector": "Project Atlas",
                        "topics": ["communication"],
                    },
                    {
                        "id": "action_brief_style",
                        "kind": "style",
                        "layer": "style",
                        "content": "Style: direct, concrete, and sparse when writing launch updates.",
                        "importance": 3,
                        "sector": "Project Atlas",
                        "topics": ["communication"],
                    },
                ],
                "tasks": [
                    {
                        "id": "action_brief_open_task",
                        "kind": "action",
                        "content": "Confirm the Project Atlas invite includes Ask citation proof before sending the beta batch.",
                        "status": "open",
                        "importance": 5,
                        "topics": ["launch", "qa"],
                    }
                ],
                "entities": [],
            },
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_action_brief_returns_cited_operational_context(self) -> None:
        brief = self.store.action_brief(
            self.user_id,
            "prepare Project Atlas launch invite with citations",
            sector="Project Atlas",
        )

        self.assertEqual(brief["sector"], "Project Atlas")
        self.assertEqual(brief["status"], "strong")
        # Distilled front: a short, cited essence of the brief (not the full dump).
        self.assertTrue(brief["key_points"])
        self.assertLessEqual(len(brief["key_points"]), 5)
        for point in brief["key_points"]:
            self.assertTrue(point["point"] and point["memory_id"])  # every distilled point is cited
        self.assertIn("## Key points", brief["markdown"])
        self.assertTrue(brief["primary_context"])
        self.assertTrue(brief["procedures"])
        self.assertTrue(brief["current_decisions"])
        self.assertTrue(brief["preferences"])
        self.assertTrue(brief["negative_constraints"])
        self.assertTrue(brief["style_signals"])
        self.assertTrue(brief["open_actions"])
        self.assertTrue(brief["action_plan"])
        self.assertTrue(brief["execution_checklist"])
        self.assertEqual(brief["action_plan"][0]["section"], "negative_constraints")
        self.assertTrue(any(item["section"] == "open_actions" for item in brief["action_plan"]))
        self.assertTrue(any("Ask citation proof" in item["action"] for item in brief["action_plan"]))
        checklist_phases = [item["phase"] for item in brief["execution_checklist"]]
        self.assertIn("guardrails", checklist_phases)
        self.assertIn("decision_boundary", checklist_phases)
        self.assertIn("procedure", checklist_phases)
        self.assertIn("open_action", checklist_phases)
        self.assertIn("verification", checklist_phases)
        self.assertTrue(any("verify Ask citations" in item["step"] for item in brief["execution_checklist"]))
        self.assertTrue(any("Ask citation proof" in item["step"] for item in brief["execution_checklist"]))
        self.assertTrue(all(item["citation"] for item in brief["execution_checklist"] if item["phase"] != "verification"))
        self.assertGreaterEqual(brief["coverage"]["cited_memories"], 4)
        self.assertIn("obsidian", {item["source"] for item in brief["coverage"]["source_mix"]})
        self.assertIn("constraints_present", {item["code"] for item in brief["risk_flags"]})
        self.assertTrue(any("negative constraints" in item for item in brief["next_actions"]))
        self.assertTrue(any("current decisions" in item for item in brief["next_actions"]))
        self.assertTrue(any("open actions" in item for item in brief["next_actions"]))
        self.assertIn("Cortex Action Brief", brief["markdown"])
        self.assertIn("Action Plan", brief["markdown"])
        self.assertIn("Execution Checklist", brief["markdown"])
        self.assertIn("Open Actions", brief["markdown"])
        self.assertIn("Next Actions", brief["markdown"])
        self.assertTrue(all(item["citation"]["source_url"].startswith("local-file://Launch.md") for item in brief["procedures"]))

        with connect(self.db_path) as conn:
            event_count = conn.execute(
                "SELECT COUNT(*) FROM memory_events WHERE user_id = ? AND event_type = 'action_brief'",
                (self.user_id,),
            ).fetchone()[0]
        self.assertGreaterEqual(event_count, 1)

    def test_mcp_prepare_action_brief_is_read_only_and_supports_markdown(self) -> None:
        self.assertIn("prepare_action_brief", READ_TOOLS)
        tool = next(tool for tool in TOOLS if tool["name"] == "prepare_action_brief")
        self.assertIn("task", tool["inputSchema"]["required"])

        payload = call_tool(
            self.store,
            self.user_id,
            "prepare_action_brief",
            {"task": "prepare Project Atlas launch invite", "sector": "Project Atlas"},
        )
        self.assertEqual(payload["status"], "strong")
        self.assertTrue(payload["current_decisions"])
        self.assertTrue(payload["action_plan"])

        markdown = call_tool(
            self.store,
            self.user_id,
            "prepare_action_brief",
            {"task": "prepare Project Atlas launch invite", "sector": "Project Atlas", "format": "markdown"},
        )
        self.assertIn("# Cortex Action Brief", markdown)
        self.assertIn("Current Decisions", markdown)

    def test_action_brief_open_actions_respect_source_account_review_policy(self) -> None:
        user_id = "action-brief-source-policy-user"
        self.store.update_settings(user_id, {"review_new_captures": True, "allow_pending_in_context": False})
        trusted_account = self.store.upsert_source_account(
            user_id,
            source="slack",
            account_label="Trusted Slack",
            account_identifier="trusted-workspace",
            policy={"mode": "trusted", "review_required": False, "allow_ai_context": True},
        )
        gated_account = self.store.upsert_source_account(
            user_id,
            source="slack",
            account_label="Gated Slack",
            account_identifier="gated-workspace",
            policy={"mode": "default", "review_required": True, "allow_ai_context": True},
        )
        self.store.sync_source_account_records(
            user_id,
            trusted_account["id"],
            records=[
                {
                    "external_id": "trusted-next-action",
                    "content": "Follow up with the amber-beta customer before Friday.",
                    "title": "Trusted action",
                    "metadata": {"channel": "customer-success", "line_start": 7},
                    "extracted": {
                        "tasks": [
                            {
                                "id": "trusted_action_brief_task",
                                "kind": "action",
                                "content": "Follow up with the amber-beta customer before Friday.",
                                "status": "open",
                                "importance": 5,
                                "topics": ["amber-beta", "customer"],
                            }
                        ],
                        "records": [],
                    },
                }
            ],
            processing="sync",
        )
        gated_sync = self.store.sync_source_account_records(
            user_id,
            gated_account["id"],
            records=[
                {
                    "external_id": "gated-next-action",
                    "content": "Send the unreviewed violet-beta escalation plan to the customer.",
                    "title": "Gated action",
                    "metadata": {"channel": "customer-success", "line_start": 11},
                    "extracted": {
                        "tasks": [
                            {
                                "id": "gated_action_brief_task",
                                "kind": "action",
                                "content": "Send the unreviewed violet-beta escalation plan to the customer.",
                                "status": "open",
                                "importance": 5,
                                "topics": ["violet-beta", "customer"],
                            }
                        ],
                        "records": [],
                    },
                }
            ],
            processing="sync",
        )

        brief = self.store.action_brief(user_id, "customer beta follow up", limit=6)
        payload_text = str(brief)
        self.assertIn("amber-beta", payload_text)
        self.assertNotIn("violet-beta", payload_text)
        self.assertTrue(any("amber-beta" in item["step"] for item in brief["execution_checklist"]))
        self.assertTrue(any("amber-beta" in item["action"] for item in brief["action_plan"]))
        self.assertIn("amber-beta", brief["markdown"])
        self.assertNotIn("violet-beta", brief["markdown"])
        self.assertTrue(all(item["citation"]["source_account_id"] == trusted_account["id"] for item in brief["open_actions"]))

        self.assertEqual(self.store.approve_capture(user_id, gated_sync["capture_ids"][0]), True)
        approved_brief = self.store.action_brief(user_id, "violet-beta customer escalation", limit=6)
        self.assertIn("violet-beta", str(approved_brief))


if __name__ == "__main__":
    unittest.main()
