// Cortex Memory - service worker (MV3 background).
//
// Centralizes ALL network calls to the local Cortex server. Content scripts and the popup
// never fetch the loopback server directly (they run on third-party origins / extension pages);
// they message this worker, which holds the paired token + base_url in chrome.storage.sync and
// speaks to http://127.0.0.1:8766 (or whatever the user configured) with a Bearer header.
//
// Never throws to callers: every handler resolves with {ok:false, error} on failure.

const DEFAULT_BASE_URL = "http://127.0.0.1:8766";

// Cross-browser: Firefox exposes `browser`, Chrome/Edge expose `chrome`. `chrome` is defined in
// both MV3 WebExtension runtimes, so we standardize on it.
const api = globalThis.chrome || globalThis.browser;

function trimTrailingSlash(value) {
  return String(value || "").replace(/\/+$/, "");
}

async function loadConfig() {
  // storage.sync so the paired token roams with the user's browser profile.
  const stored = await api.storage.sync.get(["baseUrl", "token"]);
  const baseUrl = trimTrailingSlash(stored.baseUrl || DEFAULT_BASE_URL);
  const token = (stored.token || "").trim();
  return { baseUrl, token };
}

// Build a fetch that never hangs forever (loopback should be instant; if the server is down we
// want a prompt failure, not a spinner). AbortController gives us a hard timeout.
async function timedFetch(url, options = {}, timeoutMs = 12000) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    return await fetch(url, { ...options, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function authHeaders(token, extra = {}) {
  const headers = { ...extra };
  if (token) headers.Authorization = `Bearer ${token}`;
  return headers;
}

// GET /ready (no auth) — is the local server up and configured?
async function checkReady(baseUrl) {
  try {
    const resp = await timedFetch(`${baseUrl}/ready`, { method: "GET" }, 8000);
    let body = null;
    try {
      body = await resp.json();
    } catch (_) {
      body = null;
    }
    return { ok: resp.ok, status: resp.status, body };
  } catch (err) {
    return { ok: false, status: 0, error: describeError(err, baseUrl) };
  }
}

// GET /v1/tools/schema?format=openai (Bearer) — confirms the token is valid + read-scoped.
async function checkSchema(baseUrl, token) {
  if (!token) return { ok: false, status: 0, error: "No token configured." };
  try {
    const resp = await timedFetch(
      `${baseUrl}/v1/tools/schema?format=openai`,
      { method: "GET", headers: authHeaders(token) },
      8000
    );
    let body = null;
    try {
      body = await resp.json();
    } catch (_) {
      body = null;
    }
    let toolCount = null;
    const schema = body && body.schema;
    if (Array.isArray(schema)) toolCount = schema.length;
    else if (schema && Array.isArray(schema.tools)) toolCount = schema.tools.length;
    else if (schema && Array.isArray(schema.functions)) toolCount = schema.functions.length;
    return { ok: resp.ok, status: resp.status, toolCount, body };
  } catch (err) {
    return { ok: false, status: 0, error: describeError(err, baseUrl) };
  }
}

// Pull cited context for a task. Prefer /v1/context with format=markdown (clean text for
// injection). Falls back to /v1/tools/call use_cortex if /v1/context is unavailable, extracting
// whatever text-ish payload comes back.
async function pullContext(task) {
  const { baseUrl, token } = await loadConfig();
  if (!token) {
    return { ok: false, error: "not_configured", message: "Cortex is not paired. Open the extension options and paste your token." };
  }
  const cleanTask = String(task || "").trim();
  if (!cleanTask) {
    return { ok: false, error: "empty_task", message: "Type something in the input first — Cortex uses it as the task to retrieve context for." };
  }

  // Primary path: /v1/context, markdown format -> ready-to-inject cited text.
  try {
    const resp = await timedFetch(
      `${baseUrl}/v1/context`,
      {
        method: "POST",
        headers: authHeaders(token, { "Content-Type": "application/json" }),
        body: JSON.stringify({ task: cleanTask, token_budget: 1500, format: "markdown", surface: "chat" }),
      },
      15000
    );
    if (resp.ok) {
      const markdown = (await resp.text()).trim();
      if (markdown) {
        return { ok: true, markdown, source: "/v1/context" };
      }
      // Empty pack is a valid (if unhelpful) answer; fall through to the tool call for a retry.
    } else if (resp.status === 401 || resp.status === 403) {
      return { ok: false, error: "unauthorized", message: `Cortex rejected the token (HTTP ${resp.status}). Re-pair in options.` };
    }
    // Other non-OK statuses: try the tool-call fallback below.
  } catch (err) {
    // Network failure on the primary path; try the fallback, then surface a clean error.
  }

  // Fallback path: /v1/tools/call use_cortex -> {tool, result:{routed_to, result}}.
  try {
    const resp = await timedFetch(
      `${baseUrl}/v1/tools/call`,
      {
        method: "POST",
        headers: authHeaders(token, { "Content-Type": "application/json" }),
        body: JSON.stringify({ name: "use_cortex", arguments: { task: cleanTask } }),
      },
      15000
    );
    if (!resp.ok) {
      if (resp.status === 401 || resp.status === 403) {
        return { ok: false, error: "unauthorized", message: `Cortex rejected the token (HTTP ${resp.status}). Re-pair in options.` };
      }
      return { ok: false, error: "http", message: `Cortex returned HTTP ${resp.status}.` };
    }
    const body = await resp.json();
    const markdown = extractMarkdownFromToolResult(body);
    if (markdown) {
      return { ok: true, markdown, source: "/v1/tools/call" };
    }
    return { ok: false, error: "empty", message: "Cortex returned no context for that task." };
  } catch (err) {
    return { ok: false, error: "network", message: describeError(err, baseUrl) };
  }
}

// The use_cortex envelope is {tool, result:{routed_to, task, alternatives, result:<payload>}}.
// The inner <payload> shape depends on the routed tool. Dig out human-readable text defensively.
function extractMarkdownFromToolResult(body) {
  try {
    const inner = body && body.result ? body.result : body;
    const payload = inner && inner.result !== undefined ? inner.result : inner;
    const text = coerceText(payload);
    if (text && text.trim()) return text.trim();
    // Last resort: pretty-print whatever we got so the user still sees the cited data.
    if (payload && typeof payload === "object") {
      return "```json\n" + JSON.stringify(payload, null, 2) + "\n```";
    }
    return "";
  } catch (_) {
    return "";
  }
}

// Pull the most text-like field out of an unknown payload shape.
function coerceText(payload) {
  if (payload == null) return "";
  if (typeof payload === "string") return payload;
  if (typeof payload !== "object") return String(payload);
  const keys = ["markdown", "context", "context_pack", "text", "content", "pack", "summary", "brief"];
  for (const key of keys) {
    const value = payload[key];
    if (typeof value === "string" && value.trim()) return value;
    if (value && typeof value === "object") {
      const nested = coerceText(value);
      if (nested) return nested;
    }
  }
  return "";
}

function describeError(err, baseUrl) {
  const name = err && err.name;
  if (name === "AbortError") {
    return `Cortex did not respond in time at ${baseUrl}. Is the Cortex app running?`;
  }
  return `Could not reach Cortex at ${baseUrl}. Is the Cortex app running? (${(err && err.message) || "network error"})`;
}

// Message router. Content scripts + popup send {type, ...}; we reply via sendResponse. Returning
// true keeps the channel open for the async reply.
api.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  const type = message && message.type;
  (async () => {
    try {
      if (type === "cortex:pullContext") {
        sendResponse(await pullContext(message.task));
        return;
      }
      if (type === "cortex:getConfig") {
        sendResponse({ ok: true, config: await loadConfig() });
        return;
      }
      if (type === "cortex:openOptions") {
        // Content scripts can't open the options page themselves; the worker can.
        try {
          if (api.runtime.openOptionsPage) api.runtime.openOptionsPage();
        } catch (_) {}
        sendResponse({ ok: true });
        return;
      }
      if (type === "cortex:testConnection") {
        const baseUrl = trimTrailingSlash(message.baseUrl || DEFAULT_BASE_URL);
        const token = (message.token || "").trim();
        const ready = await checkReady(baseUrl);
        const schema = await checkSchema(baseUrl, token);
        sendResponse({ ok: true, ready, schema });
        return;
      }
      sendResponse({ ok: false, error: "unknown_message", message: `Unknown message type: ${type}` });
    } catch (err) {
      // Never let a handler throw uncaught — always reply with a structured failure.
      sendResponse({ ok: false, error: "handler_exception", message: (err && err.message) || String(err) });
    }
  })();
  return true;
});
