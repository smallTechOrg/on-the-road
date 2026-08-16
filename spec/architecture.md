# Architecture — On The Road

## System Overview

One self-contained async Python server ("control server") runs on the sandbox VM. It serves the static PWA, exposes a small REST + WebSocket API, persists every agent event to SQLite **before** fan-out, and spawns/supervises agent subprocesses through an `AgentAdapter` abstraction. The phone is a thin client: it renders persisted events and never holds authoritative state — reconnect is always "give me everything after seq N".

```
Phone PWA (static, zero-build)
   │  HTTPS/WSS + Bearer token
   ▼
FastAPI control server (uvicorn, port 8100)
   ├── /            static PWA (single origin)
   ├── /api/*       REST (sessions, usage, approvals, files)
   ├── /api/sessions/{id}/stream   WebSocket (live events + since_seq backfill)
   ├── /preview/{id}/{port}/*      reverse proxy to session dev servers
   ├── SessionManager ── AgentAdapter ── subprocess per session
   │                       ├── EchoAdapter   (dev stub, line protocol)
   │                       └── HermesACPAdapter (ACP JSON-RPC over stdio)
   └── SQLite (WAL) — sessions, events (monotonic seq), usage, push subs
```

### Key data flow (a user turn)

1. Phone POSTs message → server persists a `user_message` event (assigning `seq = last+1` under the per-session write lock) → forwards to the adapter.
2. Adapter subprocess streams protocol output; the adapter normalizes each chunk into an `AgentEvent` (`agent_text`, `agent_thought`, `tool_start`, `tool_update`, `tool_end`, `usage`, `git_status`, `permission_request`, `status`, `turn_end`, `error`).
3. SessionManager persists each event (durability first), then broadcasts to all connected WebSockets for that session.
4. A client connecting with `?since_seq=N` first receives persisted events N+1..latest, then joins live fan-out — a single ordered stream, so no gaps/dupes by construction.
5. Disconnected phones change nothing: the adapter keeps running; events keep persisting.

### Restart/durability model

- SQLite WAL mode, one process, one serialized writer per session. Events are the source of truth; transcript views (jam/debug) are pure client-side projections of the same event stream.
- On server restart, sessions are marked `idle` (subprocess died with the server unless externalized); transcripts fully intact; attaching resumes Hermes via ACP `session/load` with the stored Hermes-side session id. > **Assumed:** V1 accepts that an agent turn in flight during a *server* crash is aborted (transcript preserved, session resumable) — HA/process-separation is out of scope.

### Multi-user/multi-VM readiness (architecture only)

`sessions.owner` and `sessions.vm_id` columns exist from Phase 0 with constant values (`"owner"`, `"local"`); auth is a middleware boundary; adapters are registered by name. Adding users/VMs later touches auth + a VM-dispatch layer, not the data model.

## AgentAdapter — the core interface

```python
class AgentAdapter(ABC):
    name: str                      # "hermes", "echo"; registry key
    async def start(self, workdir: Path, resume_ref: str | None) -> str: ...
        # spawn subprocess, handshake; returns adapter-side session ref (persisted for resume)
    async def send_user_message(self, text: str) -> None: ...
    def events(self) -> AsyncIterator[AgentEvent]: ...   # normalized stream
    async def respond_permission(self, request_id: str, option_id: str) -> None: ...
    async def cancel(self) -> None: ...                  # interrupt current turn
    async def stop(self) -> None: ...                    # terminate subprocess
```

`AgentEvent` is a frozen dataclass: `type`, `payload: dict`, `ts`. The server assigns `seq` at persist time (adapters never number events). New agents (Codex, Claude Code, aider) = one new module implementing this ABC + a registry entry; nothing else changes.

### HermesACPAdapter — concrete headless protocol (investigated 2026-08-16 against `~/.hermes/hermes-agent`)

