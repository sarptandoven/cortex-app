from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import now_iso
from backend.app.storage import (
    CONTEXT_INTENT_WEIGHTS,
    CONTEXT_MAX_TOKEN_BUDGET,
    CONTEXT_MIN_TOKEN_BUDGET,
    CortexStore,
    _derive_context_intent,
)


class ContextEngineBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        init_db(root / "cortex.db")
        self.store = CortexStore(root / "cortex.db", root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "ctx-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, content: str, *, layer: str = "semantic", kind: str = "claim",
              entities: list[dict] | None = None, cite: bool = True, source: str = "macos") -> None:
        extracted = {
            "_timestamp": now_iso(),
            "summary": content,
            "records": [
                {"id": memory_id, "kind": kind, "layer": layer, "content": content,
                 "confidence": "confirmed", "importance": 3, "topics": [],
                 "entity_ids": [entity["id"] for entity in (entities or [])]}
            ],
            "tasks": [],
            "entities": entities or [],
        }
        self.store.save_capture(
            user_id=self.user_id, content=content, source=source, source_url=None,
            title="", extracted=extracted, cite_capture_provenance=cite,
        )

    def _seed_corpus(self) -> None:
        atlas = {"id": "ent_atlas", "kind": "project", "name": "Atlas", "aliases": [], "context": ""}
        alice = {"id": "ent_alice", "kind": "person", "name": "Alice", "aliases": [], "context": ""}
        self._seed("m_dec", "We decided to use PostgreSQL for Atlas because of jsonb support.",
                   layer="decision", kind="decision", entities=[atlas])
        self._seed("m_fact", "Alice leads the Atlas project.", entities=[alice, atlas])
        self._seed("m_neg", "Never deploy Atlas on Fridays.", layer="negative", entities=[atlas])
        self._seed("m_pref", "I prefer short direct emails about Atlas.", layer="preference", kind="preference", entities=[atlas])


