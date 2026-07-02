# Pipeline silent-data-loss audit (2026-07-02)

After fixing a silent 80%-loss bug in extraction (a hard 40-candidate cap that
dropped everything past the top 40 memories of a large flat capture), an adversarial
find→verify sweep hunted the whole capture → extract → store → retrieve pipeline for
siblings of the same class: code that silently drops primary user content with no
error and no user-visible signal. Findings were triaged by hand against the real code
(the automated verifiers over-confirmed — one "confirmed" finding was a false
positive), and split into fixed / deferred / rejected below.

## Fixed

- **Extraction candidate cap (the exemplar).** `_extract_locally` kept only the top
  `BASE_EXTRACTION_CANDIDATE_LIMIT` (40) prioritized candidates. Now scales to
  `MAX_EXTRACTION_CANDIDATE_LIMIT` (2000) for flat non-conversational text; the base
  cap is retained for conversational captures (turn-sensitive, chunked upstream).
- **LLM extraction path truncation** (`extractor._extract_with_claude`). The Claude
  path sent only `raw_text[:40000]` to the model in a single request, silently
  dropping everything past ~40k chars on the production (API-key) path. Large captures
  are now windowed (`_claude_extraction_windows`, `_extract_with_claude_windowed`) into
  `<= CLAUDE_EXTRACTION_WINDOW_CHARS` pieces and merged (`_merge_extractions`), so
  nothing is dropped; small captures stay a single request. Bounded by
  `MAX_CLAUDE_EXTRACTION_WINDOWS` to cap model fan-out.
- **JSONL transcript abort-on-corrupt-line** (`source_ingest._parse_consumer_ai_jsonl_asset`).
  A single truncated/corrupt line (common in large exports) returned `[]`, discarding
  every valid conversation already parsed from the file. Now skips the bad line and
  keeps the rest — JSONL lines are independent records.

## Deferred — deliberate scale bounds (tracked, not silently ignored)

These are real truncations, but they are **intentional, per-type-tuned scale bounds**
(not accidental bugs), present consistently across ~15 import parsers. Removing or
raising them blindly risks memory/latency regressions at exactly the 10k scale we are
protecting, and the "right" fix is a coherent design decision, not scattered cap bumps.
The recommended fix is uniform: **chunk the overflow into additional capture records
(as connectors already do for marker-delimited exports) and surface a truncation count
to the import result**, instead of silently dropping. Until then they are recorded here.

| Site (`source_ingest.py`) | Cap | Drops |
| --- | --- | --- |
| `_twitter_tweet_record` | `payload[:1000]` | tweets past 1000 |
| `_twitter_dm_record` | `payload[:300]` | DM conversations past 300 |
| `_linkedin_messages_record` | `rows[:1000]` | messages past 1000 |
| `_linkedin_connections_record` | `rows[:1500]` | connections past 1500 |
| `_parse_google_chat` | `messages[:1500]` | messages past 1500 |
| `_teams_json_record` / `_teams_csv_record` | `[:1500]` | messages/rows past 1500 |
| `_format_csv_export` / `_format_json_export` | `rows[:1000]` | rows past 1000 |
| `_format_contacts_csv` / `_parse_contacts_asset` | `[:2000]` | contacts past 2000 |
| `_parse_calendar_asset` | `[:1000]` | events past 1000 |
| `_parse_bookmarks_asset` / `_parse_browser_bookmarks_json_asset` | `[:3000]` | bookmarks past 3000 |
| `_parse_mbox` | `index >= 500: break` | emails past 500 |
| `_parse_imessage_db` | `records[:40]` | iMessage chats past 40 (notably low) |
| `SourceAsset.read_bytes` | `> MAX_TEXT_BYTES (12MB) -> b""` | whole file treated as empty |

Priority within this class: `read_bytes` (whole-file loss) and `_parse_imessage_db`
(cap of 40 is conspicuously low) first; make truncation observable everywhere.

## Rejected (verified NOT bugs)

- `import_source_records` broad `except` (storage.py:4441) — **false positive**. It
  isolates a single failing record but increments `failed`, appends to `errors`, and
  surfaces them via `last_batch_failed` / `last_error` / `completed`. Not silent.
- `_sentences` <12-char filter and `_extract_locally` <5-word "claim" floor — intended
  noise filters (short fragments are not memories), not data-loss bugs.
- `_answer_excerpt(limit=220)`, `entity_ids[:12]`, `topics[:4]`, `max_tokens` output
  ceiling — field-shaping / display bounds, not primary-content loss.