- **Spawn:** `<hermes_venv_python> -m acp_adapter.entry` with `cwd=<session workdir>` (`ONTHEROAD_HERMES_PYTHON` defaults to `~/.hermes/hermes-agent/venv/bin/python`, falling back to `.venv/bin/python`; equivalent to the `hermes acp` console script). stdout is reserved for ACP JSON-RPC; logs go to stderr (captured into our structured log). Hermes loads its own model credentials from `~/.hermes/.env` — the product itself needs no LLM key.
- **Transport:** Agent Client Protocol — JSON-RPC 2.0, newline-delimited over stdio (Hermes runs `acp.run_agent(agent, use_unstable_protocol=True)`; the Python `agent-client-protocol` package's stdio framing).
- **Handshake:** we send `initialize` (`{protocolVersion, clientCapabilities}`); response advertises `agentCapabilities` incl. session list/resume/fork. If `authMethods` demands `terminal-setup`, we surface a clear error event ("run `hermes acp --setup` on the VM") rather than proxying interactive setup.
- **Session start:** `session/new` → `{"sessionId": ...}` (persisted as `adapter_session_ref`). **Attach after restart:** `session/load` (falling back to `session/resume`) with the stored id — Hermes replays state from its own SessionDB.
- **User message:** `session/prompt` with `{"sessionId", "prompt": [{"type": "text", "text": ...}]}`. The call stays open for the whole turn; its response `{"stopReason": "end_turn" | "cancelled" | ...}` maps to our `turn_end`.
- **Streamed events:** `session/update` notifications; `update.sessionUpdate` discriminates: `agent_message_chunk` → `agent_text`; `agent_thought_chunk` → `agent_thought`; `tool_call` / `tool_call_update` (with status, kind, content) → `tool_start`/`tool_update`/`tool_end`; `plan` → `status`; `usage_update` (token `Usage`) → `usage`; `current_mode_update`/`session_info_update` → `status`.
- **Permissions:** Hermes calls the client method `session/request_permission`; we persist a `permission_request` event (options included), render approve/deny buttons, and reply via `respond_permission` (Phase 1 auto-selects the reject-safe default with a labelled notice; Phase 2 wires real buttons).
- **Cancel:** `session/cancel` notification.
- **Risk note:** the ACP surface is stable enough for Phase 1 implementation, but live-key testing is gated behind `ONTHEROAD_TEST_HERMES=1` (see roadmap assumption); the EchoAdapter (same ABC, trivial line protocol subprocess: replies by streaming the user text back in chunks with fake tool events) is the deterministic test double and a real selectable "Echo (dev)" agent in the UI.

### Hermes placement

> **Assumed:** Hermes runs **in the same container** as the server (Docker image installs hermes-agent into `/opt/hermes`, mounts `~/.hermes` as a volume for config/keys/session DB). One container keeps compose trivial and matches "server must be self-contained"; local no-Docker dev uses the already-installed `~/.hermes/hermes-agent`.

## Stack

> **Assumed** throughout — intake stated no stack preference; each choice traces to a requirement.

| Choice | What | Rationale |
|---|---|---|
| Language/runtime | Python 3.12 + `uv` | Hermes is Python; subprocess + ACP client integration is native; one-language repo. |
| Web framework | FastAPI + uvicorn | Async WebSockets + REST + static files in one process; the brief's "plain uvicorn command" requirement. |
| Realtime transport | WebSocket (single per attached session) | Bidirectional (user messages + approvals inbound, events outbound); `since_seq` backfill makes reconnect trivial vs SSE+POST split. |
| Persistence | SQLite (WAL) via `aiosqlite`, in-repo migration runner (`ontheroad/db/migrate.py`, numbered SQL files) | Brief names SQLite; single-user single-process; production engine == test engine; zero ops cost. |
| Agent protocol client | Hand-rolled ACP JSON-RPC client (asyncio streams, ~200 lines) | Only a client-side subset is needed; avoids depending on the fast-moving `agent-client-protocol` package server API; keeps the adapter agent-agnostic. |
| Frontend | Zero-build static PWA: vanilla ES modules, vendored `marked.min.js` (markdown) + `dompurify` (sanitize), CSS custom props | Served single-origin by FastAPI; no build toolchain per brief; phone-first CSS. |
| E2E | Playwright (headless Chromium) script | The primary journey is JS-driven; per phases.md the smoke must execute the real browser path and assert content. |
| Reverse proxy | In-app `httpx.AsyncClient` streaming proxy at `/preview/{id}/{port}/` | Stable URL per session/port with no extra infra (no nginx/traefik). |
| Auth | Single bearer token (`ONTHEROAD_TOKEN`, ≥32 chars) — `Authorization` header on REST, `?token=` on WS upgrade; constant-time compare | Brief mandates token auth even on Tailscale. |
| Observability | `structlog`-style JSON lines to stdout via stdlib `logging` + custom formatter: `ts, level, event, session_id, seq, method, path, status, latency_ms, input_summary, output_summary, error` | Phase-1 requirement; grep-able on the VM; no vendor. |
| Push (Phase 3) | `pywebpush` + VAPID keys | Standard PWA Web Push, no APNs/FCM accounts needed for browser push. |
| Deploy | One Docker image (server + PWA + SQLite volume + Hermes), docker-compose locally, same image on a GCP VM | Brief requirement; low cost = one small VM. |
| LLM provider | None in-product; Hermes brings its own via `~/.hermes/.env` | Brief: no AI in product V1 (`spec/agent.md`). |

## Layout

```
on-the-road/
├── pyproject.toml            # uv-managed; fastapi, uvicorn, aiosqlite, httpx, websockets
├── uv.lock
├── .env.example              # ONTHEROAD_TOKEN, ONTHEROAD_DB_PATH, ONTHEROAD_PORT,
│                             # ONTHEROAD_HERMES_PYTHON, ONTHEROAD_TEST_HERMES, (P3) VAPID keys
├── Dockerfile                # server + PWA + hermes-agent in /opt/hermes
├── docker-compose.yml        # one service; volumes: ./data:/data, ~/.hermes:/root/.hermes
├── README.md
├── spec/
├── src/ontheroad/
│   ├── main.py               # app factory, router mounting, static mount, lifespan
│   ├── config.py             # env-driven Settings (single source of config truth)
│   ├── auth.py               # bearer-token middleware + WS token check
│   ├── logging.py            # structured JSON logging setup + request middleware
│   ├── db/
│   │   ├── __init__.py       # connection management (WAL, per-session write lock)
│   │   ├── migrate.py        # runner: apply + --verify
│   │   └── migrations/       # 0001_init.sql, 0002_usage.sql, ...
│   ├── adapters/
│   │   ├── base.py           # AgentAdapter ABC, AgentEvent, registry
│   │   ├── echo.py           # EchoAdapter + echo_child.py subprocess script
│   │   └── hermes_acp.py     # HermesACPAdapter + minimal ACP JSON-RPC client
│   ├── sessions/
│   │   ├── manager.py        # SessionManager: lifecycle, persist-then-broadcast
│   │   └── store.py          # event/session queries (append, range since_seq, list)
│   ├── api/
│   │   ├── sessions.py       # REST: list/create/attach/message/cancel
│   │   ├── stream.py         # WebSocket endpoint (backfill + live)
│   │   ├── approvals.py      # (P2) permission responses
│   │   └── usage.py          # (P2) daily totals + event/audit queries
│   ├── usage/                # (P2) rollup logic
│   ├── preview/              # (P2) reverse proxy; (P3) screenshot.py
│   ├── push/                 # (P3) web push
│   ├── term/                 # (P3) PTY websocket
│   ├── files/                # (P3) diff/file endpoints
│   └── static/               # the PWA (zero-build)
│       ├── index.html  app.js  api.js  chat.js  styles.css
│       ├── jam.js usage.js preview.js git.js   # (P2)
│       ├── term.js diff.js                     # (P3)
│       ├── manifest.webmanifest  sw.js  icons/
│       └── vendor/           # marked.min.js, purify.min.js, (P3) xterm
└── tests/
    ├── unit/                 # adapters, store, auth, seq logic
    ├── integration/          # real server + real SQLite + EchoAdapter; restart & backfill tests
    ├── live/                 # ONTHEROAD_TEST_HERMES=1 — real Hermes over ACP
    └── e2e/smoke.py          # Playwright: token → session → send → stream → reload → intact
```

## Conventions

- **Naming:** modules/functions `snake_case`; classes `PascalCase`; constants `UPPER_SNAKE`; event types are lowercase snake strings matching the `AgentEvent.type` literals listed above; API JSON keys `snake_case`.
- **Error shape (all REST errors):** `{"error": {"code": "<machine_code>", "message": "<human>", "detail": {...}}}` with proper HTTP status; codes: `unauthorized`, `not_found`, `session_conflict`, `adapter_unavailable`, `bad_request`, `internal`. WebSocket fatal errors send a final `{"type":"error", ...}` event then close with code 1011 (or 4401 for auth).
- **IDs:** session ids are `uuid4` hex strings generated server-side; `seq` is a per-session `INTEGER` starting at 1, assigned only inside the store's append (single writer lock per session).
- **Logging fields:** every request/WS/adapter log line is one JSON object with at minimum `ts`, `level`, `event`; requests add `method, path, status, latency_ms`; session events add `session_id, seq, event_type, input_summary/output_summary` (summaries truncated to 200 chars). No print(), no secrets/token values in logs.
- **Async discipline:** all I/O async; adapter subprocess reads via `asyncio` streams; blocking work (screenshots) in `asyncio.to_thread`.
- **Tests:** pytest + pytest-asyncio; files `tests/<tier>/test_<unit>.py`; integration tests get a fresh temp SQLite file (same engine as prod) and run migrations via the real runner; no mocking of the DB or the adapter subprocess in integration; asserts check concrete values (exact seq ranges, exact proxied bytes, summed token counts), never just status codes.
- **Frontend:** ES modules, no framework, no globals except `window.OTR` namespace; all agent-origin HTML sanitized with DOMPurify before insertion; every stubbed control carries the `.stub` class and a visible "coming soon" label.
- **Migrations:** additive numbered SQL; never edit an applied migration; `migrate.py --verify` prints the applied version and is part of every gate.
