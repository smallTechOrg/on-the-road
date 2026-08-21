# On The Road — Roadmap

## What

A mobile-first PWA that lets a programmer steer coding agents running in a remote sandbox VM from a phone: attach to running agent sessions, chat with them, watch streamed output live, and never lose a transcript — even across disconnects, tab kills, and server restarts.

## Who

Single user for V1 (the owner-programmer), authenticated with one strong bearer token. Architected so multiple users/VMs can attach later. Sessions are short bursts and long jams; one long-lived session per project is common; V1 targets one VM with ~1–5 concurrent sessions.

## Success Criteria

- [ ] On a phone browser: authenticate with token → see session list → create or attach a session → send a message → watch agent output stream live.
- [ ] Kill the tab mid-stream, reopen: transcript is intact and complete — no gaps, no duplicates (verified by monotonic per-session sequence numbers).
- [ ] Agent keeps working while the phone is disconnected; output produced while offline appears on reconnect.
- [ ] Server restart never loses a transcript (all events persisted server-side before delivery).
- [ ] Hermes runs headlessly behind a clean `AgentAdapter` interface; a second adapter (echo/dev) proves the interface is agent-agnostic.
- [ ] Two render modes work: "jam" (condensed, chat prominent) and "debug" (full raw tool-call stream).
- [ ] Running daily token total across sessions is visible and queryable.
- [ ] Agent dev servers reachable from the phone via reverse proxy at a stable URL and via direct port links.
- [ ] Agent git activity (branch/commit/push/PR) is surfaced with PR links; user approves in chat.
- [ ] The whole system runs in one Docker container via docker-compose AND without Docker via a plain uvicorn command.

## Out of Scope (V1)

- Multi-user accounts, OAuth, RBAC (single bearer token only; multi-user is an architecture consideration, not a feature).
- Multiple VMs (interface anticipates it; only one VM wired).
- AI inside the product (no summarizer/concierge; the product needs no LLM key of its own — see `spec/agent.md`).
- High availability, horizontal scaling, managed DB (SQLite + one process is the design point).
- Editing files from the phone (viewers only, and those are late-phase).
- App-store native apps (PWA only).

---

## Phases of Development

Fixed dev port: **8100**. Documented run command (no Docker): `uv run uvicorn ontheroad.main:app --host 0.0.0.0 --port 8100`. Docker: `docker compose up --build`.

> **Assumed:** Phase 1 gates run against the **EchoAdapter** (a real subprocess speaking the same internal event protocol, clearly labelled "Echo (dev)" in the UI) because driving Hermes requires an LLM key in `~/.hermes/.env` and burns tokens per test run. The HermesACPAdapter is nonetheless **implemented and registered in Phase 1**; a separate opt-in live test (`ONTHEROAD_TEST_HERMES=1`) exercises it for the human gate. This follows the brief's explicit permission for this sequencing.

### Phase 0 — Scaffold

**Goal:** minimal runnable skeleton of the `## Layout` in `spec/architecture.md`.

- **Gate (all must pass, per `phases.md` scaffold gate):**
  1. `uv sync` installs cleanly (aiosqlite/uvicorn/fastapi in main deps).
  2. `uv run uvicorn ontheroad.main:app --host 0.0.0.0 --port 8100` boots from repo root with no error; `GET /healthz` returns `{"status":"ok"}`.
  3. `uv run pytest tests/unit -q` green (one smoke test).
  4. Migration runner wired: `uv run python -m ontheroad.db.migrate --verify` prints the current schema version.
  5. `.env.example` documents `ONTHEROAD_TOKEN`, `ONTHEROAD_DB_PATH`, `ONTHEROAD_PORT`, `ONTHEROAD_HERMES_PYTHON`, `ONTHEROAD_TEST_HERMES`. (No LLM key: the product uses none.)
  6. First commit pushed.
- **How the user tests it:** run the uvicorn command, open `http://localhost:8100/healthz`, see `{"status":"ok"}`.

