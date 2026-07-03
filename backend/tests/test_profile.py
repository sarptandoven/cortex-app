"""Tests for backend/app/profile.py — the pure SORT/ORGANIZE + cited-assembly
core of the Personal Profile layer.

These tests are fully pure: they construct ``personal_profile``-shaped dicts by
hand (no store, no DB) and assert on the deterministic, cited Section/Element
output of :func:`build_profile_sections`.
"""

from __future__ import annotations

import unittest

from backend.app.profile import build_profile_sections


# --- Builders for the personal_profile() dict shape -------------------------


def make_item(
    item_id: str,
    layer: str,
    content: str,
    *,
    source: str = "calendar",
    source_url=None,
    importance: int = 3,
    confidence="medium",
    captured_at: str = "2026-06-01T10:00:00",
    summary: str = "",
    topics=None,
):
    """One per-layer memory item, matching _profile_memory_item's shape."""
    return {
        "id": item_id,
        "kind": layer,
        "layer": layer,
        "content": content,
        "summary": summary,
        "source": source,
        "source_url": source_url,
        "sector": "",
        "source_type": "",
        "captured_at": captured_at,
        "occurred_at": None,
        "valid_from": None,
        "valid_to": None,
        "topics": topics or [],
        "entity_ids": [],
        # importance/confidence exist on the raw memory; profile items may drop
        # them, so profile.py reads them defensively — we include them here to
        # exercise the ranking/abstention logic.
        "importance": importance,
        "confidence": confidence,
    }


def make_profile(*, layers=None, topics=None, entities=None):
    """Assemble a minimal personal_profile()-shaped dict.

    ``layers`` maps a layer name -> list of items. ``topics``/``entities`` are
    the list-of-dicts shapes returned by list_topics / list_entities.
    """
    layers = layers or {}
    layer_order = [
        "preference",
        "negative",
        "style",
        "decision",
        "procedural",
        "episodic",
        "semantic",
    ]
    sections = [
        {"layer": layer, "title": layer.title(), "description": "", "count": len(layers.get(layer, [])), "items": layers.get(layer, [])}
        for layer in layer_order
    ]
    return {
        "generated_at": "2026-07-02T00:00:00",
        "name": "Cortex Personal Adaptation Profile",
        "sections": sections,
        "topics": topics or [],
        "entities": entities or [],
        "focus": [],
        "open_loops": [],
        "limitations": [],
        "readiness": 50,
    }


def make_topic(topic: str, count: int, last_seen: str = "2026-06-01T00:00:00"):
    return {"topic": topic, "count": count, "last_seen": last_seen}


def make_entity(name: str, memory_count: int, *, kind: str = "person", entity_id: str = "", last_seen: str = "2026-06-01T00:00:00"):
    return {
        "id": entity_id or ("entity_" + name.lower()),
        "name": name,
        "kind": kind,
        "context": "",
        "first_seen": "2026-01-01T00:00:00",
        "last_seen": last_seen,
        "memory_count": memory_count,
    }


# --- Tests ------------------------------------------------------------------


class DeterminismTest(unittest.TestCase):
    def test_identical_input_yields_identical_output(self):
        profile = make_profile(
            layers={
                "preference": [make_item(f"p{i}", "preference", "prefers concise summaries", source="obsidian") for i in range(4)],
                "decision": [make_item("d1", "decision", "chose Postgres over Mongo", source="github", importance=5)],
            },
            topics=[make_topic("infra", 4), make_topic("writing", 2)],
            entities=[make_entity("Alice", 3), make_entity("Cortex", 2, kind="project")],
        )
        out1 = build_profile_sections(profile, provider="hash")
        out2 = build_profile_sections(profile, provider="hash")
        self.assertEqual(out1, out2)

    def test_section_order_follows_fixed_catalog(self):
        profile = make_profile(
            layers={
                "decision": [make_item(f"d{i}", "decision", "settled on weekly releases", source="linear") for i in range(3)],
                "preference": [make_item(f"p{i}", "preference", "prefers dark mode", source="obsidian") for i in range(3)],
                "style": [make_item(f"s{i}", "style", "writes in short sentences", source="email") for i in range(3)],
                "negative": [make_item(f"n{i}", "negative", "avoids long meetings", source="calendar") for i in range(3)],
            },
            topics=[make_topic("infra", 5)],
            entities=[make_entity("Bob", 3)],
        )
        out = build_profile_sections(profile, provider="hash")
        ids = [s["id"] for s in out]
        # Fixed catalog order.
        self.assertEqual(
            ids,
            ["how_you_work", "preferences", "dislikes", "decisions", "focus", "people_projects"],
        )


