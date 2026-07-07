// Cortex Memory - popup logic.
// Shows quick status (paired? server reachable? supported site?) and offers a one-click
// "Pull context into this page" that asks the active tab's content script to inject.

const api = globalThis.chrome || globalThis.browser;

const dot = document.getElementById("dot");
const statusText = document.getElementById("statusText");
const siteMeta = document.getElementById("siteMeta");
const pullBtn = document.getElementById("pull");
const optionsBtn = document.getElementById("openOptions");
const msg = document.getElementById("msg");

const SUPPORTED = [
  { match: /(^|\.)chatgpt\.com$/, name: "ChatGPT" },
  { match: /(^|\.)openai\.com$/, name: "ChatGPT" },
  { match: /(^|\.)claude\.ai$/, name: "Claude.ai" },
  { match: /(^|\.)notion\.so$/, name: "Notion" },
];

function setMsg(text, kind = "info") {
  msg.textContent = text || "";
  msg.className = kind;
}

function setStatus(kind, text) {
  dot.className = `dot ${kind}`;
  statusText.textContent = text;
}

async function getActiveTab() {
  try {
    const tabs = await api.tabs.query({ active: true, currentWindow: true });
    return tabs && tabs[0];
  } catch (_) {
    return null;
  }
}

function siteFor(url) {
  try {
    const host = new URL(url).hostname;
    for (const entry of SUPPORTED) {
      if (entry.match.test(host)) return { supported: true, name: entry.name, host };
    }
    return { supported: false, name: null, host };
  } catch (_) {
    return { supported: false, name: null, host: "" };
  }
}

async function init() {
  const tab = await getActiveTab();
  const site = tab && tab.url ? siteFor(tab.url) : { supported: false, name: null, host: "" };

  // Config + server reachability via the worker.
  let config = { baseUrl: "http://127.0.0.1:8766", token: "" };
  let ready = null;
  try {
    const cfgResp = await api.runtime.sendMessage({ type: "cortex:getConfig" });
    if (cfgResp && cfgResp.ok) config = cfgResp.config;
    const testResp = await api.runtime.sendMessage({ type: "cortex:testConnection", baseUrl: config.baseUrl, token: config.token });
    if (testResp && testResp.ok) ready = testResp.ready;
  } catch (_) {}

  const paired = !!(config.token && config.token.trim());
  const reachable = !!(ready && ready.ok);

  if (!paired) {
    setStatus("warn", "Not paired");
    siteMeta.textContent = "Add your token in Settings to enable injection.";
    pullBtn.disabled = true;
  } else if (!reachable) {
    setStatus("err", "Cortex not reachable");
    siteMeta.textContent = `Configured: ${config.baseUrl}. Is the Cortex app running?`;
    pullBtn.disabled = true;
  } else if (!site.supported) {
    setStatus("ok", "Connected to Cortex");
    siteMeta.textContent = `This page (${site.host || "unknown"}) isn't a supported site. Open ChatGPT, Claude.ai, or Notion.`;
    pullBtn.disabled = true;
  } else {
    setStatus("ok", `Connected · ${site.name}`);
    siteMeta.textContent = `Ready to inject cited context into ${site.name}.`;
    pullBtn.disabled = false;
  }
}

pullBtn.addEventListener("click", async () => {
  setMsg("Pulling context…", "info");
  pullBtn.disabled = true;
  try {
    const tab = await getActiveTab();
    if (!tab || tab.id == null) {
      setMsg("No active tab.", "err");
      return;
    }
    const resp = await api.tabs.sendMessage(tab.id, { type: "cortex:injectNow" });
    if (resp && resp.ok) {
      setMsg("Requested — check the page for the inserted context.", "ok");
      // Close so the user sees the page update.
      setTimeout(() => window.close(), 700);
    } else {
      setMsg("The page didn't respond. Reload the tab and try again.", "err");
    }
  } catch (err) {
    // sendMessage throws if the content script isn't present (e.g. tab not yet loaded).
    setMsg("Could not reach the page's content script. Reload the tab and retry.", "err");
  } finally {
    pullBtn.disabled = false;
  }
});

optionsBtn.addEventListener("click", () => {
  try {
    if (api.runtime.openOptionsPage) api.runtime.openOptionsPage();
    else window.open(api.runtime.getURL("options.html"));
  } catch (_) {
    window.open(api.runtime.getURL("options.html"));
  }
});

init();
