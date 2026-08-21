# AI-Native Design — On The Road

**Conclusion: no AI capability needed in V1.** The product is a *control plane for* AI agents, not an AI product: all intelligence lives in the driven agent (Hermes, later Codex/Claude Code/aider), which brings its own model credentials. The product itself requires no LLM key, and Phase 1–3 contain no in-product model calls. This is a deliberate brief-level decision, re-examined below opportunity by opportunity.

## Opportunities considered (agentic-ai.md lens)

| Opportunity | Pattern it would use | Decision | Rationale (one line) |
|---|---|---|---|
| Jam-mode transcript summarizer (milestone/condensed view) | Summarization / map-reduce over event stream | **No — deterministic instead** | ACP already emits structured chunks (message vs thought vs tool_call with kind/status); rule-based condensation (collapse tool runs, show message text, plan updates as milestones) is deterministic, free, offline, and cache-friendly — an LLM summarizer is deferred, not needed, for readability. |
| Concierge / "ask about my sessions" chat | RAG over event log | **No — deferred** | Single user with ~5 sessions can scan a list; the queryable audit endpoint (`/api/sessions/{id}/events`, filterable) covers V1 needs without a model. |
| Notification triage ("is the agent truly blocked?") | Classifier step | **No** | ACP gives explicit signals (`session/request_permission`, `stopReason`, status updates); state-machine rules are exact where a classifier would guess. |
| Auto-reply / auto-approve permissions | Autonomous policy agent | **No** | Approvals are precisely the human-in-the-loop moments the product exists to surface to the phone; automating them defeats the purpose and adds risk. |
| Screenshot/preview description for small screens | Vision call | **No — deferred** | The screenshot itself is the deliverable; description adds a key requirement and cost for marginal value. |

## The agentic surface that DOES exist (and its design)

Although the product runs no model, it *hosts* agent loops, so the agent-integration architecture is designed with the agentic catalogue in mind:

- **Pattern:** external-agent orchestration via a normalized event protocol (adapter pattern over ACP), analogous to a tool-use loop where the "tool" is a whole agent. See `spec/architecture.md` → *AgentAdapter* for state (`AgentEvent` stream + persisted seq), nodes (send → stream → persist → fan-out), error handling (adapter `error` events, subprocess supervision, permission escalation to human), finalize (`turn_end` from ACP `stopReason`), and concurrency (one subprocess per session, ~5 parallel, per-session writer lock).
- **Human-in-the-loop:** ACP `session/request_permission` → persisted event → phone approve/deny (Phase 2) is the product's core HITL edge.
- **Durable state:** the append-only event log is the single state store; every projection (jam, debug, usage, git panel) derives from it — no divergent agent state.

## Revisit trigger

If a future phase adds any in-product model call (summarizer or concierge), this file must be rewritten first with the full composition (pattern citation, state, steps, error handler, assembly pseudocode) and the stack gains an LLM abstraction layer + prompts-as-files under `src/ontheroad/ai/` — none of which is scaffolded in V1 (no speculative surface).