class CitedTest(unittest.TestCase):
    def test_every_element_has_nonempty_memory_ids_when_behavioral(self):
        profile = make_profile(
            layers={
                "preference": [make_item(f"p{i}", "preference", "prefers async standups", source="slack") for i in range(4)],
            },
        )
        out = build_profile_sections(profile, provider="hash")
        self.assertTrue(out)
        for section in out:
            if section["id"] in ("focus", "people_projects"):
                continue
            for element in section["elements"]:
                self.assertTrue(element["memory_ids"], "behavioral element must cite memory ids")
                self.assertTrue(element["source"] or element["source_url"], "element must cite a source")

    def test_uncited_element_is_dropped(self):
        # No source and no source_url on any member -> cited-or-abstain drops it.
        items = [
            make_item(f"u{i}", "preference", "prefers tabs over spaces", source="", source_url=None, importance=5)
            for i in range(4)
        ]
        profile = make_profile(layers={"preference": items})
        out = build_profile_sections(profile, provider="hash")
        self.assertEqual(out, [])


class AbstainOnThinTest(unittest.TestCase):
    def test_empty_profile_returns_empty(self):
        self.assertEqual(build_profile_sections({}, provider="hash"), [])
        self.assertEqual(build_profile_sections(make_profile(), provider="hash"), [])

    def test_single_low_importance_item_section_omitted(self):
        profile = make_profile(
            layers={"preference": [make_item("p1", "preference", "used a template once", importance=2)]}
        )
        out = build_profile_sections(profile, provider="hash")
        # support 1 and importance < 4 -> below floor -> section omitted.
        self.assertEqual(out, [])

    def test_single_high_importance_item_surfaces(self):
        profile = make_profile(
            layers={"decision": [make_item("d1", "decision", "committed to shipping v2 in Q3", importance=5, source="linear")]}
        )
        out = build_profile_sections(profile, provider="hash")
        ids = [s["id"] for s in out]
        self.assertIn("decisions", ids)
        section = next(s for s in out if s["id"] == "decisions")
        self.assertEqual(len(section["elements"]), 1)
        self.assertEqual(section["elements"][0]["memory_ids"], ["d1"])
        # support 1, distinct 1 -> low confidence.
        self.assertEqual(section["confidence"], "low")


class GroupingDedupTest(unittest.TestCase):
    def test_near_identical_items_collapse_to_one_element(self):
        # Five lexically near-identical items (token-signature fallback) collapse.
        variants = [
            "prefers dark mode in the editor",
            "prefers dark mode in the code editor",
            "prefers a dark mode editor",
            "prefers dark mode editor themes",
            "prefers dark mode in the editor please",
        ]
        items = [make_item(f"p{i}", "preference", v, source="obsidian") for i, v in enumerate(variants)]
        profile = make_profile(layers={"preference": items})
        out = build_profile_sections(profile, provider="hash")
        section = next(s for s in out if s["id"] == "preferences")
        self.assertEqual(len(section["elements"]), 1)
        self.assertGreaterEqual(section["elements"][0]["count"], 3)
        self.assertEqual(len(section["elements"][0]["memory_ids"]), 5)

    def test_distinct_items_stay_separate(self):
        items = [
            make_item("a1", "preference", "prefers dark mode themes", source="obsidian"),
            make_item("a2", "preference", "prefers dark mode themes always", source="obsidian"),
            make_item("b1", "preference", "likes quarterly planning offsites", source="calendar"),
            make_item("b2", "preference", "likes quarterly planning offsites annually", source="calendar"),
        ]
        profile = make_profile(layers={"preference": items})
        out = build_profile_sections(profile, provider="hash")
        section = next(s for s in out if s["id"] == "preferences")
        # Two distinct clusters -> two elements.
        self.assertEqual(len(section["elements"]), 2)
        counts = sorted(e["count"] for e in section["elements"])
        self.assertEqual(counts, [2, 2])


