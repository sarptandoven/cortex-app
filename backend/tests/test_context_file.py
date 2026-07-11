"""Tests for the CLAUDE.md compiler: backend.app.context_file.

Covers, in order:
  1. render_context_block: determinism, budget, citation tags, cite-or-abstain.
  2. sync_context_file: managed-block round-trip (byte-preservation outside markers),
     marker-absent append, file-create, atomicity, CRLF / trailing-newline / nested
     marker-lookalike fixtures.
  3. validate_context_file_path: vault refusal, system-dir refusal, extension allowlist,
     relative-path refusal.
  4. The standalone-server routes (/v1/context-file/preview, /v1/context-file/sync)
     against a REAL CortexStore + a REAL ThreadingHTTPServer, mirroring the pattern
     StandaloneServerTests uses in test_standalone_server.py.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from backend.app.config import Settings
from backend.app.context_file import (
    BEGIN_MARKER,
    END_MARKER,
    KNOWN_CONTEXT_FILENAMES,
    render_context_block,
    render_managed_block,
    sync_context_file,
    validate_context_file_path,
)
from backend.app.database import init_db
from backend.app.storage import CortexStore

import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.adaptation_eval import USER_ID as ADAPTATION_USER_ID, seed_adaptation_memories


# --- Fixtures ------------------------------------------------------------------------

SAMPLE_PROFILE: dict = {
    "generated_at": "2026-07-11T00:00:00Z",
    "readiness": 80,
    "sections": [
        {
            "id": "facts",
            "title": "Facts",
            "confidence": "high",
            "elements": [
                {
                    "text": "Works at Doppl building Cortex, a personal memory layer for AI agents.",
                    "source": "github",
                    "count": 6,
                    "memory_ids": ["mem_1", "mem_2"],
                    "source_url": None,
                },
                {
                    "text": "Based in the Bay Area.",
                    "source": "obsidian",
                    "count": 2,
                    "memory_ids": ["mem_3"],
                    "source_url": None,
                },
            ],
        },
        {
            "id": "how_you_work",
            "title": "How you work",
            "confidence": "medium",
            "elements": [
                {
                    "text": "Ships small, verifiable slices and writes regression tests before moving on.",
                    "source": "github",
                    "count": 4,
                    "memory_ids": ["mem_4"],
                    "source_url": None,
                },
            ],
        },
        {
            "id": "decisions",
            "title": "Key decisions",
            "confidence": "medium",
            "elements": [
                {
                    "text": "Chose sharded SQLite over Postgres for the 10k launch.",
                    "source": "obsidian",
                    "count": 3,
                    "memory_ids": ["mem_6"],
                    "source_url": None,
                },
            ],
        },
        {
            "id": "open_loops",
            "title": "Open loops",
            "confidence": "medium",
            "elements": [
                {
                    "text": "Ship the CLAUDE.md compiler wedge for the judge panel review.",
                    "source": "tasks",
                    "count": 1,
                    "memory_ids": ["task_1"],
                    "source_url": None,
                },
            ],
        },
    ],
    "limitations": [],
}


class FakeProfileStore:
    """Minimal store double exposing only build_profile, for renderer-level tests that
    don't need a real database."""

    def __init__(self, profile: dict) -> None:
        self._profile = profile
        self.build_profile_calls: list[tuple] = []

    def build_profile(self, user_id: str, **kwargs) -> dict:
        self.build_profile_calls.append((user_id, kwargs))
        return self._profile


# --- 1. render_context_block ----------------------------------------------------------


