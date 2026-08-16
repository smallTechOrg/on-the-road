// diff.js — mobile-friendly git diff viewer overlay (Phase 3, slice 3C).
//
// Self-registers on window.OTR.diff.
// Exports: openDiffView(sessionId)

(function () {
  "use strict";

  const TOKEN_KEY = "otr_token";

  function getToken() {
    try { return localStorage.getItem(TOKEN_KEY) || ""; }
    catch (_) { return ""; }
  }

  async function fetchDiff(sessionId) {
    const resp = await fetch(`/api/sessions/${sessionId}/files/diff`, {
      headers: { Authorization: "Bearer " + getToken() },
    });
    if (!resp.ok) throw new Error("Failed to fetch diff: " + resp.status);
    return resp.json();
  }

  function parseDiff(raw) {
    const files = [];
    let current = null;
    for (const line of raw.split("\n")) {
      if (line.startsWith("diff --git")) {
        const m = line.split(" b/");
        current = { path: m.length > 1 ? m[1] : "unknown", hunks: [], lines: [] };
        files.push(current);
      } else if (!current) {
        continue;
      } else if (line.startsWith("@@")) {
        current.lines.push({ type: "hunk", text: line });
      } else if (line.startsWith("+++ ") || line.startsWith("--- ")) {
        // skip file headers
      } else if (line.startsWith("+")) {
        current.lines.push({ type: "add", text: line.slice(1) });
      } else if (line.startsWith("-")) {
        current.lines.push({ type: "del", text: line.slice(1) });
      } else {
        current.lines.push({ type: "ctx", text: line.startsWith(" ") ? line.slice(1) : line });
      }
    }
    return files;
  }

  function renderOverlay(data) {
    // Remove existing
    const old = document.querySelector(".diff-overlay");
    if (old) old.remove();

    const overlay = document.createElement("div");
    overlay.className = "diff-overlay";

    // Header
    const header = document.createElement("div");
    header.className = "diff-header";
    const h2 = document.createElement("h2");
    h2.textContent = "Diff";
    const closeBtn = document.createElement("button");
    closeBtn.className = "btn";
    closeBtn.textContent = "Close";
    closeBtn.onclick = () => overlay.remove();
    header.append(h2, closeBtn);
    overlay.appendChild(header);

    // Stats
    const stats = document.createElement("div");
    stats.className = "diff-stats";
    const fc = data.files_changed ? data.files_changed.length : 0;
    const ins = data.stats ? data.stats.insertions : 0;
    const del_ = data.stats ? data.stats.deletions : 0;
    stats.innerHTML = `${fc} file${fc !== 1 ? "s" : ""} changed, ` +
      `<span class="add">+${ins} insertion${ins !== 1 ? "s" : ""}</span>, ` +
      `<span class="del">-${del_} deletion${del_ !== 1 ? "s" : ""}</span>`;
    overlay.appendChild(stats);

    // Body
    const body = document.createElement("div");
    body.className = "diff-body";

    if (!data.diff || data.diff.trim() === "") {
      const p = document.createElement("p");
      p.className = "muted center";
      p.style.padding = "2rem";
      p.textContent = "No changes";
      body.appendChild(p);
    } else {
      const files = parseDiff(data.diff);
      let lineNum = 0;
      for (const file of files) {
        const details = document.createElement("details");
        details.className = "diff-file";
        details.open = true;
        const summary = document.createElement("summary");
        summary.textContent = file.path;
        details.appendChild(summary);

        for (const ln of file.lines) {
          if (ln.type === "hunk") {
            const hunkDiv = document.createElement("div");
            hunkDiv.className = "diff-hunk-header";
            hunkDiv.textContent = ln.text;
            details.appendChild(hunkDiv);
            lineNum = 0;
            continue;
          }
          lineNum++;
          const row = document.createElement("div");
          row.className = "diff-line" + (ln.type === "add" ? " add" : ln.type === "del" ? " del" : "");
          const num = document.createElement("span");
          num.className = "diff-line-num";
          num.textContent = String(lineNum);
          const content = document.createElement("span");
          content.className = "diff-line-content";
          content.textContent = ln.text;
          row.append(num, content);
          details.appendChild(row);
        }
        body.appendChild(details);
      }
    }
    overlay.appendChild(body);
    document.body.appendChild(overlay);
  }

  async function openDiffView(sessionId) {
    // Show loading overlay immediately
    const old = document.querySelector(".diff-overlay");
    if (old) old.remove();
    const overlay = document.createElement("div");
    overlay.className = "diff-overlay";
    overlay.innerHTML = '<div class="diff-header"><h2>Diff</h2></div>' +
      '<div class="diff-body"><p class="muted center" style="padding:2rem">Loading diff...</p></div>';
    document.body.appendChild(overlay);

    try {
      const data = await fetchDiff(sessionId);
      overlay.remove();
      renderOverlay(data);
    } catch (err) {
      overlay.remove();
      const errOverlay = document.createElement("div");
      errOverlay.className = "diff-overlay";
      const header = document.createElement("div");
      header.className = "diff-header";
      const h2 = document.createElement("h2");
      h2.textContent = "Diff";
      const closeBtn = document.createElement("button");
      closeBtn.className = "btn";
      closeBtn.textContent = "Close";
      closeBtn.onclick = () => errOverlay.remove();
      header.append(h2, closeBtn);
      errOverlay.appendChild(header);
      const body = document.createElement("div");
      body.className = "diff-body";
      body.innerHTML = '<p class="error-text" style="padding:2rem">Failed to load diff: ' +
        err.message + '</p>';
      errOverlay.appendChild(body);
      document.body.appendChild(errOverlay);
    }
  }

  // Self-register
  window.OTR = window.OTR || {};
  window.OTR.diff = { openDiffView };

})();
