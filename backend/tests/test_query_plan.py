from __future__ import annotations

import unittest

from backend.app import query_plan
from backend.app.query_plan import build_query_plan


class QueryPlanIntentTests(unittest.TestCase):
    def test_keyword_intents(self):
        self.assertEqual(build_query_plan("draft a reply to the team").intent, "draft")
        self.assertEqual(build_query_plan("implement the sync fix").intent, "act")
        self.assertEqual(build_query_plan("plan the launch roadmap").intent, "plan")
        self.assertEqual(build_query_plan("remind me what happened last time").intent, "recall")
        self.assertEqual(build_query_plan("what did I decide about Go?").intent, "answer")

    def test_intent_hint_is_authoritative(self):
        self.assertEqual(build_query_plan("draft a reply", intent_hint="plan").intent, "plan")
        # An invalid hint is ignored, falling back to classification.
        self.assertEqual(build_query_plan("draft a reply", intent_hint="bogus").intent, "draft")

    def test_empty_query_is_recall(self):
        self.assertEqual(build_query_plan("").intent, "recall")


class QueryPlanDecompositionTests(unittest.TestCase):
    def test_compound_query_decomposes(self):
        plan = build_query_plan("what did we decide about hiring and when did we ship v2")
        self.assertGreaterEqual(len(plan.sub_queries), 2)
        self.assertTrue(all(len(s) >= 4 for s in plan.sub_queries))

    def test_simple_query_not_decomposed(self):
        self.assertEqual(build_query_plan("what did we decide about hiring").sub_queries, ())

    def test_decomposition_capped_at_three(self):
        plan = build_query_plan("a topic and b topic and c topic and d topic and e topic")
        self.assertLessEqual(len(plan.sub_queries), 3)


class QueryPlanEntityLayerTests(unittest.TestCase):
    def test_extracts_proper_noun_entities(self):
        ents = build_query_plan("what does Alice think about Project Atlas").entities
        self.assertIn("Alice", ents)
        self.assertIn("Project Atlas", ents)

    def test_drops_sentence_initial_stopword(self):
        # "What" leads the sentence; it must not be treated as an entity.
        self.assertNotIn("What", build_query_plan("What did Bob say").entities)

    def test_layer_routing_hints(self):
        self.assertIn("style", build_query_plan("match my writing tone and voice").layers)
        self.assertIn("decision", build_query_plan("why did we decide on this rationale").layers)
        self.assertIn("procedural", build_query_plan("how to deploy the service").layers)

    def test_is_question_flag(self):
        self.assertTrue(build_query_plan("who owns billing?").is_question)
        self.assertFalse(build_query_plan("update the billing docs").is_question)


class QueryPlanFlagTests(unittest.TestCase):
    def test_flag_default_off(self):
        import os

        # In a clean env the planner is off (parity with today's keyword behavior).
        prev = os.environ.pop("CORTEX_QUERY_PLAN", None)
        try:
            self.assertFalse(query_plan.query_plan_enabled())
        finally:
            if prev is not None:
                os.environ["CORTEX_QUERY_PLAN"] = prev

    def test_flag_truthy_values(self):
        import os

        for value in ("1", "true", "on", "yes"):
            os.environ["CORTEX_QUERY_PLAN"] = value
            self.assertTrue(query_plan.query_plan_enabled(), value)
        os.environ.pop("CORTEX_QUERY_PLAN", None)


if __name__ == "__main__":
    unittest.main()
