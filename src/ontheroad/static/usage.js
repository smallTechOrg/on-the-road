// usage.js — home-screen daily token counter (Phase 2, slice 2B).
//
// Self-wiring: fetches /api/usage/daily on load, then every 60s, and whenever
// a `otr:usage` or `otr:turn_end` CustomEvent fires on window/document (chat
// may dispatch one after a turn; polling alone also keeps it fresh).
// Populates + unhides the #usage-counter chip in index.html with today's
// total tokens, human-formatted (e.g. "12.3k"). Safe to load standalone —
// no imports, no dependency on chat.js/app.js being present.

(function () {
  "use strict";

  const TOKEN_KEY = "otr_token"; // same key api.js uses (do not diverge)
  const POLL_MS = 60000;
  const COUNTER_ID = "usage-counter";

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  // 950 -> "950", 12345 -> "12.3k", 4200000 -> "4.2M"
  function formatTokens(n) {
    if (typeof n !== "number" || !isFinite(n) || n < 0) return "0";
    if (n < 1000) return String(Math.round(n));
    if (n < 1e6) {
      const k = n / 1000;
      return (k >= 100 ? Math.round(k) : Math.round(k * 10) / 10) + "k";
    }
    const m = n / 1e6;
    return (m >= 100 ? Math.round(m) : Math.round(m * 10) / 10) + "M";
  }

  let latest = null; // last /api/usage/daily body

  function render(data) {
    const el = document.getElementById(COUNTER_ID);
    if (!el || !data) return;
    const total = data.today_total || 0;
    el.textContent = formatTokens(total) + " tok today";
    el.title = "Daily token total: " + total.toLocaleString();
    el.hidden = false;
  }

  async function refresh() {
    const token = getToken();
    if (!token) return null; // not signed in yet — keep the chip hidden
    let res;
    try {
      res = await fetch("/api/usage/daily", {
        headers: { Authorization: "Bearer " + token },
      });
    } catch (_) {
      return null; // offline — keep last rendered value
    }
    if (!res.ok) return null;
    try {
      latest = await res.json();
    } catch (_) {
      return null;
    }
    render(latest);
    return latest;
  }

  function start() {
    refresh();
    setInterval(refresh, POLL_MS);
    for (const name of ["otr:usage", "otr:turn_end"]) {
      window.addEventListener(name, refresh);
      document.addEventListener(name, refresh);
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", start);
  } else {
    start();
  }

  window.OTR = window.OTR || {};
  window.OTR.usage = {
    refresh,
    // exposed for tests / debugging
    _formatTokens: formatTokens,
    _latest: function () {
      return latest;
    },
  };
})();
