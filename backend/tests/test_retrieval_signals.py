from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from backend.app.database import init_db
from backend.app.storage import RECENCY_RETRIEVAL_BOOST_MAX, CortexStore


def _store() -> CortexStore:
    tmp = Path(tempfile.mkdtemp())
    init_db(tmp / "c.db")
    return CortexStore(tmp / "c.db", tmp / "v")


def _row(layer: str, age_days: float) -> dict:
    captured = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    return {"layer": layer, "captured_at": captured, "content": "x", "summary": "", "source": "notes"}


class ContextualTextTests(unittest.TestCase):
    def test_prepends_layer_source_date(self):
        store = _store()
        row = {"layer": "decision", "source": "notes", "captured_at": "2026-01-02T03:04:05+00:00", "content": "Chose Go.", "summary": ""}
        text = store._contextualized_text(row)
        self.assertTrue(text.startswith("[layer=decision source=notes date=2026-01-02] "))
        self.assertIn("Chose Go.", text)

    def test_empty_text_stays_empty(self):
        store = _store()
        self.assertEqual(store._contextualized_text({"content": "", "summary": ""}), "")


class TemporalDecayTests(unittest.TestCase):
    def setUp(self):
        self.store = _store()
        self.now = datetime.now(timezone.utc)
        self._prev = os.environ.get("CORTEX_TEMPORAL_DECAY")

    def tearDown(self):
        if self._prev is None:
            os.environ.pop("CORTEX_TEMPORAL_DECAY", None)
        else:
            os.environ["CORTEX_TEMPORAL_DECAY"] = self._prev

    def test_flag_off_uses_step_bins(self):
        os.environ.pop("CORTEX_TEMPORAL_DECAY", None)
        # <=2 days -> top bin 0.004
        self.assertAlmostEqual(self.store._recency_boost(_row("episodic", 1.0), now=self.now), 0.004, places=6)

    def test_flag_on_exponential_per_layer(self):
        os.environ["CORTEX_TEMPORAL_DECAY"] = "1"
        # Fresh episodic ~ amplitude (0.004); after one half-life (14d) ~ half.
        fresh = self.store._recency_boost(_row("episodic", 0.0), now=self.now)
        half = self.store._recency_boost(_row("episodic", 14.0), now=self.now)
        self.assertLessEqual(fresh, RECENCY_RETRIEVAL_BOOST_MAX + 1e-9)
        self.assertAlmostEqual(half, fresh / 2.0, places=4)
        # Episodic decays faster than semantic at the same age.
        self.assertLess(
            self.store._recency_boost(_row("episodic", 60.0), now=self.now),
            self.store._recency_boost(_row("semantic", 60.0), now=self.now),
        )

    def test_boost_never_exceeds_cap(self):
        os.environ["CORTEX_TEMPORAL_DECAY"] = "1"
        for layer in ("episodic", "semantic", "decision", "style", "preference", "procedural", "negative", "unknown"):
            self.assertLessEqual(self.store._recency_boost(_row(layer, 0.0), now=self.now), RECENCY_RETRIEVAL_BOOST_MAX + 1e-9)


if __name__ == "__main__":
    unittest.main()
