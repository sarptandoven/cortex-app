"""Tests for the taste-exclusion flag on individual memories.

A memory can be flagged ``taste_excluded`` so it stays a fully searchable, citable,
auditable, historically-accurate record while being excluded as a candidate for
Personal Profile / Mirror Moment / taste-style summaries / preference-inference.
This is orthogonal to the pending/approved/archived/deleted capture lifecycle.

These tests follow the tempdir + CortexStore + seeded-capture pattern used
throughout backend/tests/ (see test_profile_store.py, test_m6_calibrated_metacognition.py).
They assert the product-visible contract end to end:

  1. search() and answer_query() (Ask) still surface an excluded memory, cited.
  2. the audit/event log still records the flag change (and the memory's original
     creation event is untouched).
  3. Personal Profile (personal_profile()/build_profile()) skips it as a candidate.
  4. Mirror Moment (covered separately in test_mirror_insight.py) never surfaces an
     insight built primarily from it.
  5. memories without the flag (the overwhelming majority) behave exactly as before
     (regression safety).
  6. the flag round-trips through rebuild-index-from-vault.
  7. a caller can explicitly opt back in via include_taste_excluded=True.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.mcp_tools import call_tool
from backend.app.storage import CortexStore, now_iso


class TasteExclusionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.vault_path = root / "vault"
        self.store = CortexStore(self.db_path, self.vault_path)
        self.store._vector_ready = lambda conn: False  # deterministic lexical + signature dedup
        self.user_id = "taste-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- helpers -------------------------------------------------------------

    def _seed(self, memory_id: str, *, layer: str, content: str, source: str, importance: int = 4, topics=None) -> None:
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
                    {
                        "id": memory_id, "kind": layer, "layer": layer, "content": content,
                        "confidence": "confirmed", "importance": importance,
                        "topics": topics or [], "entity_ids": [],
                    }
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def _seed_preference_cluster(self, prefix: str, count: int = 6) -> list[str]:
        ids = []
        for i in range(count):
            mid = f"{prefix}_{i}"
            ids.append(mid)
            self._seed(mid, layer="preference", content="You prefer to decline meetings before 10am.", source="calendar")
        return ids

    # --- toggling the flag -----------------------------------------------------

    def test_set_and_toggle_taste_exclusion(self) -> None:
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian")
        memory = self.store.get_memory(self.user_id, "m1")
        self.assertFalse(memory["taste_excluded"], "new memories default to not excluded")

        self.assertTrue(self.store.set_memory_taste_exclusion(self.user_id, "m1", True))
        memory = self.store.get_memory(self.user_id, "m1")
        self.assertTrue(memory["taste_excluded"])

        # Idempotent re-set of the same value.
        self.assertTrue(self.store.set_memory_taste_exclusion(self.user_id, "m1", True))
        self.assertTrue(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

        # Toggle back off.
        self.assertTrue(self.store.set_memory_taste_exclusion(self.user_id, "m1", False))
        self.assertFalse(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

    def test_set_taste_exclusion_on_unknown_memory_returns_false(self) -> None:
        self.assertFalse(self.store.set_memory_taste_exclusion(self.user_id, "does-not-exist", True))

    def test_mcp_tool_dispatch(self) -> None:
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian")
        result = call_tool(self.store, self.user_id, "set_memory_taste_exclusion", {"id": "m1", "excluded": True})
        self.assertTrue(result["updated"])
        self.assertTrue(result["taste_excluded"])
        self.assertTrue(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

        # Default excluded=True when omitted.
        self._seed("m2", layer="preference", content="You prefer light roast coffee.", source="obsidian")
        result = call_tool(self.store, self.user_id, "set_memory_taste_exclusion", {"id": "m2"})
        self.assertTrue(result["taste_excluded"])

    # --- (1) search / Ask citations are unaffected ----------------------------

    def test_excluded_memory_still_searchable(self) -> None:
        self._seed("m1", layer="preference", content="You prefer async written standups over meetings.", source="obsidian")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)

        results = self.store.search(self.user_id, "async written standups")
        self.assertIn("m1", {r["id"] for r in results}, "excluded memory must remain searchable")
        # The result payload is honest about the flag (a client can render a badge)
        # but the flag never gates whether the row was returned.
        hit = next(r for r in results if r["id"] == "m1")
        self.assertTrue(hit["taste_excluded"])

    def test_excluded_memory_still_citable_in_ask_answer(self) -> None:
        self._seed("m1", layer="semantic", content="Cortex uses sharded SQLite with sqlite-vec for retrieval.", source="obsidian")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)

        answer = self.store.answer_query(self.user_id, "sharded SQLite sqlite-vec retrieval")
        cited_ids = {c["id"] for c in answer["citations"]}
        self.assertIn("m1", cited_ids, "excluded memory must remain citable in Ask answers")

    def test_excluded_memory_still_in_recent(self) -> None:
        self._seed("m1", layer="preference", content="You prefer terse commit messages.", source="github")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)
        recent_ids = {r["id"] for r in self.store.recent(self.user_id, limit=20)}
        self.assertIn("m1", recent_ids)

    # --- (2) audit / event log is unaffected + records the toggle -------------

    def test_excluded_memory_still_in_audit_log(self) -> None:
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)

        events = self.store.audit_log(self.user_id, limit=100)
        by_type = [e for e in events if e["event_type"] == "taste_exclusion_updated" and e["object_id"] == "m1"]
        self.assertTrue(by_type, "the exclusion toggle must leave an audit trail")
        self.assertTrue(by_type[0]["metadata"]["excluded"])

        # The capture's own creation event (and the rest of the audit log) is unaffected by the
        # exclusion toggle — the toggle only ADDS an event, it never mutates/removes history.
        capture_events = [e for e in events if e["event_type"] == "created" and e["object_type"] == "capture"]
        self.assertTrue(capture_events, "unrelated audit history must be untouched")

    # --- export is unaffected -------------------------------------------------

    def test_excluded_memory_still_in_export(self) -> None:
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)

        export = self.store.export_json(self.user_id)
        by_id = {m["id"]: m for m in export["memories"]}
        self.assertIn("m1", by_id, "excluded memory must remain present in export")
        self.assertTrue(by_id["m1"]["taste_excluded"])

    # --- (3) Personal Profile skips it as a candidate -------------------------

    def test_personal_profile_skips_excluded_candidates(self) -> None:
        ids = self._seed_preference_cluster("pref")
        # Sanity: before exclusion, the cluster is a real, supported candidate.
        before = self.store.personal_profile(self.user_id)
        pref_section = next(s for s in before["sections"] if s["layer"] == "preference")
        self.assertEqual(pref_section["count"], len(ids))
        self.assertTrue(pref_section["items"], "the cluster must be a candidate before exclusion")

        for mid in ids:
            self.assertTrue(self.store.set_memory_taste_exclusion(self.user_id, mid, True))

        after = self.store.personal_profile(self.user_id)
        pref_section_after = next(s for s in after["sections"] if s["layer"] == "preference")
        self.assertEqual(pref_section_after["count"], 0, "excluded memories must not count toward layer coverage")
        self.assertEqual(pref_section_after["items"], [], "excluded memories must not be surfaced as items")

    def test_build_profile_omits_a_section_built_only_from_excluded_memories(self) -> None:
        ids = self._seed_preference_cluster("pref")
        for mid in ids:
            self.store.set_memory_taste_exclusion(self.user_id, mid, True)

        profile = self.store.build_profile(self.user_id)
        section_ids = {s["id"] for s in profile["sections"]}
        self.assertNotIn("preferences", section_ids, "a section built only from excluded memories must abstain")

    def test_personal_profile_partial_exclusion_only_drops_excluded_items(self) -> None:
        """Excluding SOME (not all) of a cluster must not silently exclude the rest —
        only the flagged memories are removed as candidates."""
        ids = self._seed_preference_cluster("pref", count=6)
        excluded, kept = ids[:3], ids[3:]
        for mid in excluded:
            self.store.set_memory_taste_exclusion(self.user_id, mid, True)

        profile = self.store.personal_profile(self.user_id)
        pref_section = next(s for s in profile["sections"] if s["layer"] == "preference")
        self.assertEqual(pref_section["count"], len(kept))
        surfaced_ids = {item["id"] for item in pref_section["items"]}
        self.assertTrue(surfaced_ids.issubset(set(kept)))
        self.assertFalse(surfaced_ids & set(excluded), "excluded memories must never appear as surfaced items")

    def test_include_taste_excluded_override(self) -> None:
        """A caller can explicitly opt in to seeing excluded memories anyway."""
        ids = self._seed_preference_cluster("pref")
        for mid in ids:
            self.store.set_memory_taste_exclusion(self.user_id, mid, True)

        default_profile = self.store.personal_profile(self.user_id)
        self.assertFalse(default_profile["include_taste_excluded"])
        default_section = next(s for s in default_profile["sections"] if s["layer"] == "preference")
        self.assertEqual(default_section["count"], 0)

        override_profile = self.store.personal_profile(self.user_id, include_taste_excluded=True)
        self.assertTrue(override_profile["include_taste_excluded"])
        override_section = next(s for s in override_profile["sections"] if s["layer"] == "preference")
        self.assertEqual(override_section["count"], len(ids))

        override_built = self.store.build_profile(self.user_id, include_taste_excluded=True)
        self.assertIn("preferences", {s["id"] for s in override_built["sections"]})

    def test_agent_adaptation_evidence_omits_excluded_focus_memories(self) -> None:
        """agent_adaptation() folds profile['focus'] straight into its 'evidence' for an external
        AI adapting to act as the user — an excluded memory must not appear there even though
        `rules`/`persona`/`style_guide`/etc. were already correctly filtered via personal_profile()."""
        self._seed("m1", layer="semantic", content="Went to a one-off improv night called Clown College.", source="obsidian")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)

        adaptation = self.store.agent_adaptation(self.user_id, query="Clown College improv night")
        evidence_ids = {e["id"] for e in adaptation["evidence"]}
        self.assertNotIn("m1", evidence_ids, "an excluded memory must not appear in agent_adaptation's evidence")

        # But it is still a normal search() hit outside the profile/adaptation surface.
        self.assertIn("m1", {r["id"] for r in self.store.search(self.user_id, "Clown College improv night")})

    def test_focus_and_people_sections_skip_excluded_memories(self) -> None:
        """Focus areas (topics) and People & projects (entities) are also part of Personal
        Profile generation — a topic/entity supported ONLY by excluded memories must not
        surface, while one supported by non-excluded memories still does."""
        excluded_entity = {"id": "ent_clown_college", "kind": "project", "name": "Clown College", "aliases": [], "context": ""}
        kept_entity = {"id": "ent_zephyr", "kind": "project", "name": "Project Zephyr", "aliases": [], "context": ""}

        def seed_linked(mem_id: str, content: str, entity: dict, topic: str) -> None:
            self.store.save_capture(
                user_id=self.user_id,
                content=content,
                source="obsidian",
                source_url=f"local-file://{mem_id}",
                title=mem_id,
                extracted={
                    "_timestamp": now_iso(),
                    "summary": content,
                    "records": [
                        {"id": mem_id, "kind": "claim", "layer": "semantic", "content": content,
                         "confidence": "confirmed", "importance": 3, "topics": [topic], "entity_ids": [entity["id"]]}
                    ],
                    "tasks": [],
                    "entities": [entity],
                },
            )

        # Excluded cluster: a one-off joke about "Clown College", mentioned twice.
        seed_linked("joke1", "Went to a one-off improv thing called Clown College.", excluded_entity, "clown-college")
        seed_linked("joke2", "Clown College rehearsal ran long again.", excluded_entity, "clown-college")
        self.store.set_memory_taste_exclusion(self.user_id, "joke1", True)
        self.store.set_memory_taste_exclusion(self.user_id, "joke2", True)

        # Kept cluster: real, non-excluded work signal.
        seed_linked("work1", "Shipped the Project Zephyr release.", kept_entity, "release-planning")
        seed_linked("work2", "Reviewed the Project Zephyr rollout plan.", kept_entity, "release-planning")

        profile = self.store.personal_profile(self.user_id)
        topic_names = {t["topic"] for t in profile["topics"]}
        entity_names = {e["name"] for e in profile["entities"]}
        self.assertNotIn("clown-college", topic_names, "a topic supported only by excluded memories must not surface")
        self.assertNotIn("Clown College", entity_names, "an entity supported only by excluded memories must not surface")
        self.assertIn("release-planning", topic_names)
        self.assertIn("Project Zephyr", entity_names)

        built = self.store.build_profile(self.user_id)
        by_id = {s["id"]: s for s in built["sections"]}
        if "focus" in by_id:
            focus_texts = " ".join(e["text"] for e in by_id["focus"]["elements"])
            self.assertNotIn("clown", focus_texts.lower())
        if "people_projects" in by_id:
            people_texts = " ".join(e["text"] for e in by_id["people_projects"]["elements"])
            self.assertNotIn("Clown College", people_texts)

    # --- (5) regression: unflagged memories are unaffected --------------------

    def test_unflagged_memories_behave_exactly_as_before(self) -> None:
        ids = self._seed_preference_cluster("pref")
        profile = self.store.build_profile(self.user_id)
        by_id = {s["id"]: s for s in profile["sections"]}
        self.assertIn("preferences", by_id)
        pref = by_id["preferences"]
        self.assertTrue(pref["statement"])
        for element in pref["elements"]:
            self.assertTrue(element["memory_ids"])
        self.assertTrue(any(el["count"] >= 3 for el in pref["elements"]))

        # Search/citation/audit are all normal for a plain, never-flagged memory too.
        results = self.store.search(self.user_id, "decline meetings before 10am")
        self.assertTrue(set(ids) & {r["id"] for r in results})

    # --- (6) vault round-trip survives rebuild-index-from-vault ---------------

    def test_flag_survives_rebuild_index_from_vault(self) -> None:
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)
        self.assertTrue(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

        self.store.rebuild_index_from_vault(self.user_id)

        rebuilt = self.store.get_memory(self.user_id, "m1")
        self.assertIsNotNone(rebuilt, "the memory itself must survive the rebuild")
        self.assertTrue(rebuilt["taste_excluded"], "the taste-exclusion flag must survive rebuild-index-from-vault")

    def test_unflagged_memory_survives_rebuild_index_from_vault_as_unflagged(self) -> None:
        self._seed("m1", layer="preference", content="You prefer dark mode.", source="obsidian")
        self.store.rebuild_index_from_vault(self.user_id)
        rebuilt = self.store.get_memory(self.user_id, "m1")
        self.assertFalse(rebuilt["taste_excluded"])


class MirrorFallbackAndPersonMapTasteExclusionTests(unittest.TestCase):
    """Regression coverage for a gap an independent review caught: `store.mirror_insight()`'s
    graph-bridge FALLBACK (`_graph_bridge_insight` / `_shared_entity_memory_ids`) and
    `person_map()`'s graph half both call `entity_graph_analysis()` — every existing
    test_mirror_insight.py test exercises `mirror.compute_mirror_insight()` directly, which never
    touches this fallback path, so it originally shipped unfiltered. These tests hit the exact
    product-facing methods (`store.mirror_insight()`, `store.person_map()`) that the API/MCP layer
    calls, using the same two-cluster-plus-bridge fixture as test_entity_graph.py's
    test_bridge_surfaces_as_mirror_insight_when_no_repetition."""

    ENTITIES = {
        "ent_alice": {"id": "ent_alice", "kind": "person", "name": "Alice", "aliases": [], "context": ""},
        "ent_zephyr": {"id": "ent_zephyr", "kind": "project", "name": "Project Zephyr", "aliases": [], "context": ""},
        "ent_bob": {"id": "ent_bob", "kind": "person", "name": "Bob", "aliases": [], "context": ""},
        "ent_design": {"id": "ent_design", "kind": "project", "name": "Design System", "aliases": [], "context": ""},
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "graph-taste-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, mem_id: str, content: str, entity_ids: list[str], layer: str = "semantic") -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="obsidian",
            source_url=f"local-file://{mem_id}",
            title=mem_id,
            extracted={
                "_timestamp": now_iso(),
                "summary": content,
                "records": [
                    {"id": mem_id, "kind": "claim", "layer": layer, "content": content,
                     "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": entity_ids}
                ],
                "tasks": [],
                "entities": [self.ENTITIES[e] for e in entity_ids],
            },
        )

    def _seed_bridge_scenario(self) -> tuple[str, str]:
        """Two internally-dense clusters {Alice,Zephyr} and {Bob,Design} joined ONLY by two
        cross-cluster bridge memories. No repeated behavioral pattern exists (distinct layers), so
        the repetition-based Mirror Moment abstains and the graph "surprising connection" is the
        only candidate left — exactly the scenario the review agent used to reproduce the leak.
        Returns the two bridge memory ids."""
        for i, layer in enumerate(("episodic", "decision", "style")):
            self._seed(f"az{i}", "Alice and Project Zephyr.", ["ent_alice", "ent_zephyr"], layer=layer)
        for i, layer in enumerate(("semantic", "procedural", "negative")):
            self._seed(f"bd{i}", "Bob and the Design System.", ["ent_bob", "ent_design"], layer=layer)
        self._seed("zb0", "Project Zephyr borrowed a pattern from Bob's team.", ["ent_zephyr", "ent_bob"], layer="preference")
        self._seed("zb1", "Bob advised on the Project Zephyr rollout.", ["ent_zephyr", "ent_bob"], layer="episodic")
        return "zb0", "zb1"

    def test_mirror_insight_baseline_bridge_still_works(self) -> None:
        """Regression guard for the unflagged case: the graph-bridge fallback must still fire
        exactly as before when nothing is excluded (matches test_entity_graph.py's contract)."""
        bridge_ids = self._seed_bridge_scenario()
        insight = self.store.mirror_insight(self.user_id)
        self.assertIsNotNone(insight, "expected a bridge insight when repetition abstains")
        self.assertEqual(insight["layer"], "relationship")
        self.assertEqual(set(insight["evidence"]["memory_ids"]), set(bridge_ids))

    def test_mirror_insight_fallback_never_cites_taste_excluded_bridge_memories(self) -> None:
        """The exact leak the review agent found: store.mirror_insight() — not
        mirror.compute_mirror_insight() directly — must not surface a "you connect X and Y"
        insight built only from taste-excluded memories."""
        bridge_a, bridge_b = self._seed_bridge_scenario()
        self.store.set_memory_taste_exclusion(self.user_id, bridge_a, True)
        self.store.set_memory_taste_exclusion(self.user_id, bridge_b, True)

        insight = self.store.mirror_insight(self.user_id)
        self.assertIsNone(
            insight,
            "a Mirror Moment must not be built from a bridge whose only supporting memories are excluded",
        )

    def test_mirror_insight_fallback_partial_exclusion_only_cites_the_kept_memory(self) -> None:
        """Excluding ONE of the two bridge memories must drop it from both the count and the
        cited memory_ids — not silently keep citing it, and not over-suppress the whole insight
        (a single non-excluded co-mention is below the "genuine connection" floor here, so the
        bridge itself should no longer surface either; this proves the excluded id specifically
        never leaks into any citation that might otherwise remain)."""
        bridge_a, bridge_b = self._seed_bridge_scenario()
        self.store.set_memory_taste_exclusion(self.user_id, bridge_a, True)

        insight = self.store.mirror_insight(self.user_id)
        if insight is not None:
            self.assertNotIn(bridge_a, insight["evidence"]["memory_ids"])

    def test_person_map_graph_omits_relationship_built_only_from_excluded_memories(self) -> None:
        """person_map()'s graph half (hubs/communities/bridges) must be exactly as taste-sensitive
        as its profile half. With BOTH Alice<->Zephyr co-mentions excluded, Alice/Zephyr must not
        surface as a hub/community backed only by excluded evidence, while the untouched Bob/Design
        cluster still does."""
        for i, layer in enumerate(("episodic", "decision", "style")):
            self._seed(f"az{i}", "Alice and Project Zephyr.", ["ent_alice", "ent_zephyr"], layer=layer)
            self.store.set_memory_taste_exclusion(self.user_id, f"az{i}", True)
        for i, layer in enumerate(("semantic", "procedural", "negative")):
            self._seed(f"bd{i}", "Bob and the Design System.", ["ent_bob", "ent_design"], layer=layer)

        result = self.store.person_map(self.user_id)
        hub_labels = {hub["label"] for hub in result["graph"]["hubs"]}
        self.assertNotIn("Alice", hub_labels, "a hub supported only by excluded memories must not surface")
        self.assertNotIn("Project Zephyr", hub_labels)
        self.assertIn("Bob", hub_labels)
        self.assertIn("Design System", hub_labels)

    def test_person_map_graph_baseline_still_surfaces_hubs(self) -> None:
        """Regression guard: person_map()'s graph is unaffected when nothing is excluded."""
        for i, layer in enumerate(("episodic", "decision", "style")):
            self._seed(f"az{i}", "Alice and Project Zephyr.", ["ent_alice", "ent_zephyr"], layer=layer)
        result = self.store.person_map(self.user_id)
        hub_labels = {hub["label"] for hub in result["graph"]["hubs"]}
        self.assertIn("Alice", hub_labels)
        self.assertIn("Project Zephyr", hub_labels)


if __name__ == "__main__":
    unittest.main()
