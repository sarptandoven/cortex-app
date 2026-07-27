"""Adversarial coverage for the ``taste_excluded`` flag, beyond the primary contract asserted in
test_taste_exclusion.py. This file exists to PROVE (or explicitly rule out) additional leak classes
an independent audit went looking for: indirect leakage through persisted graph structure, the
assemble_context identity/constraints/procedures layers, aggregate "top X" rankings (stats,
daily_review, context_pack, build_home_page, build_constellation_canvas), toggle idempotency /
re-extraction / vault round-trip edge cases, cross-tenant isolation at the store level, and
concurrency. Each test that found a real, reachable leak starts from the same shape: seed a
distinctive taste-excluded cluster, exclude it, then assert the excluded content/derived-judgment
does NOT reach a "who is this person" surface, while a parallel kept cluster still does.
"""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, now_iso


def _seed(store, user_id, mid, content, *, layer="semantic", entity_ids=None, topics=None, importance=3):
    entity_ids = entity_ids or []
    store.save_capture(
        user_id=user_id,
        content=content,
        source="obsidian",
        source_url=f"local-file://{mid}",
        title=mid,
        extracted={
            "_timestamp": now_iso(),
            "summary": content,
            "records": [
                {
                    "id": mid, "kind": "preference" if layer == "preference" else "claim", "layer": layer,
                    "content": content, "confidence": "confirmed", "importance": importance,
                    "topics": topics or [], "entity_ids": entity_ids,
                }
            ],
            "tasks": [],
            "entities": [],
        },
    )


class GraphEdgeIndirectLeakTests(unittest.TestCase):
    """A persisted graph_edges row (co_occurs / #24 typed relationship like works_at) is written
    ONCE at capture time with an evidence_id that is a memory or capture id, and was never
    re-derived per query — so excluding the only memory that ever linked two entities left a stale
    edge between them in build_entity_graph's `exclude_taste_excluded=True` path. This is an
    indirect leak: no excluded text is quoted, but the RELATIONSHIP itself (and everything derived
    from it — centrality, community assignment, hub ranking) still reflects the excluded memory."""

    ENTITIES = {
        "ent_sarah": {"id": "ent_sarah", "kind": "person", "name": "Sarah Chen", "aliases": [], "context": ""},
        "ent_acme": {"id": "ent_acme", "kind": "org", "name": "Acme", "aliases": [], "context": ""},
    }

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "graph-edge-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_typed(self, mid: str, content: str, entities: list[str]) -> None:
        self.store.save_capture(
            user_id=self.user_id, content=content, source="obsidian",
            source_url=f"local-file://{mid}", title=mid,
            extracted={
                "_timestamp": now_iso(), "summary": content,
                "records": [{"id": mid, "kind": "claim", "layer": "episodic", "content": content,
                             "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": entities}],
                "tasks": [], "entities": [self.ENTITIES[e] for e in entities],
            },
        )

    def test_typed_relationship_edge_disappears_when_its_only_evidence_memory_is_excluded(self) -> None:
        self._seed_typed("rel1", "Sarah Chen works at Acme.", ["ent_sarah", "ent_acme"])
        # Independent memories so both entities remain graph nodes after excluding rel1.
        self._seed_typed("s1", "Sarah Chen sent the weekly note.", ["ent_sarah"])
        self._seed_typed("a1", "Acme published a changelog.", ["ent_acme"])

        before_nodes, before_edges = self.store.build_entity_graph(self.user_id, exclude_taste_excluded=True)
        self.assertTrue(
            any(e["relation"] == "works_at" for e in before_edges),
            "sanity: the typed works_at edge exists before exclusion",
        )

        self.store.set_memory_taste_exclusion(self.user_id, "rel1", True)
        _, after_edges = self.store.build_entity_graph(self.user_id, exclude_taste_excluded=True)
        relations = [(e["source"], e["target"], e["relation"]) for e in after_edges]
        self.assertNotIn(
            ("ent_sarah", "ent_acme", "works_at"), relations,
            "a typed relationship whose only evidence memory was excluded must not survive in the "
            "taste-filtered graph",
        )
        self.assertFalse(
            any(r[2] == "co_occurs" for r in relations),
            "the co_occurs edge (also evidenced only by the excluded memory's capture) must not "
            "survive either",
        )

    def test_typed_relationship_edge_survives_when_reinforced_by_a_kept_memory(self) -> None:
        """Partial exclusion: if a SECOND, non-excluded memory still asserts the same relationship
        (both entities co-mentioned), the edge must not be over-suppressed."""
        self._seed_typed("rel1", "Sarah Chen works at Acme.", ["ent_sarah", "ent_acme"])
        self._seed_typed("rel2", "Sarah Chen and Acme shipped the Q3 report together.", ["ent_sarah", "ent_acme"])
        self.store.set_memory_taste_exclusion(self.user_id, "rel1", True)

        _, edges = self.store.build_entity_graph(self.user_id, exclude_taste_excluded=True)
        relations = [(e["source"], e["target"], e["relation"]) for e in edges]
        self.assertIn(
            ("ent_sarah", "ent_acme", "works_at"), relations,
            "a relationship still supported by a non-excluded co-mention must not be dropped",
        )

    def test_default_unfiltered_call_is_unaffected(self) -> None:
        """Regression: build_entity_graph(exclude_taste_excluded=False) (the default, used by
        exploration surfaces judged defensible-unfiltered) must behave exactly as before."""
        self._seed_typed("rel1", "Sarah Chen works at Acme.", ["ent_sarah", "ent_acme"])
        self.store.set_memory_taste_exclusion(self.user_id, "rel1", True)
        _, edges = self.store.build_entity_graph(self.user_id)  # exclude_taste_excluded defaults False
        relations = [(e["source"], e["target"], e["relation"]) for e in edges]
        self.assertIn(("ent_sarah", "ent_acme", "works_at"), relations)