class ContextEngineTests(ContextEngineBase):
    def test_deterministic_output(self) -> None:
        self._seed_corpus()
        first = self.store.assemble_context(self.user_id, "what did we decide about Atlas?")
        second = self.store.assemble_context(self.user_id, "what did we decide about Atlas?")
        first.pop("generated_at")
        second.pop("generated_at")
        self.assertEqual(first, second)

    def test_budget_clamped_and_reported(self) -> None:
        self._seed_corpus()
        low = self.store.assemble_context(self.user_id, "Atlas", token_budget=1)
        self.assertEqual(low["budget"]["token_budget"], CONTEXT_MIN_TOKEN_BUDGET)
        high = self.store.assemble_context(self.user_id, "Atlas", token_budget=10 ** 9)
        self.assertEqual(high["budget"]["token_budget"], CONTEXT_MAX_TOKEN_BUDGET)
        pack = self.store.assemble_context(self.user_id, "Atlas", token_budget=2000)
        self.assertLessEqual(pack["budget"]["used_tokens"], 2000)

    def test_every_item_is_cited_with_provenance(self) -> None:
        self._seed_corpus()
        pack = self.store.assemble_context(self.user_id, "tell me about the Atlas database decision")
        item_count = 0
        for layer in pack["layers"]:
            for item in layer.get("items") or []:
                item_count += 1
                self.assertTrue(item.get("memory_id") or item.get("task_id"), item)
                self.assertTrue(item.get("source"), item)
                self.assertIn(item.get("provenance_class"), {"connector_synced", "agent_written", "user_authored"})
                self.assertIs(item.get("treat_as_data"), True)
        self.assertGreater(item_count, 0)
        self.assertEqual(len(pack["citations"]), item_count)

    def test_uncited_content_is_excluded_and_counted(self) -> None:
        self._seed("m_uncited", "Atlas retention owner is Priya per an uncited scratch note.",
                   cite=False, source="scratchpad")
        pack = self.store.assemble_context(self.user_id, "who owns Atlas retention?")
        for layer in pack["layers"]:
            for item in layer.get("items") or []:
                self.assertNotEqual(item.get("memory_id"), "m_uncited")
        self.assertGreater(pack["coverage"]["excluded_uncited"], 0)

    def test_abstains_on_empty_store(self) -> None:
        pack = self.store.assemble_context(self.user_id, "what is the capital of Mongolia?")
        self.assertEqual(pack["coverage"]["status"], "no_cited_evidence")
        for layer in pack["layers"]:
            self.assertFalse(layer.get("items"), layer)

    def test_identity_layer_read_gated_with_visible_omission(self) -> None:
        self._seed_corpus()
        gated = self.store.assemble_context(self.user_id, "draft a reply about Atlas", include_identity=False)
        identity = next(layer for layer in gated["layers"] if layer["layer"] == "identity")
        self.assertEqual(identity.get("omitted"), {"reason": "requires read scope", "required_scopes": ["read"]})
        self.assertEqual(gated["coverage"]["sections_omitted"], ["identity"])
        open_pack = self.store.assemble_context(self.user_id, "draft a reply about Atlas", include_identity=True)
        identity_open = next(layer for layer in open_pack["layers"] if layer["layer"] == "identity")
        self.assertIsNone(identity_open.get("omitted"))
        self.assertTrue(any(item["memory_id"] == "m_pref" for item in identity_open.get("items") or []))

    def test_intent_derivation_and_weight_shift(self) -> None:
        self.assertEqual(_derive_context_intent("write a reply to Bob", None), "draft")
        self.assertEqual(_derive_context_intent("fix the login bug", None), "act")
        self.assertEqual(_derive_context_intent("plan next steps for Q3", None), "plan")
        self.assertEqual(_derive_context_intent("who leads Atlas?", None), "answer")
        self.assertEqual(_derive_context_intent("", None), "recall")
        self.assertEqual(_derive_context_intent("write a reply", "plan"), "plan")  # explicit wins
        # draft weights identity above act's identity weight
        self.assertGreater(CONTEXT_INTENT_WEIGHTS["draft"]["identity"], CONTEXT_INTENT_WEIGHTS["act"]["identity"])
        self._seed_corpus()
        self.assertEqual(self.store.assemble_context(self.user_id, "write a reply to Alice about Atlas")["intent"], "draft")

    def test_constraints_are_never_sacrificed_to_budget(self) -> None:
        self._seed_corpus()
        pack = self.store.assemble_context(self.user_id, "deploy Atlas", token_budget=CONTEXT_MIN_TOKEN_BUDGET)
        constraints = next(layer for layer in pack["layers"] if layer["layer"] == "constraints")
        self.assertTrue(constraints["items"], "the protected constraints layer was dropped")
        self.assertTrue(any("Never deploy Atlas" in item["content"] for item in constraints["items"]))

    def test_superseded_decisions_never_served(self) -> None:
        self._seed_corpus()
        self._seed("m_dec2", "We decided to switch Atlas from PostgreSQL to CockroachDB.",
                   layer="decision", kind="decision")
        resolved = self.store.resolve_conflict(self.user_id, stale_id="m_dec", current_id="m_dec2")
        self.assertTrue(resolved)
        pack = self.store.assemble_context(self.user_id, "which database did we decide on for Atlas?")
        served = {item["memory_id"] for layer in pack["layers"] for item in layer.get("items") or []}
        self.assertNotIn("m_dec", served)

    def test_entity_layer_carries_graph_connections(self) -> None:
        self._seed_corpus()
        pack = self.store.assemble_context(self.user_id, "prepare me for a chat with Alice")
        entity = next(layer for layer in pack["layers"] if layer["layer"] == "entity")
        labels = {connection.get("label") for connection in entity.get("connections") or []}
        self.assertIn("Atlas", labels)

    def test_markdown_render_contains_included_ids(self) -> None:
        self._seed_corpus()
        pack = self.store.assemble_context(self.user_id, "what did we decide about Atlas?")
        markdown = self.store.assemble_context(self.user_id, "what did we decide about Atlas?", format="markdown")
        self.assertIsInstance(markdown, str)
        for layer in pack["layers"]:
            for item in layer.get("items") or []:
                self.assertIn(str(item["memory_id"]), markdown)

    def test_fails_closed_when_agent_reads_disabled(self) -> None:
        self.store.update_settings(self.user_id, {"allow_agent_reads": False})
        with self.assertRaises(PermissionError):
            self.store.assemble_context(self.user_id, "anything")

    def test_dropped_counts_are_visible_under_tiny_budget(self) -> None:
        for index in range(12):
            self._seed(f"m_bulk{index}", f"Atlas milestone {index} shipped the widget batch number {index} successfully.")
        pack = self.store.assemble_context(self.user_id, "Atlas milestone widget", token_budget=CONTEXT_MIN_TOKEN_BUDGET)
        total_dropped = sum(int(layer.get("dropped") or 0) for layer in pack["layers"])
        self.assertGreater(total_dropped, 0)
        facts = next(layer for layer in pack["layers"] if layer["layer"] == "facts")
        # Truncation guarantee: the top fact is present (possibly truncated), never silently absent.
        self.assertTrue(facts["items"])


if __name__ == "__main__":
    unittest.main()