class SupportConfidenceTest(unittest.TestCase):
    def test_five_agreeing_one_source_is_medium(self):
        items = [make_item(f"p{i}", "preference", "prefers written updates", source="slack") for i in range(5)]
        profile = make_profile(layers={"preference": items})
        out = build_profile_sections(profile, provider="hash")
        section = next(s for s in out if s["id"] == "preferences")
        self.assertEqual(section["elements"][0]["count"], 5)
        # support 5 but distinct sources == 1 -> not high, support>=3 -> medium.
        self.assertEqual(section["confidence"], "medium")

    def test_five_agreeing_two_sources_is_high(self):
        items = [make_item(f"a{i}", "preference", "prefers written updates", source="slack") for i in range(3)]
        items += [make_item(f"b{i}", "preference", "prefers written updates", source="email") for i in range(2)]
        profile = make_profile(layers={"preference": items})
        out = build_profile_sections(profile, provider="hash")
        section = next(s for s in out if s["id"] == "preferences")
        self.assertEqual(section["elements"][0]["count"], 5)
        # support 5 AND distinct sources 2 -> high.
        self.assertEqual(section["confidence"], "high")


class PeopleProjectsTest(unittest.TestCase):
    def test_single_link_entity_is_omitted(self):
        profile = make_profile(entities=[make_entity("Solo", 1)])
        out = build_profile_sections(profile, provider="hash")
        self.assertEqual([s["id"] for s in out], [])

    def test_two_link_entity_surfaces_at_medium_cap(self):
        profile = make_profile(
            entities=[make_entity("Alice", 6, kind="person"), make_entity("Cortex", 3, kind="project")]
        )
        out = build_profile_sections(profile, provider="hash")
        section = next(s for s in out if s["id"] == "people_projects")
        self.assertEqual(section["title"], "People & projects")
        # Even with 6 links, confidence is capped at medium for entity evidence.
        self.assertEqual(section["confidence"], "medium")
        # Most-linked entity first.
        self.assertEqual(section["elements"][0]["text"], "Alice (person)")
        self.assertEqual(section["elements"][0]["count"], 6)
        # Entities carry no per-memory citation of their own.
        self.assertEqual(section["elements"][0]["memory_ids"], [])
        self.assertIsNone(section["elements"][0]["source_url"])


class FocusTest(unittest.TestCase):
    def test_thin_topic_is_omitted(self):
        profile = make_profile(topics=[make_topic("misc", 1)])
        out = build_profile_sections(profile, provider="hash")
        self.assertEqual([s["id"] for s in out], [])

    def test_repeated_topics_surface_as_focus(self):
        profile = make_profile(topics=[make_topic("infra", 5), make_topic("writing", 3), make_topic("thin", 1)])
        out = build_profile_sections(profile, provider="hash")
        section = next(s for s in out if s["id"] == "focus")
        self.assertEqual(section["title"], "Focus areas")
        texts = [e["text"] for e in section["elements"]]
        self.assertEqual(texts, ["infra", "writing"])  # thin dropped, ranked by count
        self.assertEqual(section["confidence"], "medium")  # top count >= 3, capped at medium


class EmbeddingMergeTest(unittest.TestCase):
    def test_embed_fn_used_only_when_provider_not_hash(self):
        # An embedder that maps everything to the same vector => all merge.
        def const_embed(_text):
            return [1.0, 0.0, 0.0]

        # Two lexically-distinct concepts, each with two members so that under
        # token-signature dedup each survives the support floor as its own
        # cluster (support 2), while a semantics-collapsing embedder folds
        # everything into one.
        items = [
            make_item("s1", "preference", "prefers ocean sailing trips", source="obsidian"),
            make_item("s2", "preference", "prefers ocean sailing trips often", source="obsidian"),
            make_item("h1", "preference", "prefers mountain hiking weekends", source="obsidian"),
            make_item("h2", "preference", "prefers mountain hiking weekends always", source="obsidian"),
        ]
        profile = make_profile(layers={"preference": items})

        # provider != hash: the constant embedder collapses everything -> 1 element.
        merged = build_profile_sections(profile, embed_fn=const_embed, provider="openai")
        section = next(s for s in merged if s["id"] == "preferences")
        self.assertEqual(len(section["elements"]), 1)
        self.assertEqual(section["elements"][0]["count"], 4)

        # provider == hash: embed_fn ignored, token-signature keeps the two
        # distinct concepts separate -> 2 elements, each with support 2.
        hashed = build_profile_sections(profile, embed_fn=const_embed, provider="hash")
        section2 = next(s for s in hashed if s["id"] == "preferences")
        self.assertEqual(len(section2["elements"]), 2)
        self.assertEqual(sorted(e["count"] for e in section2["elements"]), [2, 2])


if __name__ == "__main__":
    unittest.main()