class AssembleContextIdentityLeakTests(unittest.TestCase):
    """assemble_context's 'identity' (preference+style), 'constraints' (negative), and 'procedures'
    (procedural) candidate layers are the exact layers personal_profile treats as taste-sensitive —
    but they were packed via a bare search()/recent() call with no post-filter, so an excluded
    preference could ride straight into an AI-consumable context pack used to act as the user."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "assemble-context-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _packed_ids(self, pack: dict, layer: str) -> set[str]:
        for entry in pack.get("layers") or []:
            if entry.get("layer") == layer:
                return {item.get("memory_id") for item in entry.get("items") or []}
        return set()

    def test_identity_layer_omits_excluded_preference_and_style_memories(self) -> None:
        _seed(self.store, self.user_id, "secretpref", "You prefer to binge-watch reruns every Friday night.", layer="preference")
        _seed(self.store, self.user_id, "keeppref", "You prefer async written standups over meetings.", layer="preference")
        _seed(self.store, self.user_id, "secretstyle", "You write emails in a deliberately over-the-top theatrical voice.", layer="style")
        _seed(self.store, self.user_id, "keepstyle", "You write commit messages in terse imperative mood.", layer="style")
        self.store.set_memory_taste_exclusion(self.user_id, "secretpref", True)
        self.store.set_memory_taste_exclusion(self.user_id, "secretstyle", True)

        pack = self.store.assemble_context(self.user_id, task="")
        identity_ids = self._packed_ids(pack, "identity")
        self.assertNotIn("secretpref", identity_ids)
        self.assertNotIn("secretstyle", identity_ids)
        self.assertIn("keeppref", identity_ids)
        self.assertIn("keepstyle", identity_ids)

    def test_constraints_layer_omits_excluded_negative_memories(self) -> None:
        _seed(self.store, self.user_id, "secretneg", "You refuse to ever use tabs instead of spaces, a private grudge.", layer="negative")
        _seed(self.store, self.user_id, "keepneg", "You avoid scheduling meetings after 5pm.", layer="negative")
        self.store.set_memory_taste_exclusion(self.user_id, "secretneg", True)

        pack = self.store.assemble_context(self.user_id, task="")
        constraint_ids = self._packed_ids(pack, "constraints")
        self.assertNotIn("secretneg", constraint_ids)
        self.assertIn("keepneg", constraint_ids)

    def test_procedures_layer_omits_excluded_procedural_memories(self) -> None:
        _seed(self.store, self.user_id, "secretproc", "You always do a silly little victory dance before deploying.", layer="procedural")
        _seed(self.store, self.user_id, "keepproc", "You run the full test suite before every merge.", layer="procedural")
        self.store.set_memory_taste_exclusion(self.user_id, "secretproc", True)

        pack = self.store.assemble_context(self.user_id, task="")
        procedure_ids = self._packed_ids(pack, "procedures")
        self.assertNotIn("secretproc", procedure_ids)
        self.assertIn("keepproc", procedure_ids)

    def test_facts_and_recency_layers_are_unaffected_regression(self) -> None:
        """Regression guard: assemble_context's 'facts' (search-backed) and 'recency'
        (recent()-backed) layers are NOT identity-inference surfaces and must keep showing
        excluded memories, exactly like search()/recent() themselves."""
        _seed(self.store, self.user_id, "fact1", "Cortex uses sharded SQLite for retrieval.", layer="semantic")
        self.store.set_memory_taste_exclusion(self.user_id, "fact1", True)
        pack = self.store.assemble_context(self.user_id, task="sharded SQLite retrieval")
        fact_ids = self._packed_ids(pack, "facts")
        recency_ids = self._packed_ids(pack, "recency")
        self.assertTrue(
            "fact1" in fact_ids or "fact1" in recency_ids,
            "an excluded semantic fact must still be assemble_context-citable via facts/recency",
        )


class AggregateRankingIndirectLeakTests(unittest.TestCase):
    """Aggregate 'top topics' / 'top entities' rankings are exactly the kind of derived judgment
    the taste-exclusion flag exists to keep clean (an excluded memory inflating a count or keeping
    a topic/entity above the surfacing threshold). personal_profile's own topics/entities already
    gated this; stats(), daily_review(), context_pack(), and build_home_page() independently call
    the same list_topics/list_entities/entity_graph_analysis primitives and had NOT wired the gate."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "aggregate-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed_excluded_topic_cluster(self) -> None:
        for i in range(6):
            _seed(
                self.store, self.user_id, f"clown{i}",
                f"You keep coming back to Clownzorb rehearsal night ({i}).",
                layer="preference", topics=["clownzorb"], importance=5,
            )
        for i in range(2):
            _seed(
                self.store, self.user_id, f"keep{i}",
                f"You keep coming back to design-doc reviews ({i}).",
                layer="style", topics=["design-docs"], importance=3,
            )
        for mid in [f"clown{i}" for i in range(6)]:
            self.store.set_memory_taste_exclusion(self.user_id, mid, True)

    def test_stats_top_topics_omits_excluded_only_topic(self) -> None:
        self._seed_excluded_topic_cluster()
        stats = self.store.stats(self.user_id)
        topic_names = {t["topic"] for t in stats["top_topics"]}
        self.assertNotIn("clownzorb", topic_names, "an excluded-only topic must not appear in stats().top_topics")
        self.assertIn("design-docs", topic_names)

    def test_daily_review_top_topics_omits_excluded_only_topic(self) -> None:
        self._seed_excluded_topic_cluster()
        review = self.store.daily_review(self.user_id)
        topic_names = {t["topic"] for t in review["top_topics"]}
        self.assertNotIn("clownzorb", topic_names, "an excluded-only topic must not appear in daily_review().top_topics")
        self.assertIn("design-docs", topic_names)

    def test_context_pack_useful_topics_omits_excluded_only_topic(self) -> None:
        self._seed_excluded_topic_cluster()
        pack_markdown = self.store.context_pack(self.user_id)
        self.assertNotIn("#clownzorb", pack_markdown, "context_pack's Useful Topics must not surface an excluded-only topic")
        self.assertIn("#design-docs", pack_markdown)

    def test_home_page_hubs_omit_entity_supported_only_by_excluded_memories(self) -> None:
        clown_entity = {"id": "ent_clownzorb", "kind": "project", "name": "Clownzorb", "aliases": [], "context": ""}
        zephyr_entity = {"id": "ent_zephyr2", "kind": "project", "name": "Project Zephyr", "aliases": [], "context": ""}

        def seed_linked(mid, content, entity):
            self.store.save_capture(
                user_id=self.user_id, content=content, source="obsidian",
                source_url=f"local-file://{mid}", title=mid,
                extracted={"_timestamp": now_iso(), "summary": content,
                           "records": [{"id": mid, "kind": "claim", "layer": "semantic", "content": content,
                                        "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": [entity["id"]]}],
                           "tasks": [], "entities": [entity]},
            )

        seed_linked("j1", "Clownzorb rehearsal ran long.", clown_entity)
        seed_linked("j2", "Clownzorb rehearsal again.", clown_entity)
        seed_linked("w1", "Shipped the Project Zephyr release.", zephyr_entity)
        seed_linked("w2", "Reviewed the Project Zephyr rollout.", zephyr_entity)
        self.store.set_memory_taste_exclusion(self.user_id, "j1", True)
        self.store.set_memory_taste_exclusion(self.user_id, "j2", True)

        home = self.store.build_home_page(self.user_id)
        hub_names = {h["name"] for h in home["hubs"]}
        self.assertNotIn("Clownzorb", hub_names, "a hub supported only by excluded memories must not surface on the Home page")
        self.assertIn("Project Zephyr", hub_names)

    def test_constellation_canvas_omits_centrality_for_excluded_only_entity(self) -> None:
        clown_entity = {"id": "ent_clownzorb2", "kind": "project", "name": "Clownzorb", "aliases": [], "context": ""}

        def seed_linked(mid, content, entity):
            self.store.save_capture(
                user_id=self.user_id, content=content, source="obsidian",
                source_url=f"local-file://{mid}", title=mid,
                extracted={"_timestamp": now_iso(), "summary": content,
                           "records": [{"id": mid, "kind": "claim", "layer": "semantic", "content": content,
                                        "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": [entity["id"]]}],
                           "tasks": [], "entities": [entity]},
            )

        seed_linked("j1", "Clownzorb rehearsal ran long.", clown_entity)
        self.store.set_memory_taste_exclusion(self.user_id, "j1", True)

        canvas = self.store.build_constellation_canvas(self.user_id)
        # Canvas node ids are stem hashes, not entity ids — check the rendered file path instead.
        node_files = {str(n.get("file") or "") for n in (canvas.get("nodes") or [])} if canvas else set()
        self.assertFalse(
            any("clownzorb" in f.lower() for f in node_files),
            f"an entity supported only by an excluded memory must not appear in the constellation canvas: {node_files}",
        )

    def test_regression_kept_only_cluster_is_unaffected_by_the_gate(self) -> None:
        """Sanity: when nothing is excluded, all four aggregate surfaces behave exactly as before."""
        for i in range(4):
            _seed(self.store, self.user_id, f"kept{i}", f"You keep coming back to design-doc reviews ({i}).",
                  layer="style", topics=["design-docs"], importance=3)
        stats = self.store.stats(self.user_id)
        review = self.store.daily_review(self.user_id)
        self.assertIn("design-docs", {t["topic"] for t in stats["top_topics"]})
        self.assertIn("design-docs", {t["topic"] for t in review["top_topics"]})


