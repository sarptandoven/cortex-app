from __future__ import annotations

import json
import tempfile
import threading
import unittest
from pathlib import Path
from urllib import error, request

from backend.app.database import init_db
from backend.app.import_diff import compare_vendor_export, compare_vendor_memory
from backend.app.source_ingest import (
    normalize_vendor_facts,
    parse_vendor_memory_export,
    vendor_memory_label,
)
from backend.app.storage import CortexStore


# ---------------------------------------------------------------------------
# Realistic synthetic vendor MEMORY exports (the SHORT "saved memories" lists,
# NOT full conversations). One per vendor, in the shape each actually ships.
# ---------------------------------------------------------------------------

# ChatGPT surfaces "Saved memories" as a bulleted list (copy-paste) or a small JSON.
CHATGPT_MARKDOWN = """\
Saved memories

- Prefers Python for backend work and TypeScript on the frontend.
- Is building a startup called Cortex, a shared memory layer for AI assistants.
- Lives in San Francisco.
- Drinks oat milk, not dairy.
"""

CHATGPT_JSON = json.dumps(
    {
        "saved_memories": [
            {"content": "Prefers Python for backend work and TypeScript on the frontend.", "created_at": "2026-02-01T10:00:00Z"},
            {"content": "Is building a startup called Cortex, a shared memory layer for AI assistants."},
            {"content": "Lives in San Francisco."},
            {"content": "Drinks oat milk, not dairy."},
        ]
    }
)

# Claude ships its memory/profile summary as prose bullets under a heading.
CLAUDE_MARKDOWN = """\
# What Claude remembers about you

* You prefer Python for backend work and TypeScript on the frontend.
* You are the founder of Cortex, a memory layer for AI assistants.
* You are based in San Francisco.
* You are vegetarian.
"""

# Gemini's Personalization page ("Things Gemini remembers") — plain lines.
GEMINI_TEXT = """\
Things Gemini remembers
Prefers Python for backend and TypeScript for frontend.
Founder of a startup named Cortex.
Based in San Francisco.
Enjoys hiking on weekends.
"""

# A bare JSON list of strings (a minimal export shape).
BARE_LIST_JSON = json.dumps(
    [
        "Prefers Python for backend work.",
        "Building Cortex.",
    ]
)


