from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from backend.app import extractor
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
    def test_pathological_unbroken_blob_does_not_hang(self) -> None:
        # A real Claude/ChatGPT export message can be a huge unbroken run (base64 attachment,
        # minified code, a long URL). The old path/URL strip regexes backtracked O(n^2) on these
        # and hung extraction for minutes -> the app's import spinner never finished. Extraction
        # must complete quickly and not crash.
        import time as _time
        for blob in ("X" * 500_000,
                     "https://example.com/" + "a" * 400_000,
                     ("QUJDREVG" * 60_000)):
            start = _time.monotonic()
            data = extract_local(blob, "claude")
            self.assertLess(_time.monotonic() - start, 10.0, "extraction hung on an unbroken blob")
            self.assertIsInstance(data.get("records"), list)
        # A path/URL blob must not seed junk entities, and real names still survive.
        ents = [e["name"] for e in extract_local(
            "Met Marcus Feld about Project Atlas. DB at /Users/me/index.sqlite; see example.com.",
            "docs")["entities"]]
        self.assertIn("Marcus Feld", ents)
        self.assertNotIn("Users", ents)


    def test_self_authored_keeps_personal_memory_on_conversation_sources(self) -> None:
        # remember_this is an explicit "save this about me": personal memories must survive even
        # when the calling agent labels the source claude/chatgpt/slack (conversation sources that
        # gate unattributed personal text on the import path). Regression for the silent drop
        # where remember_this("I dislike ...", source="claude") produced ZERO memories.
        for source in ("claude", "chatgpt", "slack", "ai-chat"):
            with self.subTest(source=source):
                with patch.dict(os.environ, {"ANTHROPIC_API_KEY": ""}):
                    data = extract_context("I dislike long status meetings.", source, self_authored=True)
                self.assertTrue(
                    any(record["kind"] == "negative" for record in data["records"]),
                    data["records"],
                )
        # The import path (self_authored omitted) still drops unattributed personal text from
        # multi-author sources — anti-pollution stays intact.
        unattributed = extract_local("I dislike long status meetings.", "slack")
        self.assertFalse(any(record["kind"] in PERSONAL_MEMORY_KINDS for record in unattributed["records"]))

    def test_deterministic_triggers_cover_all_memory_layers(self) -> None:
        # Every layer must be reachable from natural phrasing variants — not just canonical
        # keyword forms. Regression for preference/negative/episodic falling through to semantic.
        cases = {
            "I strongly prefer concise, direct writing over long prose.": "preference",
            "I always use tabs over spaces.": "preference",
            "I really dislike long status meetings.": "negative",
            "I can't stand vague acceptance criteria.": "negative",
            "My writing voice is warm but precise.": "style",
            "We decided to use PostgreSQL for the main store.": "decision",
            "To release: bump the version, tag it, then notarize.": "procedural",
            "I met Marcus in Lisbon to plan the offsite.": "episodic",
            "Had a call with Priya about the roadmap.": "episodic",
            "Our Q3 revenue target is two million dollars.": "semantic",
            "Users prefer the dark theme in our surveys.": "semantic",  # not the USER's preference
        }
        for text, expected_layer in cases.items():
            with self.subTest(text=text):
                data = extract_local(text, "docs")
                layers = [record.get("layer") for record in data["records"]]
                self.assertIn(expected_layer, layers, f"{text!r} -> {layers}")

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
2026-06-29T13:02:00+00:00 Sarpt: We decided Project Atlas should keep Home, Review, Ask, and Connections & Privacy.
""",
            "slack",
        )
        joined_content = "\n".join(record["content"] for record in data["records"])
        self.assertFalse(any(record["kind"] == "negative" for record in data["records"]))
        for leaked in ("splashy launch", "bad fit", "animated onboarding", "not helpful"):
            self.assertNotIn(leaked, joined_content)
            self.assertNotIn(leaked, data["summary"])
        self.assertTrue(any(record["kind"] == "decision" and "Home, Review, Ask" in record["content"] for record in data["records"]))

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

    def test_browser_capture_unattributed_personal_text_does_not_seed_personal_memory(self) -> None:
        data = extract_local(
            "I prefer promotional product pages with long testimonials. "
            "My writing style is breathless and emoji-heavy. "
            "Never use local-first privacy language.",
            "browser-capture",
        )
        joined_content = "\n".join(record["content"] for record in data["records"])

        self.assertFalse(any(record["kind"] in PERSONAL_MEMORY_KINDS for record in data["records"]))
        for leaked in ("promotional product pages", "breathless", "local-first privacy"):
            self.assertNotIn(leaked, joined_content)
            self.assertNotIn(leaked, data["summary"])

    def test_local_docs_style_headings_still_seed_personal_memory(self) -> None:
        data = extract_local(
            """Voice:
