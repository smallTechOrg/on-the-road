// chat.js — chat view: debug transcript render, WebSocket stream with
// since_seq resume + exponential backoff, idempotent render (dedupe by seq).
// All agent-origin HTML goes through marked -> DOMPurify before insertion.

import * as api from "/api.js";

const $ = (id) => document.getElementById(id);

// marked + DOMPurify are vendored classic scripts (window globals).
function renderMarkdown(text) {
  const raw = window.marked ? window.marked.parse(text, { async: false }) : text;
  return window.DOMPurify
    ? window.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } })
    : escapeHtml(text);
}
function escapeHtml(s) {
  const d = document.createElement("div");
  d.textContent = s;
  return d.innerHTML;
}

const TOOL_ICONS = {
  execute: "🖥️", read: "📄", edit: "✏️", write: "✏️", delete: "🗑️",
  search: "🔎", fetch: "🌐", think: "💭", other: "🔧",
};

export class ChatView {
  constructor({ onExit, onUnauthorized }) {
    this.onExit = onExit;
    this.onUnauthorized = onUnauthorized;
    this.session = null;
    this.ws = null;
    this.closed = true;
    this.lastSeq = 0;           // highest seq rendered — reconnect resumes here
    this.rendered = new Set();  // seqs rendered (idempotent render / dedupe)
    this.backoff = 1000;
    this.reconnectTimer = null;
    // status line state
    this.turnRunning = false;
    this.turnStartTs = null;
    this.stepCount = 0;
    this.activity = "idle";
    this.statusTimer = null;
    this.autoScroll = true;
    this._bindStatic();
  }

