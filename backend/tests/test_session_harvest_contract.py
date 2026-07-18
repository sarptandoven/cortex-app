from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from backend.app.source_ingest import (
    _parse_chatgpt,
    _parse_claude,
    _collect_assets,
    analyze_sources,
    import_source_records,
)


# A realistic ChatGPT session-harvest: a JSON array of conversation DETAIL objects,
# each shaped exactly like the live /backend-api/conversation/{id} response
# (title / create_time / mapping of nodes / current_node). This is the shape the
# in-app session harvester writes to conversations.json before /v1/imports parses it.
MOCK_CHATGPT_HARVEST = [
    {
        "title": "Trip planning",
        "create_time": 1700000000.0,
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["n1"]},
            "n1": {
                "id": "n1",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Plan a 3-day trip to Kyoto in autumn."]},
                    "create_time": 1700000001.0,
                },
                "parent": "root",
                "children": ["n2"],
            },
            "n2": {
                "id": "n2",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["Day 1: Fushimi Inari at sunrise, then Gion."]},
                    "create_time": 1700000002.0,
                },
                "parent": "n1",
                "children": [],
            },
        },
        "current_node": "n2",
    },
    {
        "title": "Sourdough starter",
        "create_time": 1700100000.0,
        "mapping": {
            "root": {"id": "root", "message": None, "parent": None, "children": ["m1"]},
            "m1": {
                "id": "m1",
                "message": {
                    "author": {"role": "user"},
                    "content": {"content_type": "text", "parts": ["Why did my sourdough starter stop rising?"]},
                    "create_time": 1700100001.0,
                },
                "parent": "root",
                "children": ["m2"],
            },
            "m2": {
                "id": "m2",
                "message": {
                    "author": {"role": "assistant"},
                    "content": {"content_type": "text", "parts": ["It likely needs warmer temps and more frequent feeding."]},
                    "create_time": 1700100002.0,
                },
                "parent": "m1",
                "children": [],
            },
        },
        "current_node": "m2",
    },
]


# A realistic Claude session-harvest, shaped like the Claude export bundle:
# {"conversations":[{uuid,name,created_at,chat_messages:[{sender,text},...]}]}.
MOCK_CLAUDE_HARVEST = {
    "conversations": [
        {
            "uuid": "11111111-1111-1111-1111-111111111111",
            "name": "Rust ownership",
            "created_at": "2024-01-02T03:04:05Z",
            "chat_messages": [
                {"sender": "human", "text": "Explain Rust ownership like I'm five."},
                {"sender": "assistant", "text": "Every value has one owner; when the owner ends, the value is dropped."},
            ],
        }
    ]
}


class SessionHarvestChatGPTContractTests(unittest.TestCase):
    """The session harvester writes conversation detail objects to conversations.json;
    prove that exact shape flows through the EXISTING ChatGPT parser used by /v1/imports."""

    def test_chatgpt_harvest_parses_through_existing_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.json"
            path.write_text(json.dumps(MOCK_CHATGPT_HARVEST), encoding="utf-8")

            # Parse via the same entry the /v1/imports path uses.
            records = import_source_records([str(path)])
            chatgpt = [r for r in records if r.source == "chatgpt"]
            self.assertGreaterEqual(len(chatgpt), 1)

            # The user + assistant message text survives into the record content.
            corpus = "\n".join(r.content for r in chatgpt)
            self.assertIn("Plan a 3-day trip to Kyoto in autumn.", corpus)
            self.assertIn("Day 1: Fushimi Inari at sunrise, then Gion.", corpus)
            self.assertIn("Why did my sourdough starter stop rising?", corpus)

            # Both harvested conversation titles are represented.
            titles = {r.title for r in chatgpt}
            self.assertIn("Trip planning", titles)
            self.assertIn("Sourdough starter", titles)

            # The dedicated _parse_chatgpt parser (not a generic fallback) is what produces them.
            direct = _parse_chatgpt(_collect_assets([str(path)]), "")
            self.assertGreaterEqual(len(direct), 2)
            self.assertTrue(all(r.source == "chatgpt" for r in direct))
            self.assertIn("Plan a 3-day trip to Kyoto in autumn.", "\n".join(r.content for r in direct))

            # analyze_sources (the pre-import summary shown in the UI) also counts them.
            analysis = analyze_sources([str(path)])
            self.assertGreaterEqual(analysis["records_found"], 2)
            self.assertTrue(any(s["source"] == "chatgpt" for s in analysis["sources"]))


class SessionHarvestClaudeContractTests(unittest.TestCase):
    """Prove the Claude harvest bundle shape flows through the EXISTING Claude parser."""

    def test_claude_harvest_parses_through_existing_parser(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conversations.json"
            path.write_text(json.dumps(MOCK_CLAUDE_HARVEST), encoding="utf-8")

            records = import_source_records([str(path)])
            claude = [r for r in records if r.source == "claude"]
            self.assertGreaterEqual(len(claude), 1)

            corpus = "\n".join(r.content for r in claude)
            self.assertIn("Explain Rust ownership like I'm five.", corpus)
            self.assertIn("Every value has one owner; when the owner ends, the value is dropped.", corpus)
            self.assertIn("Rust ownership", {r.title for r in claude})

            # The dedicated _parse_claude parser produces the record with both turns.
            direct = _parse_claude(_collect_assets([str(path)]), "")
            self.assertGreaterEqual(len(direct), 1)
            self.assertTrue(all(r.source == "claude" for r in direct))
            direct_corpus = "\n".join(r.content for r in direct)
            self.assertIn("Explain Rust ownership like I'm five.", direct_corpus)
            self.assertIn("Every value has one owner; when the owner ends, the value is dropped.", direct_corpus)


if __name__ == "__main__":
    unittest.main()
