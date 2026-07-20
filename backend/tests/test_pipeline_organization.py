"""Storage-side organization pipeline (#17-#25): deterministic layering, retroactive entity
back-merge, extractor versioning + reprocess-on-change, semantic near-dup guards, orphan sweep,
typed entity relationships, and the porter FTS tokenizer. Additive + reconciled; the retrieval /
context_pack eval floors are covered separately by the eval scripts."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.storage import (
    CortexStore,
    EXTRACTOR_VERSION,
    _claims_conflict,
    _cosine_similarity,
    reconcile_memory_layer,
)
from backend.app.graph_analysis import summarize_typed_relationships

USER = "org-user"
TS = "2026-07-09T00:00:00+00:00"


class OrganizationPipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.db = self.root / "org.sqlite"
        init_db(self.db)
        self.store = CortexStore(self.db, self.root / "vault")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _save_entity(self, conn, eid: str, name: str) -> dict:
        return self.store._save_entity(conn, USER, {"id": eid, "kind": "person", "name": name, "aliases": []}, TS)

    def _seed_memory(self, conn, mem_id: str, content: str, entity_ids: list[str]) -> None:
        conn.execute(
            "INSERT INTO memories (id, user_id, kind, layer, content, source, captured_at, recorded_at, entity_ids_json) "
            "VALUES (?, ?, 'claim', 'semantic', ?, 'test', ?, ?, ?)",
            (mem_id, USER, content, TS, TS, json.dumps(entity_ids)),
        )
        for entity_id in entity_ids:
            conn.execute(
                "INSERT INTO memory_entities(memory_id, entity_id, user_id, created_at) VALUES (?, ?, ?, ?)",
                (mem_id, entity_id, USER, TS),
            )

    # ---- #17 deterministic layer classification -----------------------------

    def test_layer_reconcile_honors_valid_explicit(self) -> None:
        self.assertEqual(reconcile_memory_layer("claim", "decision", "anything"), "decision")

    def test_layer_reconcile_uses_content_when_unlabeled(self) -> None:
        # No explicit layer, kind gives no signal -> content heuristic classifies (not blind semantic).
        layer = reconcile_memory_layer("claim", None, "I never use marketing prose or hype.")
        self.assertEqual(layer, "negative")

    def test_layer_reconcile_defaults_semantic_for_plain_fact(self) -> None:
        self.assertEqual(reconcile_memory_layer("claim", "", "The API endpoint is api.example.com."), "semantic")

    # ---- #18 retroactive entity resolution (back-merge) ----------------------

    def test_retroactive_backmerge_reclaims_single_token(self) -> None:
        with connect(self.store.db_path) as conn:
            self._save_entity(conn, "person_sarah", "Sarah")
            self._seed_memory(conn, "mem_sarah", "Sarah owns the roadmap", ["person_sarah"])
            result = self._save_entity(conn, "person_sarah-chen", "Sarah Chen")

            self.assertEqual(result["id"], "person_sarah-chen")
            self.assertEqual(result.get("_backmerge_remap"), {"person_sarah": "person_sarah-chen"})
            # The single-token node is gone and its name survives as a searchable alias (no data loss).
            self.assertIsNone(
                conn.execute("SELECT 1 FROM entities WHERE user_id = ? AND id = ?", (USER, "person_sarah")).fetchone()
            )
            self.assertIn("Sarah", result["aliases"])
            # References are repointed bidirectionally: entity_ids_json + memory_entities.
            eids = json.loads(
                conn.execute("SELECT entity_ids_json FROM memories WHERE id = ?", ("mem_sarah",)).fetchone()[0]
            )
            self.assertEqual(eids, ["person_sarah-chen"])
            linked = conn.execute(
                "SELECT entity_id FROM memory_entities WHERE memory_id = ?", ("mem_sarah",)
            ).fetchone()[0]
            self.assertEqual(linked, "person_sarah-chen")
            # A provenance event records the merge.
            merged = conn.execute(
                "SELECT metadata_json FROM memory_events WHERE user_id = ? AND object_id = ? AND event_type = 'merged'",
                (USER, "person_sarah-chen"),
            ).fetchone()
            self.assertIsNotNone(merged)
            self.assertEqual(json.loads(merged[0]).get("merged_from"), "person_sarah")

    def test_retroactive_backmerge_skips_ambiguous(self) -> None:
        # Two people could own "Sarah" -> ambiguous -> the single-token node must NOT be reclaimed.
        with connect(self.store.db_path) as conn:
            self._save_entity(conn, "person_sarah-chen", "Sarah Chen")
            self._save_entity(conn, "person_sarah-kim", "Sarah Kim")
            # Forward resolution won't merge "Sarah" into either (two leading candidates), so the
            # single-token node survives alongside both full names.
            single = self._save_entity(conn, "person_sarah", "Sarah")
            self.assertEqual(single["id"], "person_sarah")
            # Re-saving a fuller name must NOT reclaim the now-ambiguous single token.
            result = self._save_entity(conn, "person_sarah-chen", "Sarah Chen")
            self.assertFalse(result.get("_backmerge_remap"))
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM entities WHERE user_id = ? AND id = ?", (USER, "person_sarah")).fetchone()
            )

    def test_retroactive_backmerge_does_not_touch_other_people(self) -> None:
        with connect(self.store.db_path) as conn:
            self._save_entity(conn, "person_alex", "Alex")
            result = self._save_entity(conn, "person_sarah-chen", "Sarah Chen")
            self.assertFalse(result.get("_backmerge_remap"))
            self.assertIsNotNone(
                conn.execute("SELECT 1 FROM entities WHERE user_id = ? AND id = ?", (USER, "person_alex")).fetchone()
            )

    # ---- #20 extractor versioning + reprocess-on-change ----------------------

    def _materialize_capture(self) -> str:
        saved = self.store.save_capture(
            user_id=USER,
            content="Sarah Chen leads the Meridian project.",
            source="note",
            source_url=None,
            title="note",
            extracted={"records": [], "tasks": [], "entities": [], "summary": "", "_timestamp": TS},
            auto_approve=True,
        )
        return saved["capture"]["id"] if isinstance(saved.get("capture"), dict) else saved["capture_id"]

    def test_extractor_version_stamped_on_materialize(self) -> None:
        capture_id = self._materialize_capture()
        with connect(self.store.db_path) as conn:
            version = conn.execute(
                "SELECT extractor_version FROM capture_processing_state WHERE user_id = ? AND capture_id = ?",
                (USER, capture_id),
            ).fetchone()[0]
        self.assertEqual(version, EXTRACTOR_VERSION)

    def test_reprocess_enqueued_on_extractor_version_change(self) -> None:
        capture_id = self._materialize_capture()
        # Simulate a capture materialized by an OLDER extractor.
        with connect(self.store.db_path) as conn:
            conn.execute(
                "UPDATE capture_processing_state SET extractor_version = 'extract_v0' WHERE user_id = ? AND capture_id = ?",
                (USER, capture_id),
            )
            conn.commit()
        # A fresh store open reconciles: it must enqueue a re-extraction for the stale capture.
        CortexStore(self.db, self.root / "vault")
        with connect(self.store.db_path) as conn:
            job = conn.execute(
                "SELECT unique_key, job_type, status FROM memory_jobs "
                "WHERE user_id = ? AND object_id = ? AND unique_key LIKE 'reextract_capture:%'",
                (USER, capture_id),
            ).fetchone()
        self.assertIsNotNone(job)
        self.assertEqual(job["job_type"], "extract_capture")
        self.assertIn(EXTRACTOR_VERSION, job["unique_key"])

    def test_reprocess_noop_when_version_current(self) -> None:
        capture_id = self._materialize_capture()
        # Version already current -> a fresh store open must NOT enqueue a re-extraction.
        CortexStore(self.db, self.root / "vault")
        with connect(self.store.db_path) as conn:
            job = conn.execute(
                "SELECT 1 FROM memory_jobs WHERE user_id = ? AND object_id = ? AND unique_key LIKE 'reextract_capture:%'",
                (USER, capture_id),
            ).fetchone()
        self.assertIsNone(job)

    # ---- #21 semantic near-dup guards ---------------------------------------

    def test_claims_conflict_negation_polarity(self) -> None:
        self.assertTrue(_claims_conflict("The launch is approved", "The launch is not approved"))
        self.assertFalse(_claims_conflict("The launch is approved", "The launch has been approved"))

    def test_claims_conflict_number_mismatch(self) -> None:
        self.assertTrue(_claims_conflict("Budget is 50 thousand", "Budget is 80 thousand"))

    def test_cosine_similarity_bounds(self) -> None:
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
        self.assertAlmostEqual(_cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)
        self.assertEqual(_cosine_similarity([], [1.0]), 0.0)

    def test_semantic_collapse_inert_under_hash_embedder(self) -> None:
        # Under the deterministic hash embedder (the test default) semantic collapse never fires.
        with connect(self.store.db_path) as conn:
            self._seed_memory(conn, "mem_a", "The team ships on Friday", [])
            twin = self.store._find_semantic_near_duplicate(
                conn, USER, "cap_x", "mem_new", "claim", "semantic", "We deploy at the end of the week"
            )
        self.assertIsNone(twin)

    # ---- #23 orphan sweep ---------------------------------------------------

    def test_orphan_sweep_flags_uncapturable_memory(self) -> None:
        with connect(self.store.db_path) as conn:
            # Active memory with no entities AND no source_url AND no capture -> flagged for review.
            self._seed_memory(conn, "mem_orphan", "A floating unlinked fact", [])
            conn.commit()
        summary = self.store.sweep_orphan_memories(USER)
        self.assertEqual(summary["orphan_memories"], 1)
        self.assertEqual(summary["flagged_for_review"], 1)
        with connect(self.store.db_path) as conn:
            flagged = conn.execute(
                "SELECT 1 FROM memory_events WHERE user_id = ? AND object_id = ? AND event_type = 'orphan_flagged_for_review'",
                (USER, "mem_orphan"),
            ).fetchone()
        self.assertIsNotNone(flagged)

    def test_orphan_sweep_ignores_linked_or_cited(self) -> None:
        with connect(self.store.db_path) as conn:
            self._save_entity(conn, "person_x", "Xavier")
            self._seed_memory(conn, "mem_linked", "Xavier leads sales", ["person_x"])
            conn.execute(
                "INSERT INTO memories (id, user_id, kind, layer, content, source, source_url, captured_at, recorded_at) "
                "VALUES ('mem_cited', ?, 'claim', 'semantic', 'A cited fact', 'test', 'https://x/y', ?, ?)",
                (USER, TS, TS),
            )
            conn.commit()
        summary = self.store.sweep_orphan_memories(USER)
        self.assertEqual(summary["orphan_memories"], 0)

    # ---- #24 typed entity relationships -------------------------------------

    def test_typed_relationships_persisted_with_provenance(self) -> None:
        entities = [
            {"id": "person_sarah-chen", "name": "Sarah Chen", "aliases": []},
            {"id": "org_acme", "name": "Acme", "aliases": []},
        ]
        memories = [{"id": "mem_rel", "content": "Sarah Chen works at Acme."}]
        with connect(self.store.db_path) as conn:
            created = self.store._save_typed_entity_relationships(conn, USER, memories, entities, TS)
            self.assertEqual(len(created), 1)
            edge = conn.execute(
                "SELECT source_id, target_id, kind, evidence_id FROM graph_edges WHERE user_id = ? AND kind = 'works_at'",
                (USER,),
            ).fetchone()
        self.assertEqual(edge["source_id"], "person_sarah-chen")
        self.assertEqual(edge["target_id"], "org_acme")
        self.assertEqual(edge["evidence_id"], "mem_rel")  # the memory is the edge provenance

    def test_typed_relationships_skip_unresolvable_names(self) -> None:
        entities = [{"id": "person_sarah-chen", "name": "Sarah Chen", "aliases": []}]
        memories = [{"id": "mem_rel", "content": "Sarah Chen works at Globex."}]  # Globex is unknown
        with connect(self.store.db_path) as conn:
            created = self.store._save_typed_entity_relationships(conn, USER, memories, entities, TS)
        self.assertEqual(created, [])

    def test_summarize_typed_relationships_pure(self) -> None:
        edges = [
            {"source": "a", "target": "b", "relation": "works_at", "evidence": "m1", "weight": 1.0},
            {"source": "a", "target": "b", "relation": "works_at", "evidence": "m2", "weight": 1.0},
            {"source": "c", "target": "d", "relation": "reports_to", "evidence": "m3"},
            {"source": "x", "target": "x", "relation": "blocks"},  # self-loop dropped
            {"source": "e", "target": "f", "relation": "co_mention"},  # untyped dropped
        ]
        summary = summarize_typed_relationships(edges)
        self.assertEqual(list(summary.keys()), ["works_at", "reports_to"])
        works_at = summary["works_at"][0]
        self.assertEqual(works_at["source"], "a")
        self.assertEqual(works_at["weight"], 2.0)
        self.assertEqual(sorted(works_at["evidence"]), ["m1", "m2"])
        # Deterministic: identical input -> identical output.
        self.assertEqual(summarize_typed_relationships(edges), summary)

    # ---- #25 FTS tokenizer --------------------------------------------------

    def test_fts_uses_porter_tokenizer(self) -> None:
        with connect(self.store.db_path) as conn:
            sql = conn.execute("SELECT sql FROM sqlite_master WHERE name = 'memory_fts'").fetchone()[0]
            marker = conn.execute("SELECT tokenizer FROM fts_index_meta WHERE id = 1").fetchone()
        self.assertIn("porter", sql.lower())
        self.assertEqual(marker[0], "porter")


if __name__ == "__main__":
    unittest.main()
