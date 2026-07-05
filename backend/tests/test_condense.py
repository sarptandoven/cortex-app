"""Tests for the profile condensation layer (backend/app/condense.py).

Two concerns are covered:

1. The deterministic template — the only path production ships by default and the
   only path these assertions inspect for exact wording/citation. Same section in
   => same statement out; ids are always a subset of the section's element ids;
   an empty / element-less section abstains (returns None).

2. The LLM path — exercised only with a FAKE anthropic client injected via
   ``patch``, so no network and no real model output is ever asserted on. We
   verify the *validation contract*: hallucinated ids are dropped, ABSTAIN and
   malformed/raising responses fall back to the template, and the LLM can never
   invent a citation.
"""

from __future__ import annotations

import json
import os
import unittest
from unittest.mock import patch

from backend.app import condense
from backend.app.condense import condense_section


def _section(**overrides) -> dict:
    """A representative, ranked profile section (anchor first)."""
    section = {
        "id": "preferences",
        "title": "What you prefer",
        "confidence": "high",
        "elements": [
            {
                "text": "Prefers concise technical answers with clear tradeoffs.",
                "source": "chatgpt",
                "count": 5,
                "memory_ids": ["m3", "m1"],
                "source_url": None,
            },
            {
                "text": "Prefers dark mode in every editor.",
                "source": "obsidian",
                "count": 2,
                "memory_ids": ["m2"],
                "source_url": None,
            },
        ],
    }
    section.update(overrides)
    return section


# --- Fake anthropic client --------------------------------------------------


class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeResponse:
    def __init__(self, text: str) -> None:
        self.content = [_FakeBlock(text)]


class _FakeMessages:
    def __init__(self, text: str | None, raises: bool) -> None:
        self._text = text
        self._raises = raises

    def create(self, **kwargs):  # noqa: ANN003 - mirrors the SDK surface
        if self._raises:
            raise RuntimeError("simulated client failure")
        return _FakeResponse(self._text or "")


class _FakeAnthropic:
    """Stand-in for ``anthropic.Anthropic``. ``payload`` is the raw text the fake
    model returns; ``raises=True`` makes ``.messages.create`` blow up."""

    payload: str | None = None
    raises: bool = False

    def __init__(self, *args, **kwargs) -> None:
        self.messages = _FakeMessages(type(self).payload, type(self).raises)


def _fake_anthropic_module(payload: str | None = None, raises: bool = False):
    """Build a module-like object exposing ``Anthropic`` so that condense.py's
    ``from anthropic import Anthropic`` resolves to our fake."""

    class _Client(_FakeAnthropic):
        pass

    _Client.payload = payload
    _Client.raises = raises

    import types

    module = types.ModuleType("anthropic")
    module.Anthropic = _Client
    return module


def _run_llm(section: dict, payload: str | None = None, raises: bool = False, **kwargs) -> dict | None:
    """Invoke condense_section on the LLM path with a fake client + dummy key."""
    fake_module = _fake_anthropic_module(payload=payload, raises=raises)
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "dummy-key"}):
        with patch.dict("sys.modules", {"anthropic": fake_module}):
            return condense_section(section, allow_llm=True, **kwargs)


# --- Deterministic template -------------------------------------------------


class TemplateCondenseTests(unittest.TestCase):
    def test_deterministic_same_section_same_statement(self) -> None:
        section = _section()
        first = condense_section(section)
        second = condense_section(_section())
        self.assertIsNotNone(first)
        self.assertEqual(first, second)
        self.assertEqual(first["statement"], second["statement"])

    def test_template_shape_and_method(self) -> None:
        result = condense_section(_section())
        self.assertEqual(result["method"], "template")
        self.assertEqual(result["section_id"], "preferences")
        self.assertEqual(result["confidence"], "high")
        self.assertTrue(result["statement"])
        self.assertTrue(result["example"])
        # Second-person, cites its source and signal count.
        self.assertTrue(result["statement"].lower().startswith("you"))
        self.assertIn("from", result["statement"])

    def test_memory_ids_are_sorted_deduped_subset(self) -> None:
        section = _section()
        union = set()
        for element in section["elements"]:
            union.update(element["memory_ids"])
        result = condense_section(section)
        self.assertEqual(result["memory_ids"], sorted(set(result["memory_ids"])))
        self.assertTrue(set(result["memory_ids"]).issubset(union))
        self.assertEqual(set(result["memory_ids"]), {"m1", "m2", "m3"})
        self.assertEqual(result["sources"], ["chatgpt", "obsidian"])

    def test_max_chars_cap(self) -> None:
        section = _section(
            elements=[
                {
                    "text": "Prefers " + ("very long-winded explanations " * 20).strip() + ".",
                    "source": "obsidian",
                    "count": 4,
                    "memory_ids": ["m9"],
                    "source_url": None,
                }
            ]
        )
        result = condense_section(section, max_chars=80)
        self.assertLessEqual(len(result["statement"]), 80)

    def test_lead_fallback_for_third_person_anchor(self) -> None:
        # Anchor text that mirror's mechanical rewrite leaves third-person, so the
        # section-appropriate lead phrase is used instead.
        section = _section(
            id="how_you_work",
            elements=[
                {
                    "text": "Reviews pull requests early in the morning.",
                    "source": "github",
                    "count": 6,
                    "memory_ids": ["g1"],
                    "source_url": None,
                }
            ],
        )
        result = condense_section(section)
        self.assertTrue(result["statement"].startswith("You tend to"))
        self.assertEqual(result["method"], "template")

    def test_empty_and_elementless_sections_abstain(self) -> None:
        self.assertIsNone(condense_section({}))
        self.assertIsNone(condense_section(None))
        self.assertIsNone(condense_section({"id": "x", "elements": []}))
        # Elements present but none usable: blank text, or text with NO citation at all
        # (neither memory_ids nor a source).
        self.assertIsNone(
            condense_section(
                {
                    "id": "x",
                    "confidence": "low",
                    "elements": [
                        {"text": "", "source": "a", "count": 1, "memory_ids": ["m1"]},
                        {"text": "Something", "source": "", "count": 1, "memory_ids": []},
                    ],
                }
            )
        )

    def test_source_cited_element_without_memory_ids_is_usable(self) -> None:
        # People & projects / focus areas are cited by their source + support count, not memory_ids,
        # so a source-only element must still condense (not abstain).
        result = condense_section(
            {
                "id": "people_projects",
                "confidence": "medium",
                "elements": [
                    {"text": "Alice (person)", "source": "entities", "count": 4, "memory_ids": [], "source_url": None}
                ],
            }
        )
        self.assertIsNotNone(result)
        self.assertTrue(result["statement"])
        self.assertEqual(result["method"], "template")
        self.assertEqual(result["memory_ids"], [])