### Phase 1 — Golden Slice: Auth → Sessions → Live Chat → Durable Reconnect

**Goal:** the full primary journey, first-time-right: open PWA on the phone → paste token → session list → create/attach session → send message → watch output stream live → kill tab → reopen → transcript intact with zero gaps/dupes.

Capabilities delivered (real): token-auth, session-management, agent-adapter (EchoAdapter live + HermesACPAdapter implemented), chat-streaming (debug render: markdown, code blocks, collapsible tool calls, status line with activity/elapsed/step counter), transcript-durability (SQLite event log + sequence-number backfill), structured logging. UI stubs (visually present, labelled "coming soon", non-functional): jam-mode toggle, preview button, git panel, notifications bell, daily-token counter.

**Independent slices** (disjoint file ownership; fan out concurrently):

| Slice | Owns (paths) | Depends on |
|---|---|---|
| 1A Core server: config, auth middleware, DB layer + migrations, event store with per-session monotonic `seq`, structured logging | `src/ontheroad/main.py`, `src/ontheroad/config.py`, `src/ontheroad/auth.py`, `src/ontheroad/db/`, `src/ontheroad/logging.py` | — |
| 1B Adapter layer: `AgentAdapter` ABC, internal `AgentEvent` types, `EchoAdapter` subprocess, `HermesACPAdapter` (ACP stdio JSON-RPC), adapter registry | `src/ontheroad/adapters/` | — (pure interfaces; integrates via 1C) |
| 1C Session orchestration + API: session manager (create/attach/list/lifecycle), REST endpoints, WebSocket stream endpoint with `since_seq` backfill | `src/ontheroad/sessions/`, `src/ontheroad/api/` | 1A (event store), 1B (`AgentAdapter` interface) — interface-level only; declared in `spec/api.md`/`spec/data.md` so all three build concurrently against the spec |
| 1D PWA frontend: static app shell, manifest, service worker (shell caching only), token screen, session list, chat view (debug render), reconnect logic, labelled stubs | `src/ontheroad/static/` (`index.html`, `app.js`, `chat.js`, `api.js`, `styles.css`, `manifest.webmanifest`, `sw.js`, `vendor/`) | none at build time (codes to `spec/api.md`) |
| 1E Tests: unit + integration + Playwright E2E smoke, reconnect/gap-free property test, opt-in live Hermes test | `tests/` | integrates last against 1A–1D |

- **Gate:** `uv run pytest tests/unit tests/integration -q && uv run python tests/e2e/smoke.py`
  - Integration tests boot the real server against real SQLite (production engine), drive the EchoAdapter subprocess end-to-end, and assert: streamed content matches sent message deterministically; a client that disconnects mid-stream and reconnects with `since_seq=N` receives exactly seqs N+1..latest (value-asserted, no gaps/dupes) from a fixture long enough (≥200 events) that a partial replay would fail; transcript survives a server-process restart; a structured log line exists for the run.
  - `tests/e2e/smoke.py` is a headless Playwright script against the live server: enters the token, creates a session, sends "hello on the road", asserts the echoed content renders in the transcript DOM, reloads the page, asserts the transcript persists.
- **How the user tests it:** start the server (uvicorn command above, or `docker compose up`), open `http://<host>:8100` on the phone, paste `ONTHEROAD_TOKEN`, create a session with the "Echo (dev)" adapter (labelled as a stub agent), send a message, watch it stream; kill the tab, reopen, see the full transcript. Optionally pick the "Hermes" adapter (requires Hermes installed with an LLM key in `~/.hermes/.env`) and ask it to run a real command. Jam toggle, preview, git, notifications, token counter are visible but labelled "coming soon".

### Phase 2 — Jam Mode, Usage, Preview, Git Surfacing

**Goal:** the daily-driver chat experience: readable jam mode, cost awareness, tap-through to the agent's dev server, and PR links in chat. Requires Hermes live (real LLM key in `~/.hermes/.env`).

