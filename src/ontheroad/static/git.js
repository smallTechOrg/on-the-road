// git.js — git surfacing + interactive ACP permission approvals (Phase 2, 2D).
//
// Hook contract (called by the chat renderer, slice 2A):
//   window.OTR.git.renderEvent(ev, { sessionId }) -> HTMLElement | null
// Claims git_status, permission_request and permission_response events:
//   * git_status  -> tappable chip row; PR links open in a new tab;
//   * permission_request -> card with approve/deny buttons that POST
//     /api/sessions/{id}/approvals/{request_id} with {option_id};
//   * permission_response -> outcome line; also disables the buttons of the
//     matching request card (including timeout auto-approvals, which are
//     labelled clearly).
// Safe to load standalone — no imports, no dependency on chat.js.

(function () {
  "use strict";

  const TOKEN_KEY = "otr_token"; // same key api.js uses (do not diverge)
  // request_id -> { card, buttons[], note } for pending permission cards.
  const pendingCards = new Map();

  function getToken() {
    try {
      return localStorage.getItem(TOKEN_KEY) || "";
    } catch (_) {
      return "";
    }
  }

  function injectStyles() {
    if (document.getElementById("otr-git-styles")) return;
    const style = document.createElement("style");
    style.id = "otr-git-styles";
    style.textContent = [
      ".otr-git-row{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:6px 0;}",
      ".otr-git-chip{display:inline-flex;align-items:center;gap:6px;padding:6px 14px;",
      "border-radius:999px;background:#15803d;color:#fff;font-size:14px;font-weight:600;",
      "text-decoration:none;line-height:1.2;border:1px solid rgba(255,255,255,.25);}",
      ".otr-git-chip:active{opacity:.8;}",
      ".otr-git-chip.plain{background:#374151;}",
      ".otr-perm-card{border:1px solid #d97706;border-radius:10px;padding:10px 12px;",
      "margin:8px 0;background:rgba(217,119,6,.08);font-size:14px;}",
      ".otr-perm-title{font-weight:600;margin-bottom:8px;}",
      ".otr-perm-buttons{display:flex;gap:8px;flex-wrap:wrap;}",
      ".otr-perm-btn{padding:8px 16px;border-radius:8px;border:none;font-size:14px;",
      "font-weight:600;cursor:pointer;color:#fff;background:#6b7280;}",
      ".otr-perm-btn.approve{background:#15803d;}",
      ".otr-perm-btn.deny{background:#b91c1c;}",
      ".otr-perm-btn:disabled{opacity:.45;cursor:default;}",
      ".otr-perm-note{display:block;margin-top:8px;font-size:13px;opacity:.85;}",
      ".otr-perm-note.error{color:#f87171;}",
      ".otr-perm-outcome{font-size:13px;opacity:.85;margin:4px 0;}",
    ].join("");
    document.head.appendChild(style);
  }

  // ---- git_status ----

  function renderGitStatus(ev) {
    injectStyles();
    const p = ev.payload || {};
    const row = document.createElement("div");
    row.className = "otr-git-row";

    const summary = document.createElement("span");
    summary.className = "otr-git-chip plain";
    const bits = [];
    if (p.branch) bits.push(p.branch);
    if (p.summary) bits.push(p.summary);
    summary.textContent = "⎇ " + (bits.join(" — ") || p.state || "git activity");
    row.appendChild(summary);

    const prUrl = p.pr_url || p.prUrl || p.url;
    if (typeof prUrl === "string" && /^https?:\/\//.test(prUrl)) {
      const chip = document.createElement("a");
      chip.className = "otr-git-chip";
      chip.target = "_blank";
      chip.rel = "noopener";
      chip.href = prUrl;
      chip.textContent = "↗ Open PR";
      row.appendChild(chip);
    }
    return row;
  }

  // ---- permission_request ----

  function isAllow(opt) {
    const kind = String(opt.kind || "").toLowerCase();
    const oid = String(opt.optionId || opt.option_id || "").toLowerCase();
    return kind.includes("allow") || oid.includes("allow");
  }

  async function postApproval(sessionId, requestId, optionId) {
    const res = await fetch(
      `/api/sessions/${encodeURIComponent(sessionId)}/approvals/${encodeURIComponent(requestId)}`,
      {
        method: "POST",
        headers: {
          Authorization: `Bearer ${getToken()}`,
          "Content-Type": "application/json",
        },
        body: JSON.stringify({ option_id: optionId }),
      }
    );
    if (!res.ok) {
      let msg = `HTTP ${res.status}`;
      try {
        const body = await res.json();
        if (body && body.error && body.error.message) msg = body.error.message;
      } catch (_) {}
      throw new Error(msg);
    }
  }

  function settleCard(requestId, text, isError) {
    const entry = pendingCards.get(requestId);
    if (!entry) return;
    for (const b of entry.buttons) b.disabled = true;
    entry.note.textContent = text;
    entry.note.classList.toggle("error", !!isError);
    if (!isError) pendingCards.delete(requestId);
  }

  function renderPermissionRequest(ev, opts) {
    injectStyles();
    const p = ev.payload || {};
    const requestId = String(p.request_id || "");
    const card = document.createElement("div");
    card.className = "otr-perm-card";
    card.dataset.requestId = requestId;

    const title = document.createElement("div");
    title.className = "otr-perm-title";
    const tc = p.tool_call || {};
    title.textContent =
      "Permission requested: " + (tc.title || p.title || requestId || "tool call");
    card.appendChild(title);

    const row = document.createElement("div");
    row.className = "otr-perm-buttons";
    const note = document.createElement("span");
    note.className = "otr-perm-note";
    note.textContent = "waiting for your decision…";
    const buttons = [];

    (p.options || []).forEach((o) => {
      const optionId = String(o.optionId || o.option_id || "");
      const b = document.createElement("button");
      b.type = "button";
      b.className = "otr-perm-btn " + (isAllow(o) ? "approve" : "deny");
      b.textContent = o.name || optionId || "option";
      b.addEventListener("click", async () => {
        for (const btn of buttons) btn.disabled = true;
        note.textContent = "sending…";
        note.classList.remove("error");
        try {
          await postApproval(opts.sessionId, requestId, optionId);
          settleCard(requestId, `answered: ${b.textContent} (${optionId})`);
        } catch (e) {
          // Re-enable so the user can retry (unless it already resolved).
          if (pendingCards.has(requestId)) {
            for (const btn of buttons) btn.disabled = false;
          }
          note.textContent = `failed: ${e.message}`;
          note.classList.add("error");
        }
      });
      buttons.push(b);
      row.appendChild(b);
    });

    card.appendChild(row);
    card.appendChild(note);
    if (requestId) pendingCards.set(requestId, { card, buttons, note });
    return card;
  }

  // ---- permission_response ----

  function renderPermissionResponse(ev) {
    injectStyles();
    const p = ev.payload || {};
    const requestId = String(p.request_id || "");
    const optionId = p.option_id == null ? "cancelled" : String(p.option_id);
    const timedOut = p.source === "timeout";
    const text = timedOut
      ? `⏱ no answer in time — auto-rejected ${optionId}`
      : `permission answered: ${optionId}`;
    settleCard(requestId, text);
    const line = document.createElement("div");
    line.className = "otr-perm-outcome";
    line.textContent = text + (requestId ? ` (${requestId})` : "");
    return line;
  }

  function renderEvent(ev, opts) {
    if (!ev || !opts || !opts.sessionId) return null;
    try {
      if (ev.type === "git_status") return renderGitStatus(ev);
      if (ev.type === "permission_request") return renderPermissionRequest(ev, opts);
      if (ev.type === "permission_response") return renderPermissionResponse(ev);
    } catch (_) {
      return null; // fall back to chat.js built-in rendering
    }
    return null;
  }

  window.OTR = window.OTR || {};
  window.OTR.git = {
    renderEvent,
    // exposed for tests / debugging
    _pendingCards: pendingCards,
  };
})();
