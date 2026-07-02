from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore


# Deterministic, no-API-key retrieval safety corpus.
#
# Every test seeds memories through CortexStore and asserts a HARD invariant of
# the deterministic retrieval path (no embeddings). The fixtures are adversarial
# on purpose: shared vocabulary, plausible distractors, and active relationship
# expansion, so a passing test reflects real isolation rather than the absence of
# collisions.
#
# The abstention copy Cortex emits when it finds no source-backed evidence. This
# is asserted verbatim so a silent regression in the abstention path is caught.
NO_CITED_EVIDENCE_ANSWER = (
    "Cortex did not find a cited item for this question yet. "
    "Import or approve more source material, then ask again."
)


class RetrievalSafetyBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "retrieval-safety.sqlite"
        self.vault_path = Path(self.tmp.name) / "Cortex.vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)
        self.user_id = "retrieval-safety-test"
        # Deterministic path only: force the lexical/FTS retrieval path so no test
        # accidentally depends on an embedding backend being present.
        self.store._vector_ready = lambda conn: False
        self.store.update_settings(
            self.user_id,
            {"review_new_captures": False, "allow_pending_in_context": True},
        )

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _save(
        self,
        *,
        source: str,
        source_url: str | None,
        title: str,
        records: list[dict[str, object]],
        timestamp: str = "2026-07-01T12:00:00Z",
        entities: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        content = "\n".join(str(record["content"]) for record in records)
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=source_url,
            title=title,
            extracted={
                "_timestamp": timestamp,
                "summary": title,
                "records": [
                    {
                        "summary": str(record.get("summary") or record["content"]),
                        "confidence": "confirmed",
                        "importance": 4,
                        "entity_ids": [],
                        **record,
                    }
                    for record in records
                ],
                "tasks": [],
                "entities": entities or [],
            },
        )


class AbstentionSafetyTests(RetrievalSafetyBase):
    def test_ask_abstains_with_no_cited_evidence_when_only_uncited_match_exists(self) -> None:
        # Adversarial: the ONLY memory that matches the query is a strong lexical
        # match, high importance, and confirmed -- but it has no source_url. A
        # naive ranker would happily "answer" from it. Cortex must abstain because
        # there is no cited evidence, and must not fabricate a citation.
        self._save(
            source="scratchpad",
            source_url=None,
            title="Uncited scratch note",
            records=[
                {
                    "id": "safety_abstain_uncited_only",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": (
                        "Project Quokka data-retention owner is Priya according to an "
                        "uncited scratch note with no source link."
                    ),
                    "importance": 5,
                }
            ],
        )

        query = "who is the Project Quokka data-retention owner"
        answer = self.store.answer_query(self.user_id, query, limit=5)

        # The uncited match is allowed to appear in raw results...
        result_ids = [item["id"] for item in answer.get("results") or []]
        self.assertIn("safety_abstain_uncited_only", result_ids)
        # ...but it must NOT be promoted to a citation, and the answer abstains.
        self.assertEqual(answer["status"], "no_cited_evidence")
        self.assertEqual(answer.get("citations") or [], [])
        self.assertEqual(answer["answer"], NO_CITED_EVIDENCE_ANSWER)

    def test_ask_abstains_when_nothing_matches_the_query_at_all(self) -> None:
        # A completely unrelated, source-backed memory is present. A query about a
        # different topic must not surface it as evidence.
        self._save(
            source="github",
            source_url="cortex-source://github#service=github&file=issues.json&line=3&excerpt=unrelated",
            title="Unrelated cited memory",
            records=[
                {
                    "id": "safety_abstain_unrelated_cited",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Walrus billing cadence is monthly per the signed contract issue.",
                    "importance": 4,
                }
            ],
        )

        answer = self.store.answer_query(
            self.user_id,
            "what is the escalation runbook for the Zephyr incident bridge",
            limit=5,
        )

        citation_ids = [citation["id"] for citation in answer.get("citations") or []]
        self.assertNotIn("safety_abstain_unrelated_cited", citation_ids)
        self.assertEqual(answer["status"], "no_cited_evidence")
        self.assertEqual(answer.get("citations") or [], [])
        self.assertEqual(answer["answer"], NO_CITED_EVIDENCE_ANSWER)

    def test_every_ask_citation_is_source_backed(self) -> None:
        # Mix a cited and an uncited memory for the SAME claim. The uncited one is
        # higher importance (a distractor). Cortex must cite only the source-backed
        # record and never emit a citation without a source_url.
        self._save(
            source="notepad",
            source_url=None,
            title="Uncited higher-importance distractor",
            records=[
                {
                    "id": "safety_citation_uncited",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Narwhal launch owner is Dana in an uncited note.",
                    "importance": 5,
                }
            ],
        )
        self._save(
            source="notion",
            source_url="cortex-source://notion#service=notion&page=narwhal&line=7&excerpt=narwhal-owner",
            title="Cited canonical owner record",
            records=[
                {
                    "id": "safety_citation_cited",
                    "kind": "claim",
                    "layer": "semantic",
                    "content": "Project Narwhal launch owner is Mira per the canonical Notion page.",
                    "importance": 1,
                }
            ],
        )

        answer = self.store.answer_query(self.user_id, "Project Narwhal launch owner", limit=4)

        self.assertEqual(answer["status"], "cited")
        citations = answer.get("citations") or []
        self.assertTrue(citations)
        self.assertEqual(citations[0]["id"], "safety_citation_cited")
        # HARD invariant: no citation without a source_url.
        self.assertTrue(all(citation.get("source_url") for citation in citations))
        self.assertNotIn("safety_citation_uncited", {citation["id"] for citation in citations})


