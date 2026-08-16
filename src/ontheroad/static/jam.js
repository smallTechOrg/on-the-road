// jam.js — jam-mode renderer: a condensed, chat-prominent projection of the
// SAME persisted event stream the debug renderer shows.
//   - agent_text: prominent markdown bubbles (coalesced chunk-by-chunk, same
//     coalescing contract as debug: consecutive chunks grow one bubble)
//   - agent_thought: tucked behind a subtle <details> disclosure
//   - tool calls: consecutive calls condensed into one "ran N tool calls"
//     group; each call is a one-line row (icon + title + status)
//   - status events: compact timeline markers
// Pure DOM module — no fetches; chat.js owns the event list and dispatch.

export function createJamRenderer(ctx) {
  // ctx: { append(el, scroll), scroll(), renderMarkdown(text), toolIcons }
  let streamText = null;    // { el, buf } — coalesced agent_text bubble
  let thoughtStream = null; // { el, buf } — coalesced thought inside disclosure
  let toolGroup = null;     // { el, list, count } — current condensed group

  function closeText() { streamText = null; }
  function closeThoughts() { thoughtStream = null; }
  function closeTools() { toolGroup = null; }
  function closeAll() { closeText(); closeThoughts(); closeTools(); }

  function marker(text, cls) {
    const div = document.createElement("div");
    div.className = `evt jam-marker ${cls || ""}`.trim();
    div.textContent = text;
    ctx.append(div, true);
    return div;
  }

  function ensureToolGroup() {
    if (toolGroup) return toolGroup;
    const el = document.createElement("details");
    el.className = "evt jam-tools";
    el.innerHTML =
      '<summary><span class="jam-tools-label"></span></summary>' +
      '<div class="jam-tools-list"></div>';
    ctx.append(el, true);
    toolGroup = { el, list: el.querySelector(".jam-tools-list"), count: 0 };
    return toolGroup;
  }

  function toolRow(evt) {
    const p = evt.payload || {};
    const toolId = p.tool_call_id || p.id || `seq-${evt.seq}`;
    // Update in place if the row already exists anywhere in the transcript
    // (tool_update / tool_end may arrive after a re-render).
    let row = document.querySelector(`#transcript .jam-tool-row[data-tool-id="${CSS.escape(toolId)}"]`);
    const g = ensureToolGroup();
    if (!row) {
      row = document.createElement("div");
      row.className = "jam-tool-row";
      row.dataset.toolId = toolId;
      row.innerHTML =
        '<span class="tool-kind"></span><span class="tool-title"></span>' +
        '<span class="tool-status"></span>';
      g.list.appendChild(row);
      g.count += 1;
      g.el.querySelector(".jam-tools-label").textContent =
        `ran ${g.count} tool call${g.count === 1 ? "" : "s"} — expand`;
    }
    const kind = p.kind || "other";
    row.querySelector(".tool-kind").textContent =
      ctx.toolIcons[kind] || ctx.toolIcons.other;
    const titleEl = row.querySelector(".tool-title");
    if (p.title) titleEl.textContent = p.title;
    else if (!titleEl.textContent) titleEl.textContent = kind;
    const status =
      evt.type === "tool_end" ? (p.status || "completed") : (p.status || "running");
    const st = row.querySelector(".tool-status");
    st.textContent = status === "completed" ? "✓" : status === "failed" ? "✗" : "…";
    st.className = `tool-status ${status}`;
    st.title = status;
  }

  return {
    reset() { closeAll(); },

    // Returns true when the event was handled (chat.js falls back otherwise).
    render(evt) {
      const p = evt.payload || {};
      switch (evt.type) {
        case "user_message": {
          closeAll();
          const div = document.createElement("div");
          div.className = "evt evt-user";
          div.textContent = p.text ?? "";
          ctx.append(div, true);
          return true;
        }
        case "agent_text": {
          closeThoughts(); closeTools();
          if (!streamText) {
            const el = document.createElement("div");
            el.className = "evt evt-agent jam-text";
            ctx.append(el, true);
            streamText = { el, buf: "" };
          }
          streamText.buf += p.text ?? "";
          streamText.el.innerHTML = ctx.renderMarkdown(streamText.buf);
          ctx.scroll(); // keep scroll pinned as the bubble grows
          return true;
        }
        case "agent_thought": {
          closeText(); closeTools();
          if (!thoughtStream) {
            const details = document.createElement("details");
            details.className = "evt jam-thoughts";
            details.innerHTML =
              '<summary>💭 thoughts</summary><div class="jam-thoughts-body"></div>';
            ctx.append(details, true);
            thoughtStream = { el: details.querySelector(".jam-thoughts-body"), buf: "" };
          }
          thoughtStream.buf += p.text ?? "";
          thoughtStream.el.innerHTML = ctx.renderMarkdown(thoughtStream.buf);
          ctx.scroll();
          return true;
        }
        case "tool_start":
        case "tool_update":
        case "tool_end":
          closeText(); closeThoughts();
          toolRow(evt);
          return true;
        case "status":
          closeAll();
          marker(`◆ ${p.title || p.text || p.status || "working…"}`, "jam-milestone");
          return true;
        case "usage":
          return true; // hidden in jam mode (daily total lives on the home screen)
        case "git_status":
          closeAll();
          marker(`⎇ ${p.summary || "git activity"}`, "jam-git");
          return true;
        case "permission_request":
          closeAll();
          marker(`🔐 permission requested: ${p.title || p.tool_title || "action"}`, "jam-perm");
          return true;
        case "permission_response":
          marker(`🔐 permission ${p.outcome || p.option_id || "answered"}`, "jam-perm");
          return true;
        case "turn_end":
          closeAll();
          return true;
        case "error": {
          closeAll();
          const div = document.createElement("div");
          div.className = "evt evt-error";
          div.textContent = p.message || "agent error";
          ctx.append(div, true);
          return true;
        }
        default:
          return true; // unknown event types stay hidden in jam mode
      }
    },
  };
}
