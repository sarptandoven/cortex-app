from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.database import init_db
from backend.app.extractor import extract_context
from backend.app.storage import CortexStore


class CaptureProvenanceCitationTests(unittest.TestCase):
    """Regression for the core capture->ask loop: a fact the user deliberately captured in the
    app (no external source_url) must be citable by Ask once approved. Before this fix every
    manual capture had source_url=None, `_has_source_citation` rejected it, and Ask false-
    abstained ('import more source material') on questions it had the approved answer to."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        self.store._vector_ready = lambda conn: False
        self.user_id = "capture-cite-user"
        # Auto-approve so the test focuses on citation eligibility, not the review queue.
        self.store.update_settings(self.user_id, {"review_new_captures": False})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _capture(self, content: str, *, cite: bool, source: str = "macos") -> dict:
        extracted = extract_context(content, source)
        return self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source=source,
            source_url=None,
            title="",
            extracted=extracted,
            cite_capture_provenance=cite,
        )

    def test_manual_capture_is_cited_by_ask(self) -> None:
        saved = self._capture(
            "We decided to use PostgreSQL for the Atlas project because of jsonb support.",
            cite=True,
        )
        capture_id = saved["capture_id"]
        # The capture row itself is the provenance.
        self.assertTrue(
            any(str(m.get("source_url") or "").startswith("cortex-capture://") for m in saved["memories"]),
            saved["memories"],
        )
        answer = self.store.answer_query(self.user_id, "which database did we decide to use for Atlas", limit=5)
        self.assertEqual(answer["status"], "cited", answer)
        citations = answer.get("citations") or []
        self.assertTrue(citations)
        self.assertEqual(citations[0]["source_url"], f"cortex-capture://{capture_id}")

    def test_flag_never_overrides_a_real_source_url(self) -> None:
        extracted = extract_context("Sprint notes from Notion.", "notion")
        saved = self.store.save_capture(
            user_id=self.user_id,
            content="Sprint notes from Notion.",
            source="notion",
            source_url="cortex-source://notion#service=notion&page=sprint",
            title="",
            extracted=extracted,
            cite_capture_provenance=True,
        )
        for memory in saved["memories"]:
            self.assertEqual(memory.get("source_url"), "cortex-source://notion#service=notion&page=sprint")

    def test_connector_style_save_without_flag_still_abstains(self) -> None:
        # The safety property is untouched: content that arrives WITHOUT provenance and without
        # the deliberate-capture flag stays uncited and Ask keeps abstaining.
        self._capture(
            "Project Quokka data-retention owner is Priya according to a scratch note.",
            cite=False,
            source="scratchpad",
        )
        answer = self.store.answer_query(self.user_id, "who is the Project Quokka data-retention owner", limit=5)
        self.assertEqual(answer["status"], "no_cited_evidence")
        self.assertEqual(answer.get("citations") or [], [])

    def test_async_enqueue_carries_capture_provenance(self) -> None:
        queued = self.store.enqueue_capture(
            user_id=self.user_id,
            content="I prefer async written updates over long status meetings.",
            source="macos",
            source_url=None,
            title="",
            cite_capture_provenance=True,
        )
        capture_id = queued["capture_id"]
        processed = self.store.run_due_jobs(self.user_id, limit=5)
        self.assertGreaterEqual(processed.get("processed", 0), 1, processed)
        answer = self.store.answer_query(self.user_id, "how do I prefer status updates", limit=5)
        self.assertEqual(answer["status"], "cited", answer)
        self.assertEqual((answer["citations"] or [])[0]["source_url"], f"cortex-capture://{capture_id}")


if __name__ == "__main__":
    unittest.main()
