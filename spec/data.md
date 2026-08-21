# Data Model — On The Road

SQLite (WAL), single file at `ONTHEROAD_DB_PATH` (default `./data/ontheroad.db`; `/data/ontheroad.db` in Docker). Migrations in `src/ontheroad/db/migrations/`.

## Entities

### sessions
| Field | Type | Notes |
|---|---|---|
| id | TEXT PK | uuid4 hex, server-generated |
| title | TEXT | user-supplied or default "Session <short-id>" |
| adapter | TEXT | registry key: `hermes`, `echo` |
| adapter_session_ref | TEXT NULL | agent-side id (ACP sessionId) for `session/load` resume |
| workdir | TEXT | absolute path on the VM |
| status | TEXT | `starting` \| `running` \| `waiting_input` \| `blocked` \| `idle` \| `error` |
| owner | TEXT | constant `owner` in V1 (multi-user readiness) |
| vm_id | TEXT | constant `local` in V1 (multi-VM readiness) |
| created_at / updated_at | TEXT | ISO-8601 UTC |
| last_seq | INTEGER | cached max seq (authoritative value derivable from events) |

Lifecycle: `starting` → `running` ⇄ `waiting_input`/`blocked` → `idle` (turn done or server restart) → reattach → `running`; `error` on adapter failure (reattachable). Sessions are never auto-deleted; delete is explicit (V1: no delete endpoint — out of scope, transcripts are sacred).

### events (append-only; the source of truth)
| Field | Type | Notes |
|---|---|---|
| session_id | TEXT FK | |
| seq | INTEGER | per-session monotonic from 1; PK is (session_id, seq) |
| type | TEXT | `user_message`, `agent_text`, `agent_thought`, `tool_start`, `tool_update`, `tool_end`, `usage`, `git_status`, `permission_request`, `permission_response`, `status`, `turn_end`, `error` |
| payload | TEXT | JSON; shape per type (e.g. tool_*: `{tool_call_id, title, kind, status, content}`; usage: `{input_tokens, output_tokens, total_tokens, cost_usd?}`; permission_request: `{request_id, options[]}`) |
| ts | TEXT | ISO-8601 UTC, server clock at persist time |

Inserted only via `store.append()` under the per-session write lock (this is what makes seq gap/dupe-free). Doubles as the queryable audit log (commands, tool calls, costs) via filtered range queries.

### usage_daily (Phase 2 — materialized rollup)
| Field | Type | Notes |
|---|---|---|
| day | TEXT | `YYYY-MM-DD` UTC; PK (day) |
| input_tokens / output_tokens / total_tokens | INTEGER | summed across all sessions from `usage` events |
| cost_usd | REAL NULL | when adapter reports cost |

Updated transactionally with each `usage` event append; recomputable from events.

### push_subscriptions (Phase 3)
| Field | Type |
|---|---|
| endpoint | TEXT PK |
| keys_json | TEXT |
| created_at | TEXT |

### schema_migrations
`version INTEGER PK, applied_at TEXT` — owned by the migration runner.

## Relationships

- sessions 1—N events (cascade: never; events outlive nothing — sessions aren't deleted in V1).
- usage_daily derives from events(type='usage'); push_subscriptions standalone.

## Invariants

- For every session, `SELECT seq FROM events` is exactly 1..last_seq with no holes (asserted by an integration test on a ≥200-event fixture).
- An event is persisted before any client receives it.
- `payload` is always valid JSON; clients treat unknown `type` values as debug-only raw entries (forward compatibility for future adapters).