class RenderContextBlockTests(unittest.TestCase):
    def test_deterministic_given_same_profile(self) -> None:
        block1 = render_context_block(profile=SAMPLE_PROFILE)
        block2 = render_context_block(profile=SAMPLE_PROFILE)
        self.assertEqual(block1, block2)

    def test_deterministic_across_store_calls(self) -> None:
        store = FakeProfileStore(SAMPLE_PROFILE)
        block1 = render_context_block(store, "alice", style="claude")
        block2 = render_context_block(store, "alice", style="claude")
        self.assertEqual(block1, block2)
        self.assertEqual(store.build_profile_calls, [("alice", {}), ("alice", {})])

    def test_requires_store_and_user_id_or_profile(self) -> None:
        with self.assertRaises(ValueError):
            render_context_block()

    def test_citation_tags_reference_memory_ids(self) -> None:
        block = render_context_block(profile=SAMPLE_PROFILE)
        self.assertIn("<!-- mem:mem_1, mem:mem_2 -->", block)
        self.assertIn("<!-- mem:mem_4 -->", block)
        self.assertIn("<!-- mem:task_1 -->", block)

    def test_citation_tags_are_html_comments_content_stays_readable(self) -> None:
        block = render_context_block(profile=SAMPLE_PROFILE)
        # The cited fact text itself must read as plain prose (no bracket footnote
        # markers cluttering it) -- only a trailing HTML comment carries the citation.
        self.assertIn("- Works at Doppl building Cortex, a personal memory layer for AI agents.", block)
        # No bracket-style [mem:...] markers should appear (spec allows either
        # comments or footnotes; this renderer chose comments for max readability).
        self.assertNotIn("[mem:", block)

    def test_element_without_memory_ids_renders_without_citation_tag(self) -> None:
        profile = {
            "sections": [
                {
                    "id": "focus",
                    "confidence": "low",
                    "elements": [{"text": "Cortex CLAUDE.md compiler", "source": "topics", "count": 3, "memory_ids": [], "source_url": None}],
                }
            ]
        }
        block = render_context_block(profile=profile)
        self.assertIn("- Cortex CLAUDE.md compiler\n", block)
        self.assertNotIn("<!--", block.split("Cortex CLAUDE.md compiler")[1].split("\n")[0])

    def test_abstains_on_empty_profile(self) -> None:
        block = render_context_block(profile={"sections": []})
        self.assertIn("no well-supported memory", block.lower())
        # Even the abstain body is still wrapped with the managed markers by the writer
        # (tested below); the renderer itself just returns honest prose, not silence.
        self.assertNotIn("## ", block)

    def test_abstains_on_missing_sections_key(self) -> None:
        block = render_context_block(profile={})
        self.assertIn("no well-supported memory", block.lower())

    def test_section_with_no_cited_elements_is_omitted(self) -> None:
        profile = {
            "sections": [
                {"id": "how_you_work", "confidence": "low", "elements": []},
                {"id": "facts", "confidence": "high", "elements": [{"text": "A fact.", "source": "github", "count": 2, "memory_ids": ["m1"], "source_url": None}]},
            ]
        }
        block = render_context_block(profile=profile)
        self.assertNotIn("## How I work", block)
        self.assertIn("## Who I am", block)

    def test_element_missing_text_is_skipped_not_blank_bullet(self) -> None:
        profile = {
            "sections": [
                {
                    "id": "facts",
                    "confidence": "high",
                    "elements": [
                        {"text": "", "source": "github", "count": 2, "memory_ids": ["m1"], "source_url": None},
                        {"text": "Real fact.", "source": "github", "count": 2, "memory_ids": ["m2"], "source_url": None},
                    ],
                }
            ]
        }
        block = render_context_block(profile=profile)
        self.assertNotIn("- <!--", block)
        self.assertIn("- Real fact.", block)

    def test_unknown_section_id_is_ignored(self) -> None:
        profile = {"sections": [{"id": "some_future_layer", "confidence": "high", "elements": [{"text": "x", "source": "s", "count": 1, "memory_ids": [], "source_url": None}]}]}
        block = render_context_block(profile=profile)
        self.assertIn("no well-supported memory", block.lower())

    def test_multiline_element_text_collapses_to_one_bullet_line(self) -> None:
        profile = {
            "sections": [
                {
                    "id": "facts",
                    "confidence": "high",
                    "elements": [{"text": "Line one\nLine two\r\nLine three", "source": "github", "count": 2, "memory_ids": ["m1"], "source_url": None}],
                }
            ]
        }
        block = render_context_block(profile=profile)
        self.assertIn("- Line one Line two Line three", block)
        self.assertEqual(block.count("- Line one"), 1)

    def test_footer_present_with_iso_date(self) -> None:
        block = render_context_block(profile=SAMPLE_PROFILE)
        self.assertIn("Compiled by Cortex on", block)
        self.assertIn("edits inside this block are overwritten", block)
        self.assertIn("keep your own notes outside the markers", block)

    def test_line_budget_is_respected(self) -> None:
        # A pathologically large, well-supported corpus must still respect the ~150-line
        # budget so the rendered block stays cheap for a coding agent to load.
        sections = []
        for i in range(30):
            sections.append(
                {
                    "id": "facts" if i == 0 else "how_you_work",
                    "confidence": "high",
                    "elements": [
                        {"text": f"Fact number {j} in cluster {i}.", "source": "github", "count": 5, "memory_ids": [f"m{i}_{j}"], "source_url": None}
                        for j in range(20)
                    ],
                }
            )
        profile = {"sections": sections}
        block = render_context_block(profile=profile)
        self.assertLessEqual(block.count("\n"), 150)

    def test_invalid_style_falls_back_to_default(self) -> None:
        block_default = render_context_block(profile=SAMPLE_PROFILE, style="claude")
        block_bogus = render_context_block(profile=SAMPLE_PROFILE, style="not-a-real-style")
        self.assertEqual(block_default, block_bogus)

    def test_open_loops_gets_more_room_than_behavioral_sections(self) -> None:
        elements = [
            {"text": f"Open loop {i}", "source": "tasks", "count": 1, "memory_ids": [f"t{i}"], "source_url": None}
            for i in range(8)
        ]
        profile = {"sections": [{"id": "open_loops", "confidence": "medium", "elements": elements}]}
        block = render_context_block(profile=profile)
        # All 8 open loops should survive (the open-loop cap is bigger than the 5-element
        # behavioral cap; only truly excessive corpora hit the outer MAX_BLOCK_LINES).
        for i in range(8):
            self.assertIn(f"Open loop {i}", block)


