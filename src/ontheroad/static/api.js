// api.js — REST + WebSocket client for the On The Road control server.
// Codes exactly to spec/api.md Phase 1. No framework; ES module.

const TOKEN_KEY = "otr_token";

export function getToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}
export function setToken(token) {
  localStorage.setItem(TOKEN_KEY, token);
}
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
}

// Error thrown on non-2xx REST responses; carries the spec error shape.
export class ApiError extends Error {
  constructor(status, body) {
    const err = body && body.error ? body.error : {};
    super(err.message || `HTTP ${status}`);
    this.status = status;
    this.code = err.code || (status === 401 ? "unauthorized" : "internal");
    this.detail = err.detail || {};
  }
}

async function request(method, path, body) {
  let res;
  try {
    res = await fetch(path, {
      method,
      headers: {
        Authorization: `Bearer ${getToken()}`,
        ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
      },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
  } catch (e) {
    // Network failure (offline / server down)
    const err = new ApiError(0, { error: { code: "network", message: "Server unreachable" } });
    err.network = true;
    throw err;
  }
  let json = null;
  try { json = await res.json(); } catch (_) { /* empty body ok (202 {}) */ }
  if (!res.ok) throw new ApiError(res.status, json);
  return json;
}

// --- Phase 1 endpoints (spec/api.md) ---

export function checkToken() {
  return request("GET", "/api/me"); // {"ok":true} or 401
}

export async function listSessions() {
  const data = await request("GET", "/api/sessions");
  return data.sessions; // [{id,title,adapter,status,workdir,last_seq,updated_at}]
}

export function createSession({ adapter, title, workdir }) {
  const body = { adapter };
  if (title) body.title = title;
  if (workdir) body.workdir = workdir;
  return request("POST", "/api/sessions", body); // 201 {session}
}

export function getSession(id) {
  return request("GET", `/api/sessions/${id}`); // {session}
}

export function attachSession(id) {
  return request("POST", `/api/sessions/${id}/attach`); // {session}
}

export function sendMessage(id, text) {
  return request("POST", `/api/sessions/${id}/message`, { text }); // 202 {"seq":N}
}

export function cancelTurn(id) {
  return request("POST", `/api/sessions/${id}/cancel`); // 202 {}
}

export function getEvents(id, { sinceSeq = 0, limit, type } = {}) {
  const q = new URLSearchParams({ since_seq: String(sinceSeq) });
  if (limit) q.set("limit", String(limit));
  if (type) q.set("type", type);
  return request("GET", `/api/sessions/${id}/events?${q}`); // {"events":[...],"last_seq":L}
}

// WS URL with ?token= and ?since_seq= per spec (browsers can't set WS headers).
export function streamUrl(id, sinceSeq) {
  const proto = location.protocol === "https:" ? "wss:" : "ws:";
  const q = new URLSearchParams({ token: getToken(), since_seq: String(sinceSeq) });
  return `${proto}//${location.host}/api/sessions/${id}/stream?${q}`;
}