Capabilities delivered: render-modes (jam view: milestones + condensed tool summaries, instant client-side toggle to debug — both derived from the same persisted event stream, no AI), usage-metering (per-event token/cost capture from ACP `usage_update`, daily total across sessions on home screen, queryable audit log endpoint), preview (reverse proxy `/preview/{session_id}/{port}/…` at a stable URL + direct port links), git-flow (adapter surfaces git/PR activity as `git_status` events; PR-ready links rendered as tappable chips; user approves via chat message and Hermes ACP permission requests are surfaced as approve/deny buttons).

**Independent slices:**

| Slice | Owns | Depends on |
|---|---|---|
| 2A Jam renderer + mode toggle + status-line polish | `src/ontheroad/static/jam.js`, edits to `chat.js`/`styles.css` | Phase 1 |
| 2B Usage metering: usage columns/rollup queries, `/api/usage/daily`, `/api/sessions/{id}/events` audit query, home-screen counter wiring | `src/ontheroad/usage/`, migration file, `static/usage.js` | Phase 1 |
| 2C Preview reverse proxy + port links UI | `src/ontheroad/preview/`, `static/preview.js` | Phase 1 |
| 2D Git surfacing + ACP permission approvals | `src/ontheroad/adapters/hermes_acp.py` (extend), `static/git.js`, `src/ontheroad/api/approvals.py` | Phase 1 |

- **Gate:** `uv run pytest tests/unit tests/integration -q && ONTHEROAD_TEST_HERMES=1 uv run pytest tests/live -q`
  - `tests/live` drives real Hermes over ACP (key from `~/.hermes/.env`): asserts a streamed assistant reply with non-empty content, at least one persisted `usage` event whose token count > 0, and that `/api/usage/daily` returns that exact summed value. Preview test spins a throwaway HTTP server on a session port and asserts proxied body content matches byte-for-byte.
- **How the user tests it:** attach to a real Hermes session; toggle jam/debug and see the same conversation condensed vs raw; check the home screen daily token total ticks up after a turn; ask Hermes to start a dev server and tap the Preview chip to load it through the proxy; ask Hermes to open a PR and tap the PR link; approve a permission request from the chat.

### Phase 3 — Notifications, Terminal, Diff Viewer, Screenshots

**Goal:** full V1: get pinged when the agent blocks or finishes, and inspect the VM from the phone.

Capabilities delivered: notifications (Web Push via service worker when a session becomes `blocked`/`done`), terminal-view (read-oriented xterm.js pane over a PTY WebSocket into the session workdir), diff/file viewer (server-side `git diff`/file read endpoints, mobile-friendly rendering), on-demand headless screenshot of a previewed port (Playwright on the server).

**Independent slices:** 3A push (`src/ontheroad/push/`, `sw.js` extend), 3B terminal (`src/ontheroad/term/`, `static/term.js`, vendored xterm.js), 3C diff/files (`src/ontheroad/files/`, `static/diff.js`), 3D screenshot (`src/ontheroad/preview/screenshot.py`). All depend only on Phase 1/2 surfaces; mutually disjoint.

- **Gate:** `uv run pytest tests/unit tests/integration -q && ONTHEROAD_TEST_HERMES=1 uv run pytest tests/live -q` (integration adds: push subscription stored and a notification payload generated on a synthetic `blocked` event; terminal round-trips `echo otr-ok` and asserts `otr-ok` in output; diff endpoint returns the exact hunk for a fixture repo change; screenshot endpoint returns a decodable PNG > 10 KB of a known page).
- **How the user tests it:** enable notifications, lock the phone, have Hermes finish a task → push arrives; open the terminal tab and run `ls`; view the diff of the agent's branch; tap "screenshot" on a preview and see the rendered page image.

---

Every capability maps to a phase: token-auth, session-management, agent-adapter, chat-streaming, transcript-durability → Phase 1; render-modes, usage-metering, preview, git-flow → Phase 2; notifications, terminal-view (incl. diff/screenshot) → Phase 3.