# --- 2. sync_context_file (managed-block writer) --------------------------------------


class SyncContextFileWriterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.store = FakeProfileStore(SAMPLE_PROFILE)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_creates_missing_file(self) -> None:
        target = self.dir / "CLAUDE.md"
        result = sync_context_file(self.store, "alice", str(target), style="claude")
        self.assertTrue(result["created"])
        self.assertTrue(target.exists())
        text = target.read_text(encoding="utf-8")
        self.assertIn(BEGIN_MARKER, text)
        self.assertIn(END_MARKER, text)

    def test_appends_when_markers_absent(self) -> None:
        target = self.dir / "CLAUDE.md"
        original = "# My hand-written notes\n\nSome prose I wrote myself.\n"
        target.write_text(original, encoding="utf-8")
        result = sync_context_file(self.store, "alice", str(target), style="claude")
        self.assertFalse(result["created"])
        text = target.read_text(encoding="utf-8")
        self.assertTrue(text.startswith(original.rstrip("\n")))
        self.assertIn(BEGIN_MARKER, text)
        self.assertEqual(text.count(BEGIN_MARKER), 1)

    def test_replaces_only_content_between_markers(self) -> None:
        target = self.dir / "CLAUDE.md"
        before = "# Header\n\nBefore text.\n\n"
        after = "\n\nAfter text that must survive.\n"
        original = f"{before}{BEGIN_MARKER}\nSTALE CONTENT HERE\n{END_MARKER}{after}"
        target.write_text(original, encoding="utf-8")
        sync_context_file(self.store, "alice", str(target), style="claude")
        text = target.read_text(encoding="utf-8")
        self.assertIn("Before text.", text)
        self.assertIn("After text that must survive.", text)
        self.assertNotIn("STALE CONTENT HERE", text)
        self.assertEqual(text.count(BEGIN_MARKER), 1)
        self.assertEqual(text.count(END_MARKER), 1)
        # Byte-preservation: everything before the opener is untouched.
        self.assertEqual(text.split(BEGIN_MARKER)[0], before)
        # Everything after the closer is untouched.
        self.assertEqual(text.split(END_MARKER)[1], after)

    def test_indented_marker_lookalike_in_prose_is_not_the_managed_region(self) -> None:
        # Regression (adversarial review, HIGH): a user documenting Cortex's OWN markers
        # inside an indented code block must not have that illustrative content destroyed.
        # The real managed block must be appended separately, the example left byte-exact.
        target = self.dir / "CLAUDE.md"
        example = (
            "# How Cortex marks its block\n\n"
            "```\n"
            f"    {BEGIN_MARKER}\n"
            "    some example illustrative content\n"
            f"    {END_MARKER}\n"
            "```\n"
        )
        target.write_text(example, encoding="utf-8")
        sync_context_file(self.store, "alice", str(target), style="claude")
        text = target.read_text(encoding="utf-8")
        # The indented example survives verbatim...
        self.assertIn("    some example illustrative content", text)
        self.assertIn(example.rstrip("\n"), text)
        # ...and the REAL managed block was appended at column 0 (a genuine marker line).
        self.assertIn(f"\n{BEGIN_MARKER}\n", text)

    def test_mixed_endings_preserve_untouched_lines_byte_for_byte(self) -> None:
        # Regression (adversarial review, MEDIUM): a predominantly-LF file with even one
        # CRLF must NOT have all its untouched LF line endings rewritten to CRLF.
        target = self.dir / "CLAUDE.md"
        original = b"line1\nline2\r\nline3\nline4\n"
        target.write_bytes(original)
        sync_context_file(self.store, "alice", str(target), style="claude")
        raw = target.read_bytes()
        # Every original line ending is preserved exactly where it was.
        self.assertIn(b"line1\nline2\r\nline3\nline4\n", raw)
        # We did not flip line1/line3/line4 to CRLF.
        self.assertNotIn(b"line1\r\n", raw)
        self.assertNotIn(b"line3\r\n", raw)

    def test_resync_is_idempotent_marker_count_stays_one(self) -> None:
        target = self.dir / "CLAUDE.md"
        sync_context_file(self.store, "alice", str(target), style="claude")
        sync_context_file(self.store, "alice", str(target), style="claude")
        sync_context_file(self.store, "alice", str(target), style="claude")
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text.count(BEGIN_MARKER), 1)
        self.assertEqual(text.count(END_MARKER), 1)

    def test_crlf_file_stays_crlf_outside_and_inside_block(self) -> None:
        target = self.dir / "AGENTS.md"
        target.write_bytes(b"Windows notes.\r\nSecond line.\r\n")
        sync_context_file(self.store, "alice", str(target), style="agents")
        raw = target.read_bytes()
        # No bare LF anywhere: every newline byte is part of a CRLF pair.
        self.assertEqual(raw.replace(b"\r\n", b"").count(b"\n"), 0)
        self.assertIn(b"Windows notes.\r\n", raw)

    def test_crlf_preserved_across_marker_replace(self) -> None:
        target = self.dir / "AGENTS.md"
        original = (
            "Before.\r\n\r\n" + BEGIN_MARKER + "\r\nold\r\n" + END_MARKER + "\r\n\r\nAfter.\r\n"
        )
        target.write_bytes(original.encode("utf-8"))
        sync_context_file(self.store, "alice", str(target), style="agents")
        raw = target.read_bytes()
        self.assertEqual(raw.replace(b"\r\n", b"").count(b"\n"), 0)
        self.assertIn(b"Before.\r\n", raw)
        self.assertIn(b"After.\r\n", raw)
        self.assertNotIn(b"old", raw)

    def test_trailing_newline_state_of_untouched_region_preserved_no_markers_case(self) -> None:
        target = self.dir / "CLAUDE.md"
        # No trailing newline at all in the original file.
        target.write_bytes(b"No trailing newline here")
        sync_context_file(self.store, "alice", str(target), style="claude")
        raw = target.read_bytes()
        self.assertTrue(raw.startswith(b"No trailing newline here"))

    def test_marker_lookalike_inside_user_prose_without_real_closer_is_not_corrupted(self) -> None:
        # Regression (the vault_markdown lesson): an OPENER-shaped line with no matching
        # closer anywhere below it must never be treated as a real managed region --
        # nothing may be truncated. The safe fallback is to append a NEW, well-formed
        # managed block rather than mangling the user's existing prose.
        target = self.dir / "CLAUDE.md"
        original = (
            "Some notes.\n\n"
            + BEGIN_MARKER
            + "\nI was telling a friend about this exact marker string, no closer here though.\n\n"
            "Still more of my own notes after.\n"
        )
        target.write_text(original, encoding="utf-8")
        sync_context_file(self.store, "alice", str(target), style="claude")
        text = target.read_text(encoding="utf-8")
        # The user's original text (including their own lookalike opener line) survives
        # byte-for-byte as a prefix; nothing before the appended block was truncated.
        self.assertTrue(text.startswith(original.rstrip("\n")))
        self.assertIn("Still more of my own notes after.", text)
        self.assertIn("I was telling a friend about this exact marker string", text)
        # A second, real (matched) managed block was appended after it.
        self.assertEqual(text.count(BEGIN_MARKER), 2)
        self.assertEqual(text.count(END_MARKER), 1)

    def test_content_ending_exactly_at_closer_with_no_trailing_newline(self) -> None:
        target = self.dir / "CLAUDE.md"
        original = "Intro.\n\n" + BEGIN_MARKER + "\nold body\n" + END_MARKER  # no trailing \n
        target.write_text(original, encoding="utf-8")
        sync_context_file(self.store, "alice", str(target), style="claude")
        text = target.read_text(encoding="utf-8")
        self.assertIn("Intro.", text)
        self.assertNotIn("old body", text)
        self.assertEqual(text.count(BEGIN_MARKER), 1)

    def test_atomic_write_leaves_no_partial_file_on_simulated_failure(self) -> None:
        target = self.dir / "CLAUDE.md"
        target.write_text("Original untouched content.\n", encoding="utf-8")

        import backend.app.context_file as context_file_module

        original_replace = os.replace

        def boom(*args, **kwargs):
            raise OSError("simulated crash mid-write")

        os.replace = boom
        try:
            with self.assertRaises(OSError):
                sync_context_file(self.store, "alice", str(target), style="claude")
        finally:
            os.replace = original_replace

        # The original file must be completely unchanged (no partial/half-written file).
        self.assertEqual(target.read_text(encoding="utf-8"), "Original untouched content.\n")
        # No stray temp files left behind in the directory.
        leftovers = [p for p in self.dir.iterdir() if p.name.startswith(".") and p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_returns_expected_result_shape(self) -> None:
        target = self.dir / "CLAUDE.md"
        result = sync_context_file(self.store, "alice", str(target), style="claude")
        self.assertEqual(set(result.keys()), {"path", "bytes_written", "block_lines", "created"})
        self.assertEqual(result["path"], str(target))
        self.assertGreater(result["bytes_written"], 0)
        self.assertGreater(result["block_lines"], 0)
        self.assertTrue(result["created"])

    def test_render_managed_block_wraps_body_exactly(self) -> None:
        wrapped = render_managed_block("hello\nworld")
        self.assertEqual(wrapped, f"{BEGIN_MARKER}\nhello\nworld\n{END_MARKER}")


# --- 3. validate_context_file_path -----------------------------------------------------


class ValidateContextFilePathTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "vault").mkdir()
        (self.dir / "project").mkdir()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_accepts_absolute_md_path_with_existing_parent(self) -> None:
        target = self.dir / "project" / "CLAUDE.md"
        resolved = validate_context_file_path(str(target))
        self.assertEqual(resolved.name, "CLAUDE.md")

    def test_accepts_known_dotfile_without_md_extension(self) -> None:
        target = self.dir / "project" / ".cursorrules"
        resolved = validate_context_file_path(str(target))
        self.assertEqual(resolved.name, ".cursorrules")

    def test_all_known_filenames_are_accepted(self) -> None:
        for name in KNOWN_CONTEXT_FILENAMES:
            target = self.dir / "project" / name
            resolved = validate_context_file_path(str(target))
            self.assertEqual(resolved.name, name)

    def test_rejects_empty_path(self) -> None:
        with self.assertRaises(ValueError):
            validate_context_file_path("")

    def test_rejects_relative_path(self) -> None:
        with self.assertRaises(ValueError):
            validate_context_file_path("relative/CLAUDE.md")

    def test_rejects_unknown_extension(self) -> None:
        with self.assertRaises(ValueError):
            validate_context_file_path(str(self.dir / "project" / "notes.txt"))

    def test_expands_tilde(self) -> None:
        home = str(Path.home())
        # We can't guarantee a writable spot under the real home in CI, but expansion
        # itself (not resolution failure) is what's under test: no ValueError about "~".
        try:
            validate_context_file_path("~/CLAUDE.md")
        except ValueError as exc:
            self.assertNotIn("absolute", str(exc))

    def test_rejects_path_inside_vault(self) -> None:
        vault_root = self.dir / "vault"
        target = vault_root / "CLAUDE.md"
        with self.assertRaises(ValueError) as ctx:
            validate_context_file_path(str(target), vault_root=str(vault_root))
        self.assertIn("vault", str(ctx.exception).lower())

    def test_rejects_path_deep_inside_vault(self) -> None:
        vault_root = self.dir / "vault"
        nested = vault_root / "memories" / "sub"
        nested.mkdir(parents=True)
        target = nested / "CLAUDE.md"
        with self.assertRaises(ValueError):
            validate_context_file_path(str(target), vault_root=str(vault_root))

    def test_allows_path_outside_vault_even_when_vault_root_given(self) -> None:
        vault_root = self.dir / "vault"
        target = self.dir / "project" / "CLAUDE.md"
        resolved = validate_context_file_path(str(target), vault_root=str(vault_root))
        self.assertEqual(resolved.name, "CLAUDE.md")

    def test_rejects_system_directories(self) -> None:
        for bad in ("/etc/CLAUDE.md", "/usr/local/CLAUDE.md", "/System/CLAUDE.md", "/bin/CLAUDE.md"):
            with self.assertRaises(ValueError):
                validate_context_file_path(bad)

    def test_rejects_missing_parent_directory(self) -> None:
        target = self.dir / "does-not-exist-dir" / "CLAUDE.md"
        with self.assertRaises(ValueError):
            validate_context_file_path(str(target))

    def test_case_insensitive_md_extension(self) -> None:
        target = self.dir / "project" / "NOTES.MD"
        resolved = validate_context_file_path(str(target))
        self.assertEqual(resolved.name, "NOTES.MD")

    def test_rejects_symlink_named_like_a_context_file(self) -> None:
        # Regression (adversarial review, HIGH): a symlink named CLAUDE.md that points at
        # an arbitrary non-.md file (a shell rc, ssh authorized_keys, aws creds) must be
        # refused — otherwise the extension allowlist is bypassed and "sync my context
        # file" becomes an arbitrary write (and, for .zshrc/.bashrc, code execution).
        project = self.dir / "project"
        project.mkdir(exist_ok=True)
        secret = project / "zshrc.txt"
        secret.write_text("# real shell rc\n", encoding="utf-8")
        link = project / "CLAUDE.md"
        link.symlink_to(secret)
        with self.assertRaises(ValueError):
            validate_context_file_path(str(link))
        # The write must never have happened through the link.
        self.assertEqual(secret.read_text(encoding="utf-8"), "# real shell rc\n")

    def test_rejects_symlink_whose_resolved_name_leaves_the_allowlist(self) -> None:
        # Even a symlink pointing at another allowed-looking dir must not redirect the
        # write to a file whose RESOLVED name is outside the .md allowlist.
        project = self.dir / "project"
        project.mkdir(exist_ok=True)
        target = project / "credentials"  # extensionless, not allowlisted
        target.write_text("secret\n", encoding="utf-8")
        link = project / "AGENTS.md"
        link.symlink_to(target)
        with self.assertRaises(ValueError):
            validate_context_file_path(str(link))