class TemporalSupersessionSafetyTests(RetrievalSafetyBase):
    def test_supersession_chain_v3_ranks_first_and_v1_v2_are_hidden(self) -> None:
        # A three-version supersession chain for the SAME fact. v1 and v2 are marked
        # superseded_by their successor; v3 is the live version. All three share the
        # same distinctive vocabulary, so a lexical ranker without supersession
        # handling would happily return the stale versions. Cortex must show only v3
        # and fully hide v1/v2 from search.
        self._save(
            source="notion",
            source_url="cortex-source://notion#service=notion&page=quetzal-policy&line=1&excerpt=quetzal-v1v2v3",
            title="Project Quetzal refund policy history",
            records=[
                {
                    "id": "safety_supersede_v1",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Quetzal refund policy grants 7-day refunds (version one, original).",
                    "importance": 5,
                    "superseded_by": "safety_supersede_v2",
                },
                {
                    "id": "safety_supersede_v2",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Quetzal refund policy grants 14-day refunds (version two, interim).",
                    "importance": 5,
                    "superseded_by": "safety_supersede_v3",
                },
                {
                    "id": "safety_supersede_v3",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Quetzal refund policy grants 30-day refunds (version three, current).",
                    "importance": 3,
                },
            ],
        )

        results = self.store.search(self.user_id, "Project Quetzal refund policy", limit=10)
        result_ids = [item["id"] for item in results]

        # The current version ranks #1...
        self.assertTrue(result_ids)
        self.assertEqual(result_ids[0], "safety_supersede_v3")
        # ...and the superseded versions are fully hidden, not merely deprioritized.
        self.assertNotIn("safety_supersede_v1", result_ids)
        self.assertNotIn("safety_supersede_v2", result_ids)
        # Ask agrees and cites only the live version.
        answer = self.store.answer_query(self.user_id, "Project Quetzal refund policy", limit=5)
        citation_ids = [citation["id"] for citation in answer.get("citations") or []]
        self.assertEqual(answer["status"], "cited")
        self.assertEqual(citation_ids[0], "safety_supersede_v3")
        self.assertNotIn("safety_supersede_v1", citation_ids)
        self.assertNotIn("safety_supersede_v2", citation_ids)

    def test_expired_and_future_validity_windows_are_excluded_but_current_survives(self) -> None:
        # Three records for the same fact: one expired (valid_to in the past), one
        # not-yet-valid (valid_from in the future), and one currently valid. Only the
        # currently-valid record may surface. The expired record is the highest
        # importance, so this catches importance overriding validity.
        self._save(
            source="notion",
            source_url="cortex-source://notion#service=notion&page=okapi-tier&line=1&excerpt=okapi-validity",
            title="Project Okapi support tier validity",
            records=[
                {
                    "id": "safety_validity_expired",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Okapi support tier is platinum (expired policy).",
                    "importance": 5,
                    "valid_to": "2020-01-01T00:00:00+00:00",
                },
                {
                    "id": "safety_validity_future",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Okapi support tier is diamond (future policy).",
                    "importance": 5,
                    "valid_from": "2999-01-01T00:00:00+00:00",
                },
                {
                    "id": "safety_validity_current",
                    "kind": "decision",
                    "layer": "decision",
                    "content": "Project Okapi support tier is gold (current policy).",
                    "importance": 3,
                    "valid_from": "2000-01-01T00:00:00+00:00",
                },
            ],
        )

        results = self.store.search(self.user_id, "Project Okapi support tier", limit=10)
        result_ids = [item["id"] for item in results]

        self.assertIn("safety_validity_current", result_ids)
        self.assertNotIn("safety_validity_expired", result_ids)
        self.assertNotIn("safety_validity_future", result_ids)
        self.assertEqual(result_ids[0], "safety_validity_current")


