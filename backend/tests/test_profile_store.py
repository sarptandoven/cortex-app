from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, now_iso


class BuildProfileStoreTests(unittest.TestCase):
    """P0/profile wiring: store.build_profile assembles a cited, structured profile from the
    existing personal_profile output (profile.py organize + condense.py statements), abstaining on
    a thin corpus. The deterministic core is exercised (no LLM key => condensed == False)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False  # deterministic lexical + signature dedup
        self.user_id = "profile-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, *, layer: str, content: str, source: str, importance: int = 4) -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=f"{source}://{memory_id}",
            title=memory_id,
            extracted={
                "_timestamp": now_iso(),
                "summary": content,
                "records": [
                    {"id": memory_id, "kind": layer, "layer": layer, "content": content,
                     "confidence": "confirmed", "importance": importance, "topics": ["meetings"], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def test_abstains_on_thin_corpus(self) -> None:
        # A single LOW-importance, unsupported signal is not "who you are" -> abstain (omit section).
        # (A single high-importance element legitimately surfaces via the single-element rule.)
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian", importance=2)
        profile = self.store.build_profile(self.user_id)
        self.assertEqual(profile["sections"], [])
        self.assertIn("limitations", profile)

    def test_builds_cited_sections(self) -> None:
        for i in range(6):
            self._seed(f"pref_{i}", layer="preference",
                       content="You prefer to decline meetings before 10am.", source="calendar")
        for i in range(3):
            self._seed(f"dec_{i}", layer="decision",
                       content="We decided to use sharded SQLite over Postgres.", source="obsidian")
        profile = self.store.build_profile(self.user_id)

        self.assertFalse(profile["condensed"])  # no ANTHROPIC_API_KEY in the test env
        self.assertIsInstance(profile["readiness"], int)
        self.assertTrue(profile["sections"], profile)

        by_id = {s["id"]: s for s in profile["sections"]}
        self.assertIn("preferences", by_id)
        pref = by_id["preferences"]
        # Every section has a statement + confidence, and every element is CITED.
        self.assertTrue(pref["statement"])
        self.assertIn(pref["confidence"], {"high", "medium", "low"})
        for element in pref["elements"]:
            self.assertTrue(element["memory_ids"], element)
        # The near-identical preferences collapsed into a supported, cited element.
        self.assertTrue(any(el["count"] >= 3 for el in pref["elements"]), pref)

    def test_deterministic(self) -> None:
        for i in range(5):
            self._seed(f"pref_{i}", layer="preference",
                       content="You prefer async written updates over meetings.", source="slack")
        # generated_at is a second-precision wall-clock stamp; two builds can legitimately
        # straddle a second boundary. Content determinism is the contract.
        first = self.store.build_profile(self.user_id)
        second = self.store.build_profile(self.user_id)
        first.pop("generated_at", None)
        second.pop("generated_at", None)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
