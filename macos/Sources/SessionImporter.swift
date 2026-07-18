import SwiftUI
import AppKit
import WebKit

// MARK: - Embedded session importer (DIRECT / notarized-DMG build only)
//
// This is the "sign in to your own ChatGPT/Claude account in a private in-app window and import
// your conversations using YOUR logged-in session" path. It is NOT an official OpenAI/Anthropic
// feature and it only ever reads the signed-in user's own data into their local memory on this Mac.
//
// The ENTIRE feature is excluded from the App Store build. There is no compile flag in this
// codebase; the idiom is a runtime guard on `!DistributionMode.isAppStore` (see QuickCapture's
// `setEnabled` at QuickCapture.swift and the Downloads export watcher at CortexApp.swift's
// `startExportWatcher`). Every entry point here — and `AppState.importFromSessionHarvest` — carries
// that guard. Presenters (AIChatsImportCard in ConnectionsPrivacySheet) must never set the vendor
// state under the App Store build, and this view refuses to do anything if it is somehow reached.
//
// Mechanics: a WKWebView on the vendor's origin (chatgpt.com / claude.ai) using the PERSISTENT
// WKWebsiteDataStore.default(), so a login survives between imports and re-imports don't force a
// re-login. A WKScriptMessageHandler named "cortexImport" receives streamed results. The harvester
// JavaScript is injected via evaluateJavaScript only when the user taps Import — it never auto-runs.

/// The two vendors whose live web sessions we can harvest. Raw value is the `source_hint` the
/// backend expects (display-cased "ChatGPT"/"Claude"); it must match the strings the importer and
/// the export-request UI use everywhere else. `Identifiable` so a presenter can drive `.sheet(item:)`.
enum AIChatImportVendor: String, Identifiable, CaseIterable {
    case chatgpt = "ChatGPT"
    case claude = "Claude"
    case perplexity = "Perplexity"
    case notion = "Notion"

    var id: String { rawValue }

    /// Notion is a whole-workspace ASYNC EXPORT, not a per-conversation stream: the harvester enqueues
    /// a server-side export, polls until it produces a pre-signed download URL, and hands that zip to
    /// the backend. It does NOT use the on-disk spool at all (openSpool/appendToSpool/closeSpool are
    /// never touched for it). Every other vendor streams conversation details through the spool.
    var usesAsyncExport: Bool {
        switch self {
        case .notion: return true
        case .chatgpt, .claude, .perplexity: return false
        }
    }

    /// One-tap collapse: for the chat vendors, once the private window reports a signed-in session we
    /// auto-run the harvest exactly once so import is a single tap. Notion is deliberately excluded —
    /// a whole-workspace export is heavy, so it always waits for an explicit "Export my workspace" tap.
    var autoStartOnLogin: Bool {
        switch self {
        case .chatgpt, .claude, .perplexity: return true
        case .notion: return false
        }
    }

    /// The primary action button's label: chat vendors import conversations, Notion exports a workspace.
    var primaryActionLabel: String {
        usesAsyncExport ? "Export my workspace" : "Import my chats"
    }

    /// User-facing product name (same as the raw value today, kept separate so copy never couples
    /// to the `source_hint` contract).
    var displayName: String { rawValue }

    /// The company behind the product, for the "this is not an official … feature" honesty line.
    var vendorCompany: String {
        switch self {
        case .chatgpt: return "OpenAI"
        case .claude: return "Anthropic"
        case .perplexity: return "Perplexity"
        case .notion: return "Notion"
        }
    }

    /// The origin the WKWebView loads so the user can sign in. All harvester fetches are relative to
    /// this origin (same-origin, cookies auto-attach).
    var originURL: String {
        switch self {
        case .chatgpt: return "https://chatgpt.com"
        case .claude: return "https://claude.ai"
        case .perplexity: return "https://www.perplexity.ai"
        case .notion: return "https://www.notion.so"
        }
    }

    var symbolName: String {
        switch self {
        case .chatgpt: return "bubble.left.and.text.bubble.right"
        case .claude: return "sparkle"
        case .perplexity: return "magnifyingglass.circle"
        case .notion: return "note.text"
        }
    }

    /// Incremental JSON framing for the streamed on-disk spool (see SessionImportCoordinator). ChatGPT
    /// expects a top-level ARRAY of detail objects; Claude expects `{"conversations":[…]}`. These
    /// wrap the comma-separated conversation elements the coordinator appends one at a time, so the
    /// finished file matches exactly what the backend parsers accept from a downloaded export.
    /// Perplexity streams a top-level ARRAY of `{title, created_at, messages}` objects — the shape the
    /// backend's consumer-AI transcript parser accepts — so it reuses the ChatGPT `[` / `]` framing.
    /// Notion never streams to the spool (async export), so its values here are inert placeholders.
    var spoolOpening: String {
        switch self {
        case .chatgpt, .perplexity, .notion: return "["
        case .claude: return "{\"conversations\":["
        }
    }

    var spoolClosing: String {
        switch self {
        case .chatgpt, .perplexity, .notion: return "]"
        case .claude: return "]}"
        }
    }

    /// A tiny JS probe that reports whether the current session is signed in, via
    /// postMessage({type:"login", loggedIn:Bool}). Run on every navigation finish. For ChatGPT this
    /// reads the session accessToken; for Claude it checks that at least one organization is visible.
    var loginCheckJS: String {
        switch self {
        case .chatgpt:
            return """
            (async () => {
              try {
                const s = await fetch("/api/auth/session", { credentials: "include" }).then(r => r.ok ? r.json() : null).catch(() => null);
                const ok = !!(s && s.accessToken && String(s.accessToken).length > 0);
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: ok });
              } catch (e) {
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: false });
              }
            })();
            """
        case .claude:
            return """
            (async () => {
              try {
                const r = await fetch("/api/organizations", { credentials: "include" });
                const j = r.ok ? await r.json() : null;
                const ok = !!(j && j.length && j[0] && j[0].uuid);
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: ok });
              } catch (e) {
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: false });
              }
            })();
            """
        case .perplexity:
            return """
            (async () => {
              try {
                const r = await fetch("/rest/thread/list_ask_threads?version=2.18&source=default", {
                  method: "POST",
                  credentials: "include",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({ limit: 1, offset: 0, search_term: "" })
                });
                let ok = false;
                if (r.ok) {
                  try { const j = await r.json(); ok = !!j; } catch (e) { ok = false; }
                }
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: ok });
              } catch (e) {
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: false });
              }
            })();
            """
        case .notion:
            return """
            (async () => {
              try {
                const r = await fetch("/api/v3/getSpaces", {
                  method: "POST",
                  credentials: "include",
                  headers: { "Content-Type": "application/json" },
                  body: JSON.stringify({})
                });
                let ok = false;
                if (r.ok) {
                  try {
                    const j = await r.json();
                    if (j && typeof j === "object") {
                      for (const userId in j) {
                        const sp = j[userId] && j[userId].space;
                        if (sp && Object.keys(sp).length > 0) { ok = true; break; }
                      }
                    }
                  } catch (e) { ok = false; }
                }
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: ok });
              } catch (e) {
                window.webkit.messageHandlers.cortexImport.postMessage({ type: "login", loggedIn: false });
              }
            })();
            """
        }
    }

