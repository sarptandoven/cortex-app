from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore
from scripts.adaptation_eval import ADAPTATION_SEEDS, evaluate_adaptation, seed_adaptation_memories


class AdaptationQualityHarnessTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "adaptation-quality.sqlite"
        self.vault_path = Path(self.tmp.name) / "Cortex.vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)
        self.user_id = "adaptation-quality-test"

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_seed_adaptation_memories_covers_all_layers(self) -> None:
        memories = seed_adaptation_memories(self.store, self.user_id)

        self.assertEqual({memory["layer"] for memory in memories}, {seed.layer for seed in ADAPTATION_SEEDS})
        self.assertTrue(all(memory["source_url"] for memory in memories))

    def test_agent_adaptation_eval_passes_behavior_contract(self) -> None:
        result = evaluate_adaptation(self.store, self.user_id)

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["seeded_memories"], len(ADAPTATION_SEEDS))
        self.assertGreaterEqual(result["rules"], len(ADAPTATION_SEEDS))
        self.assertGreaterEqual(result["readiness"], 80)
        self.assertFalse(result["failures"])
        self.assertTrue(all(check["status"] == "ok" for check in result["checks"]))
        check_names = {check["name"] for check in result["checks"]}
        self.assertIn("noisy_external_speaker_preferences_excluded", check_names)
        self.assertIn("rules_cover_all_memory_layers", check_names)

    def test_adaptation_pack_carries_cited_persona(self) -> None:
        # The "operate as this user" pack an external AI loads must include the new cited Profile
        # persona (plain "who this person is" statements), on top of the per-layer rules — this is
        # what makes the user's own AI operate AS them. Additive: existing keys stay intact.
        seed_adaptation_memories(self.store, self.user_id)
        pack = self.store.agent_adaptation(self.user_id)

        self.assertIn("persona", pack)
        self.assertIsInstance(pack["persona"], list)
        self.assertTrue(pack["persona"], "expected persona sections for a well-seeded corpus")
        self.assertTrue(pack["rules"])  # per-layer rules still present (no regression)
        for section in pack["persona"]:
            self.assertIn(
                section["id"],
                {
                    "how_you_work",
                    "voice_style",
                    "preferences",
                    "dislikes",
                    "decisions",
                    "facts",
                    "recent_timeline",
                    "focus",
                    "people_projects",
                    "open_loops",
                },
            )
            self.assertTrue(str(section.get("statement") or "").strip())
            # Cited-or-abstain: every persona element carries a citation (memory_ids or a source).
            for element in section.get("elements", []):
                self.assertTrue(element.get("memory_ids") or str(element.get("source") or "").strip(), element)
        self.assertIn("Who you're adapting to", pack["markdown"])


if __name__ == "__main__":
    unittest.main()
