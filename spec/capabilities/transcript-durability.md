# Capability: Transcript Durability & Reconnect Backfill

## What It Does
Guarantees no transcript is ever lost and reconnects are seamless: every event is persisted with a per-session monotonic sequence number before delivery, and clients resume with `since_seq` to receive exactly the missed range — no gaps, no duplicates.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| AgentEvents / user messages | typed events | SessionManager | continuous |
| since_seq | int | client (WS query / events endpoint) | on reconnect |

## Outputs
| Output | Type | Destination |
|---|---|---|
| append-only events rows (session_id, seq, type, payload, ts) | SQLite | events table |
| backfill range seqs N+1..last | WS frames / JSON | client |
| audit query results (filter by type, range, paging) | JSON | `/api/sessions/{id}/events` |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| SQLite (WAL) | INSERT under per-session write lock; range SELECT | append failure aborts broadcast and raises `internal` — never deliver unpersisted data |

## Business Rules
- seq assigned only inside `store.append()`; strictly 1..last_seq with no holes (invariant, tested).
- Agent keeps working while no client is connected; offline-produced events backfill on reconnect.
- WS join protocol: snapshot last_seq → send persisted backfill → atomically join broadcast before new appends release → one strictly-increasing stream.
- Client renders idempotently keyed by seq and reconnects from its highest rendered seq.
- Events double as the audit log (commands, tool calls, costs) — no separate log store.

## Success Criteria
- [ ] Integration: disconnect mid-stream at seq K on a ≥200-event run, reconnect with since_seq=K → received seqs are exactly K+1..last (values asserted, not counts alone).
- [ ] Server process killed and restarted → full transcript returned by the events endpoint; session reattachable.
- [ ] Gap/dupe invariant holds under concurrent append + connect (stress test with interleaved joins).
- [ ] E2E smoke: page reload mid-conversation shows the complete transcript.