    /// The full harvester. Injected ONLY on the user's explicit Import tap. It paginates the list,
    /// then fetches each conversation detail sequentially with randomized 1500-4000ms pacing, HTTP
    /// 429 exponential backoff (min(1000*2^attempt, 10000)ms, up to 3 attempts), and per-conversation
    /// skip-on-failure so one bad conversation never aborts the run. It streams each detail object as
    /// {type:"conversation", data:…} and a final {type:"done"} so Swift shows live progress and memory
    /// stays bounded. It checks window.__cortexCancel between items, between retry attempts, AND during
    /// every pacing/backoff sleep (via cancellableSleep) so Cancel stops it promptly — it never keeps
    /// fetching from the vendor through a multi-second backoff after the user asked it to stop.
    var harvestJS: String {
        switch self {
        case .chatgpt:
            return """
            (async () => {
              const post = (m) => window.webkit.messageHandlers.cortexImport.postMessage(m);
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              const jitter = () => 1500 + Math.floor(Math.random() * 2500);
              const backoff = (attempt) => Math.min(1000 * Math.pow(2, attempt), 10000);
              // Cancel-aware sleep: wake every 200ms to check the cancel flag so a long backoff/pacing
              // wait unwinds promptly instead of blocking for up to 10s after the user taps Cancel.
              const cancellableSleep = async (ms) => {
                const step = 200;
                for (let t = 0; t < ms; t += step) {
                  if (window.__cortexCancel) return;
                  await sleep(Math.min(step, ms - t));
                }
              };
              try {
                window.__cortexCancel = false;
                const sess = await fetch("/api/auth/session", { credentials: "include" }).then((r) => r.ok ? r.json() : null).catch(() => null);
                const token = sess && sess.accessToken;
                if (!token) {
                  post({ type: "error", message: "You are not signed in to ChatGPT yet. Sign in above, then tap Import my chats." });
                  return;
                }
                const headers = { "Authorization": "Bearer " + token, "X-Authorization": "Bearer " + token };
                const ids = [];
                let offset = 0;
                let total = null;
                while (true) {
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  const res = await fetch("/backend-api/conversations?offset=" + offset + "&limit=100", { credentials: "include", headers: headers });
                  if (!res.ok) { post({ type: "error", message: "Could not list your ChatGPT conversations (HTTP " + res.status + ")." }); return; }
                  const page = await res.json();
                  const items = (page && page.items) || [];
                  // Only trust `total` when the response actually carries a number. Falling back to
                  // items.length here would set total = a single page's size and stop after ONE page,
                  // silently dropping the rest of the history. When total is absent we paginate until
                  // a page comes back EMPTY instead.
                  if (total === null && page && typeof page.total === "number") total = page.total;
                  for (const it of items) { if (it && it.id) ids.push(it.id); }
                  offset += 100;
                  // Stop on an empty page (the reliable end marker), or — only when total is a
                  // trustworthy number — once we've paged past it. A missing/unreliable total never
                  // ends the loop early.
                  if (items.length === 0 || (total !== null && offset >= total)) break;
                  await cancellableSleep(700);
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                }
                const count = ids.length;
                post({ type: "progress", done: 0, total: count });
                let done = 0;
                for (const id of ids) {
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  let ok = false;
                  for (let attempt = 0; attempt < 3 && !ok; attempt++) {
                    if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                    try {
                      const r = await fetch("/backend-api/conversation/" + id, { credentials: "include", headers: headers });
                      if (r.status === 429) { await cancellableSleep(backoff(attempt)); if (window.__cortexCancel) { post({ type: "canceled" }); return; } continue; }
                      if (!r.ok) { await cancellableSleep(backoff(attempt)); if (window.__cortexCancel) { post({ type: "canceled" }); return; } continue; }
                      const detail = await r.json();
                      post({ type: "conversation", data: detail });
                      ok = true;
                    } catch (e) {
                      await cancellableSleep(backoff(attempt));
                      if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                    }
                  }
                  done += 1;
                  post({ type: "progress", done: done, total: count });
                  await cancellableSleep(jitter());
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                }
                post({ type: "done" });
              } catch (e) {
                post({ type: "error", message: (e && e.message) ? e.message : "Reading your ChatGPT history failed." });
              }
            })();
            """
        case .claude:
            return """
            (async () => {
              const post = (m) => window.webkit.messageHandlers.cortexImport.postMessage(m);
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              const jitter = () => 1500 + Math.floor(Math.random() * 2500);
              const backoff = (attempt) => Math.min(1000 * Math.pow(2, attempt), 10000);
              // Cancel-aware sleep: wake every 200ms to check the cancel flag so a long backoff/pacing
              // wait unwinds promptly instead of blocking for up to 10s after the user taps Cancel.
              const cancellableSleep = async (ms) => {
                const step = 200;
                for (let t = 0; t < ms; t += step) {
                  if (window.__cortexCancel) return;
                  await sleep(Math.min(step, ms - t));
                }
              };
              try {
                window.__cortexCancel = false;
                const orgs = await fetch("/api/organizations", { credentials: "include" }).then((r) => r.ok ? r.json() : null).catch(() => null);
                if (!orgs || !orgs.length || !orgs[0] || !orgs[0].uuid) {
                  post({ type: "error", message: "You are not signed in to Claude yet. Sign in above, then tap Import my chats." });
                  return;
                }
                const org = orgs[0].uuid;
                const listRes = await fetch("/api/organizations/" + org + "/chat_conversations", { credentials: "include" });
                if (!listRes.ok) { post({ type: "error", message: "Could not list your Claude conversations (HTTP " + listRes.status + ")." }); return; }
                const list = await listRes.json();
                const ids = (list || []).map((c) => c && c.uuid).filter(Boolean);
                const count = ids.length;
                post({ type: "progress", done: 0, total: count });
                let done = 0;
                for (const uuid of ids) {
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  let ok = false;
                  for (let attempt = 0; attempt < 3 && !ok; attempt++) {
                    if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                    try {
                      const url = "/api/organizations/" + org + "/chat_conversations/" + uuid + "?tree=True&rendering_mode=messages&render_all_tools=true";
                      const r = await fetch(url, { credentials: "include" });
                      if (r.status === 429) { await cancellableSleep(backoff(attempt)); if (window.__cortexCancel) { post({ type: "canceled" }); return; } continue; }
                      if (!r.ok) { await cancellableSleep(backoff(attempt)); if (window.__cortexCancel) { post({ type: "canceled" }); return; } continue; }
                      const detail = await r.json();
                      post({ type: "conversation", data: detail });
                      ok = true;
                    } catch (e) {
                      await cancellableSleep(backoff(attempt));
                      if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                    }
                  }
                  done += 1;
                  post({ type: "progress", done: done, total: count });
                  await cancellableSleep(jitter());
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                }
                post({ type: "done" });
              } catch (e) {
                post({ type: "error", message: (e && e.message) ? e.message : "Reading your Claude history failed." });
              }
            })();
            """
        case .perplexity:
            return """
            (async () => {
              const post = (m) => window.webkit.messageHandlers.cortexImport.postMessage(m);
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              const jitter = () => 1500 + Math.floor(Math.random() * 2500);
              const backoff = (attempt) => Math.min(1000 * Math.pow(2, attempt), 10000);
              // Cancel-aware sleep: wake every 200ms to check the cancel flag so a long backoff/pacing
              // wait unwinds promptly instead of blocking for up to 10s after the user taps Cancel.
              const cancellableSleep = async (ms) => {
                const step = 200;
                for (let t = 0; t < ms; t += step) {
                  if (window.__cortexCancel) return;
                  await sleep(Math.min(step, ms - t));
                }
              };
              const listThreads = (offset) => fetch("/rest/thread/list_ask_threads?version=2.18&source=default", {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ limit: 20, offset: offset, search_term: "" })
              });
              try {
                window.__cortexCancel = false;
                const slugs = [];
                let offset = 0;
                const CAP = 3000;  // hard safety cap on thread count
                while (true) {
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  const res = await listThreads(offset);
                  if (!res.ok) { post({ type: "error", message: "Could not list your Perplexity threads (HTTP " + res.status + ")." }); return; }
                  const page = await res.json();
                  const threads = (page && (page.threads || page.entries || page.data || (Array.isArray(page) ? page : []))) || [];
                  for (const t of threads) { if (t && t.slug) slugs.push(t.slug); }
                  offset += 20;
                  // Stop on an empty page, when the API says there's no next page, or at the safety cap.
                  if (threads.length === 0 || (page && page.has_next_page !== true) || slugs.length >= CAP) break;
                  await cancellableSleep(700);
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                }
                const count = slugs.length;
                post({ type: "progress", done: 0, total: count });
                let done = 0;
                for (const slug of slugs) {
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  let ok = false;
                  for (let attempt = 0; attempt < 3 && !ok; attempt++) {
                    if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                    try {
                      const r = await fetch("/rest/thread/" + encodeURIComponent(slug) + "?version=2.18&source=default&limit=50&offset=0&from_first=true", { credentials: "include" });
                      if (r.status === 429) { await cancellableSleep(backoff(attempt)); if (window.__cortexCancel) { post({ type: "canceled" }); return; } continue; }
                      if (!r.ok) { await cancellableSleep(backoff(attempt)); if (window.__cortexCancel) { post({ type: "canceled" }); return; } continue; }
                      const d = await r.json();
                      const title = d.title || slug;
                      const turns = d.entries || d.messages || d.steps || [];
                      const messages = [];
                      for (const e of turns) {
                        const userText = (typeof e.query === "string" ? e.query : (e.query && e.query.text)) || e.query_str || "";
                        const asstText = (e.answer && (e.answer.text || e.answer.answer)) || (typeof e.answer === "string" ? e.answer : "") || e.final_response || "";
                        if (userText) messages.push({ role: "user", text: userText });
                        if (asstText) messages.push({ role: "assistant", text: asstText });
                      }
                      const created_at = d.created_at || (turns[0] && turns[0].created_at);
                      post({ type: "conversation", data: { title: title, created_at: created_at, messages: messages } });
                      ok = true;
                    } catch (e) {
                      await cancellableSleep(backoff(attempt));
                      if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                    }
                  }
                  done += 1;
                  post({ type: "progress", done: done, total: count });
                  await cancellableSleep(jitter());
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                }
                post({ type: "done" });
              } catch (e) {
                post({ type: "error", message: (e && e.message) ? e.message : "Reading your Perplexity history failed." });
              }
            })();
            """
        case .notion:
            return """
            (async () => {
              const post = (m) => window.webkit.messageHandlers.cortexImport.postMessage(m);
              const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
              // Cancel-aware sleep: wake every 200ms to check the cancel flag so a long poll wait
              // unwinds promptly instead of blocking after the user taps Cancel.
              const cancellableSleep = async (ms) => {
                const step = 200;
                for (let t = 0; t < ms; t += step) {
                  if (window.__cortexCancel) return;
                  await sleep(Math.min(step, ms - t));
                }
              };
              const jpost = (path, body) => fetch(path, {
                method: "POST",
                credentials: "include",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify(body)
              });
              // Export ONE space: enqueue the server-side export, then poll to completion. Returns the
              // pre-signed download URL, or null on ANY failure (bad enqueue, no task id, timeout, no
              // link, or cancel). The caller records a null as a failed space and CONTINUES to the rest
              // — one broken/empty/slow workspace must never abort the whole run. A progress heartbeat
              // is posted on EVERY poll iteration, INCLUDING transient getTasks failures, so a run of
              // failed polls can never trip the 60s silence watchdog. total is 0 (pages aren't known
              // up front).
              const exportSpace = async (spaceId) => {
                const enqRes = await jpost("/api/v3/enqueueTask", {
                  task: {
                    eventName: "exportSpace",
                    request: {
                      spaceId: spaceId,
                      shouldExportComments: false,
                      exportOptions: { exportType: "markdown", timeZone: "America/New_York", locale: "en" }
                    }
                  }
                });
                if (!enqRes.ok) return null;
                const enqJson = await enqRes.json();
                const taskId = enqJson && enqJson.taskId;
                if (!taskId) return null;
                const deadline = Date.now() + 12 * 60 * 1000;  // generous ~12 min per space
                while (true) {
                  if (window.__cortexCancel) return null;
                  if (Date.now() > deadline) return null;
                  await cancellableSleep(2500);
                  if (window.__cortexCancel) return null;
                  const tasksRes = await jpost("/api/v3/getTasks", { taskIds: [taskId] });
                  if (!tasksRes.ok) { post({ type: "progress", done: 0, total: 0 }); continue; }
                  const tasksJson = await tasksRes.json();
                  const task = tasksJson && tasksJson.results && tasksJson.results[0];
                  if (!task) { post({ type: "progress", done: 0, total: 0 }); continue; }
                  const pagesExported = (task.status && task.status.pagesExported) || 0;
                  post({ type: "progress", done: pagesExported, total: 0 });
                  const complete = task.state === "success" || (task.status && task.status.type === "complete");
                  if (complete) return (task.status && task.status.exportURL) || null;
                }
              };
              try {
                window.__cortexCancel = false;
                const spacesRes = await jpost("/api/v3/getSpaces", {});
                if (!spacesRes.ok) { post({ type: "error", message: "Could not read your Notion workspaces (HTTP " + spacesRes.status + ")." }); return; }
                const spacesJson = await spacesRes.json();
                const spaceIds = [];
                for (const userId in spacesJson) {
                  const sp = spacesJson[userId] && spacesJson[userId].space;
                  if (sp) { for (const spaceId in sp) { if (spaceIds.indexOf(spaceId) === -1) spaceIds.push(spaceId); } }
                }
                if (spaceIds.length === 0) { post({ type: "error", message: "No Notion workspace was found for this account." }); return; }
                // Export EVERY space FIRST (continuing past any per-space failure), streaming each
                // download link back as it becomes ready. Swift ACCUMULATES the links but stays in the
                // cancel-able harvesting state — it only starts downloading/importing on {type:"done"},
                // so Cancel stays live for the whole (multi-minute) export instead of the sheet locking
                // out its controls the instant the first space finishes.
                let failedSpaces = 0;
                let collected = 0;
                for (const spaceId of spaceIds) {
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  let exportURL = null;
                  try {
                    exportURL = await exportSpace(spaceId);
                  } catch (e) {
                    exportURL = null;
                  }
                  if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                  if (exportURL) {
                    collected += 1;
                    post({ type: "exportURL", url: exportURL });
                  } else {
                    failedSpaces += 1;
                  }
                }
                if (window.__cortexCancel) { post({ type: "canceled" }); return; }
                // Zero links means every workspace failed — a hard error. Otherwise hand off with an
                // honest failure count so Swift can report a partial import ("Imported N of M").
                if (collected === 0) {
                  post({ type: "error", message: "Could not export any of your Notion workspaces. Try again." });
                  return;
                }
                post({ type: "done", totalSpaces: spaceIds.length, failedSpaces: failedSpaces });
              } catch (e) {
                post({ type: "error", message: (e && e.message) ? e.message : "Exporting your Notion workspace failed." });
              }
            })();
            """
        }
    }
}

