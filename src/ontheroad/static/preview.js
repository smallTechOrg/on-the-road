// preview.js — port-link chips for the preview reverse proxy (Phase 2, 2C).
//
// Hook contract (called by the chat renderer, slice 2A):
//   window.OTR.preview.renderEvent(ev, { sessionId }) -> HTMLElement | null
// `ev` is a persisted event object {seq, type, payload, ts}. For agent_text /
// tool_end events, the payload text is scanned for localhost/127.0.0.1/0.0.0.0
// port mentions; the first *new* port (deduped per session+port) yields a chip
// element the caller inserts into the transcript. Safe to load standalone —
// no imports, no dependency on chat.js being present.

(function () {
  "use strict";

  const TOKEN_KEY = "otr_token"; // same key api.js uses (do not diverge)
  const seen = new Set(); // "sessionId:port" dedupe

  const HOST_PORT_RE = /(?:localhost|127\.0\.0\.1|0\.0\.0\.0):(\d{2,5})/g;

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  // Pull every candidate text string out of an event payload.
  function payloadText(ev) {
    const p = ev && ev.payload;
    if (!p) return "";
    const parts = [];
    for (const key of ["text", "output", "content", "summary", "title", "detail"]) {
      if (typeof p[key] === "string") parts.push(p[key]);
    }
    if (parts.length === 0) {
      try {
        parts.push(JSON.stringify(p));
      } catch (_) {
        /* circular — ignore */
      }
    }
    return parts.join("\n");
  }

  function findPorts(text) {
    const ports = [];
    let m;
    HOST_PORT_RE.lastIndex = 0;
    while ((m = HOST_PORT_RE.exec(text)) !== null) {
      const port = parseInt(m[1], 10);
      if (port >= 1024 && port <= 65535 && !ports.includes(port)) ports.push(port);
    }
    return ports;
  }

  function injectStyles() {
    if (document.getElementById("otr-preview-styles")) return;
    const style = document.createElement("style");
    style.id = "otr-preview-styles";
    style.textContent = [
      ".otr-preview-chip-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;",
      "margin:6px 0;}",
      ".otr-preview-chip{display:inline-flex;align-items:center;gap:6px;",
      "padding:6px 14px;border-radius:999px;background:#1d4ed8;color:#fff;",
      "font-size:14px;font-weight:600;text-decoration:none;line-height:1.2;",
      "border:1px solid rgba(255,255,255,.25);}",
      ".otr-preview-chip:active{opacity:.8;}",
      ".otr-preview-direct{font-size:13px;color:#93c5fd;text-decoration:underline;",
      "padding:4px 6px;}",
    ].join("");
    document.head.appendChild(style);
  }

  function buildChip(sessionId, port) {
    injectStyles();
    const row = document.createElement("div");
    row.className = "otr-preview-chip-row";
    row.dataset.port = String(port);

    const chip = document.createElement("a");
    chip.className = "otr-preview-chip";
    chip.target = "_blank";
    chip.rel = "noopener";
    const token = encodeURIComponent(getToken());
    chip.href = `/preview/${sessionId}/${port}/?token=${token}`;
    chip.textContent = `▶ Preview :${port}`;
    row.appendChild(chip);

    const direct = document.createElement("a");
    direct.className = "otr-preview-direct";
    direct.target = "_blank";
    direct.rel = "noopener";
    direct.href = `http://${location.hostname}:${port}/`;
    direct.textContent = `direct :${port}`;
    row.appendChild(direct);

    return row;
  }

  /**
   * Render preview chips for an event, or null when the event mentions no new
   * dev-server port. Dedupes per (sessionId, port) for the page lifetime.
   */
  function renderEvent(ev, opts) {
    if (!ev || !opts || !opts.sessionId) return null;
    if (ev.type !== "agent_text" && ev.type !== "tool_end") return null;
    const ports = findPorts(payloadText(ev));
    const fresh = ports.filter((p) => {
      const key = `${opts.sessionId}:${p}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    if (fresh.length === 0) return null;
    if (fresh.length === 1) return buildChip(opts.sessionId, fresh[0]);
    const wrap = document.createElement("div");
    for (const p of fresh) wrap.appendChild(buildChip(opts.sessionId, p));
    return wrap;
  }

  window.OTR = window.OTR || {};
  window.OTR.preview = {
    renderEvent,
    // exposed for tests / debugging
    _findPorts: findPorts,
    _resetSeen: function () {
      seen.clear();
    },
  };
})();
