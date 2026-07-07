// Cortex Memory - options page logic.
// Loads/saves { baseUrl, token } to chrome.storage.sync and offers a "Test connection" that
// verifies /ready (GET, no auth) and /v1/tools/schema?format=openai (GET, Bearer) via the
// background worker (which centralizes the fetches).

const api = globalThis.chrome || globalThis.browser;
const DEFAULT_BASE_URL = "http://127.0.0.1:8766";

const baseUrlEl = document.getElementById("baseUrl");
const tokenEl = document.getElementById("token");
const statusEl = document.getElementById("status");
const saveBtn = document.getElementById("save");
const testBtn = document.getElementById("test");
const toggleBtn = document.getElementById("toggle");

function setStatus(message, kind = "info") {
  statusEl.textContent = message;
  statusEl.className = `show ${kind}`;
}

function clearStatus() {
  statusEl.className = "";
  statusEl.textContent = "";
}

function normalizeBaseUrl(value) {
  return String(value || "").trim().replace(/\/+$/, "") || DEFAULT_BASE_URL;
}

async function load() {
  try {
    const stored = await api.storage.sync.get(["baseUrl", "token"]);
    baseUrlEl.value = stored.baseUrl || DEFAULT_BASE_URL;
    tokenEl.value = stored.token || "";
  } catch (err) {
    setStatus(`Could not load saved settings: ${(err && err.message) || err}`, "err");
  }
}

async function save() {
  const baseUrl = normalizeBaseUrl(baseUrlEl.value);
  const token = (tokenEl.value || "").trim();
  baseUrlEl.value = baseUrl;
  try {
    await api.storage.sync.set({ baseUrl, token });
    setStatus(token ? "Saved. You're connected to " + baseUrl : "Saved base URL. Paste a token to enable context injection.", token ? "ok" : "info");
  } catch (err) {
    setStatus(`Could not save: ${(err && err.message) || err}`, "err");
  }
}

async function testConnection() {
  const baseUrl = normalizeBaseUrl(baseUrlEl.value);
  const token = (tokenEl.value || "").trim();
  baseUrlEl.value = baseUrl;
  setStatus("Testing connection…", "info");
  testBtn.disabled = true;
  try {
    // Persist first so a passing test reflects what will actually be used.
    await api.storage.sync.set({ baseUrl, token });

    const resp = await api.runtime.sendMessage({ type: "cortex:testConnection", baseUrl, token });
    if (!resp || !resp.ok) {
      setStatus(`Test failed: ${(resp && resp.message) || "no response from background worker"}`, "err");
      return;
    }

    const lines = [];
    // /ready
    if (resp.ready && resp.ready.ok) {
      lines.push(`✓ Server reachable at ${baseUrl} (/ready → ${resp.ready.status}).`);
    } else if (resp.ready && resp.ready.status) {
      lines.push(`✗ /ready returned HTTP ${resp.ready.status}. Cortex may still be starting up.`);
    } else {
      lines.push(`✗ Could not reach ${baseUrl}. ${(resp.ready && resp.ready.error) || "Is the Cortex app running?"}`);
    }
    // schema (auth)
    if (!token) {
      lines.push("• No token set — paste your paired token to enable context injection.");
    } else if (resp.schema && resp.schema.ok) {
      const count = resp.schema.toolCount != null ? ` (${resp.schema.toolCount} tools)` : "";
      lines.push(`✓ Token accepted — tool schema loaded${count}.`);
    } else if (resp.schema && (resp.schema.status === 401 || resp.schema.status === 403)) {
      lines.push(`✗ Token rejected (HTTP ${resp.schema.status}). Re-pair in the Cortex app.`);
    } else if (resp.schema && resp.schema.status) {
      lines.push(`✗ Schema check returned HTTP ${resp.schema.status}.`);
    } else {
      lines.push(`✗ Schema check failed: ${(resp.schema && resp.schema.error) || "unknown error"}.`);
    }

    const allGood = resp.ready && resp.ready.ok && (!token || (resp.schema && resp.schema.ok));
    setStatus(lines.join("\n"), allGood ? "ok" : "err");
  } catch (err) {
    setStatus(`Test failed: ${(err && err.message) || err}`, "err");
  } finally {
    testBtn.disabled = false;
  }
}

function toggleTokenVisibility() {
  const showing = tokenEl.type === "text";
  tokenEl.type = showing ? "password" : "text";
  toggleBtn.textContent = showing ? "Show token" : "Hide token";
}

saveBtn.addEventListener("click", save);
testBtn.addEventListener("click", testConnection);
toggleBtn.addEventListener("click", toggleTokenVisibility);
baseUrlEl.addEventListener("input", clearStatus);
tokenEl.addEventListener("input", clearStatus);

load();