class VendorMemoryParseTests(unittest.TestCase):
    """source_ingest.parse_vendor_memory_export: the SHORT vendor memory-list mode."""

    def _texts(self, result: dict) -> list[str]:
        return [fact["text"] for fact in result["facts"]]

    def test_parses_chatgpt_markdown_bullets(self) -> None:
        result = parse_vendor_memory_export(CHATGPT_MARKDOWN, vendor_hint="chatgpt")
        self.assertEqual(result["vendor"], "chatgpt")
        self.assertEqual(result["vendor_label"], "ChatGPT")
        texts = self._texts(result)
        # The "Saved memories" heading is boilerplate, not a fact.
        self.assertNotIn("Saved memories", texts)
        self.assertIn("Prefers Python for backend work and TypeScript on the frontend.", texts)
        self.assertIn("Lives in San Francisco.", texts)
        self.assertEqual(result["count"], 4)
        for fact in result["facts"]:
            self.assertEqual(fact["vendor"], "chatgpt")

    def test_parses_chatgpt_json_with_captured_at(self) -> None:
        result = parse_vendor_memory_export(CHATGPT_JSON, vendor_hint="chatgpt")
        self.assertEqual(result["count"], 4)
        first = result["facts"][0]
        self.assertEqual(first["text"], "Prefers Python for backend work and TypeScript on the frontend.")
        self.assertEqual(first["captured_at"], "2026-02-01T10:00:00Z")

    def test_parses_claude_markdown(self) -> None:
        result = parse_vendor_memory_export(CLAUDE_MARKDOWN, vendor_hint="claude")
        self.assertEqual(result["vendor"], "claude")
        texts = self._texts(result)
        self.assertNotIn("What Claude remembers about you", texts)
        self.assertTrue(any("Python" in text for text in texts))
        self.assertTrue(any("vegetarian" in text for text in texts))

    def test_parses_gemini_plain_text(self) -> None:
        result = parse_vendor_memory_export(GEMINI_TEXT, vendor_hint="gemini")
        self.assertEqual(result["vendor"], "gemini")
        texts = self._texts(result)
        self.assertNotIn("Things Gemini remembers", texts)
        self.assertTrue(any("hiking" in text for text in texts))

    def test_parses_bare_json_string_list(self) -> None:
        result = parse_vendor_memory_export(BARE_LIST_JSON, vendor_hint="chatgpt")
        self.assertEqual(self._texts(result), ["Prefers Python for backend work.", "Building Cortex."])

    def test_vendor_sniffed_from_content_without_hint(self) -> None:
        result = parse_vendor_memory_export(CLAUDE_MARKDOWN)
        self.assertEqual(result["vendor"], "claude")

    def test_accepts_pre_parsed_python_object(self) -> None:
        # Already-decoded JSON (list/dict), not a string, is handled too.
        payload = [{"text": "Prefers Python."}, {"text": "Building Cortex."}]
        result = parse_vendor_memory_export(payload, vendor_hint="gemini")
        self.assertEqual(result["count"], 2)

    def test_empty_export_yields_no_facts(self) -> None:
        for empty in ("", "   \n\n", "[]", "{}", None):
            result = parse_vendor_memory_export(empty, vendor_hint="chatgpt")
            self.assertEqual(result["facts"], [])
            self.assertEqual(result["count"], 0)

    def test_malformed_input_never_crashes(self) -> None:
        for junk in ("{not json at all", "\x00\x01\x02", b"\xff\xfe binary", '{"memories": [null, 123, {"nope": {}}]}'):
            result = parse_vendor_memory_export(junk, vendor_hint="chatgpt")
            self.assertIsInstance(result["facts"], list)  # tolerant, no exception

    def test_dedupes_and_bounds_facts(self) -> None:
        dup = "\n".join(["- Prefers Python."] * 5 + ["- Building Cortex."])
        result = parse_vendor_memory_export(dup, vendor_hint="chatgpt")
        self.assertEqual(self._texts(result), ["Prefers Python.", "Building Cortex."])

    def test_normalize_pre_parsed_facts_strings_and_objects(self) -> None:
        facts = normalize_vendor_facts(
            [
                "Prefers Python.",
                {"text": "Building Cortex.", "vendor": "claude", "captured_at": "2026-01-01"},
                {"content": "Lives in SF."},
                123,  # non-string / non-dict is skipped
            ],
            vendor_hint="chatgpt",
        )
        texts = [fact["text"] for fact in facts]
        self.assertEqual(texts, ["Prefers Python.", "Building Cortex.", "Lives in SF."])
        # A per-fact vendor overrides the export hint.
        by_text = {fact["text"]: fact for fact in facts}
        self.assertEqual(by_text["Building Cortex."]["vendor"], "claude")
        self.assertEqual(by_text["Prefers Python."]["vendor"], "chatgpt")

    def test_vendor_label_helper(self) -> None:
        self.assertEqual(vendor_memory_label("openai"), "ChatGPT")
        self.assertEqual(vendor_memory_label("anthropic"), "Claude")
        self.assertEqual(vendor_memory_label(""), "AI assistant")