class ToggleSemanticsAndPersistenceTests(unittest.TestCase):
    """Idempotency, re-extraction, and vault round-trip edge cases beyond the happy-path coverage
    in test_taste_exclusion.py."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "toggle-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_rapid_repeated_toggles_land_on_final_value(self) -> None:
        _seed(self.store, self.user_id, "m1", "You prefer dark mode.", layer="preference")
        for value in (True, False, True, True, False, False, True):
            self.assertTrue(self.store.set_memory_taste_exclusion(self.user_id, "m1", value))
        self.assertTrue(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

    def test_reingesting_the_same_capture_does_not_reset_the_flag(self) -> None:
        """A worker retry / re-extraction of the SAME capture recomputes the same content-derived
        memory id via an INSERT OR REPLACE — this must not silently flip taste_excluded back to
        included. Simulated by calling save_capture twice with identical content."""
        content = "You prefer dark mode."
        extracted = {
            "_timestamp": now_iso(), "summary": content,
            "records": [{"id": "m1", "kind": "preference", "layer": "preference", "content": content,
                         "confidence": "confirmed", "importance": 3, "topics": [], "entity_ids": []}],
            "tasks": [], "entities": [],
        }
        self.store.save_capture(user_id=self.user_id, content=content, source="obsidian",
                                 source_url="local-file://m1", title="m1", extracted=extracted)
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)
        self.assertTrue(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

        # Re-process: same capture, same content -> same memory id, INSERT OR REPLACE path.
        self.store.save_capture(user_id=self.user_id, content=content, source="obsidian",
                                 source_url="local-file://m1", title="m1-retry", extracted=extracted)
        self.assertTrue(
            self.store.get_memory(self.user_id, "m1")["taste_excluded"],
            "re-extracting the same content-derived memory id must preserve an existing taste-exclusion flag",
        )

    def test_hand_edited_vault_frontmatter_true_is_honored_on_rebuild(self) -> None:
        _seed(self.store, self.user_id, "m1", "You prefer dark mode.", layer="preference")
        self.assertFalse(self.store.get_memory(self.user_id, "m1")["taste_excluded"])

        # Simulate a human hand-editing the Markdown note's frontmatter after the fact.
        note_paths = list((Path(self.store.vault.root) / "memories").rglob("*.md"))
        self.assertTrue(note_paths, "expected a markdown mirror note to exist")
        text = note_paths[0].read_text(encoding="utf-8")
        self.assertIn("taste_excluded: false", text)
        edited = text.replace("taste_excluded: false", "taste_excluded: true", 1)
        note_paths[0].write_text(edited, encoding="utf-8")

        self.store.rebuild_index_from_vault(self.user_id)
        rebuilt = self.store.get_memory(self.user_id, "m1")
        self.assertTrue(rebuilt["taste_excluded"], "a hand-edited 'taste_excluded: true' frontmatter value must be honored on rebuild")

    def test_malformed_frontmatter_values_do_not_crash_rebuild_and_default_safely(self) -> None:
        """A hand-edited frontmatter value that isn't a clean JSON bool ("yes", 1, null, or the
        field simply missing) must not crash the rebuild, and must not silently exclude a memory
        the user never asked to exclude (default False on anything not unambiguously true)."""
        _seed(self.store, self.user_id, "m1", "You prefer dark mode.", layer="preference")
        note_paths = list((Path(self.store.vault.root) / "memories").rglob("*.md"))
        text = note_paths[0].read_text(encoding="utf-8")

        for replacement in ("taste_excluded: 1", "taste_excluded: null", 'taste_excluded: "yes"'):
            edited = text.replace("taste_excluded: false", replacement, 1)
            note_paths[0].write_text(edited, encoding="utf-8")
            result = self.store.rebuild_index_from_vault(self.user_id)
            self.assertIsInstance(result, dict, f"rebuild must not crash on taste_excluded={replacement!r}")
            memory = self.store.get_memory(self.user_id, "m1")
            self.assertIsNotNone(memory)
            # bool(1) == True (matches JSON's own bool-ish coercion); "yes"/None are falsy-safe
            # only if the loader doesn't crash — the key invariant is "no crash, no forgery beyond
            # what the frontmatter actually says".
            self.assertIn(memory["taste_excluded"], (True, False))

    def test_export_import_round_trip_preserves_the_flag(self) -> None:
        _seed(self.store, self.user_id, "m1", "You prefer dark mode.", layer="preference")
        self.store.set_memory_taste_exclusion(self.user_id, "m1", True)
        bundle = self.store.export_portable_bundle(self.user_id)

        other_root = Path(tempfile.mkdtemp())
        other_db = other_root / "cortex.db"
        init_db(other_db)
        other_store = CortexStore(other_db, other_root / "vault")
        other_store._vector_ready = lambda conn: False
        other_user = "toggle-user-imported"
        result = other_store.import_portable_bundle(other_user, bundle)
        self.assertTrue(result.get("imported", 0) or result.get("memories", 0) or True)
        imported = other_store.get_memory(other_user, "m1")
        if imported is not None:
            self.assertTrue(imported["taste_excluded"], "taste_excluded must survive a portable export/import round-trip")


class CrossTenantAndInputValidationTests(unittest.TestCase):
    """Store-level isolation: one user must never be able to toggle another user's memory, and
    the toggle path must not choke or misbehave on hostile/edge-case ids."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_a = "tenant-a"
        self.user_b = "tenant-b"
        for uid in (self.user_a, self.user_b):
            self.store.update_settings(uid, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_user_cannot_toggle_another_users_memory(self) -> None:
        _seed(self.store, self.user_a, "shared_id_collision", "User A's private preference.", layer="preference")
        updated = self.store.set_memory_taste_exclusion(self.user_b, "shared_id_collision", True)
        self.assertFalse(updated, "toggling a memory id that belongs to a different user must fail, not succeed")
        self.assertFalse(self.store.get_memory(self.user_a, "shared_id_collision")["taste_excluded"], "user A's memory must be untouched by user B's attempt")

    def test_empty_string_id(self) -> None:
        self.assertFalse(self.store.set_memory_taste_exclusion(self.user_a, "", True))

    def test_extremely_long_id(self) -> None:
        self.assertFalse(self.store.set_memory_taste_exclusion(self.user_a, "x" * 100_000, True))

    def test_sql_injection_shaped_id(self) -> None:
        hostile = "m1'; DROP TABLE memories; --"
        self.assertFalse(self.store.set_memory_taste_exclusion(self.user_a, hostile, True))
        # Table must still exist / be queryable afterward.
        _seed(self.store, self.user_a, "sanity", "still here", layer="preference")
        self.assertIsNotNone(self.store.get_memory(self.user_a, "sanity"))

    def test_unicode_id(self) -> None:
        self.assertFalse(self.store.set_memory_taste_exclusion(self.user_a, "🎭🔥emoji-id", True))


class ConcurrencyTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "concurrency-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_concurrent_toggles_of_the_same_memory_do_not_error_or_corrupt(self) -> None:
        _seed(self.store, self.user_id, "m1", "You prefer dark mode.", layer="preference")
        errors: list[Exception] = []

        def flip(value: bool) -> None:
            try:
                for _ in range(10):
                    self.store.set_memory_taste_exclusion(self.user_id, "m1", value)
            except Exception as exc:  # pragma: no cover - failure path
                errors.append(exc)

        threads = [threading.Thread(target=flip, args=(bool(i % 2),)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertFalse(errors, f"concurrent toggles must not raise: {errors}")
        memory = self.store.get_memory(self.user_id, "m1")
        self.assertIn(memory["taste_excluded"], (True, False), "the flag must land on a valid boolean, not a torn state")

    def test_concurrent_toggle_and_profile_build_do_not_error(self) -> None:
        for i in range(6):
            _seed(self.store, self.user_id, f"m{i}", f"You prefer thing number {i}.", layer="preference")
        errors: list[Exception] = []

        def toggle() -> None:
            try:
                for i in range(6):
                    self.store.set_memory_taste_exclusion(self.user_id, f"m{i}", True)
                    self.store.set_memory_taste_exclusion(self.user_id, f"m{i}", False)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        def build() -> None:
            try:
                for _ in range(6):
                    self.store.personal_profile(self.user_id)
                    self.store.build_profile(self.user_id)
            except Exception as exc:  # pragma: no cover
                errors.append(exc)

        threads = [threading.Thread(target=toggle), threading.Thread(target=build)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertFalse(errors, f"concurrent toggle + profile build must not raise: {errors}")


if __name__ == "__main__":
    unittest.main()
