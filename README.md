# On The Road

A mobile-first PWA to steer coding agents running on a remote sandbox VM from your
phone: attach to sessions, chat live, and never lose a transcript.

## Setup

```sh
uv sync
cp .env.example .env   # then set ONTHEROAD_TOKEN (>= 32 chars)
```

## Run (no Docker)

```sh
uv run uvicorn ontheroad.main:app --host 0.0.0.0 --port 8100
```

Open `http://localhost:8100/healthz` — you should see `{"status":"ok"}`.

## Run (Docker)

```sh
docker compose up --build
```

One service on port 8100; volumes `./data:/data` (SQLite) and `~/.hermes:/root/.hermes`
(Hermes config/keys/session DB).

## Database migrations

Migrations run automatically at server startup. To apply and verify manually:

```sh
uv run python -m ontheroad.db.migrate --verify
```

Prints the current schema version.

## Testing

Phase 1 gate (unit + integration + Playwright E2E smoke):

```sh
uv run pytest tests/unit tests/integration -q && uv run python tests/e2e/smoke.py
```

- `tests/unit` — fast, no server.
- `tests/integration` — boots the real server (uvicorn subprocess) on a temp
  SQLite file and drives the real EchoAdapter subprocess end-to-end: full
  journey, `since_seq` reconnect backfill (>= 200-event fixture, exact seq
  range asserted), transcript survival across a server-process kill/restart,
  structured JSON log assertions.
- `tests/e2e/smoke.py` — headless Playwright (first time: `uv run playwright
  install chromium`): starts its own server, enters the token, creates an
  "Echo (dev)" session, sends "hello on the road", asserts the echoed content
  renders in the transcript DOM, reloads, asserts the transcript persists.
  Exits nonzero on failure and always kills the server it started.
- `tests/live` — opt-in real Hermes over ACP (skipped by default; burns real
  LLM tokens): `ONTHEROAD_TEST_HERMES=1 uv run pytest tests/live -q`.

## Configuration

See `.env.example` for all variables: `ONTHEROAD_TOKEN`, `ONTHEROAD_DB_PATH`,
`ONTHEROAD_PORT`, `ONTHEROAD_HERMES_PYTHON`, `ONTHEROAD_TEST_HERMES`.