My writing style uses terse project notes.

Preference:
I prefer source-backed answers with direct caveats.

Avoid:
Never use ceremonial launch intros.
""",
            "docs",
        )
        records = data["records"]

        self.assertTrue(any(record["kind"] == "style" and "terse project notes" in record["content"] for record in records))
        self.assertTrue(any(record["kind"] == "preference" and "source-backed answers" in record["content"] for record in records))
        self.assertTrue(any(record["kind"] == "negative" and "ceremonial launch intros" in record["content"] for record in records))

    def test_obsidian_markdown_cleanup_keeps_memory_without_markup(self) -> None:
        data = extract_local(
            """---
title: Project Atlas
tags: #cortex #todo
created: 2026-06-29
---
# Project Atlas

> [!NOTE] Template block
> This callout should not become memory.

```dataview
TABLE file.mtime
FROM #cortex
```

- [ ] Follow up with Dana about [[Project Atlas|Atlas]] review.
I decided [[Project Atlas|Atlas]] should use [source-backed retrieval](https://example.com) for MCP memory.
I prefer #cortex notes that keep [[People/Dana|Dana]] citations clean.
Never use [[Templates/Marketing]] boilerplate in memory.
""",
            "obsidian",
        )
        records = data["records"]
        tasks = data["tasks"]
        joined_content = "\n".join([*(record["content"] for record in records), *(task["content"] for task in tasks), data["summary"]])

        for leaked in ("tags:", "#todo", "[[", "]]", "](https://example.com)", "dataview", "Template block", "This callout should not become memory.", "TABLE file.mtime"):
            self.assertNotIn(leaked, joined_content)
        self.assertFalse(any(task["content"].startswith("tags:") for task in tasks))
        self.assertTrue(any(task["content"] == "Follow up with Dana about Atlas review." for task in tasks))
        self.assertTrue(any(record["kind"] == "decision" and "Atlas should use source-backed retrieval" in record["content"] for record in records))
        self.assertTrue(any(record["kind"] == "preference" and "cortex notes" in record["content"] and "Dana citations" in record["content"] for record in records))
        self.assertTrue(any(record["kind"] == "negative" and "Marketing boilerplate" in record["content"] for record in records))

    def test_model_extraction_post_filter_drops_disallowed_personal_records(self) -> None:
        raw_text = (
            "I prefer scraped marketing pages with long testimonials. "
            "We decided Project Atlas should keep cited source paths."
        )
        data = extractor._normalize_extraction(
            {
                "records": [
                    {
                        "kind": "preference",
                        "content": "I prefer scraped marketing pages with long testimonials.",
                    },
                    {
                        "kind": "decision",
                        "content": "We decided Project Atlas should keep cited source paths.",
                    },
                ],
                "tasks": [],
                "entities": [],
                "summary": "",
            },
            raw_text,
            "browser-capture",
        )

        filtered = extractor._filter_disallowed_personal_records(data, raw_text, "browser-capture")
        contents = [record["content"] for record in filtered["records"]]

        self.assertNotIn("I prefer scraped marketing pages with long testimonials.", contents)
        self.assertIn("We decided Project Atlas should keep cited source paths.", contents)

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
            "We decided Project Atlas uses Home, Review, and Ask. We decided Project Atlas uses Home, Review, and Ask.",
            "notes",
        )
        contents = [record["content"] for record in data["records"]]

        self.assertEqual(contents.count("We decided Project Atlas uses Home, Review, and Ask."), 1)
        self.assertEqual(data["summary"].count("Ada likes coffee."), 1)
        self.assertEqual(data["summary"].count("We decided Project Atlas uses Home, Review, and Ask."), 1)

    def test_sentence_initial_trigger_words_do_not_become_entities(self) -> None:
        # "Decided"/"Process" opening a sentence are extraction labels, not people — before
        # the common-word guard they polluted the graph and People & projects as fake persons.
        data = extract_local("Decided to use PostgreSQL. Process: always tag releases.", "notes")
        entities = {entity["name"]: entity for entity in data["entities"]}
        self.assertNotIn("Decided", entities)
        self.assertNotIn("Process", entities)
        # The real mid-sentence CamelCase product still survives, classified as an org.
        self.assertIn("PostgreSQL", entities)
        self.assertEqual(entities["PostgreSQL"]["kind"], "org")

    def test_person_and_project_entities_survive_the_common_word_guard(self) -> None:
        # Multi-word candidates shed the sentence-initial trigger verb ("Met") instead of
        # being dropped wholesale or kept as the bogus person "Met Marcus Feld".
        data = extract_local("Met Marcus Feld about Project Atlas.", "notes")
        ids = {entity["id"] for entity in data["entities"]}
        self.assertIn("person_marcus-feld", ids)
        self.assertIn("project_project-atlas", ids)
        self.assertNotIn("person_met-marcus-feld", ids)

    def test_sentence_initial_person_name_is_still_extracted(self) -> None:
        # A sentence-initial capitalized token that is NOT a common English word stays a person.
        data = extract_local("Marcus joined the call yesterday.", "notes")
        ids = {entity["id"] for entity in data["entities"]}
        self.assertIn("person_marcus", ids)

    def test_path_fragments_do_not_become_entities(self) -> None:
        # "/Users/me/data/index.sqlite" must not seed person "Users" or filename entities.
        data = extract_local("The db is at /Users/me/data/index.sqlite", "notes")
        self.assertEqual(data["entities"], [])

    def test_connector_labeled_content_is_preserved_without_metadata_labels(self) -> None:
        data = extract_local(
            """Repository: doppl/cortex
State: open
Author: alex
URL: https://github.com/doppl-tech/cortex-app/issues/42
Title: Decision: Project LabelOnly should preserve title-only connector memories.
Summary: Project SummaryOnly should keep labeled connector summaries retrievable.
Description: Procedure: Project DescriptionOnly should review source labels before Ask.
Highlight: I prefer Project HighlightOnly answers that keep source highlights cited.
""",
            "github",
        )
        joined = "\n".join([*(record["content"] for record in data["records"]), data["summary"]])

        self.assertTrue(any(record["kind"] == "decision" and "Project LabelOnly" in record["content"] for record in data["records"]))
        self.assertTrue(any(record["kind"] == "claim" and "Project SummaryOnly" in record["content"] for record in data["records"]))
        self.assertTrue(any(record["kind"] == "procedure" and "Project DescriptionOnly" in record["content"] for record in data["records"]))
        self.assertFalse(any(record["kind"] == "preference" and "Project HighlightOnly" in record["content"] for record in data["records"]))
        for leaked in ("Repository:", "State:", "Author:", "URL:", "Title:", "Summary:", "Description:", "Highlight:"):
            self.assertNotIn(leaked, joined)


import re


def _milestone_note(count: int) -> str:
    subsystems = ["auth", "billing", "search", "sync", "graph"]
    return "\n".join(
        f"The migration milestone number {i} shipped the {subsystems[i % len(subsystems)]} "
        f"subsystem on day {i}."
        for i in range(1, count + 1)
    )


def _milestones_covered(records: list[dict]) -> set[int]:
    covered: set[int] = set()
    for record in records:
        match = re.search(r"milestone number (\d+)", record.get("content", ""))
        if match:
            covered.add(int(match.group(1)))
    return covered


class LargeCaptureExtractionTests(unittest.TestCase):
    """A large flat free-form capture (a long pasted note or an un-chunked free-form
    import doc) has no chunk markers, so it is extracted as a single unit. It must not be
    silently truncated to the base per-capture candidate budget the way it was before the
    proportional-budget fix (it dropped ~80% of a 200-fact note)."""

    def test_large_flat_capture_is_not_truncated(self) -> None:
        data = extract_local(_milestone_note(200), "note")
        covered = _milestones_covered(data["records"])
        # Before the fix this was exactly BASE_EXTRACTION_CANDIDATE_LIMIT (40) of 200.
        self.assertGreaterEqual(
            len(covered),
            190,
            f"large flat capture lost content: only {len(covered)}/200 facts survived",
        )
        self.assertGreater(len(covered), extractor.BASE_EXTRACTION_CANDIDATE_LIMIT * 2)

    def test_sub_cap_flat_capture_keeps_every_candidate(self) -> None:
        # A note below the base budget must still yield all of its facts (unchanged path).
        count = extractor.BASE_EXTRACTION_CANDIDATE_LIMIT - 10
        data = extract_local(_milestone_note(count), "note")
        covered = _milestones_covered(data["records"])
        self.assertEqual(len(covered), count)

    def test_conversation_capture_scales_past_the_base_cap(self) -> None:
        # A long conversation pasted DIRECTLY into the capture box is a single un-chunked unit
        # (only imports chunk multi-turn exports upstream). The old fixed base cap silently
        # discarded everything past the top 40 candidates — a confirmed data-loss defect — so
        # conversational captures now scale with content too, bounded by the safety ceiling.
        turns = []
        for i in range(1, 61):
            turns.append(
                f"user: I want feature number {i} built with careful attention to detail please."
            )
            turns.append(f"assistant: Understood, I will build feature {i} for you soon.")
        data = extract_local("--- Messages ---\n" + "\n".join(turns), "chatgpt")
        # With 60 distinct user turns, the records must NOT be truncated to the base cap.
        self.assertGreater(len(data["records"]), extractor.BASE_EXTRACTION_CANDIDATE_LIMIT)
        self.assertLessEqual(len(data["records"]), extractor.MAX_EXTRACTION_CANDIDATE_LIMIT)

    def test_extraction_candidate_limit_policy(self) -> None:
        base = extractor.BASE_EXTRACTION_CANDIDATE_LIMIT
        ceiling = extractor.MAX_EXTRACTION_CANDIDATE_LIMIT
        # Both shapes: never below base, scale with content, clamped to the safety ceiling.
        # (A fixed conversational cap silently dropped candidates from big pasted conversations.)
        for has_known_turns in (True, False):
            self.assertEqual(extractor._extraction_candidate_limit(5, has_known_turns), base)
            self.assertEqual(extractor._extraction_candidate_limit(base + 160, has_known_turns), base + 160)
            self.assertEqual(extractor._extraction_candidate_limit(ceiling + 500, has_known_turns), ceiling)


class ClaudeWindowedExtractionTests(unittest.TestCase):
    """The LLM extraction path sent only the first CLAUDE_EXTRACTION_WINDOW_CHARS of a
    capture to the model, silently dropping everything past it on the production path. Large
    captures must be windowed and merged so nothing is lost, while small captures stay a
    single call."""

    def test_windows_are_lossless_and_bounded(self) -> None:
        window = extractor.CLAUDE_EXTRACTION_WINDOW_CHARS
        small = "one line\nsecond line\n"
        self.assertEqual(extractor._claude_extraction_windows(small), [small])

        line = ("x" * 200) + "\n"
        big = "".join(f"line {i} {line}" for i in range(600))  # a few windows worth
        windows = extractor._claude_extraction_windows(big)
        self.assertGreaterEqual(len(windows), 2)
        self.assertLessEqual(len(windows), extractor.MAX_CLAUDE_EXTRACTION_WINDOWS)
        # No character is lost when the content fits within the window budget.
        self.assertEqual("".join(windows), big)
        for w in windows:
            self.assertLessEqual(len(w), window)

    def test_windows_hard_split_a_single_overlong_line(self) -> None:
        window = extractor.CLAUDE_EXTRACTION_WINDOW_CHARS
        big_line = "y" * (window * 2 + 100)
        windows = extractor._claude_extraction_windows(big_line)
        self.assertGreaterEqual(len(windows), 3)
        self.assertEqual("".join(windows), big_line)

    def test_windows_are_capped_for_pathological_input(self) -> None:
        window = extractor.CLAUDE_EXTRACTION_WINDOW_CHARS
        huge = "".join(f"line {i} {'z' * 300}\n" for i in range(window))  # far more than the cap
        windows = extractor._claude_extraction_windows(huge)
        self.assertEqual(len(windows), extractor.MAX_CLAUDE_EXTRACTION_WINDOWS)

    def test_merge_dedupes_records_and_unions_entities(self) -> None:
        parts = [
            {
                "records": [{"id": "a", "content": "A", "kind": "claim"}],
                "tasks": [{"id": "t1", "content": "T1"}],
                "entities": [{"id": "e1", "name": "E1"}],
                "summary": "s1",
            },
            {
                "records": [
                    {"id": "a", "content": "A", "kind": "claim"},
                    {"id": "b", "content": "B", "kind": "claim"},
                ],
                "tasks": [],
                "entities": [{"id": "e1"}, {"id": "e2", "name": "E2"}],
                "summary": "s2",
            },
        ]
        merged = extractor._merge_extractions(parts, "raw capture text", "note")
        self.assertEqual({r["id"] for r in merged["records"]}, {"a", "b"})
        self.assertEqual(len(merged["tasks"]), 1)
        self.assertEqual({e["id"] for e in merged["entities"]}, {"e1", "e2"})
        self.assertEqual(merged["summary"], "s1 s2")

    def test_extract_context_windows_large_capture_over_llm_path(self) -> None:
        calls: list[str] = []

        def fake_claude(raw_text, source, author_aliases=None, self_authored=False):
            calls.append(raw_text)
            return {
                "records": [{"id": f"mem_{len(calls)}", "kind": "claim", "content": raw_text}],
                "tasks": [],
                "entities": [],
                "summary": "",
            }

        line = ("w" * 200) + "\n"
        big = "STARTTOKEN\n" + "".join(f"fact {i} {line}" for i in range(500)) + "ENDTOKEN\n"
        self.assertGreater(len(big), extractor.CLAUDE_EXTRACTION_WINDOW_CHARS)

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False), \
                patch.object(extractor, "_extract_with_claude", side_effect=fake_claude):
            result = extract_context(big, source="note")

        self.assertGreaterEqual(len(calls), 2, "large capture was not windowed on the LLM path")
        merged_content = "\n".join(r["content"] for r in result["records"])
        # Content from both the first and last window is present -> nothing silently dropped.
        self.assertIn("STARTTOKEN", merged_content)
        self.assertIn("ENDTOKEN", merged_content)

    def test_extract_context_small_capture_stays_single_llm_call(self) -> None:
        calls: list[str] = []

        def fake_claude(raw_text, source, author_aliases=None, self_authored=False):
            calls.append(raw_text)
            return {"records": [], "tasks": [], "entities": [], "summary": ""}

        small = "A short capture that easily fits in one window."
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-test"}, clear=False), \
                patch.object(extractor, "_extract_with_claude", side_effect=fake_claude):
            extract_context(small, source="note")

        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0], small)


class MachineArtifactGateTests(unittest.TestCase):
    """Machine output (ffmpeg concat lists, path dumps, JSON debris) must never become
    memories, summaries, or entities. This is the gate behind the Review-tab quality bar:
    a card whose entire body is absolute paths gives the user nothing to review."""

    CONCAT = (
        "file '/Users/u/Documents/Codex/2026-05-17/outputs/972d89aa1cce4b53ae34cb42d83d318c/videos/scene_mux/0001_scene_1.mp4'\n"
        "file '/Users/u/Documents/Codex/2026-05-17/outputs/972d89aa1cce4b53ae34cb42d83d318c/videos/scene_mux/0002_scene_2.mp4'\n"
        "file '/Users/u/Documents/Codex/2026-05-17/outputs/972d89aa1cce4b53ae34cb42d83d318c/videos/scene_mux/0004_scene_4.mp4'"
    )

    def test_ffmpeg_concat_list_extracts_nothing(self) -> None:
        result = extract_context(self.CONCAT, source="obsidian", extraction_mode="local")
        self.assertEqual(result["records"], [])
        self.assertEqual(result["tasks"], [])
        self.assertEqual(result["summary"], "")

    def test_whole_capture_verdict(self) -> None:
        self.assertTrue(extractor.content_is_machine_artifact(self.CONCAT))
        self.assertFalse(extractor.content_is_machine_artifact(
            "We decided to use PostgreSQL for Atlas because of jsonb support."
        ))
        # Prose that merely mentions one path stays prose.
        self.assertFalse(extractor.content_is_machine_artifact(
            "The deploy config lives in /etc/cortex/deploy.yaml and we decided to keep it there.\n"
            "We agreed the migration ships Tuesday."
        ))

    def test_prose_mentioning_a_path_still_extracts(self) -> None:
        text = "The deploy config lives in /etc/cortex/deploy.yaml and we decided to keep it there."
        result = extract_context(text, source="obsidian", extraction_mode="local")
        self.assertEqual(len(result["records"]), 1)

    def test_escaped_newlines_are_treated_as_line_breaks(self) -> None:
        text = r"I decided to use PostgreSQL for Atlas.\n We agreed the migration ships Tuesday."
        result = extract_context(text, source="structured-export", extraction_mode="local")
        contents = [r["content"] for r in result["records"]]
        self.assertEqual(len(contents), 2)
        for content in contents:
            self.assertNotIn("\\n", content)

    def test_json_fragment_lines_are_rejected(self) -> None:
        text = (
            '{"is_private": false, "is_starter_project": true, "prompt_template": "", '
            '"created_at": "2026-06-10T06:01:50.038271+00:00"}'
        )
        result = extract_context(text, source="structured-export", extraction_mode="local")
        self.assertEqual(result["records"], [])

    def test_acronym_phrases_are_topics_not_people(self) -> None:
        text = (
            "The AI API pricing changed today. AI Coding Costs went up. "
            "API Pricing is under review. Marcus Chen approved the budget."
        )
        entities = {e["name"]: e["kind"] for e in extractor._entities(text)}
        self.assertEqual(entities.get("AI API"), "topic")
        self.assertEqual(entities.get("AI Coding Costs"), "topic")
        self.assertEqual(entities.get("Marcus Chen"), "person")


if __name__ == "__main__":
    unittest.main()
