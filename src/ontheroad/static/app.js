// app.js — bootstrap, routing between token / home / chat screens.
// No framework; the only global is window.OTR (namespace convention).

import * as api from "/api.js";
import { ChatView } from "/chat.js";

const $ = (id) => document.getElementById(id);
const screens = ["screen-token", "screen-home", "screen-chat"];

const OTR = {
  api,
  chat: null,
  refreshTimer: null,
};
window.OTR = OTR;

function show(screenId) {
  screens.forEach((s) => { $(s).hidden = s !== screenId; });
}

// ---------- token screen ----------

function showTokenScreen() {
  if (OTR.chat) { OTR.chat.close(); }
  stopHomeRefresh();
  show("screen-token");
  $("token-error").hidden = true;
  $("token-input").value = "";
}

$("token-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const token = $("token-input").value.trim();
  if (!token) return;
  api.setToken(token);
  const btn = e.target.querySelector("button");
  btn.disabled = true;
  btn.textContent = "Connecting…";
  try {
    await api.checkToken(); // GET /api/me -> {"ok":true}
    $("token-error").hidden = true;
    await showHome();
  } catch (err) {
    api.clearToken();
    $("token-error").textContent = err.network ? "Server unreachable" : "Invalid token";
    $("token-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Connect";
  }
});

// Any 401 anywhere sends the user back to the token screen.
function handleUnauthorized() {
  api.clearToken();
  showTokenScreen();
}

// ---------- home: session list ----------

async function showHome() {
  show("screen-home");
  startHomeRefresh();
  await refreshSessions();
}

async function refreshSessions() {
  const list = $("session-list");
  try {
    const sessions = await api.listSessions();
    list.innerHTML = "";
    if (!sessions.length) {
      const p = document.createElement("p");
      p.className = "muted center";
      p.textContent = "No sessions yet — create one";
      list.appendChild(p);
      return;
    }
    sessions.forEach((s) => list.appendChild(sessionCard(s)));
  } catch (e) {
    if (e.status === 401) return handleUnauthorized();
    list.innerHTML = "";
    const p = document.createElement("p");
    p.className = "error-text center";
    p.textContent = e.network ? "Server unreachable — retrying…" : `Failed to load sessions: ${e.message}`;
    list.appendChild(p);
  }
}

function sessionCard(s) {
  const card = document.createElement("button");
  card.type = "button";
  card.className = "session-card";
  card.dataset.sessionId = s.id;

  const row1 = document.createElement("div");
  row1.className = "row1";
  const title = document.createElement("span");
  title.className = "title";
  title.textContent = s.title || `${s.adapter} session`;
  const pill = document.createElement("span");
  pill.className = `pill ${s.status}`;
  pill.textContent = s.status;
  row1.append(title, pill);

  const row2 = document.createElement("div");
  row2.className = "row2";
  const badge = document.createElement("span");
  badge.className = "badge";
  badge.textContent = s.adapter === "echo" ? "Echo (dev) — stub agent" : "Hermes";
  const when = document.createElement("span");
  when.textContent = relativeTime(s.updated_at);
  const seq = document.createElement("span");
  seq.textContent = `${s.last_seq} events`;
  row2.append(badge, when, seq);

  card.append(row1, row2);
  card.addEventListener("click", () => openSession(s));
  return card;
}

function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const secs = Math.max(0, Math.floor((Date.now() - then) / 1000));
  if (secs < 60) return "just now";
  if (secs < 3600) return `${Math.floor(secs / 60)}m ago`;
  if (secs < 86400) return `${Math.floor(secs / 3600)}h ago`;
  return `${Math.floor(secs / 86400)}d ago`;
}

function startHomeRefresh() {
  stopHomeRefresh();
  OTR.refreshTimer = setInterval(refreshSessions, 15000);
}
function stopHomeRefresh() {
  clearInterval(OTR.refreshTimer);
  OTR.refreshTimer = null;
}

// Refresh on tab return (spec: list refreshes on visibilitychange).
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && !$("screen-home").hidden) {
    refreshSessions();
  }
});

// Pull-to-refresh (simple touch-based: pull down at top of list).
(function pullToRefresh() {
  const list = $("session-list");
  let startY = null;
  list.addEventListener("touchstart", (e) => {
    startY = list.scrollTop === 0 ? e.touches[0].clientY : null;
  }, { passive: true });
  list.addEventListener("touchend", (e) => {
    if (startY !== null && e.changedTouches[0].clientY - startY > 80) refreshSessions();
    startY = null;
  }, { passive: true });
})();

// ---------- new-session sheet ----------

$("fab-new").addEventListener("click", () => {
  $("ns-error").hidden = true;
  $("new-session-sheet").hidden = false;
});
$("ns-cancel").addEventListener("click", () => { $("new-session-sheet").hidden = true; });
$("new-session-sheet").addEventListener("click", (e) => {
  if (e.target === $("new-session-sheet")) $("new-session-sheet").hidden = true;
});

$("new-session-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const btn = $("ns-create");
  btn.disabled = true;
  btn.textContent = "Creating…";
  try {
    const session = await api.createSession({
      adapter: $("ns-adapter").value,
      title: $("ns-title").value.trim(),
      workdir: $("ns-workdir").value.trim(),
    });
    $("new-session-sheet").hidden = true;
    $("ns-title").value = "";
    $("ns-workdir").value = "";
    openSession(session);
  } catch (err) {
    if (err.status === 401) return handleUnauthorized();
    $("ns-error").textContent =
      err.code === "adapter_unavailable"
        ? `Agent unavailable: ${err.message}`
        : `Create failed: ${err.message}`;
    $("ns-error").hidden = false;
  } finally {
    btn.disabled = false;
    btn.textContent = "Create";
  }
});

// ---------- chat ----------

function openSession(session) {
  stopHomeRefresh();
  show("screen-chat");
  OTR.currentSessionId = session.id;
  OTR.chat.open(session);
}

// Wire terminal/diff buttons here where session state is guaranteed
$("btn-terminal")?.addEventListener("click", () => {
  if (OTR.currentSessionId && OTR.term) OTR.term.openTerminal(OTR.currentSessionId);
});
$("btn-diff")?.addEventListener("click", () => {
  if (OTR.currentSessionId && OTR.diff) OTR.diff.openDiffView(OTR.currentSessionId);
});

function exitChat() {
  OTR.chat.close();
  OTR.currentSessionId = null;
  showHome();
}

// ---------- boot ----------

async function boot() {
  OTR.chat = new ChatView({ onExit: exitChat, onUnauthorized: handleUnauthorized });

  if ("serviceWorker" in navigator) {
    navigator.serviceWorker.register("/sw.js").catch(() => {});
  }

  if (!api.getToken()) return showTokenScreen();
  try {
    await api.checkToken();
    await showHome();
  } catch (e) {
    if (e.network) {
      // Offline with a stored token: show home shell with reconnect message.
      await showHome();
    } else {
      handleUnauthorized();
    }
  }
}

boot();