# --- LLM path (fake client only) --------------------------------------------


class LlmCondenseTests(unittest.TestCase):
    def test_allow_llm_false_never_calls_model(self) -> None:
        # Even with a key set, allow_llm=False must stay on the template path.
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "dummy-key"}):
            with patch.dict(
                "sys.modules", {"anthropic": _fake_anthropic_module(payload="{}")}
            ):
                result = condense_section(_section(), allow_llm=False)
        self.assertEqual(result["method"], "template")

    def test_valid_llm_response_used(self) -> None:
        payload = json.dumps(
            {"statement": "You favour concise, tradeoff-first answers.", "used_ids": ["m1", "m3"]}
        )
        result = _run_llm(_section(), payload=payload)
        self.assertEqual(result["method"], "llm")
        self.assertEqual(result["statement"], "You favour concise, tradeoff-first answers.")
        self.assertEqual(result["memory_ids"], ["m1", "m3"])
        # Sources recomputed from the validated ids (m1, m3 both from chatgpt).
        self.assertEqual(result["sources"], ["chatgpt"])

    def test_hallucinated_id_dropped_real_ids_kept(self) -> None:
        payload = json.dumps(
            {
                "statement": "You favour concise answers.",
                "used_ids": ["m1", "HALLUCINATED", "m2"],
            }
        )
        result = _run_llm(_section(), payload=payload)
        self.assertEqual(result["method"], "llm")
        self.assertEqual(result["memory_ids"], ["m1", "m2"])
        self.assertNotIn("HALLUCINATED", result["memory_ids"])

    def test_all_ids_hallucinated_falls_back_to_template(self) -> None:
        payload = json.dumps(
            {"statement": "You favour concise answers.", "used_ids": ["nope1", "nope2"]}
        )
        result = _run_llm(_section(), payload=payload)
        self.assertEqual(result["method"], "template")
        self.assertEqual(set(result["memory_ids"]), {"m1", "m2", "m3"})

    def test_abstain_falls_back_to_template(self) -> None:
        result = _run_llm(_section(), payload="ABSTAIN")
        self.assertEqual(result["method"], "template")
        # Also honoured when embedded in JSON.
        result2 = _run_llm(
            _section(), payload=json.dumps({"statement": "ABSTAIN", "used_ids": ["m1"]})
        )
        self.assertEqual(result2["method"], "template")

    def test_first_person_rejected_falls_back(self) -> None:
        payload = json.dumps({"statement": "I prefer concise answers.", "used_ids": ["m1"]})
        result = _run_llm(_section(), payload=payload)
        self.assertEqual(result["method"], "template")

    def test_identity_assertion_rejected_falls_back(self) -> None:
        payload = json.dumps({"statement": "You are a concise person.", "used_ids": ["m1"]})
        result = _run_llm(_section(), payload=payload)
        self.assertEqual(result["method"], "template")

    def test_fenced_json_is_parsed(self) -> None:
        payload = "```json\n" + json.dumps(
            {"statement": "You favour concise answers.", "used_ids": ["m1"]}
        ) + "\n```"
        result = _run_llm(_section(), payload=payload)
        self.assertEqual(result["method"], "llm")
        self.assertEqual(result["memory_ids"], ["m1"])

    def test_malformed_json_falls_back_never_raises(self) -> None:
        result = _run_llm(_section(), payload="not json at all {{{")
        self.assertEqual(result["method"], "template")

    def test_client_exception_falls_back_never_raises(self) -> None:
        result = _run_llm(_section(), raises=True)
        self.assertEqual(result["method"], "template")

    def test_missing_api_key_stays_on_template(self) -> None:
        # allow_llm=True but no key -> never touches the model.
        fake_module = _fake_anthropic_module(payload="{}")
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            with patch.dict("sys.modules", {"anthropic": fake_module}):
                result = condense_section(_section(), allow_llm=True)
        self.assertEqual(result["method"], "template")

    def test_llm_path_still_abstains_on_no_elements(self) -> None:
        self.assertIsNone(_run_llm({"id": "x", "elements": []}))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