# --- Real-store integration: renderer determinism against a genuine corpus -----------


class RealStoreRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "index.sqlite"
        self.vault_path = Path(self.tmp.name) / "Cortex.vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_render_against_real_seeded_corpus_is_deterministic_and_cited(self) -> None:
        seed_adaptation_memories(self.store, ADAPTATION_USER_ID)
        block1 = render_context_block(self.store, ADAPTATION_USER_ID, style="claude")
        block2 = render_context_block(self.store, ADAPTATION_USER_ID, style="claude")
        self.assertEqual(block1, block2)
        self.assertIn("<!-- mem:", block1)
        self.assertIn("Compiled by Cortex on", block1)
        self.assertLessEqual(block1.count("\n"), 150)

    def test_render_against_empty_corpus_abstains_honestly(self) -> None:
        block = render_context_block(self.store, "nobody-has-memories-yet", style="claude")
        self.assertIn("no well-supported memory", block.lower())

    def test_sync_against_real_store_writes_readable_file(self) -> None:
        seed_adaptation_memories(self.store, ADAPTATION_USER_ID)
        target = Path(self.tmp.name) / "CLAUDE.md"
        result = sync_context_file(self.store, ADAPTATION_USER_ID, str(target), style="claude")
        self.assertTrue(result["created"])
        text = target.read_text(encoding="utf-8")
        self.assertIn(BEGIN_MARKER, text)
        self.assertIn(END_MARKER, text)

    def test_vault_root_path_helper_matches_actual_vault(self) -> None:
        self.assertEqual(self.store.vault_root_path(ADAPTATION_USER_ID), str(self.vault_path))