/// Where the harvest is in its lifecycle. Drives which controls the sheet shows.
enum SessionImportPhase {
    case idle        // signed-in state unknown or waiting for the user to tap Import
    case harvesting  // JS is reading conversations out of the live session
    case importing   // handing the collected JSON to the backend via importFromPath
    case done        // import finished successfully
    case failed      // a fatal harvest/import error (recoverable — the user can retry)
}

// MARK: - Coordinator (script message handler + navigation delegate)

/// Owns the harvest lifecycle for one vendor: detects login, injects the harvester on demand,
/// accumulates the streamed conversation detail objects, builds the vendor-correct JSON, and hands
/// it to `AppState.importFromSessionHarvest`. All @Published mutations happen on the main thread —
/// WebKit delivers both `didReceive` and navigation callbacks on the main queue.
final class SessionImportCoordinator: NSObject, ObservableObject, WKScriptMessageHandler, WKNavigationDelegate {
    let vendor: AIChatImportVendor
    private let state: AppState
    private let onFinished: (Bool) -> Void

    @Published var loggedIn = false
    @Published var phase: SessionImportPhase = .idle
    @Published var harvestedCount = 0
    @Published var totalCount = 0
    @Published var statusText = ""

    /// Set by the NSViewRepresentable host once the webview exists. Weak so the webview (which holds
    /// this coordinator strongly through its userContentController) is the sole owner — no cycle.
    weak var webView: WKWebView?

