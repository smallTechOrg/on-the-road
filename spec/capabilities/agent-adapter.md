# Capability: Agent Adapter (Hermes ACP + Echo)

## What It Does
Normalizes any coding agent into one `AgentAdapter` interface emitting typed `AgentEvent`s; V1 ships HermesACPAdapter (headless Hermes over Agent Client Protocol) and EchoAdapter (deterministic dev stub, selectable and labelled in the UI).

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| workdir, resume_ref | path, string? | SessionManager | yes / no |
| user message text | string | SessionManager | per turn |
| permission responses | option_id | approvals API | when requested |
| ONTHEROAD_HERMES_PYTHON | path | env | no (default `~/.hermes/hermes-agent/venv/bin/python`) |

## Outputs
| Output | Type | Destination |
|---|---|---|
| AgentEvent stream (agent_text, agent_thought, tool_*, usage, git_status, permission_request, status, turn_end, error) | dataclasses | SessionManager (persist-then-broadcast) |
| adapter_session_ref | string | sessions table |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| Hermes subprocess (`$ONTHEROAD_HERMES_PYTHON -m acp_adapter.entry`, cwd=workdir) | ACP JSON-RPC over stdio: initialize, session/new, session/load, session/prompt, session/cancel; notifications session/update; client method session/request_permission | error event + session `error`; stderr captured to structured log; crash mid-turn → turn aborted, transcript intact |
| Echo subprocess (`python -m ontheroad.adapters.echo_child`) | newline-JSON line protocol mirroring the event types | same handling |

Exact ACP message mapping is specified in `spec/architecture.md` → HermesACPAdapter (one fact, one place).

## Business Rules
- Adapters never assign seq numbers; the store does.
- Unknown ACP update kinds map to `status` events with raw payload (forward compatible).
- The product carries no LLM key; Hermes reads its own from `~/.hermes/.env`. Missing/invalid Hermes auth → actionable error event ("run `hermes acp --setup` on the VM").
- New agents plug in as one module + registry entry; nothing outside `adapters/` changes.
- Phase 1 auto-answers permission_requests with the safest/reject default and says so in the transcript; Phase 2 routes to the user.

## Success Criteria
- [ ] EchoAdapter: sent text streams back chunked with fake tool events, deterministically (integration-tested).
- [ ] HermesACPAdapter passes `hermes acp --check`-level import/handshake in an opt-in live test (`ONTHEROAD_TEST_HERMES=1`): real prompt → non-empty agent_text, ≥1 tool event when asked to run a command, turn_end with stopReason.
- [ ] Killing the adapter subprocess mid-turn yields a persisted `error` event and session status `error`, and the session can be re-attached.
- [ ] Both adapters run under the identical SessionManager code path (no isinstance branches outside the registry).
