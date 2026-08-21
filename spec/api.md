# API Contract — On The Road

Single origin, port 8100. All `/api/*` and `/preview/*` require `Authorization: Bearer <ONTHEROAD_TOKEN>`; WebSockets pass `?token=<...>` on upgrade (browsers can't set headers on WS). Errors use the shape in `spec/architecture.md` → Conventions. `/healthz` and static files are unauthenticated (the PWA shell contains no data).

## Phase 1

| Method & Path | Purpose | Request | Response |
|---|---|---|---|
| GET `/healthz` | liveness | — | `{"status":"ok","version":"..."}` |
| GET `/api/me` | token check for the login screen | — | `{"ok":true}` (401 otherwise) |
| GET `/api/sessions` | home-screen list | — | `{"sessions":[{id,title,adapter,status,workdir,last_seq,updated_at}]}` sorted by updated_at desc |
| POST `/api/sessions` | create session | `{"adapter":"hermes"\|"echo","title"?,"workdir"?}` (workdir default `ONTHEROAD_DEFAULT_WORKDIR`, else server cwd) | 201 `{session}` — spawns adapter; `adapter_unavailable` if spawn/handshake fails |
| GET `/api/sessions/{id}` | session detail | — | `{session}` |
| POST `/api/sessions/{id}/attach` | (re)start adapter for an idle/error session (uses `adapter_session_ref` → ACP `session/load`) | — | `{session}` |
| POST `/api/sessions/{id}/message` | send user turn | `{"text":"..."}` | 202 `{"seq":N}` (the persisted user_message seq) |
| POST `/api/sessions/{id}/cancel` | interrupt current turn | — | 202 `{}` |
| GET `/api/sessions/{id}/events?since_seq=N&limit=M&type=...` | transcript/audit range query (paged, ordered by seq) | — | `{"events":[{seq,type,payload,ts}],"last_seq":L}` |
| WS `/api/sessions/{id}/stream?token=...&since_seq=N` | live stream | client→server frames: `{"type":"user_message","text"}` \| `{"type":"ping"}` | server→client: backfill events N+1.. then live, each `{"seq","type","payload","ts"}`; `{"type":"pong"}`; fatal `{"type":"error",...}` then close |

Stream contract (the durability core): the server snapshots `last_seq`, sends persisted N+1..last_seq, then atomically joins the broadcast set before releasing new appends to it — the client sees one strictly-increasing seq stream. Clients render idempotently by seq (dedupe on the off chance of overlap) and reconnect with `since_seq = highest rendered`.

## Phase 2

| Method & Path | Purpose | Response |
|---|---|---|
| GET `/api/usage/daily?days=30` | daily token totals across sessions | `{"days":[{day,input_tokens,output_tokens,total_tokens,cost_usd}] ,"today_total":T}` |
| POST `/api/sessions/{id}/approvals/{request_id}` | answer a permission_request | body `{"option_id":"..."}` → 202 `{}`; persists `permission_response`, forwards via adapter |
| GET/… `/preview/{id}/{port}/{path...}` | streaming reverse proxy to `127.0.0.1:{port}` (all methods, headers/body streamed; `X-Forwarded-Prefix` set) | proxied response; 502 `adapter_unavailable` if the port refuses |

## Phase 3

| Method & Path | Purpose |
|---|---|
| POST `/api/push/subscribe` / DELETE same | store/remove Web Push subscription; body = PushSubscription JSON |
| GET `/api/push/vapid` | public VAPID key for the client |
| WS `/api/sessions/{id}/term?token=...` | PTY in session workdir; raw bytes both ways; resize frame `{"type":"resize","cols","rows"}` |
| GET `/api/sessions/{id}/diff` | `{"branch","files":[{path,status}],"patch":"<unified diff>"}` from the session workdir git repo |
| GET `/api/sessions/{id}/file?path=...` | `{"path","content","truncated"}` (read-only, workdir-jailed, 512 KB cap) |
| POST `/api/sessions/{id}/screenshot` | body `{"port":P,"path":"/"}` → `image/png` of the proxied page (server-side headless Chromium) |