    /// Streaming spool. Rather than buffering every conversation in RAM (a multi-hundred-MB spike for
    /// a large history) and then re-serializing the whole corpus into a second in-memory copy, we
    /// append each streamed conversation to an open `conversations.json` handle as it arrives, so peak
    /// memory stays flat regardless of history size. `spoolDir` is the temp dir we own and clean up;
    /// `wroteFirstElement` drives the JSON array comma framing; `collectedCount` counts what we wrote.
    private var spoolDir: URL?
    private var spoolFileURL: URL?
    private var spoolHandle: FileHandle?
    private var wroteFirstElement = false
    private var collectedCount = 0

    /// Ensures the sheet reports its outcome to the presenter exactly once (see reportFinished).
    private var didReportFinished = false

    /// One-tap collapse guard: flipped true the first time we auto-start the harvest after a signed-in
    /// session is detected, so a re-probed `login` message can never re-fire the harvest.
    private var didAutoStart = false

    /// Notion async-export bookkeeping (unused by the spool vendors). During the multi-space export the
    /// JS posts one `{type:"exportURL"}` per workspace, which we ACCUMULATE here while staying in the
    /// cancel-able `.harvesting` phase. Only on `{type:"done"}` do we flip to `.importing` ONCE and
    /// sequentially download + import each collected zip. `notionCanceled` guards the narrow race where
    /// a Cancel lands just as the JS posts `done` (so no `canceled` message follows) — it stops the
    /// download/import from starting.
    private var notionExportURLs: [URL] = []
    private var notionCanceled = false

    /// A download session bounded by a resource timeout (a few minutes) and a per-request timeout, so a
    /// stalled S3 download fails cleanly instead of hanging on `URLSession.shared`'s ~7-day default
    /// resource timeout and wedging the sheet in `.importing` forever. Created lazily; the vendor uses
    /// it only for the Notion zip downloads.
    private lazy var notionDownloadSession: URLSession = {
        let cfg = URLSessionConfiguration.default
        cfg.timeoutIntervalForRequest = 60
        cfg.timeoutIntervalForResource = 300
        cfg.waitsForConnectivity = false
        return URLSession(configuration: cfg)
    }()

