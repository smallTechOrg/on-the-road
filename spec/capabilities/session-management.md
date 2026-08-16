# Capability: Session Management

## What It Does
Create, list, attach to, and interrupt agent sessions on the VM — the home screen shows every session, running or idle, and drops the user back into any of them.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| adapter | enum (hermes, echo) | user (create sheet) | yes |
| title, workdir | strings | user | no (defaults) |
| session id | uuid | route param | yes (attach/detail) |

## Outputs
| Output | Type | Destination |
|---|---|---|
| session record (status, last_seq, …) | JSON | client + `sessions` table |
| spawned adapter subprocess | process | SessionManager |
| status events | events table | stream |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| Adapter subprocess | spawn / handshake / session-new / session-load | 502 `adapter_unavailable`; session → `error`; error event persisted |

## Business Rules
- One adapter subprocess per active session; ~1–5 concurrent (soft cap 5 → `session_conflict` beyond).
- Sessions are never deleted in V1; idle sessions stay listed indefinitely.
- Attach on an idle Hermes session resumes via stored `adapter_session_ref` (ACP session/load).
- Status transitions per lifecycle in `spec/data.md`; every transition persists a `status` event.
- `owner`/`vm_id` columns populated with constants for future multi-user/multi-VM.

## Success Criteria
- [ ] Create → 201, subprocess running, session listed with status `running` within 5 s.
- [ ] List returns all sessions sorted by updated_at desc with accurate status pills.
- [ ] Attach after server restart resumes the same transcript and (Hermes) the same agent-side session.
- [ ] Cancel interrupts a running turn; a `turn_end` (stopReason cancelled) event is persisted.
