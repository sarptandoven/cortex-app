# Cortex Capture Surfaces

Capture is the front door of Cortex. The product rule is simple: if a user can see context somewhere, they should have a low-friction way to save it into local memory.

## Built Surfaces

### Global Clipboard

- The secondary capture tools in `Sources` can save the current clipboard.
- Users can opt into the global `Cmd+Shift+V` clipboard hotkey from `Trust > Advanced`.
- Empty clipboards are rejected before hitting the backend.

### Quick Note

- `Sources > Secondary capture tools` includes a focused note box for decisions, preferences, open loops, and project facts.
- Quick notes are saved as `macos-quick-note` captures.

### Web And Link Capture

- Users can save a URL, title/source name, and notes from the app.
- Cortex stores the URL as source metadata and includes the URL/title inside the captured content.
- The app also opens the local `/capture` page for manual browser-to-Cortex saving.

### Browser Bookmarklet

- The app can copy a JavaScript bookmarklet.
- The bookmarklet opens the local capture page with page title and URL prefilled.
- It does not include the Cortex API token in browser URLs, page DOM, or bookmarklet code.
- The user submits content through the local capture form or the native app.

### Files

- Users can choose files or drag files onto `Sources`.
- Cortex extracts text locally from:
  - `.txt`, `.md`, `.json`, `.jsonl`, `.csv`, `.tsv`, `.log`
  - common source-code and config files
  - `.rtf` / `.rtfd`
  - `.pdf`
- Unknown binary files are captured as source references with file path, type, size, and modified time.
- Large extracted content is truncated before capture to stay under the API limit.

### Capture Inbox Folder

- Cortex creates `~/Library/Application Support/Cortex/Capture Inbox`.
- Users can drop files into that folder from automations, downloads, scripts, or Finder.
- `Sources` can import the folder and then moves successfully imported files into `Capture Inbox/Imported`.

## Backend Endpoints

- `POST /v1/captures`: authenticated JSON capture API used by the app and MCP tools.
- `POST /v1/captures/queue`: authenticated async capture API that stores raw capture immediately and queues extraction for a worker.
- `GET /v1/captures/{capture_id}/status`: capture processing status, derived counts, and related jobs.
- `POST /v1/maintenance/jobs/run`: local/admin worker drain endpoint for queued extraction jobs.
- `GET /capture`: local browser capture form and query-string capture target.
- `POST /capture`: form-post target for bookmarklets and manual browser capture.

## Privacy Model

- Capture is user-initiated.
- There is no ambient screen recording.
- Files are read locally.
- Browser capture opens a local form; automatic token-bearing browser capture is intentionally avoided until scoped one-time capture grants exist.
- The local capture endpoint requires the Cortex API token when auth is enabled.

## Reliability Rules

- Every surface flows through the same backend extraction, review, vault, search, and graph pipeline.
- Unsupported file types are still represented as sources rather than silently failing.
- Capture Inbox import leaves failed files in place.
- Imported files are moved only after a successful capture.