    /// Silence watchdog. A mid-harvest page navigation (or any other tear-down of the JS context)
    /// leaves the harvester unable to ever post done/error/canceled, which would wedge the sheet in
    /// `.harvesting` forever behind a lying "Reading conversation X of Y…". This single-shot timer is
    /// (re)armed on every inbound script message; if it fires — meaning no message arrived for the
    /// silence window while still harvesting — we flip to `.failed` so the user can retry.
    private var watchdog: Timer?
    private let watchdogSilenceWindow: TimeInterval = 60

    init(vendor: AIChatImportVendor, state: AppState, onFinished: @escaping (Bool) -> Void) {
        self.vendor = vendor
        self.state = state
        self.onFinished = onFinished
        super.init()
    }

    deinit {
        // The sheet can be abandoned mid-harvest (the coordinator is released when the webview and
        // its userContentController go away). Reclaim the timer and the temp spool dir so neither
        // leaks. Never runs while an import is in flight — the sheet keeps the coordinator alive then.
        watchdog?.invalidate()
        watchdog = nil
        cleanupSpool()
    }

    /// Defense in depth on top of the presenter guard: this whole feature is DMG-only.
    var isAvailable: Bool { !DistributionMode.isAppStore }

    // MARK: Navigation

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        checkLoginState()
    }

    /// Block navigations while the harvester is running. The harvester reads history via same-origin
    /// `fetch()` (which is NOT a navigation and is unaffected here), so the page never needs to move
    /// once harvesting has begun. A stray in-page click or a redirect mid-harvest would tear down the
    /// JS context — after which it can never post done/error/canceled — so we cancel any navigation
    /// that would fire during `.harvesting`. The initial sign-in page load happens while `.idle`, so
    /// it is allowed; everything else flows normally outside the harvest.
    func webView(_ webView: WKWebView,
                 decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        if phase == .harvesting {
            decisionHandler(.cancel)
            return
        }
        decisionHandler(.allow)
    }

    /// Re-probe whether the current session is signed in. Cheap and idempotent; safe to call on any
    /// navigation finish (login redirects, the vendor SPA swapping views, etc.).
    func checkLoginState() {
        guard isAvailable, let webView else { return }
        webView.evaluateJavaScript(vendor.loginCheckJS, completionHandler: nil)
    }

    // MARK: Watchdog

    /// (Re)arm the single-shot silence watchdog. Called when the harvest starts and again on every
    /// inbound script message, so a live harvester keeps pushing the deadline out; only genuine
    /// silence for the full window trips it. Scheduled on the main run loop (we are always on the
    /// main thread here), so `watchdogFired` runs on the main thread too.
    private func armWatchdog() {
        watchdog?.invalidate()
        watchdog = Timer.scheduledTimer(withTimeInterval: watchdogSilenceWindow, repeats: false) { [weak self] _ in
            self?.watchdogFired()
        }
    }

    private func stopWatchdog() {
        watchdog?.invalidate()
        watchdog = nil
    }

    /// The harvester went silent for the whole window while still `.harvesting` — its JS context is
    /// almost certainly gone (e.g. a page navigation) and it can no longer report done/error/canceled.
    /// Unwedge the sheet: discard the partial spool and fail recoverably so the retry button returns.
    private func watchdogFired() {
        watchdog = nil
        guard phase == .harvesting else { return }
        cleanupSpool()
        phase = .failed
        statusText = "The import stopped responding. Try again."
    }

    // MARK: Harvest control (only ever invoked from the explicit Import tap)

    func startHarvest() {
        guard isAvailable, let webView else { return }
        guard phase != .harvesting, phase != .importing else { return }
        // Notion is an async whole-workspace export — it never touches the spool. The ENTIRE export
        // (every space) runs in the cancel-able `.harvesting` phase; progress posts on every poll keep
        // the watchdog alive through the multi-minute run, and Cancel stays live the whole time. We only
        // flip to `.importing` once the JS reports `done` (see beginNotionImports). Reset bookkeeping,
        // arm the watchdog, and run.
        if vendor.usesAsyncExport {
            resetNotionState()
            harvestedCount = 0
            totalCount = 0
            phase = .harvesting
            statusText = "Exporting your \(vendor.displayName) workspace… this can take a few minutes."
            armWatchdog()
            webView.evaluateJavaScript(vendor.harvestJS, completionHandler: nil)
            return
        }
        cleanupSpool()  // discard any prior run's spool before opening a fresh one
        guard openSpool() else {
            phase = .failed
            statusText = "Could not prepare to import your \(vendor.displayName) conversations. Try again."
            return
        }
        harvestedCount = 0
        totalCount = 0
        phase = .harvesting
        statusText = "Reading your \(vendor.displayName) conversations…"
        armWatchdog()  // trips if the harvester goes silent (e.g. its JS context is torn down)
        webView.evaluateJavaScript(vendor.harvestJS, completionHandler: nil)
    }

    // MARK: Spool (stream conversations to disk so peak memory stays flat)

    /// Open a fresh `conversations.json` in a temp dir we own and write the opening JSON framing.
    private func openSpool() -> Bool {
        let dir = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let fileURL = dir.appendingPathComponent("conversations.json")
        do {
            try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            guard FileManager.default.createFile(atPath: fileURL.path, contents: nil) else { return false }
            let handle = try FileHandle(forWritingTo: fileURL)
            try handle.write(contentsOf: Data(vendor.spoolOpening.utf8))
            spoolDir = dir
            spoolFileURL = fileURL
            spoolHandle = handle
            wroteFirstElement = false
            collectedCount = 0
            return true
        } catch {
            try? FileManager.default.removeItem(at: dir)
            return false
        }
    }

    /// Append one streamed conversation detail to the open spool. A value that won't serialize is
    /// skipped (not fatal), matching the harvester's per-conversation skip-on-failure.
    private func appendToSpool(_ element: Any) {
        guard let handle = spoolHandle else { return }
        guard JSONSerialization.isValidJSONObject(element),
              let elementData = try? JSONSerialization.data(withJSONObject: element) else { return }
        // Compose the separator comma and the element into ONE payload and write it in a single call.
        // Writing the comma and the element as two separate `write`s risks emitting the comma and then
        // throwing on the element write — leaving a trailing comma that corrupts the JSON array. One
        // write means we either commit the whole element (with its leading comma) or nothing at all,
        // so `wroteFirstElement`/`collectedCount` only advance on a fully-written element.
        var payload = Data()
        if wroteFirstElement { payload.append(Data(",".utf8)) }
        payload.append(elementData)
        do {
            try handle.write(contentsOf: payload)
            wroteFirstElement = true
            collectedCount += 1
        } catch {
            // Best-effort: a write failure just yields a smaller import; the run continues.
        }
    }

    /// Write the closing framing, close the handle, and return the finished file URL (nil on failure).
    private func closeSpool() -> URL? {
        guard let handle = spoolHandle, let fileURL = spoolFileURL else { return nil }
        do {
            try handle.write(contentsOf: Data(vendor.spoolClosing.utf8))
            try handle.close()
            spoolHandle = nil
            return fileURL
        } catch {
            try? handle.close()
            spoolHandle = nil
            return nil
        }
    }

    /// Best-effort teardown of the spool handle + temp dir. Safe to call repeatedly.
    private func cleanupSpool() {
        if let handle = spoolHandle { try? handle.close() }
        spoolHandle = nil
        if let dir = spoolDir { try? FileManager.default.removeItem(at: dir) }
        spoolDir = nil
        spoolFileURL = nil
        wroteFirstElement = false
        collectedCount = 0
    }

    /// The sheet's Done/Close path. Stop the watchdog and reclaim the temp spool so an abandoned
    /// mid-harvest sheet doesn't leak its dir. NEVER deletes the spool while an import is in flight —
    /// `importFromPath` is still reading that file — but the sheet hides its dismissal control during
    /// `.importing` anyway (F6), so this guard is defense in depth. Safe to call repeatedly.
    func prepareForDismissal() {
        stopWatchdog()
        if phase != .importing { cleanupSpool() }
    }

    /// Fire the presenter's completion callback exactly once when the import resolves. This callback
    /// REFRESHES the presenter's state (e.g. re-detects exports on success); it does NOT dismiss the
    /// sheet — dismissal is the Done/Close path's job (see the view's `finish`). Called only from the
    /// background import Task; the once-guard keeps a stray re-entry from re-running the refresh.
    func reportFinished(_ ok: Bool) {
        guard !didReportFinished else { return }
        didReportFinished = true
        onFinished(ok)
    }

    /// Stops the harvest immediately by flipping a JS-visible cancel flag the harvester checks between
    /// items and between retry attempts. The harvester then posts {type:"canceled"} and unwinds.
    func cancelHarvest() {
        guard let webView else { return }
        webView.evaluateJavaScript("window.__cortexCancel = true;", completionHandler: nil)
        // Also latch a Swift-side flag for the async export: it closes the narrow race where Cancel
        // lands just as the JS posts `done` (so no `canceled` message follows), stopping the
        // subsequent download/import from starting. Harmless for the spool vendors.
        if vendor.usesAsyncExport { notionCanceled = true }
        if phase == .harvesting {
            statusText = "Stopping…"
        }
    }

    // MARK: Script messages

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        guard message.name == "cortexImport" else { return }
        // A message means the harvester is alive — push the silence watchdog's deadline out. The whole
        // harvest/export runs in `.harvesting` (Notion posts a progress heartbeat on every poll across
        // all spaces), so keeping the watchdog alive only while `.harvesting` is enough; once we leave
        // for `.importing` the JS is done and no further messages arrive. A stray late/login message
        // outside a run must not resurrect it.
        if phase == .harvesting { armWatchdog() }
        guard let body = message.body as? [String: Any], let type = body["type"] as? String else { return }
        switch type {
        case "login":
            let wasLoggedIn = loggedIn
            loggedIn = (body["loggedIn"] as? Bool) ?? false
            // One-tap collapse: the first time a signed-in session appears, for the chat vendors, run
            // the harvest automatically (once) so import is a single tap. Notion opts out (heavy export
            // ⇒ explicit tap). Guarded so a re-probed login can't re-fire, and only from a clean idle.
            if loggedIn, !wasLoggedIn, vendor.autoStartOnLogin, phase == .idle, !didAutoStart {
                didAutoStart = true
                startHarvest()
            }
        case "progress":
            if let total = body["total"] as? Int { totalCount = total }
            if let done = body["done"] as? Int { harvestedCount = done }
            if phase == .harvesting {
                if vendor.usesAsyncExport {
                    let ready = notionExportURLs.count
                    if ready > 0 {
                        statusText = "Exporting your \(vendor.displayName) workspace… \(ready) ready so far."
                    } else if harvestedCount > 0 {
                        statusText = "Exporting your \(vendor.displayName) workspace… \(harvestedCount) pages so far."
                    } else {
                        statusText = "Exporting your \(vendor.displayName) workspace… this can take a few minutes."
                    }
                } else {
                    statusText = totalCount > 0
                        ? "Reading conversation \(harvestedCount) of \(totalCount)…"
                        : "Reading your \(vendor.displayName) conversations…"
                }
            }
        case "conversation":
            if let data = body["data"] { appendToSpool(data) }
        case "exportURL":
            // Notion only: a space's pre-signed S3 zip is ready. ACCUMULATE it and stay in the
            // cancel-able `.harvesting` phase — the JS is still exporting the remaining spaces. We do
            // NOT download or flip to `.importing` here; that happens once on `done` (Finding 1), so
            // Cancel stays available for the whole export.
            if vendor.usesAsyncExport, let urlStr = body["url"] as? String, let url = URL(string: urlStr) {
                notionExportURLs.append(url)
                if phase == .harvesting {
                    let ready = notionExportURLs.count
                    statusText = "Exporting your \(vendor.displayName) workspace… \(ready) ready so far."
                }
            }
        case "done":
            if vendor.usesAsyncExport {
                let total = (body["totalSpaces"] as? Int) ?? notionExportURLs.count
                beginNotionImports(totalSpaces: total)
            } else {
                finishHarvest()
            }
        case "canceled":
            stopWatchdog()
            cleanupSpool()
            resetNotionState()
            phase = .idle
            statusText = "Import canceled. Nothing was changed."
        case "error":
            stopWatchdog()
            cleanupSpool()
            resetNotionState()
            let msg = (body["message"] as? String) ?? "Reading your \(vendor.displayName) history failed."
            phase = .failed
            statusText = msg
        default:
            break
        }
    }

    // MARK: Finish

    /// Close the streamed spool (which already holds the vendor-correct JSON framing: a top-level
    /// ARRAY for ChatGPT, `{"conversations":[…]}` for Claude — matching the backend parsers) and hand
    /// the finished file to the backend. On an "already imported" no-op re-import the outcome is
    /// `.alreadyPresent`, shown as up-to-date rather than a failure, so re-harvesting the persistent
    /// login is never mis-reported as an error.
    private func finishHarvest() {
        guard phase == .harvesting else { return }
        stopWatchdog()  // the harvester reported done; we are leaving .harvesting
        let gathered = collectedCount
        guard gathered > 0, let fileURL = closeSpool() else {
            cleanupSpool()
            phase = .failed
            statusText = "No conversations were readable from your \(vendor.displayName) session. Make sure you are signed in, then try again."
            return
        }
        phase = .importing
        statusText = "Saving \(gathered) conversation\(gathered == 1 ? "" : "s") into your memory…"

        let vendor = self.vendor
        Task { @MainActor in
            let outcome = await state.importSessionHarvestFile(vendor: vendor, fileURL: fileURL)
            cleanupSpool()  // remove the temp file/dir now the backend has ingested it
            switch outcome {
            case .imported:
                phase = .done
                statusText = "Imported \(gathered) conversation\(gathered == 1 ? "" : "s"). Building your memory in the background…"
            case .alreadyPresent:
                phase = .done
                statusText = "Already imported — your memory is up to date."
            case .failed:
                phase = .failed
                statusText = "Saving your \(vendor.displayName) conversations failed. Try again."
            }
            reportFinished(outcome != .failed)
        }
    }

    // MARK: Notion async export (no spool — download each zip, hand it to the backend)

    /// Reset the Notion async-export bookkeeping. Called before a fresh export and whenever a run ends
    /// without importing (canceled/error). A no-op for the spool vendors (the fields are inert there).
    private func resetNotionState() {
        notionExportURLs = []
        notionCanceled = false
    }

    /// The JS finished exporting every space and posted `done`. Flip to `.importing` ONCE, then
    /// sequentially download + import each accumulated zip. `totalSpaces` is the honest denominator M
    /// (all workspaces, including those that failed to export) for the partial report on finalize.
    private func beginNotionImports(totalSpaces: Int) {
        guard vendor.usesAsyncExport else { return }
        stopWatchdog()  // the export is done; the download phase is a bounded local hand-off
        // A Cancel that landed right as the JS posted `done` (so no `canceled` message follows) must
        // still prevent the download/import — reset to idle exactly like the canceled path.
        if notionCanceled {
            resetNotionState()
            phase = .idle
            statusText = "Import canceled. Nothing was changed."
            return
        }
        let urls = notionExportURLs
        guard !urls.isEmpty else {
            // Shouldn't happen (the JS posts `error` when it collected zero links), but stay safe.
            resetNotionState()
            phase = .failed
            statusText = "Could not import your \(vendor.displayName) workspace. Try again."
            reportFinished(false)
            return
        }
        let total = max(totalSpaces, urls.count)
        phase = .importing
        statusText = urls.count == 1
            ? "Saving your \(vendor.displayName) workspace into your memory…"
            : "Saving your \(vendor.displayName) workspaces into your memory…"
        let vendor = self.vendor
        Task { @MainActor in
            var imported = 0
            var index = 0
            for url in urls {
                index += 1
                if urls.count > 1 {
                    statusText = "Saving your \(vendor.displayName) workspaces into your memory… (\(index) of \(urls.count))"
                }
                if await downloadAndImportNotionZip(url) { imported += 1 }
            }
            finalizeNotion(imported: imported, total: total)
        }
    }

    /// Download one space's pre-signed export zip and import it. The URL is an S3 link that needs NO
    /// cookies, so a plain download suffices — but we use `notionDownloadSession` (bounded resource +
    /// request timeouts) so a stalled download fails cleanly instead of hanging for days. Returns true
    /// only when the backend accepted the zip (imported or already present).
    private func downloadAndImportNotionZip(_ url: URL) async -> Bool {
        var tempZip: URL?
        var ok = false
        do {
            let (downloaded, _) = try await notionDownloadSession.download(from: url)
            let zipURL = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString + ".zip")
            try? FileManager.default.removeItem(at: zipURL)
            try FileManager.default.moveItem(at: downloaded, to: zipURL)
            tempZip = zipURL
            let outcome = await state.importSessionHarvestFile(vendor: vendor, fileURL: zipURL)
            ok = (outcome == .imported || outcome == .alreadyPresent)
        } catch {
            ok = false
        }
        if let tempZip { try? FileManager.default.removeItem(at: tempZip) }
        return ok
    }

    /// Finalize the Notion export with an HONEST outcome. `imported` (N) is the number of workspaces
    /// that actually landed; `total` (M) is every workspace we tried. Full success reads as `.done`; a
    /// partial success STILL reports `.done` + `reportFinished(true)` (so the history/stats refresh
    /// fires) but names the shortfall; only ZERO imported is a recoverable `.failed`.
    private func finalizeNotion(imported: Int, total: Int) {
        guard vendor.usesAsyncExport else { return }
        stopWatchdog()
        resetNotionState()
        if imported <= 0 {
            phase = .failed
            statusText = "Could not import your \(vendor.displayName) workspace. Try again."
            reportFinished(false)
            return
        }
        phase = .done
        if imported >= total {
            statusText = imported == 1
                ? "Imported your \(vendor.displayName) workspace. Building your memory in the background…"
                : "Imported all \(imported) \(vendor.displayName) workspaces. Building your memory in the background…"
        } else {
            statusText = "Imported \(imported) of \(total) \(vendor.displayName) workspaces… couldn't bring in the other \(total - imported). Building your memory in the background…"
        }
        reportFinished(true)
    }
}

