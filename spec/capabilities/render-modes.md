# Capability: Render Modes (Jam/Debug)

## What It Does
Lets the user flip the same persisted event stream between "jam" (milestones + condensed tool summaries, chat text prominent) and "debug" (full raw stream) instantly, client-side, with no AI.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| persisted/live events | event frames | stream | yes |
| mode toggle | enum jam\|debug | user; persisted per-session in localStorage | yes |

## Outputs
| Output | Type | Destination |
|---|---|---|
| jam projection (messages, milestone markers, "ran N tool calls" groups) | DOM | user |
| debug projection (every event incl. thoughts, raw payloads) | DOM | user |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| none (pure client-side projection) | — | — |

## Business Rules
- Deterministic rules only: consecutive tool_* events collapse into one expandable group; `status`/plan events become timeline milestones; agent_thought hidden in jam; agent_text always shown in both.
- Toggling never refetches or mutates data; both views derive from the same in-memory event list.
- Mode preference is per session; default jam once Phase 2 lands (debug is the Phase-1 default).

## Success Criteria
- [ ] A turn with 6 tool calls renders 6 rows in debug and exactly 1 group row ("ran 6 tool calls") in jam; expanding reveals all 6.
- [ ] Toggle completes without a network request (asserted in E2E via request interception).
- [ ] Thoughts visible dimmed in debug, absent in jam.
