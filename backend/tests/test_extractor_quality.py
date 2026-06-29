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


PERSONAL_MEMORY_KINDS = {"preference", "style", "negative"}


def extract_local(raw_text: str, source: str = "unit-test", author_aliases: list[str] | None = None) -> dict:
    with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
        return extract_context(raw_text, source, author_aliases=author_aliases)


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

    def test_timestamped_named_speakers_do_not_become_user_preferences(self) -> None:
        data = extract_local(
            """Source: Slack
Channel: general

--- Messages ---
2026-06-29T10:00:00+00:00 Alex: I prefer long onboarding checklists for you.
2026-06-29T10:01:00+00:00 Alex: My writing style is verbose and salesy.
2026-06-29T10:02:00+00:00 Sarpt: We decided Project Atlas should keep five clear tabs.
""",
            "slack",
        )
        records = data["records"]
        joined_content = "\n".join(record["content"] for record in records)

        self.assertNotIn("long onboarding checklists", joined_content)
        self.assertNotIn("verbose and salesy", joined_content)
        self.assertTrue(any(record["kind"] == "decision" and "five clear tabs" in record["content"] for record in records))
        self.assertFalse(any(record["kind"] in {"preference", "style", "negative"} for record in records))

    def test_chatgpt_claude_assistant_alias_turns_do_not_seed_personal_memories(self) -> None:
        for source, label in (("chatgpt", "ChatGPT"), ("claude", "Claude")):
            with self.subTest(source=source):
                data = extract_local(
                    f"""Source: {label}
Conversation: Assistant-only import

--- Messages ---
2026-06-29T10:00:00+00:00 {label}: I prefer verbose onboarding checklists.
My writing style should be expansive and warm.
2026-06-29T10:02:00+00:00 {label}: Never use terse implementation notes.
""",
                    source,
                )
                joined_content = "\n".join(record["content"] for record in data["records"])
                self.assertFalse(any(record["kind"] in PERSONAL_MEMORY_KINDS for record in data["records"]))
                for leaked in ("verbose onboarding", "expansive and warm", "terse implementation"):
                    self.assertNotIn(leaked, joined_content)
                    self.assertNotIn(leaked, data["summary"])

    def test_slack_lowercase_named_speakers_and_continuations_do_not_seed_personal_memories(self) -> None:
        data = extract_local(
            """Source: Slack
Channel: general
File: slack/general/2026-06-29.json

--- Messages ---
2026-06-29T12:40:00+00:00 dana: I prefer async standups.
My writing style is emoji-heavy and casual.
2026-06-29T12:41:00+00:00 priya: Never use threads for launch decisions.
""",
            "slack",
        )
        joined_content = "\n".join(record["content"] for record in data["records"])
        self.assertFalse(any(record["kind"] in PERSONAL_MEMORY_KINDS for record in data["records"]))
        for leaked in ("async standups", "emoji-heavy", "threads for launch"):
            self.assertNotIn(leaked, joined_content)
            self.assertNotIn(leaked, data["summary"])

    def test_external_named_speaker_rejections_do_not_seed_negative_memory(self) -> None:
        data = extract_local(
            """Source: Slack
Channel: launch

--- Messages ---
2026-06-29T13:00:00+00:00 Alex: Rejected splashy launch pages as a bad fit.
2026-06-29T13:01:00+00:00 Alex: The animated onboarding prototype was not helpful.
2026-06-29T13:02:00+00:00 Sarpt: We decided Project Atlas should keep the five-tab app structure.
""",
            "slack",
        )
        joined_content = "\n".join(record["content"] for record in data["records"])
        self.assertFalse(any(record["kind"] == "negative" for record in data["records"]))
        for leaked in ("splashy launch", "bad fit", "animated onboarding", "not helpful"):
            self.assertNotIn(leaked, joined_content)
            self.assertNotIn(leaked, data["summary"])
        self.assertTrue(any(record["kind"] == "decision" and "five-tab app" in record["content"] for record in data["records"]))

    def test_identity_aliases_allow_self_authored_slack_preferences(self) -> None:
        data = extract_local(
            """Source: Slack
Channel: general

--- Messages ---
2026-06-29T12:40:00+00:00 sarpt: I prefer async standups with concise summaries.
My writing style uses short direct paragraphs.
2026-06-29T12:41:00+00:00 dana: I prefer long onboarding rituals.
2026-06-29T12:42:00+00:00 priya: Never use threads for launch decisions.
""",
            "slack",
            author_aliases=["sarpt"],
        )
        joined_content = "\n".join(record["content"] for record in data["records"])
        self.assertTrue(any(record["kind"] == "preference" and "async standups" in record["content"] for record in data["records"]))
        self.assertTrue(any(record["kind"] == "style" and "short direct paragraphs" in record["content"] for record in data["records"]))
        for leaked in ("long onboarding rituals", "threads for launch"):
            self.assertNotIn(leaked, joined_content)
            self.assertNotIn(leaked, data["summary"])

    def test_external_email_sender_body_does_not_seed_personal_memories(self) -> None:
        data = extract_local(
            """Source: Email
Subject: Partner preferences
From: Alex Partner <alex@external.example>
To: sarpt@example.com
Date: Mon, 29 Jun 2026 10:00:00 +0000

I prefer weekly PDF status reports.
My writing style is formal and legalistic.
Never use Slack for contract approvals.
""",
            "email",
        )
        joined_content = "\n".join(record["content"] for record in data["records"])
        self.assertFalse(any(record["kind"] in PERSONAL_MEMORY_KINDS for record in data["records"]))
        for leaked in ("weekly PDF", "formal and legalistic", "contract approvals"):
            self.assertNotIn(leaked, joined_content)
            self.assertNotIn(leaked, data["summary"])

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

    def test_source_dates_are_preserved_when_record_text_has_no_date(self) -> None:
        email = extract_local(
            """Source: Email
Subject: Source date decision
From: Alex Partner <alex@example.com>
To: sarpt@example.com
Date: Mon, 29 Jun 2026 10:00:00 +0000

We decided Project Atlas should keep source dates for email decisions.
""",
            "email",
        )
        email_decision = next(record for record in email["records"] if "email decisions" in record["content"])
        self.assertEqual(email_decision["occurred_at"], "2026-06-29")

        slack = extract_local(
            "2026-06-29T10:01:00+00:00 Alex: We decided Project Atlas should keep Slack line dates.",
            "slack",
        )
        slack_decision = next(record for record in slack["records"] if "Slack line dates" in record["content"])
        self.assertEqual(slack_decision["occurred_at"], "2026-06-29")

        calendar = extract_local(
            "20260629T170000Z - Project Meridian calendar review verified schedule memory.",
            "calendar",
        )
        calendar_event = next(record for record in calendar["records"] if "Project Meridian" in record["content"])
        self.assertEqual(calendar_event["occurred_at"], "2026-06-29")

    def test_repeated_sentences_do_not_duplicate_records_or_summary(self) -> None:
        data = extract_local(
            "Ada likes coffee. Ada likes coffee. "
            "We decided Project Atlas uses five tabs. We decided Project Atlas uses five tabs.",
            "notes",
        )
        contents = [record["content"] for record in data["records"]]

        self.assertEqual(contents.count("We decided Project Atlas uses five tabs."), 1)
        self.assertEqual(data["summary"].count("Ada likes coffee."), 1)
        self.assertEqual(data["summary"].count("We decided Project Atlas uses five tabs."), 1)


if __name__ == "__main__":
    unittest.main()