# --- 4. standalone_server routes -------------------------------------------------------


class ContextFileRouteTests(unittest.TestCase):
    """Exercises POST /v1/context-file/preview and /v1/context-file/sync against a real
    ThreadingHTTPServer + a real CortexStore, mirroring StandaloneServerTests' pattern in
    test_standalone_server.py (a separate process-wide module under test, so we swap the
    module-level `store`/`settings` the same way and restore them in tearDown)."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.db_path = self.dir / "index.sqlite"
        self.vault_path = self.dir / "Cortex.vault"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, self.vault_path)
        seed_adaptation_memories(self.store, "local")

        from backend.app import standalone_server

        self.standalone_server = standalone_server
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        self.original_origins = standalone_server.ALLOWED_CORS_ORIGINS
        self.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.store = self.store
        standalone_server.settings = Settings(
            vault_path=self.vault_path,
            db_path=self.db_path,
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
            default_user_id="local",
        )
        standalone_server.ALLOWED_CORS_ORIGINS = {"http://127.0.0.1:8766", "http://localhost:8766"}
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()

        self.server = standalone_server.ThreadingHTTPServer(("127.0.0.1", 0), standalone_server.CortexRequestHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)
        self.standalone_server.store = self.original_store
        self.standalone_server.settings = self.original_settings
        self.standalone_server.ALLOWED_CORS_ORIGINS = self.original_origins
        self.standalone_server.REQUEST_GUARDS = self.original_guards
        self.tmp.cleanup()

    def post_json(self, path: str, payload: dict, token: str = "test-token"):
        headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
        data = json.dumps(payload).encode("utf-8")
        return request.urlopen(request.Request(self.base_url + path, data=data, headers=headers, method="POST"), timeout=5)

    def test_preview_returns_rendered_block_without_writing(self) -> None:
        with self.post_json("/v1/context-file/preview", {"style": "claude"}) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertIn("block", payload)
        self.assertIn("Compiled by Cortex on", payload["block"])
        self.assertEqual(payload["style"], "claude")

    def test_preview_rejects_unknown_style(self) -> None:
        with self.assertRaises(error.HTTPError) as ctx:
            self.post_json("/v1/context-file/preview", {"style": "not-a-style"})
        self.assertEqual(ctx.exception.code, 422)

    def test_sync_writes_file_and_returns_result_contract(self) -> None:
        target = self.dir / "CLAUDE.md"
        with self.post_json("/v1/context-file/sync", {"path": str(target), "style": "claude"}) as response:
            payload = json.loads(response.read().decode("utf-8"))
        self.assertEqual(set(payload.keys()), {"path", "bytes_written", "block_lines", "created"})
        self.assertTrue(payload["created"])
        self.assertTrue(target.exists())
        self.assertIn(BEGIN_MARKER, target.read_text(encoding="utf-8"))

    def test_sync_requires_path(self) -> None:
        with self.assertRaises(error.HTTPError) as ctx:
            self.post_json("/v1/context-file/sync", {"style": "claude"})
        self.assertEqual(ctx.exception.code, 422)

    def test_sync_rejects_path_inside_vault(self) -> None:
        target = self.vault_path / "CLAUDE.md"
        with self.assertRaises(error.HTTPError) as ctx:
            self.post_json("/v1/context-file/sync", {"path": str(target), "style": "claude"})
        self.assertEqual(ctx.exception.code, 422)
        self.assertIn("vault", ctx.exception.read().decode("utf-8").lower())

    def test_sync_rejects_system_directory(self) -> None:
        with self.assertRaises(error.HTTPError) as ctx:
            self.post_json("/v1/context-file/sync", {"path": "/etc/CLAUDE.md", "style": "claude"})
        self.assertEqual(ctx.exception.code, 422)

    def test_sync_rejects_bad_extension(self) -> None:
        target = self.dir / "notes.txt"
        with self.assertRaises(error.HTTPError) as ctx:
            self.post_json("/v1/context-file/sync", {"path": str(target), "style": "claude"})
        self.assertEqual(ctx.exception.code, 422)

    def test_sync_requires_existing_parent_directory(self) -> None:
        target = self.dir / "nope" / "CLAUDE.md"
        with self.assertRaises(error.HTTPError) as ctx:
            self.post_json("/v1/context-file/sync", {"path": str(target), "style": "claude"})
        self.assertEqual(ctx.exception.code, 422)

    def test_sync_is_idempotent_over_http(self) -> None:
        target = self.dir / "AGENTS.md"
        self.post_json("/v1/context-file/sync", {"path": str(target), "style": "agents"}).close()
        self.post_json("/v1/context-file/sync", {"path": str(target), "style": "agents"}).close()
        text = target.read_text(encoding="utf-8")
        self.assertEqual(text.count(BEGIN_MARKER), 1)

    def test_preview_requires_auth(self) -> None:
        with self.assertRaises(error.HTTPError) as ctx:
            request.urlopen(
                request.Request(
                    self.base_url + "/v1/context-file/preview",
                    data=json.dumps({"style": "claude"}).encode("utf-8"),
                    headers={"Content-Type": "application/json"},
                    method="POST",
                ),
                timeout=5,
            )
        self.assertEqual(ctx.exception.code, 401)

    def test_preview_is_read_scoped_for_api_tokens(self) -> None:
        # Global admin token (api_key) always passes; scope discipline is exercised at
        # the unit level in ContextFileScopeTests below, mirroring test_standalone_server's
        # own split between "route reaches the store" (here) and "scope math" (there).
        with self.post_json("/v1/context-file/preview", {"style": "claude"}) as response:
            self.assertEqual(response.status, 200)


class ContextFileScopeTests(unittest.TestCase):
    """_required_api_scope is pure and side-effect-free -- test it directly rather than
    standing up a server, mirroring how the module already separates scope math from
    routing."""

    def test_preview_is_read_scope(self) -> None:
        from backend.app.standalone_server import _required_api_scope

        self.assertEqual(_required_api_scope("POST", "/v1/context-file/preview"), "read")

    def test_sync_is_export_scope(self) -> None:
        from backend.app.standalone_server import _required_api_scope

        self.assertEqual(_required_api_scope("POST", "/v1/context-file/sync"), "export")


if __name__ == "__main__":
    unittest.main()
