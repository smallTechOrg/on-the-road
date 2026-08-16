# Capability: Chat Streaming

## What It Does
Streams agent output to the phone live over a WebSocket — markdown, code blocks, collapsible tool calls — with a status line showing current activity, elapsed time, and step count.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| WS connection (?token, ?since_seq) | upgrade | client | yes |
| user_message frames | JSON | client | per turn |
| AgentEvents | persisted events | SessionManager broadcast | continuous |

## Outputs
| Output | Type | Destination |
|---|---|---|
| ordered event frames {seq,type,payload,ts} | JSON over WS | all attached clients |
| rendered transcript + status line | DOM | user |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| none beyond adapter (see agent-adapter.md) | — | WS error frame + close 1011 |

## Business Rules
- Persist-then-broadcast: no client ever sees an unpersisted event.
- Multiple simultaneous clients per session supported (phone + laptop).
- agent_text chunks for one message coalesce client-side into a single markdown bubble; all agent HTML sanitized (DOMPurify).
- Status line derives from the live stream: latest tool/status title, elapsed since the turn's user_message ts, count of tool_start events this turn.
- Ping/pong every 20 s; a dead socket triggers client auto-reconnect with backoff.

## Success Criteria
- [ ] E2E smoke: sent message's streamed reply appears incrementally in the DOM (content asserted, not status codes).
- [ ] Fenced code block renders as `<pre><code>` with horizontal scroll; tool call rows expand/collapse.
- [ ] Two connected clients both receive every event exactly once, in seq order.
- [ ] Status line shows a nonzero elapsed time and correct step count during a multi-tool turn (Echo fixture emits 3 tool events → counter reads 3).