class CrossProjectNoLeakSafetyTests(RetrievalSafetyBase):
    # Two projects that deliberately share vocabulary ("beta invite", "launch",
    # "onboarding", "outreach") but have DISTINCT private facts and DISTINCT
    # entities. Unscoped search returns everything the user owns by design, so the
    # invariant under test is: a query rooted in Project A's distinctive private
    # detail must not surface Project B's private detail, and relationship
    # expansion (shared_entity) rooted in A must not drag Project B in.

    def _seed_two_projects_sharing_vocabulary(self) -> None:
        # Project Halcyon: two memories that share the Halcyon entity, so a real
        # intra-project shared_entity relation forms (relationship expansion has
        # something legitimate to follow). The private detail is a unique token
        # ("kestrel-passphrase-A7") that only appears in Halcyon.
        self._save(
            source="obsidian",
            source_url="local-file://Halcyon/Beta.md#line=3&excerpt=halcyon-beta",
            title="Project Halcyon beta note",
            records=[
                {
                    "id": "leak_halcyon_decision",
                    "kind": "decision",
                    "layer": "decision",
                    "content": (
                        "Project Halcyon beta invite outreach decision: gate onboarding "
                        "behind the kestrel-passphrase-A7 private launch code."
                    ),
                    "importance": 5,
                    "sector": "Project Halcyon",
                    "topics": ["beta", "invite", "onboarding", "launch", "outreach"],
                    "entity_ids": ["ent_project_halcyon"],
                },
                {
                    "id": "leak_halcyon_procedure",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": (
                        "Project Halcyon beta invite outreach procedure: verify the "
                        "kestrel-passphrase-A7 code before sending onboarding launch invites."
                    ),
                    "importance": 4,
                    "sector": "Project Halcyon",
                    "topics": ["beta", "invite", "onboarding", "launch", "outreach"],
                    "entity_ids": ["ent_project_halcyon"],
                },
            ],
            entities=[
                {"id": "ent_project_halcyon", "kind": "project", "name": "Project Halcyon", "aliases": ["Halcyon"]}
            ],
        )
        # Project Zephyr: shares all the generic vocabulary/topics but is a separate
        # capture with a DISTINCT entity and its own unique private token
        # ("wolverine-passphrase-Z9"). No entity is shared with Halcyon, so no
        # cross-capture relation should ever link the two projects.
        self._save(
            source="slack",
            source_url="https://slack.example.com/archives/CZEPHYR/p1782912000",
            title="Project Zephyr beta note",
            records=[
                {
                    "id": "leak_zephyr_decision",
                    "kind": "decision",
                    "layer": "decision",
                    "content": (
                        "Project Zephyr beta invite outreach decision: gate onboarding "
                        "behind the wolverine-passphrase-Z9 private launch code."
                    ),
                    "importance": 5,
                    "sector": "Project Zephyr",
                    "topics": ["beta", "invite", "onboarding", "launch", "outreach"],
                    "entity_ids": ["ent_project_zephyr"],
                },
                {
                    "id": "leak_zephyr_procedure",
                    "kind": "procedure",
                    "layer": "procedural",
                    "content": (
                        "Project Zephyr beta invite outreach procedure: verify the "
                        "wolverine-passphrase-Z9 code before sending onboarding launch invites."
                    ),
                    "importance": 4,
                    "sector": "Project Zephyr",
                    "topics": ["beta", "invite", "onboarding", "launch", "outreach"],
                    "entity_ids": ["ent_project_zephyr"],
                },
            ],
            entities=[
                {"id": "ent_project_zephyr", "kind": "project", "name": "Project Zephyr", "aliases": ["Zephyr"]}
            ],
        )

    def test_unscoped_query_rooted_in_project_a_does_not_surface_project_b_private_detail(self) -> None:
        self._seed_two_projects_sharing_vocabulary()

        # Query rooted in Halcyon's distinctive private token. Unscoped (sector=None),
        # with relationship expansion turned ON to make the adversarial surface real.
        results = self.store.search(
            self.user_id,
            "Halcyon kestrel-passphrase-A7 beta invite onboarding launch outreach",
            limit=10,
            include_related=True,
        )
        result_ids = [item["id"] for item in results]

        # Halcyon's own memories are retrievable...
        self.assertIn("leak_halcyon_decision", result_ids)
        # ...and no Project Zephyr private memory leaks into the result set.
        self.assertNotIn("leak_zephyr_decision", result_ids)
        self.assertNotIn("leak_zephyr_procedure", result_ids)

        # No result may carry a cross-project relationship (a shared_* edge whose
        # related_to_id points at a memory in a different sector).
        by_id = {item["id"]: item for item in results}
        for item in results:
            relationship = item.get("relationship")
            if not isinstance(relationship, dict) or not relationship:
                continue
            related_to_id = str(relationship.get("related_to_id") or "")
            primary = by_id.get(related_to_id)
            if primary is None:
                continue
            self.assertEqual(
                str(item.get("sector") or ""),
                str(primary.get("sector") or ""),
                msg=f"cross-project relationship leaked: {item['id']} -> {related_to_id}",
            )

        # The Zephyr private token must appear nowhere in the serialized results.
        self.assertNotIn("wolverine-passphrase-Z9", json.dumps(results, default=str))

    def test_ask_scoped_to_project_a_never_cites_project_b(self) -> None:
        self._seed_two_projects_sharing_vocabulary()

        answer = self.store.answer_query(
            self.user_id,
            "what is the Project Halcyon beta invite onboarding launch decision",
            limit=6,
            sector="Project Halcyon",
        )

        citation_ids = [citation["id"] for citation in answer.get("citations") or []]
        result_ids = [item["id"] for item in answer.get("results") or []]
        self.assertIn("leak_halcyon_decision", citation_ids)
        for zephyr_id in ("leak_zephyr_decision", "leak_zephyr_procedure"):
            self.assertNotIn(zephyr_id, citation_ids)
            self.assertNotIn(zephyr_id, result_ids)
        # No Zephyr private token anywhere in the Ask payload.
        self.assertNotIn("wolverine-passphrase-Z9", json.dumps(answer, default=str))

    def test_no_cross_project_relationship_is_created_between_separate_captures(self) -> None:
        # Structural guard on the relationship graph itself: two projects that share
        # vocabulary/topics but no entity must not be linked. Cross-capture relations
        # only form on shared entities, so the two sectors must stay disconnected.
        self._seed_two_projects_sharing_vocabulary()

        # Expand relations from a Zephyr root; any related memory must stay in Zephyr.
        results = self.store.search(
            self.user_id,
            "Project Zephyr wolverine-passphrase-Z9 beta invite onboarding launch",
            limit=10,
            include_related=True,
        )
        for item in results:
            relationship = item.get("relationship")
            if isinstance(relationship, dict) and relationship:
                # A related memory surfaced -- it must belong to Zephyr, never Halcyon.
                self.assertEqual(
                    str(item.get("sector") or ""),
                    "Project Zephyr",
                    msg=f"Zephyr relationship expansion leaked a non-Zephyr memory: {item['id']}",
                )
        self.assertNotIn("kestrel-passphrase-A7", json.dumps(results, default=str))


if __name__ == "__main__":
    unittest.main()