// MARK: - WKWebView host

/// NSViewRepresentable that hosts the sign-in webview on the vendor's origin. Uses the PERSISTENT
/// default data store so a login survives across imports. Registers the coordinator as the
/// "cortexImport" script message handler and as the navigation delegate.
private struct SessionImportWebView: NSViewRepresentable {
    @ObservedObject var coordinator: SessionImportCoordinator

    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = WKWebsiteDataStore.default()  // persistent — re-imports keep the login
        let ucc = WKUserContentController()
        ucc.add(coordinator, name: "cortexImport")
        config.userContentController = ucc

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = coordinator
        coordinator.webView = webView

        if let url = URL(string: coordinator.vendor.originURL) {
            webView.load(URLRequest(url: url))
        }
        return webView
    }

    func updateNSView(_ nsView: WKWebView, context: Context) {}
}

// MARK: - Sheet

/// The direct-signin import sheet. Hosts the webview, explains the flow in plain language, and once
/// the user is signed in lets them tap "Import my chats" to run the harvest. Shows live progress and
/// a Cancel that stops the harvest immediately. On completion it routes the harvested JSON through
/// `AppState.importSessionHarvestFile` (which the coordinator calls), then flips to `.done`/`.failed`
/// and fires `onFinished(success)` so the presenter can REFRESH its state — WITHOUT dismissing. The
/// sheet stays open on the confirmation / failure message; it dismisses only when the user taps
/// Done/Close, which calls `onDismiss`. Keeping the sheet open is what lets the user actually see the
/// "Imported N conversations" checkmark, or the "Try again." message + the returning retry button.
struct AIChatSessionImportView: View {
    let vendor: AIChatImportVendor
    /// Dismissal is the presenter's job and happens ONLY from the explicit Done/Close path (`finish`),
    /// never from the import-completion callback — otherwise the sheet would vanish the instant the
    /// import resolved, swallowing the success checkmark and any failure message + retry affordance.
    let onDismiss: () -> Void
    @StateObject private var coordinator: SessionImportCoordinator