  _bindStatic() {
    if (ChatView._bound) return;
    ChatView._bound = true;
    $("chat-back").addEventListener("click", () => this.onExit());
    $("composer").addEventListener("submit", (e) => { e.preventDefault(); this._send(); });
    $("composer-input").addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); this._send(); }
    });
    $("composer-input").addEventListener("input", (e) => {
      const t = e.target; t.style.height = "auto"; t.style.height = Math.min(t.scrollHeight, innerHeight * 0.3) + "px";
    });
    $("stop-btn").addEventListener("click", () => {
      api.cancelTurn(this.session.id).catch(() => {});
    });
    const transcript = $("transcript");
    transcript.addEventListener("scroll", () => {
      const nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 60;
      this.autoScroll = nearBottom;
      $("jump-latest").hidden = nearBottom;
    });
    $("jump-latest").addEventListener("click", () => {
      this.autoScroll = true;
      $("jump-latest").hidden = true;
      this._scrollToBottom();
    });
  }

  open(session) {
    this.session = session;
    this.closed = false;
    this.lastSeq = 0;
    this.rendered = new Set();
    this.backoff = 1000;
    this.turnRunning = false;
    this.stepCount = 0;
    this.activity = "idle";
    this.autoScroll = true;
    $("chat-title").textContent = session.title || `${session.adapter} session`;
    $("transcript").innerHTML = "";
    $("transcript").appendChild(this._emptyHint());
    $("jump-latest").hidden = true;
    this._setConn("reconnecting", "connecting…");
    this._updateStatusLine();
    this.statusTimer = setInterval(() => this._updateStatusLine(), 1000);
    this._connect();
  }

  close() {
    this.closed = true;
    clearTimeout(this.reconnectTimer);
    clearInterval(this.statusTimer);
    if (this.ws) { try { this.ws.close(); } catch (_) {} this.ws = null; }
  }

  _emptyHint() {
    const p = document.createElement("p");
    p.className = "muted center";
    p.id = "transcript-empty";
    p.textContent = "Send a message to start the agent.";
    return p;
  }

  // ---- WebSocket with resume + backoff ----

  _connect() {
    if (this.closed) return;
    const ws = new WebSocket(api.streamUrl(this.session.id, this.lastSeq));
    this.ws = ws;
    ws.onopen = () => {
      this.backoff = 1000;
      this._setConn("live", "live");
      $("composer-input").disabled = false;
    };
    ws.onmessage = (m) => {
      let evt;
      try { evt = JSON.parse(m.data); } catch (_) { return; }
      if (evt.type === "pong") return;
      if (evt.seq === undefined && evt.type === "error") {
        // fatal stream error frame (spec: {"type":"error",...} then close)
        this._renderErrorText(evt.payload?.message || evt.message || "stream error");
        return;
      }
      this.renderEvent(evt);
    };
    ws.onclose = (e) => {
      if (this.closed) return;
      if (e.code === 4401) { this.onUnauthorized(); return; }
      this._scheduleReconnect();
    };
    ws.onerror = () => { try { ws.close(); } catch (_) {} };
  }

  _scheduleReconnect() {
    this._setConn("reconnecting", "reconnecting…");
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = setTimeout(() => this._connect(), this.backoff);
    this.backoff = Math.min(this.backoff * 2, 30000); // exponential, capped
  }

  _setConn(state, text) {
    $("conn-indicator").dataset.state = state;
    $("conn-text").textContent = text;
    const offline = state === "reconnecting" || state === "offline";
    $("composer-input").disabled = offline;
    $("send-btn").disabled = offline;
  }

  // ---- sending ----

  async _send() {
    const input = $("composer-input");
    const text = input.value.trim();
    if (!text || !this.session) return;
    input.value = "";
    input.style.height = "auto";
    try {
      await api.sendMessage(this.session.id, text);
      // The persisted user_message comes back over the stream; render is
      // idempotent by seq so we wait for the authoritative event.
    } catch (e) {
      if (e.status === 401) return this.onUnauthorized();
      input.value = text; // restore so nothing is lost
      this._renderErrorText(`Send failed: ${e.message}`);
    }
  }

  // ---- rendering (idempotent by seq) ----

  renderEvent(evt) {
    const seq = evt.seq;
    if (typeof seq === "number") {
      if (this.rendered.has(seq)) return; // dedupe
      this.rendered.add(seq);
      if (seq > this.lastSeq) this.lastSeq = seq;
    }
    const hint = $("transcript-empty");
    if (hint) hint.remove();

    const p = evt.payload || {};
    switch (evt.type) {
      case "user_message":
        this._append(this._bubble("evt-user", p.text ?? ""), true);
        this._turnStarted();
        break;
      case "agent_text":
        this._appendAgentText(p.text ?? "", false, seq);
        break;
      case "agent_thought":
        this._appendAgentText(p.text ?? "", true, seq);
        break;
      case "tool_start":
        this.stepCount += 1;
        this._renderTool(evt);
        break;
      case "tool_update":
      case "tool_end":
        this._renderTool(evt);
        break;
      case "status":
        this.activity = p.title || p.text || p.status || "working…";
        this._appendMeta(`status: ${this.activity}`);
        break;
      case "usage":
        this._appendMeta(this._usageText(p));
        break;
      case "git_status":
        this._appendMeta(`git: ${p.summary || JSON.stringify(p)}`);
        break;
      case "permission_request":
        this._renderPermission(evt);
        break;
      case "turn_end":
        this._turnEnded(p);
        break;
      case "error":
        this._renderErrorText(p.message || "agent error");
        this._turnEnded({});
        break;
      default:
        this._appendMeta(`${evt.type}: ${JSON.stringify(p).slice(0, 200)}`);
    }
    this._updateStatusLine();
  }

  _turnStarted() {
    if (!this.turnRunning) {
      this.turnRunning = true;
      this.turnStartTs = Date.now();
      this.stepCount = 0;
      this.activity = "thinking…";
    }
    $("stop-btn").hidden = false;
  }

  _turnEnded(payload) {
    this.turnRunning = false;
    this.activity = payload.stop_reason ? `turn ended (${payload.stop_reason})` : "idle";
    $("stop-btn").hidden = true;
  }

  _updateStatusLine() {
    let line = this.activity;
    if (this.turnRunning && this.turnStartTs) {
      const s = Math.floor((Date.now() - this.turnStartTs) / 1000);
      const elapsed = s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`;
      line = `${this.activity} · ${elapsed} · ${this.stepCount} step${this.stepCount === 1 ? "" : "s"}`;
    }
    $("chat-status-line").textContent = line;
  }

  _bubble(cls, text) {
    const div = document.createElement("div");
    div.className = `evt ${cls}`;
    div.textContent = text; // plain text — no HTML from the user
    return div;
  }

  _appendAgentText(text, isThought, seq) {
    const div = document.createElement("div");
    div.className = `evt evt-agent${isThought ? " evt-thought" : ""}`;
    if (seq !== undefined) div.dataset.seq = String(seq);
    div.innerHTML = renderMarkdown(text); // sanitized in renderMarkdown
    this._append(div, true);
  }

  _appendMeta(text) {
    const div = document.createElement("div");
    div.className = "evt evt-meta";
    div.textContent = text;
    this._append(div, true);
  }

  _usageText(p) {
    const it = p.input_tokens ?? "?";
    const ot = p.output_tokens ?? "?";
    return `usage: ${it} in / ${ot} out tokens`;
  }

  _renderErrorText(msg) {
    const div = document.createElement("div");
    div.className = "evt evt-error";
    div.textContent = msg;
    this._append(div, true);
  }

  // Tool calls: one collapsible <details> per tool_call_id, updated in place.
  _renderTool(evt) {
    const p = evt.payload || {};
    const toolId = p.tool_call_id || p.id || `seq-${evt.seq}`;
    let el = $("transcript").querySelector(`[data-tool-id="${CSS.escape(toolId)}"]`);
    if (!el) {
      el = document.createElement("details");
      el.className = "evt evt-tool";
      el.dataset.toolId = toolId;
      el.innerHTML =
        '<summary><span class="tool-kind"></span>' +
        '<span class="tool-title"></span>' +
        '<span class="tool-status"></span></summary>' +
        '<div class="tool-body"></div>';
      this._append(el, true);
    }
    const kind = p.kind || "other";
    el.querySelector(".tool-kind").textContent = TOOL_ICONS[kind] || TOOL_ICONS.other;
    if (p.title) el.querySelector(".tool-title").textContent = p.title;
    else if (!el.querySelector(".tool-title").textContent)
      el.querySelector(".tool-title").textContent = kind;
    const status =
      evt.type === "tool_end" ? (p.status || "completed") :
      evt.type === "tool_update" ? (p.status || "running") : (p.status || "running");
    const st = el.querySelector(".tool-status");
    st.textContent = status;
    st.className = `tool-status ${status}`;
    // payload body: command / output / content — plain text only
    const parts = [];
    if (p.command) parts.push(`$ ${p.command}`);
    if (p.input) parts.push(typeof p.input === "string" ? p.input : JSON.stringify(p.input, null, 2));
    if (p.output) parts.push(typeof p.output === "string" ? p.output : JSON.stringify(p.output, null, 2));
    if (p.content) parts.push(typeof p.content === "string" ? p.content : JSON.stringify(p.content, null, 2));
    const body = el.querySelector(".tool-body");
    if (parts.length) body.textContent = parts.join("\n\n");
    else if (!body.textContent) body.textContent = "(no payload)";
  }

  // Permission request: inline card, Phase 1 auto-declined notice, buttons disabled.
  _renderPermission(evt) {
    const p = evt.payload || {};
    const div = document.createElement("div");
    div.className = "evt evt-permission";
    const title = document.createElement("div");
    title.textContent = p.title || p.tool_title || "Agent requests permission";
    div.appendChild(title);
    const opts = document.createElement("div");
    opts.className = "perm-options";
    (p.options || []).forEach((o) => {
      const b = document.createElement("button");
      b.className = "btn stub";
      b.disabled = true;
      b.textContent = o.name || o.option_id || o.id || "option";
      opts.appendChild(b);
    });
    div.appendChild(opts);
    const note = document.createElement("span");
    note.className = "perm-note";
    note.textContent = "auto-declined (safe default) — approvals coming soon";
    div.appendChild(note);
    this._append(div, true);
  }

  _append(el, scroll) {
    $("transcript").appendChild(el);
    if (scroll && this.autoScroll) this._scrollToBottom();
    else if (scroll) $("jump-latest").hidden = false;
  }

  _scrollToBottom() {
    const t = $("transcript");
    t.scrollTop = t.scrollHeight;
  }
}
