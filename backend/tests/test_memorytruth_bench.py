"""Tests for the MemoryTruth-light benchmark harness (backend/bench/memorytruth.py).

Three properties make the bench trustworthy, and each gets a test class:

1. Determinism - the same seed must produce the identical scenario and, on the
   same code, the identical report. A benchmark with run-to-run variance at a
   fixed seed cannot be hill-climbed.
2. Honesty floors - on the current store, every category must hold its floor
   (1.0 on the reference seeds). A regression in retrieval, the citation gate,
   supersession, or the integrity chain shows up here as a floor break.
3. Non-tautology - the bench must actually be able to FAIL. We sabotage the
   store in targeted ways (confabulating gate, leaking supersession filter,
   tampered integrity event) and assert the matching category score drops.
   Without these, a "1.0" would prove nothing about the pipeline.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import CortexStore, connect
from backend.bench.memorytruth import (
    InProcessClient,
    generate_scenario,
    run_bench,
)


def _fresh_store(root: Path) -> CortexStore:
    init_db(root / "bench.db")
    store = CortexStore(root / "bench.db", root / "vault")
    store._vector_ready = lambda conn: False  # deterministic lexical retrieval
    return store


class ScenarioDeterminismTests(unittest.TestCase):
    def test_same_seed_same_scenario(self) -> None:
        a = generate_scenario(7)
        b = generate_scenario(7)
        self.assertEqual(a.to_json(), b.to_json())

    def test_different_seed_different_scenario(self) -> None:
        a = generate_scenario(7)
        b = generate_scenario(8)
        self.assertNotEqual(a.to_json(), b.to_json())

    def test_slugs_are_unique_within_scenario(self) -> None:
        scenario = generate_scenario(7)
        slugs = [fact["slug"] for fact in scenario.facts]
        slugs += [rev["v1_slug"] for rev in scenario.revisions]
        slugs += [rev["v2_slug"] for rev in scenario.revisions]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_abstention_projects_never_overlap_seeded_facts(self) -> None:
        scenario = generate_scenario(7)
        seeded_text = " ".join(fact["content"] for fact in scenario.facts)
        for probe in scenario.abstention_probes:
            # The probe names a project ("adjective-noun"); that compound must not
            # appear in any seeded fact, or the probe would not be a true unknown.
            project = probe.question.split(" for the ")[1].split(" project")[0]
            self.assertNotIn(project, seeded_text)

    def test_same_seed_same_report_on_same_code(self) -> None:
        reports = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                store = _fresh_store(Path(tmp))
                client = InProcessClient(store, "bench-user")
                report = run_bench(client, generate_scenario(11), mode="inprocess")
                report.pop("duration_seconds")
                reports.append(report)
        self.assertEqual(reports[0], reports[1])


class HonestyFloorTests(unittest.TestCase):
    """The current store must hold a perfect score on the reference seeds.

    These floors are the hill-climb objective from docs/MEMORY_MOONSHOTS.md M1.
    If a change to retrieval/citation/supersession/integrity breaks one, this
    test names the category instead of letting the regression ship silently.
    """

    def _run(self, seed: int) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            return run_bench(client, generate_scenario(seed), mode="inprocess")

    def test_reference_seed_holds_all_floors(self) -> None:
        report = self._run(7)
        for category, payload in report["categories"].items():
            self.assertEqual(
                payload["score"],
                1.0,
                msg=f"{category} broke its floor: {payload['failures']}",
            )
        self.assertEqual(report["overall"], 1.0)

    def test_second_seed_holds_all_floors(self) -> None:
        report = self._run(42)
        for category, payload in report["categories"].items():
            self.assertEqual(
                payload["score"],
                1.0,
                msg=f"{category} broke its floor: {payload['failures']}",
            )


class NonTautologyTests(unittest.TestCase):
    """Sabotage the store; the matching category must drop. A bench that cannot
    fail is a tautology, which is exactly the LoCoMo failure mode M1 exists to
    avoid."""

    def test_confabulating_gate_tanks_abstention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            # Sabotage: the citation gate accepts any candidate (always relevant).
            store._primary_citation_is_relevant = lambda *args, **kwargs: True
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["abstention"]["score"],
            1.0,
            msg="bench failed to detect a confabulating citation gate",
        )

    def test_broken_supersession_tanks_temporal(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            # Sabotage: resolving a conflict no longer records supersession.
            store.resolve_conflict = lambda user_id, *, stale_id, current_id: True
            client = InProcessClient(store, "bench-user")
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertLess(
            report["categories"]["temporal"]["score"],
            1.0,
            msg="bench failed to detect broken supersession (stale fact leaks)",
        )

    def test_tampered_event_log_tanks_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            scenario = generate_scenario(7)

            original_verify = store.verify_integrity

            def tampered_verify(user_id: str, expected_head: str) -> dict:
                # Sabotage BETWEEN pin and verify: the bench pinned the chain head
                # via integrity_digest; now silently rewrite one historical event
                # the way an attacker (or corrupting bug) would, then let the real
                # verification run. Tamper-evidence means exactly this ordering is
                # caught: matches must come back False.
                with connect(store.db_path) as conn:
                    conn.execute(
                        "UPDATE memory_events SET metadata_json = '{\"forged\": true}' "
                        "WHERE id = (SELECT id FROM memory_events WHERE user_id = ? LIMIT 1)",
                        (user_id,),
                    )
                return original_verify(user_id, expected_head)

            store.verify_integrity = tampered_verify
            report = run_bench(client, scenario, mode="inprocess")
        self.assertLess(
            report["categories"]["provenance"]["score"],
            1.0,
            msg="bench failed to detect a tampered integrity chain",
        )

    def test_dead_retrieval_tanks_recall(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = _fresh_store(Path(tmp))
            client = InProcessClient(store, "bench-user")
            original_answer = store.answer_query

            def broken_answer(user_id: str, query: str, *args, **kwargs) -> dict:
                result = original_answer(user_id, query, *args, **kwargs)
                # Sabotage: retrieval returns nothing citable.
                result["citations"] = []
                result["status"] = "no_cited_evidence"
                result["results"] = []
                return result

            store.answer_query = broken_answer
            report = run_bench(client, generate_scenario(7), mode="inprocess")
        self.assertEqual(
            report["categories"]["recall"]["score"],
            0.0,
            msg="bench failed to detect dead retrieval",
        )


if __name__ == "__main__":
    unittest.main()
