"""Tests for the Mirror Moment (strategy P0-B): backend.app.mirror.

These are deliberately hermetic. We build a real app database with
``backend.app.database.init_db`` and insert memories *directly* over the
sqlite3 connection returned by ``backend.app.database.connect`` — no embeddings,
no CortexStore, no network. That keeps the tests fast and immune to the
concurrent, half-edited embeddings module.

We assert the product-visible contract of ``compute_mirror_insight``:

  (a) a clear repeated pattern -> a cited insight naming the source + count;
  (b) a thin / empty corpus -> None (honest abstention);
  (c) determinism -> identical input yields identical output; and
  (d) the headline is non-empty and evidence.count matches the supporting memories.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import connect, init_db
from backend.app.mirror import compute_mirror_insight


class MirrorInsightTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self._tmp.name) / "cortex.db"
        init_db(self.db_path)
        self.user_id = "sam"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # --- helpers ------------------------------------------------------------

    def _insert_memory(
        self,
        conn,
        *,
        memory_id: str,
        content: str,
        layer: str,
        kind: str,
        source: str,
        summary: str | None = None,
        importance: int = 3,
        status: str = "active",
        user_id: str | None = None,
        topics: list[str] | None = None,
    ) -> None:
        uid = user_id or self.user_id
        conn.execute(
            """
            INSERT INTO memories
                (id, user_id, kind, layer, content, summary, source, confidence,
                 importance, status, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'confirmed', ?, ?, '2026-07-02T00:00:00Z')
            """,
            (memory_id, uid, kind, layer, content, summary, source, importance, status),
        )
        for topic in topics or []:
            conn.execute(
                """
                INSERT OR REPLACE INTO memory_topics(memory_id, topic, user_id, created_at)
                VALUES (?, ?, ?, '2026-07-02T00:00:00Z')
                """,
                (memory_id, topic, uid),
            )

    def _seed_repeated_calendar_preference(self, conn) -> list[str]:
        """Six calendar-sourced preference memories all pointing the same way,
        plus a little unrelated noise so the pattern must actually be *selected*,
        not just returned by default."""
        ids: list[str] = []
        for i in range(6):
            mid = f"mem_pref_{i}"
            ids.append(mid)
            self._insert_memory(
                conn,
                memory_id=mid,
                content="Sam prefers to decline meetings scheduled before 10am.",
                summary="prefers to decline meetings before 10am",
                layer="preference",
                kind="preference",
                source="calendar",
                importance=3 + (i % 2),
                topics=["meetings", "mornings"],
            )
        # Unrelated single facts from another source — not a pattern.
        self._insert_memory(
            conn,
            memory_id="mem_fact_role",
            content="Sam is a staff backend engineer.",
            layer="semantic",
            kind="claim",
            source="obsidian",
            importance=4,
            topics=["role"],
        )
        self._insert_memory(
            conn,
            memory_id="mem_fact_project",
            content="Sam is leading Project Zephyr.",
            layer="semantic",
            kind="claim",
            source="obsidian",
            importance=4,
            topics=["zephyr"],
        )
        return ids

    # --- (a) clear pattern -> cited insight --------------------------------

    def test_repeated_pattern_returns_cited_insight(self) -> None:
        with connect(self.db_path) as conn:
            pref_ids = self._seed_repeated_calendar_preference(conn)
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)

        self.assertIsNotNone(insight, "a strong repeated pattern must produce an insight")
        assert insight is not None  # for type-checkers

        # It names the source and a count.
        evidence = insight["evidence"]
        self.assertEqual(evidence["source"], "calendar")
        self.assertEqual(evidence["count"], 6)

        # The cited memory ids are exactly the supporting preference memories.
        self.assertEqual(set(evidence["memory_ids"]), set(pref_ids))

        # It favored the personal preference layer over the raw semantic facts.
        self.assertEqual(insight["layer"], "preference")
        self.assertEqual(insight["confidence"], "high")  # >= 5 supporting -> high

        # Headline is a real, user-framed sentence citing the source.
        headline = insight["headline"]
        self.assertTrue(headline)
        self.assertIn("calendar", headline.lower())
        # Non-empty verbatim example drawn from the user's content.
        self.assertTrue(evidence["example"])

    def test_source_scoping_only_cites_the_matching_source(self) -> None:
        """A preference pattern must not borrow support from other sources: the
        count and cited ids stay confined to the source that actually repeated."""
        with connect(self.db_path) as conn:
            for i in range(4):
                self._insert_memory(
                    conn,
                    memory_id=f"gh_pref_{i}",
                    content="Sam prefers small, focused pull requests.",
                    summary="prefers small focused pull requests",
                    layer="preference",
                    kind="preference",
                    source="github",
                )
            # A lone, unrelated preference from a different source — below floor.
            self._insert_memory(
                conn,
                memory_id="cal_pref_lonely",
                content="Sam prefers afternoon focus blocks.",
                layer="preference",
                kind="preference",
                source="calendar",
            )
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)

        self.assertIsNotNone(insight)
        assert insight is not None
        # evidence.source is now a user-facing display name (never a raw connector id) — "github"
        # surfaces as "GitHub" so the card reads "From your GitHub".
        self.assertEqual(insight["evidence"]["source"], "GitHub")
        self.assertEqual(insight["evidence"]["count"], 4)
        self.assertNotIn("cal_pref_lonely", insight["evidence"]["memory_ids"])

    def test_topic_fallback_when_no_personal_layer_clears_floor(self) -> None:
        """With no repeated personal layer but a strongly repeated topic from one
        source, we fall back to the topic cluster rather than abstaining."""
        with connect(self.db_path) as conn:
            for i in range(5):
                self._insert_memory(
                    conn,
                    memory_id=f"mem_topic_{i}",
                    content=f"Sam wrote a note about distributed systems, part {i}.",
                    layer="semantic",
                    kind="claim",
                    source="obsidian",
                    topics=["distributed systems"],
                )
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)

        self.assertIsNotNone(insight)
        assert insight is not None
        # "obsidian" must never surface to a user; evidence.source maps to the display noun "notes".
        self.assertEqual(insight["evidence"]["source"], "notes")
        self.assertEqual(insight["evidence"]["count"], 5)
        self.assertIn("distributed systems", insight["headline"].lower())

    def test_personal_layer_beats_topic_signal_on_ties(self) -> None:
        """When both a topic cluster and a preference cluster clear the floor, the
        preference (a "knowing me" layer) must win."""
        with connect(self.db_path) as conn:
            # Preference cluster (3 = exactly the floor).
            for i in range(3):
                self._insert_memory(
                    conn,
                    memory_id=f"pref_{i}",
                    content="Sam prefers async standups.",
                    summary="prefers async standups",
                    layer="preference",
                    kind="preference",
                    source="obsidian",
                    topics=["work habits"],
                )
            # A larger topic-only cluster on plain facts.
            for i in range(5):
                self._insert_memory(
                    conn,
                    memory_id=f"fact_{i}",
                    content=f"Sam noted fact {i} about scaling.",
                    layer="semantic",
                    kind="claim",
                    source="obsidian",
                    topics=["scaling"],
                )
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)

        self.assertIsNotNone(insight)
        assert insight is not None
        self.assertEqual(insight["layer"], "preference")
        self.assertEqual(insight["evidence"]["count"], 3)

    # --- (b) thin / empty corpus -> abstain --------------------------------

    def test_empty_corpus_abstains(self) -> None:
        with connect(self.db_path) as conn:
            insight = compute_mirror_insight(conn, self.user_id)
        self.assertIsNone(insight, "empty corpus must abstain")

    def test_below_total_minimum_abstains(self) -> None:
        with connect(self.db_path) as conn:
            # Fewer than MIN_TOTAL_MEMORIES, even if they'd otherwise agree.
            for i in range(3):
                self._insert_memory(
                    conn,
                    memory_id=f"tiny_{i}",
                    content="Sam prefers to decline early meetings.",
                    layer="preference",
                    kind="preference",
                    source="calendar",
                )
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)
        self.assertIsNone(insight, "a corpus below the total minimum must abstain")

    def test_no_pattern_clears_support_floor_abstains(self) -> None:
        """Enough memories overall, but each source/layer/topic appears only once
        or twice — no real repetition — so we must say nothing."""
        with connect(self.db_path) as conn:
            self._insert_memory(conn, memory_id="a", content="Sam likes coffee.", layer="preference", kind="preference", source="obsidian", topics=["coffee"])
            self._insert_memory(conn, memory_id="b", content="Sam likes tea.", layer="preference", kind="preference", source="calendar", topics=["tea"])
            self._insert_memory(conn, memory_id="c", content="Sam decided to use SQLite.", layer="decision", kind="decision", source="github", topics=["db"])
            self._insert_memory(conn, memory_id="d", content="Sam is an engineer.", layer="semantic", kind="claim", source="email", topics=["role"])
            self._insert_memory(conn, memory_id="e", content="Sam went to Lisbon.", layer="episodic", kind="event", source="calendar", topics=["travel"])
            self._insert_memory(conn, memory_id="f", content="Sam mentors juniors.", layer="semantic", kind="claim", source="obsidian", topics=["mentoring"])
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)
        self.assertIsNone(insight, "no pattern clears the support floor -> abstain")

    def test_inactive_memories_do_not_form_a_pattern(self) -> None:
        """Archived/deleted memories must never contribute to an insight."""
        with connect(self.db_path) as conn:
            for i in range(6):
                self._insert_memory(
                    conn,
                    memory_id=f"arch_{i}",
                    content="Sam prefers to decline early meetings.",
                    layer="preference",
                    kind="preference",
                    source="calendar",
                    status="archived",
                )
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)
        self.assertIsNone(insight, "inactive memories must not count toward a pattern")

    def test_other_users_memories_are_ignored(self) -> None:
        with connect(self.db_path) as conn:
            for i in range(6):
                self._insert_memory(
                    conn,
                    memory_id=f"other_{i}",
                    content="Alex prefers to decline early meetings.",
                    layer="preference",
                    kind="preference",
                    source="calendar",
                    user_id="alex",
                )
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)
        self.assertIsNone(insight, "another user's memories must not leak into this user's insight")

    # --- (c) determinism ----------------------------------------------------

    def test_deterministic_same_input_same_output(self) -> None:
        with connect(self.db_path) as conn:
            self._seed_repeated_calendar_preference(conn)
            conn.commit()
            first = compute_mirror_insight(conn, self.user_id)
            second = compute_mirror_insight(conn, self.user_id)
        self.assertEqual(first, second, "identical corpus must yield identical insight")

    def test_deterministic_across_fresh_dbs(self) -> None:
        """The same logical corpus, seeded into two independent databases, yields
        the identical insight — determinism does not depend on rowid/insert order
        luck."""
        results = []
        for _ in range(2):
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "cortex.db"
                init_db(path)
                with connect(path) as conn:
                    self._seed_repeated_calendar_preference(conn)
                    conn.commit()
                    results.append(compute_mirror_insight(conn, self.user_id))
        self.assertIsNotNone(results[0])
        self.assertEqual(results[0], results[1])

    # --- (d) headline non-empty & count matches supporting memories --------

    def test_headline_nonempty_and_count_matches_supporting_memories(self) -> None:
        with connect(self.db_path) as conn:
            pref_ids = self._seed_repeated_calendar_preference(conn)
            conn.commit()
            insight = compute_mirror_insight(conn, self.user_id)

        self.assertIsNotNone(insight)
        assert insight is not None
        # Headline is a non-empty string.
        self.assertIsInstance(insight["headline"], str)
        self.assertTrue(insight["headline"].strip())
        # evidence.count is an int equal to the number of cited memory ids, and
        # equal to the true number of supporting memories.
        evidence = insight["evidence"]
        self.assertIsInstance(evidence["count"], int)
        self.assertEqual(evidence["count"], len(evidence["memory_ids"]))
        self.assertEqual(evidence["count"], len(pref_ids))


if __name__ == "__main__":
    unittest.main()
