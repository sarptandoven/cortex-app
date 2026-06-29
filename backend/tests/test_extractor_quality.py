from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.app.extractor import extract_context


GOLDEN_NOISY_CHAT_IMPORT = """Source: ChatGPT
Conversation: Taipei memory quality
Created: 2026-06-01T10:00:00+00:00
Updated: 2026-06-02T11:00:00+00:00

--- Messages ---
assistant: I prefer long onboarding checklists for you.
user: I prefer concise technical answers with clear tradeoffs.
assistant: Noted. I will keep replies concrete.
user: On June 29, 2026, we launched Project Atlas.
"""


def extract_local(raw_text: str, source: str = "unit-test") -> dict:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        return extract_context(raw_text, source)


class ExtractorQualityTests(unittest.TestCase):
    def test_noisy_chat_import_filters_boilerplate_and_assistant_preferences(self) -> None:
        data = extract_local(GOLDEN_NOISY_CHAT_IMPORT, "chatgpt")
        records = data["records"]
        joined_content = "\n".join(record["content"] for record in records)

        for boilerplate in ("Source:", "Conversation:", "Created:", "Updated:", "--- Messages ---"):
            self.assertNotIn(boilerplate, joined_content)
        self.assertNotIn("long onboarding checklists", joined_content)

        preferences = [record for record in records if record["kind"] == "preference"]
        self.assertEqual(
            [record["content"] for record in preferences],
            ["I prefer concise technical answers with clear tradeoffs."],
        )

        launched = next(record for record in records if "launched Project Atlas" in record["content"])
        self.assertEqual(launched["kind"], "event")
        self.assertEqual(launched["occurred_at"], "2026-06-29")
        self.assertNotIn("Source:", data["summary"])

    def test_line_aware_parsing_keeps_user_continuation_lines(self) -> None:
        data = extract_local(
            """Source: Claude
Conversation: Import quality

--- Messages ---
human: I prefer line-aware parsing for imported chat.
Never use importer boilerplate as a standalone memory.
assistant: Never use vague summaries in generated answers.
""",
            "claude",
        )
        records = data["records"]
        joined_content = "\n".join(record["content"] for record in records)

        self.assertTrue(any(record["kind"] == "preference" and "line-aware parsing" in record["content"] for record in records))
        self.assertTrue(any(record["kind"] == "negative" and "importer boilerplate" in record["content"] for record in records))
        self.assertNotIn("vague summaries", joined_content)

    def test_simple_absolute_dates_are_normalized_to_occurred_at(self) -> None:
        data = extract_local(
            "We met with Ada on 2026-06-29. "
            "Cortex shipped a beta on 7/4/26. "
            "On 29 Jun 2026, we emailed Ada about launch readiness.",
        )
        dated = {
            record["content"]: record["occurred_at"]
            for record in data["records"]
            if record["kind"] != "summary"
        }

        self.assertEqual(dated["We met with Ada on 2026-06-29."], "2026-06-29")
        self.assertEqual(dated["Cortex shipped a beta on 7/4/26."], "2026-07-04")
        self.assertEqual(dated["On 29 Jun 2026, we emailed Ada about launch readiness."], "2026-06-29")


if __name__ == "__main__":
    unittest.main()