    init(vendor: AIChatImportVendor,
         state: AppState,
         onFinished: @escaping (Bool) -> Void,
         onDismiss: @escaping () -> Void) {
        self.vendor = vendor
        self.onDismiss = onDismiss
        _coordinator = StateObject(wrappedValue: SessionImportCoordinator(vendor: vendor, state: state, onFinished: onFinished))
    }

    private var isBusy: Bool {
        coordinator.phase == .harvesting || coordinator.phase == .importing
    }

    private var canImport: Bool {
        coordinator.loggedIn && !isBusy
    }

    private var progressFraction: Double {
        guard coordinator.totalCount > 0 else { return 0 }
        return min(1, Double(coordinator.harvestedCount) / Double(coordinator.totalCount))
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            header
            Divider()
            SessionImportWebView(coordinator: coordinator)
                .frame(minWidth: 620, minHeight: 380)
                .overlay(alignment: .top) { loginHint }
            Divider()
            footer
        }
        .frame(width: 720, height: 640)
        .background(CortexDesign.cardBackground)
    }

    // MARK: Header (consent / honesty copy)

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(spacing: 10) {
                Image(systemName: vendor.symbolName)
                    .font(.title2)
                    .foregroundColor(CortexDesign.accent)
                VStack(alignment: .leading, spacing: 2) {
                    Text("Import from your \(vendor.displayName) account")
                        .font(.headline)
                        .foregroundColor(CortexDesign.ink)
                    Text("Sign in to your own \(vendor.displayName) below")
                        .font(.subheadline)
                        .foregroundColor(CortexDesign.inkSecondary)
                }
                Spacer()
            }
            Text(consentCopy)
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(16)
    }

    private var consentCopy: String {
        if vendor.usesAsyncExport {
            return "This opens your own \(vendor.displayName) account in a private in-app window. When you tap Export my workspace, \(DistributionMode.appDisplayName) asks \(vendor.displayName) to export your workspace using the session you sign in with here, downloads it, and saves it into your local memory on this Mac. This is not an official \(vendor.vendorCompany) feature, and \(DistributionMode.appDisplayName) only ever reads your own data. Nothing is sent anywhere else."
        }
        return "This opens your own \(vendor.displayName) account in a private in-app window. When you tap Import, \(DistributionMode.appDisplayName) reads your conversations using the session you sign in with here and saves them into your local memory on this Mac. This is not an official \(vendor.vendorCompany) feature, and \(DistributionMode.appDisplayName) only ever reads your own data. Nothing is sent anywhere else."
    }

    // MARK: Login hint overlay (only before sign-in)

    @ViewBuilder
    private var loginHint: some View {
        if !coordinator.loggedIn && !isBusy {
            Text("Sign in to \(vendor.displayName) in this window, then tap \(vendor.primaryActionLabel) below.")
                .font(.footnote)
                .foregroundColor(CortexDesign.inkSecondary)
                .padding(.horizontal, 12)
                .padding(.vertical, 8)
                .background(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                        .fill(CortexDesign.cardBackground.opacity(0.92))
                )
                .overlay(
                    RoundedRectangle(cornerRadius: CortexDesign.Radius.md, style: .continuous)
                        .stroke(CortexDesign.hairline, lineWidth: 1)
                )
                .padding(.top, 8)
        }
    }

    // MARK: Footer (status + controls)

    private var footer: some View {
        HStack(spacing: 12) {
            statusRow
            Spacer()
            controls
        }
        .padding(16)
    }

    @ViewBuilder
    private var statusRow: some View {
        switch coordinator.phase {
        case .harvesting:
            HStack(spacing: 10) {
                ProgressView(value: progressFraction)
                    .frame(width: 140)
                Text(coordinator.statusText)
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(1)
            }
        case .importing:
            HStack(spacing: 10) {
                ProgressView().controlSize(.small)
                Text(coordinator.statusText)
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(1)
            }
        case .done:
            Label(coordinator.statusText, systemImage: "checkmark.seal.fill")
                .font(.callout)
                .foregroundColor(CortexDesign.accent)
                .lineLimit(2)
        case .failed:
            Label(coordinator.statusText, systemImage: "exclamationmark.triangle.fill")
                .font(.callout)
                .foregroundColor(CortexDesign.inkSecondary)
                .lineLimit(2)
        case .idle:
            if !coordinator.statusText.isEmpty {
                Text(coordinator.statusText)
                    .font(.callout)
                    .foregroundColor(CortexDesign.inkSecondary)
                    .lineLimit(2)
            }
        }
    }

    @ViewBuilder
    private var controls: some View {
        if coordinator.phase == .harvesting {
            CortexButton(title: "Cancel", systemImage: "stop.fill", role: .secondary) {
                coordinator.cancelHarvest()
            }
        }
        // Hide the dismissal control while the backend hand-off is in flight (.importing). Dismissing
        // then would report the sheet's outcome BEFORE importSessionHarvestFile returns — a premature
        // (and wrong) success=false — and let the finish Task fire onFinished a second time after the
        // sheet is gone. Once the import resolves (.done/.failed/.idle) the button returns.
        if coordinator.phase != .importing {
            CortexButton(
                title: coordinator.phase == .done ? "Done" : "Close",
                role: coordinator.phase == .done ? .secondary : .ghost
            ) {
                finish()
            }
        }
        CortexButton(title: vendor.primaryActionLabel, systemImage: "tray.and.arrow.down", role: .primary) {
            coordinator.startHarvest()
        }
        .disabled(!canImport)
    }

    /// The ONLY path that dismisses the sheet. If a harvest is still running, stop it first; then tear
    /// down the watchdog and reclaim the temp spool (unless an import is mid-flight, which this control
    /// is hidden during anyway), and finally ask the presenter to dismiss. The import-completion
    /// refresh already fired via the coordinator's `onFinished` when the import resolved, so there is
    /// nothing to report here — dismissal and the success/failure refresh are deliberately decoupled.
    private func finish() {
        if isBusy { coordinator.cancelHarvest() }
        coordinator.prepareForDismissal()
        onDismiss()
    }
}
