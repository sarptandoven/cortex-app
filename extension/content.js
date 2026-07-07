// Cortex Memory - content script.
//
// Injects a small "◆ Cortex" button near the site's main input. On click it reads the current
// input text (the task), asks the background worker for cited context, and inserts that context
// — clearly delimited — ABOVE the user's text in the same input. If the input can't be located
// or written, it falls back to copying the context to the clipboard and shows a toast.
//
// Design rule: never throw uncaught. Every DOM interaction is wrapped; failures degrade to a
// toast so a site DOM change can't break the host page.

(function () {
  "use strict";

  const api = globalThis.chrome || globalThis.browser;
  const DELIMITER_TOP = "--- Cortex context (cited) ---";
  const DELIMITER_BOT = "--- end Cortex context ---";
  const BUTTON_ID = "cortex-inject-button";

  // Per-site input resolution. Each returns the best-guess editable element (contenteditable or
  // textarea), or null. Ordered by specificity, with generic fallbacks last.
  const SITE = detectSite(location.hostname);

  function detectSite(host) {
    if (host.includes("openai.com") || host.includes("chatgpt.com")) return "chatgpt";
    if (host.includes("claude.ai")) return "claude";
    if (host.includes("notion.so")) return "notion";
    return "unknown";
  }

  // Return the primary input element for the current site, trying site-specific selectors first
  // then a defensive generic sweep. Prefers a focused editable if one exists.
  function findInput() {
    try {
      const active = document.activeElement;
      if (isEditable(active)) return active;

      const selectorsBySite = {
        chatgpt: [
          "textarea#prompt-textarea",
          "div#prompt-textarea[contenteditable='true']",
          "textarea[data-id]",
          "main form textarea",
          "form [contenteditable='true']",
        ],
        claude: [
          "div[contenteditable='true'].ProseMirror",
          "div.ProseMirror[contenteditable='true']",
          "fieldset div[contenteditable='true']",
          "div[contenteditable='true']",
          "textarea",
        ],
        notion: [
          "div[contenteditable='true'][data-content-editable-leaf='true']",
          "div.notion-page-content div[contenteditable='true']",
          "div[contenteditable='true']",
          "textarea",
        ],
        unknown: ["textarea", "div[contenteditable='true']"],
      };

      const selectors = selectorsBySite[SITE] || selectorsBySite.unknown;
      for (const sel of selectors) {
        const nodes = document.querySelectorAll(sel);
        for (const node of nodes) {
          if (isVisible(node) && isEditable(node)) return node;
        }
      }
      // Last resort: any visible editable on the page, preferring the largest (main composer).
      const candidates = Array.from(
        document.querySelectorAll("textarea, [contenteditable='true']")
      ).filter((n) => isVisible(n) && isEditable(n));
      if (candidates.length) {
        candidates.sort((a, b) => area(b) - area(a));
        return candidates[0];
      }
    } catch (_) {
      // fall through to null
    }
    return null;
  }

  function isEditable(node) {
    if (!node || node.nodeType !== 1) return false;
    const tag = node.tagName;
    if (tag === "TEXTAREA") return !node.disabled && !node.readOnly;
    if (node.isContentEditable) return true;
    const attr = node.getAttribute && node.getAttribute("contenteditable");
    return attr === "true" || attr === "";
  }

  function isVisible(node) {
    if (!node) return false;
    const rect = node.getBoundingClientRect();
    if (rect.width < 8 || rect.height < 8) return false;
    const style = window.getComputedStyle(node);
    return style.display !== "none" && style.visibility !== "hidden" && style.opacity !== "0";
  }

  function area(node) {
    const r = node.getBoundingClientRect();
    return r.width * r.height;
  }

  // Read the current text out of an editable element.
  function readInput(el) {
    try {
      if (!el) return "";
      if (el.tagName === "TEXTAREA") return el.value || "";
      return el.innerText || el.textContent || "";
    } catch (_) {
      return "";
    }
  }

  // Insert `contextBlock` above the existing user text within the same input. Returns true on
  // success. For textareas we set .value + dispatch input; for contenteditable we prepend text
  // nodes and fire input so the site's framework (React/ProseMirror) notices.
  function injectAbove(el, contextBlock) {
    try {
      const existing = readInput(el);
      const combined = existing
        ? `${contextBlock}\n\n${existing}`
        : `${contextBlock}\n\n`;

      if (el.tagName === "TEXTAREA") {
        setTextareaValue(el, combined);
        return true;
      }
      if (el.isContentEditable || el.getAttribute("contenteditable") === "true") {
        setContentEditable(el, combined);
        return true;
      }
      return false;
    } catch (_) {
      return false;
    }
  }

  // React-controlled textareas ignore a plain `.value =`; use the native setter + input event so
  // React's onChange fires and the value sticks.
  function setTextareaValue(el, value) {
    const proto = window.HTMLTextAreaElement && window.HTMLTextAreaElement.prototype;
    const setter = proto && Object.getOwnPropertyDescriptor(proto, "value");
    if (setter && setter.set) {
      setter.set.call(el, value);
    } else {
      el.value = value;
    }
    el.focus();
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
    try {
      el.selectionStart = el.selectionEnd = value.length;
    } catch (_) {}
  }

  // For contenteditable composers, replace content with newline-preserving structure. We use
  // execCommand insertText where available (most reliable across ProseMirror/Notion), otherwise
  // fall back to setting textContent. Always dispatch input so the editor's model syncs.
  function setContentEditable(el, value) {
    el.focus();
    // Select all existing content, then insert the combined text. insertText respects the
    // editor's own newline handling far better than innerHTML surgery.
    let inserted = false;
    try {
      const selection = window.getSelection();
      const range = document.createRange();
      range.selectNodeContents(el);
      selection.removeAllRanges();
      selection.addRange(range);
      if (document.execCommand) {
        inserted = document.execCommand("insertText", false, value);
      }
    } catch (_) {
      inserted = false;
    }
    if (!inserted) {
      // Fallback: build paragraph-per-line so contenteditable keeps the line breaks visible.
      el.textContent = "";
      const lines = value.split("\n");
      lines.forEach((line, i) => {
        el.appendChild(document.createTextNode(line));
        if (i < lines.length - 1) el.appendChild(document.createElement("br"));
      });
    }
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new InputEvent("input", { bubbles: true, inputType: "insertText", data: value }));
  }

  async function copyToClipboard(text) {
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(text);
        return true;
      }
    } catch (_) {}
    // Legacy fallback via a hidden textarea + execCommand.
    try {
      const ta = document.createElement("textarea");
      ta.value = text;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand && document.execCommand("copy");
      document.body.removeChild(ta);
      return !!ok;
    } catch (_) {
      return false;
    }
  }

  // Toast: bottom-right, auto-dismiss. Kind controls color.
  function toast(message, kind = "info", ttl = 4200) {
    try {
      let host = document.getElementById("cortex-toast-host");
      if (!host) {
        host = document.createElement("div");
        host.id = "cortex-toast-host";
        document.body.appendChild(host);
      }
      const el = document.createElement("div");
      el.className = `cortex-toast cortex-toast-${kind}`;
      el.textContent = message;
      host.appendChild(el);
      // Force reflow so the enter transition runs.
      void el.offsetWidth;
      el.classList.add("cortex-toast-show");
      setTimeout(() => {
        el.classList.remove("cortex-toast-show");
        setTimeout(() => el.remove(), 300);
      }, ttl);
    } catch (_) {
      // As an absolute last resort, do nothing visible rather than throw.
    }
  }

  function wrapContext(markdown) {
    return `${DELIMITER_TOP}\n${String(markdown).trim()}\n${DELIMITER_BOT}`;
  }

  // Ask the background worker for context, then inject or fall back to clipboard.
  async function handleInject(button) {
    if (!button) button = document.getElementById(BUTTON_ID);
    setBusy(button, true);
    try {
      const input = findInput();
      const task = readInput(input).trim();

      let response;
      try {
        response = await api.runtime.sendMessage({ type: "cortex:pullContext", task });
      } catch (err) {
        toast(`Cortex extension error: ${(err && err.message) || "could not reach background worker"}`, "error");
        return;
      }

      if (!response || !response.ok) {
        const msg = (response && response.message) || "Cortex could not return context.";
        if (response && response.error === "not_configured") {
          toast(msg, "warn", 6000);
          try {
            api.runtime.sendMessage({ type: "cortex:openOptions" });
          } catch (_) {}
        } else {
          toast(msg, "error", 6000);
        }
        return;
      }

      const block = wrapContext(response.markdown);

      // Re-find the input at injection time (SPA may have re-rendered during the fetch).
      const target = findInput() || input;
      if (target && injectAbove(target, block)) {
        toast("Cortex context inserted above your prompt.", "success");
        return;
      }

      // Could not write to any input — copy so nothing is lost.
      const copied = await copyToClipboard(block);
      if (copied) {
        toast("Could not find the input box — copied Cortex context to your clipboard.", "warn", 6000);
      } else {
        toast("Could not insert or copy Cortex context. Try selecting the input first.", "error", 6000);
      }
    } catch (err) {
      // Absolute backstop.
      toast(`Cortex: unexpected error (${(err && err.message) || "unknown"}).`, "error");
    } finally {
      setBusy(button, false);
    }
  }

  function setBusy(button, busy) {
    if (!button) return;
    try {
      button.classList.toggle("cortex-busy", !!busy);
      button.disabled = !!busy;
      button.textContent = busy ? "◆ …" : "◆ Cortex";
    } catch (_) {}
  }

  function createButton() {
    if (document.getElementById(BUTTON_ID)) return;
    const btn = document.createElement("button");
    btn.id = BUTTON_ID;
    btn.type = "button";
    btn.className = "cortex-inject-button";
    btn.textContent = "◆ Cortex";
    btn.title = "Insert your cited Cortex memory context above your prompt";
    btn.addEventListener("click", (e) => {
      e.preventDefault();
      e.stopPropagation();
      handleInject(btn);
    });
    // Float it; positioned via CSS. Attaching to <body> avoids fighting the site's flex layout.
    document.body.appendChild(btn);
  }

  // The button lives as a floating pill so we don't depend on a fragile anchor inside the site's
  // composer. We (re)ensure it exists as the SPA mutates the DOM, cheaply and defensively.
  function ensureButton() {
    try {
      if (!document.body) return;
      if (!document.getElementById(BUTTON_ID)) createButton();
    } catch (_) {}
  }

  function boot() {
    ensureButton();
    // SPA navigation / late-rendered composers: keep the button present without hammering.
    try {
      const observer = new MutationObserver(() => ensureButton());
      observer.observe(document.documentElement, { childList: true, subtree: true });
    } catch (_) {}
    // Belt-and-suspenders: a slow interval in case the observer is throttled on a heavy page.
    setInterval(ensureButton, 3000);
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot, { once: true });
  } else {
    boot();
  }

  // Allow the popup to trigger an injection in the active tab.
  api.runtime.onMessage.addListener((message, _sender, sendResponse) => {
    try {
      if (message && message.type === "cortex:injectNow") {
        handleInject().then(() => sendResponse({ ok: true }));
        return true;
      }
    } catch (_) {
      sendResponse({ ok: false });
    }
    return false;
  });
})();
