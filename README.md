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

## Tests

```sh
uv run pytest tests/unit -q
```

## Configuration

See `.env.example` for all variables: `ONTHEROAD_TOKEN`, `ONTHEROAD_DB_PATH`,
`ONTHEROAD_PORT`, `ONTHEROAD_HERMES_PYTHON`, `ONTHEROAD_TEST_HERMES`.
