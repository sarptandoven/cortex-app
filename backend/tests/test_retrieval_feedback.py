from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore

USER = "feedback-user"


def _store() -> CortexStore:
    tmp = Path(tempfile.mkdtemp())
    init_db(tmp / "c.db")
    store = CortexStore(tmp / "c.db", tmp / "v")
    store._db_path_for_test = tmp / "c.db"  # type: ignore[attr-defined]
    # Keyword-overlapping memories so a query retrieves several candidates (some cited, some not)
    # even under the hash embedder — that's what produces feedback pairs.
    for content in [
        "I decided to use Postgres for the billing database because of its reliability.",
        "The billing database migration is scheduled for the third quarter.",
        "Billing database backups run nightly to cold storage.",
        "The billing database access is restricted to the finance team.",
        "We reviewed the billing database schema last month.",
    ]:
        store.save_capture(
            user_id=USER, content=content, source="notes",
            source_url=f"cortex-source://notes#{abs(hash(content)) % 9999}", title=None,
            extracted=extract_context(content, "notes"), cite_capture_provenance=True, auto_approve=True,
        )
    for _ in range(10):
        if not store.run_due_jobs(USER, limit=100, schedule_source_syncs=False).get("pending"):
            break
    return store


class RetrievalFeedbackTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()
        self.db = self.store._db_path_for_test  # type: ignore[attr-defined]

    def _feedback_rows(self):
        conn = sqlite3.connect(str(self.db))
        try:
            return conn.execute("SELECT metadata_json FROM memory_events WHERE event_type = 'retrieval_feedback'").fetchall()
        finally:
            conn.close()

    def test_answer_query_logs_feedback_pairs(self):
        result = self.store.answer_query(USER, "what about the billing database?", limit=2)
        self.assertTrue(result.get("citations"), "expected a cited answer to seed feedback")
        rows = self._feedback_rows()
        self.assertGreater(len(rows), 0, "answer_query should log retrieval_feedback events")
        import json
        meta = json.loads(rows[0][0])
        self.assertIn("used_features", meta)
        self.assertIn("skipped_features", meta)
        for feat in [meta["used_features"], *meta["skipped_features"]]:
            self.assertEqual(set(feat.keys()), {"sem", "rank", "ent"})

    def test_learner_reads_logged_pairs(self):
        self.store.answer_query(USER, "what about the billing database?", limit=2)
        self.store.answer_query(USER, "tell me about the billing database backups", limit=2)
        from scripts.learn_rerank_weights import load_pairs_from_events

        pairs = load_pairs_from_events(self.db)
        self.assertGreater(len(pairs), 0, "the learner should build pairs from logged feedback")
        for pos, neg in pairs:
            self.assertIn("rank", pos)
            self.assertIn("rank", neg)

    def test_feedback_disabled_by_flag(self):
        import os

        prev = os.environ.get("CORTEX_RETRIEVAL_FEEDBACK")
        os.environ["CORTEX_RETRIEVAL_FEEDBACK"] = "off"
        try:
            store = _store()
            store.answer_query(USER, "what database did I choose for billing?")
            conn = sqlite3.connect(str(store._db_path_for_test))  # type: ignore[attr-defined]
            try:
                rows = conn.execute("SELECT COUNT(*) FROM memory_events WHERE event_type = 'retrieval_feedback'").fetchone()
            finally:
                conn.close()
            self.assertEqual(rows[0], 0)
        finally:
            if prev is None:
                os.environ.pop("CORTEX_RETRIEVAL_FEEDBACK", None)
            else:
                os.environ["CORTEX_RETRIEVAL_FEEDBACK"] = prev


if __name__ == "__main__":
    unittest.main()
