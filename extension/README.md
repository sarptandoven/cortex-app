# Cortex Memory — browser extension

Cortex's local-first bridge to web apps. It runs in your browser, talks to the **local** Cortex
server on loopback (default `http://127.0.0.1:8766`) with a paired Bearer token, and injects your
**cited** memory context into web-app input boxes — so ChatGPT, Claude.ai, and Notion get Cortex
context **without Cortex ever exposing a public endpoint.**

Manifest V3 WebExtension, plain vanilla JS. **No build step, no npm, no bundler** — load it unpacked.

## What it does

- Adds a small **◆ Cortex** button (a floating pill) on ChatGPT, Claude.ai, and Notion.
- Click it: the extension reads whatever you've typed in the input as the *task*, asks your local
  Cortex server for cited context, and inserts that context — clearly delimited with
  `--- Cortex context (cited) ---` — **above** your existing text in the same input.
- If it can't find or write to the input, it copies the context to your clipboard and shows a toast,
  so nothing is lost.
- A toolbar popup shows connection status and offers a one-click "Pull context into this page".

All network calls go through the background service worker, which is the only component that holds
your token and speaks to the loopback server.

## How the pieces map to the Cortex backend

The extension never calls `/v1/pair` itself. You pair in the Cortex macOS app; it mints a
read-scoped token and shows you the token + base URL. You paste those into the extension's options.

- **Test connection** hits `GET {base_url}/ready` (no auth) and
  `GET {base_url}/v1/tools/schema?format=openai` (with the Bearer token).
- **Pull context** posts to `POST {base_url}/v1/context` with
  `{"task": <your text>, "token_budget": 1500, "format": "markdown"}` and injects the returned
  markdown. If that path is unavailable it falls back to
  `POST {base_url}/v1/tools/call` with `{"name":"use_cortex","arguments":{"task": <your text>}}`.

The Cortex server already allows `chrome-extension://` / `moz-extension://` origins via CORS, so
these `fetch` calls work from the extension. The **token**, not CORS, is the auth boundary.

## Getting your token from the Cortex app

1. Open the Cortex macOS app.
2. Pair a browser extension (this calls the app-side maintenance flow, which mints a fresh
   read-scoped token via `POST /v1/pair` for you).
3. The app shows you a **token** (starts with `cxm_`) and a **base URL** (usually
   `http://127.0.0.1:8766`).
4. Paste both into the extension's **Settings** (options) page and click **Save**, then
   **Test connection**.

## Load it unpacked

### Chrome / Edge / Brave (Chromium)

1. Go to `chrome://extensions` (Edge: `edge://extensions`).
2. Turn on **Developer mode** (top-right).
3. Click **Load unpacked** and select this `extension/` directory.
4. Pin the **Cortex Memory** toolbar icon, open it, click **Settings**, and paste your token + base URL.

### Firefox

1. Go to `about:debugging#/runtime/this-firefox`.
2. Click **Load Temporary Add-on…**.
3. Select the `manifest.json` inside this `extension/` directory.
4. Open the toolbar icon → **Settings** and paste your token + base URL.

   > Firefox temporary add-ons are removed on restart. For a persistent install, package/sign the
   > extension through AMO. The manifest already carries a `browser_specific_settings.gecko` id.

## Files

| File | Role |
| --- | --- |
| `manifest.json` | MV3 manifest: permissions, host permissions, background worker, options, popup, content scripts. |
| `background.js` | Service worker. Holds config in `chrome.storage.sync`; centralizes **all** network calls to the loopback server; message router for content scripts + popup. |
| `content.js` / `content.css` | Injects the ◆ Cortex button; reads the input, requests context, inserts it (or copies to clipboard + toast on failure). Defensive per-site input selectors; never throws uncaught. |
| `options.html` / `options.js` | Base URL + token form; **Test connection** against `/ready` and `/v1/tools/schema`. Persists to `chrome.storage.sync`. |
| `popup.html` / `popup.js` | Quick status + "Pull context into this page" + Settings link. |

## Privacy

- The extension talks **only** to the Cortex server URL you configure — your own machine on
  loopback by default. It never contacts any Cortex-hosted or third-party server.
- Your token is stored in the browser's **sync storage** and is sent only as an
  `Authorization: Bearer` header to your configured server URL.
- The injected context is exactly what your local Cortex returns — **cited**, and clearly labeled
  in the input with `--- Cortex context (cited) ---` delimiters so you can see and edit it before
  sending.

## Current limitations

- **Pull / inject only.** The flow is one-directional: you click, it pulls context and injects it.
  A server → extension **push** channel (streaming updates over SSE) is a future addition.
- Input detection is best-effort against each site's DOM. Web apps change their markup often; if the
  button can't find the composer, context is copied to your clipboard instead (with a toast).
- The button is a floating pill rather than an inline composer control, to stay robust across the
  sites' frequent layout changes.