class CompareEngineTests(unittest.TestCase):
    """import_diff.compare_vendor_memory: classification + cite-or-abstain against a seeded store."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = root / "cortex.db"
        init_db(self.db_path)
        self.store = CortexStore(self.db_path, root / "vault")
        # Hash embedder path: keyword-overlap fallback (graceful degradation under test).
        self.store._vector_ready = lambda conn: False
        self.user_id = "diff-user"
        self.store.update_settings(self.user_id, {"review_new_captures": False, "allow_pending_in_context": True})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _seed(self, memory_id: str, content: str, *, layer: str = "semantic", occurred_at: str = "2026-01-01T00:00:00Z") -> None:
        self.store.save_capture(
            user_id=self.user_id,
            content=content,
            source="obsidian",
            source_url=f"local-file://{memory_id}",
            title=memory_id,
            extracted={
                "_timestamp": occurred_at,
                "summary": content,
                "records": [
                    {"id": memory_id, "kind": "decision" if layer == "decision" else "fact",
                     "layer": layer, "content": content, "confidence": "confirmed",
                     "importance": 4, "occurred_at": occurred_at, "topics": [], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )

    def test_confirmed_carries_real_citation(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work and TypeScript on the frontend.", layer="preference")
        facts = [{"text": "Prefers Python for backend work and TypeScript on the frontend.", "vendor": "chatgpt"}]
        diff = compare_vendor_memory(self.store, self.user_id, facts, vendor="chatgpt")
        entry = diff["facts"][0]
        self.assertEqual(entry["status"], "confirmed")
        # CITE-OR-ABSTAIN: a confirmed verdict MUST carry a real Cortex memory id + source.
        self.assertEqual(entry["match"]["memory_id"], "mem_python")
        self.assertTrue(entry["match"]["source_url"])
        self.assertEqual(diff["summary"]["confirmed"], 1)
        self.assertEqual(diff["vendor_label"], "ChatGPT")

    def test_missing_when_cortex_does_not_know(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work.", layer="preference")
        facts = [{"text": "Owns a golden retriever named Biscuit.", "vendor": "chatgpt"}]
        diff = compare_vendor_memory(self.store, self.user_id, facts, vendor="chatgpt")
        entry = diff["facts"][0]
        self.assertEqual(entry["status"], "missing")
        self.assertEqual(diff["summary"]["missing"], 1)

    def test_conflicting_on_polarity_flip(self) -> None:
        # Cortex knows the user is NOT vegetarian; the vendor claims they are.
        self._seed("mem_diet", "You are not vegetarian; you eat meat regularly.", layer="preference")
        facts = [{"text": "You are vegetarian.", "vendor": "claude"}]
        diff = compare_vendor_memory(self.store, self.user_id, facts, vendor="claude")
        entry = diff["facts"][0]
        self.assertIn(entry["status"], {"conflicting", "stale"})
        # Both sides cited: the contradicting Cortex memory carries its id.
        self.assertEqual(entry["match"]["memory_id"], "mem_diet")

    def test_stale_when_cortex_superseded_the_memory(self) -> None:
        # Two contradictory memories, then resolve so the old one is superseded (stale).
        self._seed("mem_sqlite", "The database is sqlite for the launch.", layer="decision", occurred_at="2026-01-01T00:00:00Z")
        self._seed("mem_postgres", "The database is postgres for the launch.", layer="decision", occurred_at="2026-06-01T00:00:00Z")
        self.store.resolve_conflict(self.user_id, stale_id="mem_sqlite", current_id="mem_postgres")
        # The vendor still thinks it's sqlite.
        facts = [{"text": "The database is sqlite for the launch.", "vendor": "gemini"}]
        diff = compare_vendor_memory(self.store, self.user_id, facts, vendor="gemini")
        entry = diff["facts"][0]
        # Superseded memories are excluded from search, so this surfaces via the current postgres
        # memory as a contradiction, OR (if not retrieved) as missing — never as a false confirm.
        self.assertNotEqual(entry["status"], "confirmed")

    def test_cortex_only_surfaces_unmentioned_mirror_memory(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work.", layer="preference")
        self._seed("mem_secret", "Strongly dislikes being cold-called by recruiters.", layer="negative")
        # The vendor export only mentions Python; the recruiter dislike is Cortex-only.
        facts = [{"text": "Prefers Python for backend work.", "vendor": "chatgpt"}]
        diff = compare_vendor_memory(self.store, self.user_id, facts, vendor="chatgpt")
        cortex_only_ids = {item["memory_id"] for item in diff["cortex_only"]}
        self.assertIn("mem_secret", cortex_only_ids)
        # The Python memory is covered by a vendor fact -> not surfaced as cortex_only.
        self.assertNotIn("mem_python", cortex_only_ids)
        for item in diff["cortex_only"]:
            self.assertTrue(item["memory_id"])  # cited

    def test_empty_facts_returns_empty_diff(self) -> None:
        diff = compare_vendor_memory(self.store, self.user_id, [], vendor="chatgpt")
        self.assertEqual(diff["facts"], [])
        self.assertEqual(diff["summary"]["total"], 0)
        self.assertIn("caveats", diff)

    def test_hash_embedder_reports_keyword_method(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work.", layer="preference")
        diff = compare_vendor_memory(
            self.store, self.user_id,
            [{"text": "Prefers Python for backend work.", "vendor": "chatgpt"}], vendor="chatgpt",
        )
        # Under the hash embedder the compare degrades to deterministic keyword overlap.
        self.assertEqual(diff["method"], "keyword")
        self.assertTrue(any("keyword overlap" in caveat for caveat in diff["caveats"]))

    def test_deterministic_repeat(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work.", layer="preference")
        facts = [{"text": "Prefers Python for backend work.", "vendor": "chatgpt"}]
        first = compare_vendor_memory(self.store, self.user_id, facts, vendor="chatgpt")
        second = compare_vendor_memory(self.store, self.user_id, facts, vendor="chatgpt")
        self.assertEqual(first, second)

    def test_full_pipeline_from_raw_export(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work and TypeScript on the frontend.", layer="preference")
        self._seed("mem_cortex", "Is building a startup called Cortex, a shared memory layer for AI assistants.", layer="semantic")
        diff = compare_vendor_export(self.store, self.user_id, raw_export=CHATGPT_MARKDOWN, vendor="chatgpt")
        self.assertEqual(diff["parsed"]["count"], 4)
        self.assertEqual(diff["parsed"]["source"], "parsed_export")
        self.assertEqual(diff["summary"]["total"], 4)
        # At least the two seeded facts confirm with citations.
        confirmed = [entry for entry in diff["facts"] if entry["status"] == "confirmed"]
        self.assertGreaterEqual(len(confirmed), 2)
        for entry in confirmed:
            self.assertTrue(entry["match"]["memory_id"])

    def test_read_only_does_not_mutate_store(self) -> None:
        self._seed("mem_python", "Prefers Python for backend work.", layer="preference")
        before = {memory["id"] for memory in self.store.recent(self.user_id, limit=50)}
        compare_vendor_export(self.store, self.user_id, raw_export=CHATGPT_MARKDOWN, vendor="chatgpt")
        after = {memory["id"] for memory in self.store.recent(self.user_id, limit=50)}
        self.assertEqual(before, after)  # preview only: nothing imported


class _EndpointHarness(unittest.TestCase):
    """Shared seed for the endpoint tests on both servers."""

    def _seed(self, store: CortexStore, user_id: str, memory_id: str = "mem_python") -> None:
        # `memory_id` is parameterized (not hardcoded) because ImportDiffFastAPIEndpointTests
        # shares ONE process-wide `main_module.store` across all its test methods (see that
        # class's setUpClass), and unittest runs methods alphabetically within a class — so two
        # tests both seeding the literal id "mem_python" under two different user_ids would
        # otherwise contend for the same primary-key row on a store that isn't per-test isolated.
        # Before the cross-tenant id-collision guard existed the second write silently overwrote
        # the first tenant's row (masking the bug); the guard now correctly refuses that overwrite
        # and re-salts instead, so a caller seeding a second, distinct user must pass its own id.
        store.update_settings(user_id, {"review_new_captures": False, "allow_pending_in_context": True})
        store.save_capture(
            user_id=user_id,
            content="Prefers Python for backend work and TypeScript on the frontend.",
            source="obsidian",
            source_url=f"local-file://{memory_id}",
            title=memory_id,
            extracted={
                "_timestamp": "2026-01-01T00:00:00Z",
                "summary": "Prefers Python for backend work and TypeScript on the frontend.",
                "records": [
                    {"id": memory_id, "kind": "fact", "layer": "preference",
                     "content": "Prefers Python for backend work and TypeScript on the frontend.",
                     "confidence": "confirmed", "importance": 4, "occurred_at": "2026-01-01T00:00:00Z",
                     "topics": [], "entity_ids": []}
                ],
                "tasks": [],
                "entities": [],
            },
        )


class ImportDiffStandaloneEndpointTests(_EndpointHarness):
    """POST /v1/import-diff on the shipping standalone server, backed by a REAL store."""

    def setUp(self) -> None:
        from backend.app import standalone_server
        from backend.app.config import Settings

        self.standalone_server = standalone_server
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        init_db(root / "index.sqlite")
        self.real_store = CortexStore(root / "index.sqlite", root / "vault")
        self.real_store._vector_ready = lambda conn: False
        self.original_store = standalone_server.store
        self.original_settings = standalone_server.settings
        self.original_guards = standalone_server.REQUEST_GUARDS
        standalone_server.store = self.real_store
        standalone_server.settings = Settings(
            vault_path=root / "vault",
            db_path=root / "index.sqlite",
            api_key="test-token",
            public_base_url="http://127.0.0.1:8766",
        )
        standalone_server.REQUEST_GUARDS = standalone_server._RequestGuards()
        self._seed(self.real_store, standalone_server.settings.default_user_id)
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
        self.standalone_server.REQUEST_GUARDS = self.original_guards
        self.tmp.cleanup()

    def _post(self, body: dict) -> tuple[int, dict]:
        req = request.Request(
            self.base_url + "/v1/import-diff",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": "Bearer test-token", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                return response.status, json.loads(response.read())
        except error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def test_import_diff_route_with_raw_export(self) -> None:
        status, payload = self._post({"export": CHATGPT_MARKDOWN, "vendor": "chatgpt"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["parsed"]["count"], 4)
        self.assertEqual(payload["vendor_label"], "ChatGPT")
        confirmed = [entry for entry in payload["facts"] if entry["status"] == "confirmed"]
        self.assertGreaterEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["match"]["memory_id"], "mem_python")

    def test_import_diff_route_with_pre_parsed_facts(self) -> None:
        status, payload = self._post({"facts": ["Prefers Python for backend work and TypeScript on the frontend."], "vendor": "claude"})
        self.assertEqual(status, 200)
        self.assertEqual(payload["summary"]["total"], 1)

    def test_import_diff_requires_a_body(self) -> None:
        status, payload = self._post({})
        self.assertEqual(status, 422)
        self.assertIn("detail", payload)

    def test_import_diff_requires_auth(self) -> None:
        req = request.Request(
            self.base_url + "/v1/import-diff",
            data=json.dumps({"export": CHATGPT_MARKDOWN}).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=5) as response:
                status = response.status
        except error.HTTPError as exc:
            status = exc.code
        self.assertIn(status, (401, 403))


class ImportDiffFastAPIEndpointTests(_EndpointHarness):
    """POST /v1/import-diff on the FastAPI server: same shape, neighboring-route auth."""

    @classmethod
    def setUpClass(cls) -> None:
        from fastapi.testclient import TestClient

        from backend.app import main as main_module

        cls.main_module = main_module
        cls.client = TestClient(main_module.app)

    def test_import_diff_route(self) -> None:
        user_id = "import-diff-fastapi-user"
        self._seed(self.main_module.store, user_id)
        response = self.client.post(
            "/v1/import-diff",
            json={"export": CHATGPT_MARKDOWN, "vendor": "chatgpt"},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": user_id},
        )
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["parsed"]["count"], 4)
        confirmed = [entry for entry in payload["facts"] if entry["status"] == "confirmed"]
        self.assertGreaterEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["match"]["memory_id"], "mem_python")

    def test_import_diff_pre_parsed_facts(self) -> None:
        user_id = "import-diff-fastapi-user2"
        # Distinct memory_id: this class's main_module.store is shared across every test method
        # (see the _seed docstring) — reusing "mem_python" here would contend with
        # test_import_diff_route's own seed under a different user_id.
        self._seed(self.main_module.store, user_id, memory_id="mem_python_2")
        response = self.client.post(
            "/v1/import-diff",
            json={"facts": [{"text": "Prefers Python for backend work and TypeScript on the frontend.", "vendor": "claude"}]},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": user_id},
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["total"], 1)

    def test_import_diff_empty_body_is_422(self) -> None:
        response = self.client.post(
            "/v1/import-diff",
            json={},
            headers={"Authorization": "Bearer test-token", "X-Cortex-User": "x"},
        )
        self.assertEqual(response.status_code, 422)

    def test_import_diff_requires_auth(self) -> None:
        response = self.client.post("/v1/import-diff", json={"export": CHATGPT_MARKDOWN})
        self.assertIn(response.status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()
