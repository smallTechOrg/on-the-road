/**
 * term.js -- minimal <pre>-based terminal overlay for On The Road.
 * Self-registers on window.OTR.term.
 */

(function () {
  "use strict";

  window.OTR = window.OTR || {};

  /* ---------- ANSI SGR to HTML converter ---------- */

  const SGR_COLORS = {
    30: "#1e1e1e", 31: "#e06c75", 32: "#98c379", 33: "#e5c07b",
    34: "#61afef", 35: "#c678dd", 36: "#56b6c2", 37: "#abb2bf",
    90: "#5c6370", 91: "#e06c75", 92: "#98c379", 93: "#e5c07b",
    94: "#61afef", 95: "#c678dd", 96: "#56b6c2", 97: "#ffffff",
  };

  const SGR_BG_COLORS = {
    40: "#1e1e1e", 41: "#e06c75", 42: "#98c379", 43: "#e5c07b",
    44: "#61afef", 45: "#c678dd", 46: "#56b6c2", 47: "#abb2bf",
    100: "#5c6370", 101: "#e06c75", 102: "#98c379", 103: "#e5c07b",
    104: "#61afef", 105: "#c678dd", 106: "#56b6c2", 107: "#ffffff",
  };

  function ansiToHtml(text) {
    // Escape HTML entities first
    text = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");

    let result = "";
    let i = 0;
    let spanOpen = false;

    // State
    let bold = false, dim = false, italic = false, underline = false;
    let fg = null, bg = null;

    const ESC_RE = /\x1b\[([0-9;]*)m/g;
    let lastIndex = 0;
    let match;

    while ((match = ESC_RE.exec(text)) !== null) {
      // Append text before this escape
      result += text.slice(lastIndex, match.index);
      lastIndex = ESC_RE.lastIndex;

      const codes = match[1] ? match[1].split(";").map(Number) : [0];

      for (const code of codes) {
        if (code === 0) {
          bold = dim = italic = underline = false;
          fg = bg = null;
        } else if (code === 1) bold = true;
        else if (code === 2) dim = true;
        else if (code === 3) italic = true;
        else if (code === 4) underline = true;
        else if (code === 22) bold = dim = false;
        else if (code === 23) italic = false;
        else if (code === 24) underline = false;
        else if (code >= 30 && code <= 37 || code >= 90 && code <= 97) fg = SGR_COLORS[code];
        else if (code === 39) fg = null;
        else if (code >= 40 && code <= 47 || code >= 100 && code <= 107) bg = SGR_BG_COLORS[code];
        else if (code === 49) bg = null;
      }

      if (spanOpen) { result += "</span>"; spanOpen = false; }

      const parts = [];
      if (fg) parts.push("color:" + fg);
      if (bg) parts.push("background:" + bg);
      if (bold) parts.push("font-weight:bold");
      if (dim) parts.push("opacity:0.6");
      if (italic) parts.push("font-style:italic");
      if (underline) parts.push("text-decoration:underline");

      if (parts.length) {
        result += '<span style="' + parts.join(";") + '">';
        spanOpen = true;
      }
    }

    result += text.slice(lastIndex);
    if (spanOpen) result += "</span>";

    // Strip any remaining non-SGR escape sequences
    result = result.replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
    result = result.replace(/\x1b\][^\x07]*(?:\x07|\x1b\\)/g, "");

    return result;
  }

  /* ---------- Terminal overlay ---------- */

  function openTerminal(sessionId) {
    const token = localStorage.getItem("otr_token") || "";

    // Build overlay
    const overlay = document.createElement("div");
    overlay.className = "term-overlay";
    overlay.innerHTML =
      '<div class="term-header">' +
        "<h2>Terminal</h2>" +
        '<button class="btn" id="term-close">Close</button>' +
      "</div>" +
      '<pre class="term-output" id="terminal-output"></pre>' +
      '<div class="term-input-bar">' +
        '<input type="text" id="term-input" placeholder="Type command..." autocomplete="off" autocapitalize="off" spellcheck="false" />' +
        '<button class="btn primary" id="term-send">Send</button>' +
      "</div>";

    document.body.appendChild(overlay);

    const output = overlay.querySelector("#terminal-output");
    const inputEl = overlay.querySelector("#term-input");
    const sendBtn = overlay.querySelector("#term-send");
    const closeBtn = overlay.querySelector("#term-close");

    // WebSocket
    const proto = location.protocol === "https:" ? "wss:" : "ws:";
    const url = proto + "//" + location.host + "/api/sessions/" + sessionId + "/term?token=" + encodeURIComponent(token);
    let ws;
    try {
      ws = new WebSocket(url);
    } catch (e) {
      output.textContent = "Failed to connect: " + e.message;
      return;
    }

    ws.onmessage = function (ev) {
      try {
        const frame = JSON.parse(ev.data);
        if (frame.type === "output") {
          output.innerHTML += ansiToHtml(frame.data);
          output.scrollTop = output.scrollHeight;
        }
      } catch (_) {}
    };

    ws.onerror = function () {
      output.innerHTML += '<span style="color:#f87171">Connection error.</span>\n';
    };

    ws.onclose = function () {
      output.innerHTML += '<span style="color:#94a3b8">[terminal disconnected]</span>\n';
    };

    function sendInput(text) {
      if (ws && ws.readyState === WebSocket.OPEN) {
        ws.send(JSON.stringify({ type: "input", data: text }));
      }
    }

    // Keyboard capture on the output pre (for desktop)
    overlay.addEventListener("keydown", function (e) {
      if (e.target === inputEl) return; // let input bar handle its own keys

      e.preventDefault();
      let data = "";
      if (e.key === "Enter") data = "\r";
      else if (e.key === "Backspace") data = "\x7f";
      else if (e.key === "Tab") data = "\t";
      else if (e.key === "Escape") data = "\x1b";
      else if (e.key === "ArrowUp") data = "\x1b[A";
      else if (e.key === "ArrowDown") data = "\x1b[B";
      else if (e.key === "ArrowRight") data = "\x1b[C";
      else if (e.key === "ArrowLeft") data = "\x1b[D";
      else if (e.ctrlKey && e.key.length === 1) {
        const code = e.key.toLowerCase().charCodeAt(0) - 96;
        if (code > 0 && code < 27) data = String.fromCharCode(code);
      } else if (e.key.length === 1) data = e.key;

      if (data) sendInput(data);
    });

    // Mobile input bar
    function sendBarInput() {
      const text = inputEl.value;
      if (!text) return;
      sendInput(text + "\n");
      inputEl.value = "";
    }

    sendBtn.addEventListener("click", sendBarInput);
    inputEl.addEventListener("keydown", function (e) {
      if (e.key === "Enter") {
        e.preventDefault();
        sendBarInput();
      }
    });

    // Close
    function close() {
      if (ws) {
        try { ws.close(); } catch (_) {}
      }
      overlay.remove();
    }

    closeBtn.addEventListener("click", close);

    // Focus overlay for keyboard capture
    overlay.setAttribute("tabindex", "0");
    overlay.focus();
  }

  window.OTR = window.OTR || {};
  window.OTR.term = { openTerminal: openTerminal };
})();
