# Capability: Git Flow Surfacing & Approvals

## What It Does
Surfaces the agent's git activity (branch, commits, push, PR) in chat with tappable PR links, and routes agent permission requests to approve/deny buttons on the phone — the agent does the git work; the user steers.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| git/PR-related tool events + agent text | AgentEvents | HermesACPAdapter | continuous |
| permission_request options | AgentEvent | ACP session/request_permission | when raised |
| approval choice | option_id | user tap → `/approvals/{request_id}` | when requested |

## Outputs
| Output | Type | Destination |
|---|---|---|
| git_status events (branch, dirty, last commit, pr_url?) | events table + stream | git panel chip |
| PR link chips (any https://github.com/... /pull/ URL detected in events) | DOM | user |
| permission_response event + adapter reply | ACP | Hermes (unblocks the agent) |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| GitHub | none directly — Hermes owns gh/git auth on the VM | agent-side errors appear as normal tool output |
| session workdir | `git status --porcelain=v2 --branch` on turn_end to emit git_status | omit event; log warning |

## Business Rules
- The product never runs write git operations; it only observes (read-only `git status`) and relays approvals. GitHub credentials live with Hermes/VM, not the app.
- PR-ready detection: URL pattern match in agent_text/tool_end payloads → chip rendered and copied into the latest git_status payload.
- Unanswered permission_request blocks the turn and flips session status to `blocked` (feeds Phase-3 notifications).
- Approvals are idempotent; answering an already-answered request returns 409 `session_conflict`.

## Success Criteria
- [ ] Live test: ask Hermes to create a branch and commit → a git_status event with the new branch name is persisted after turn_end.
- [ ] A PR URL in agent output renders as a tappable chip in both render modes (E2E DOM assertion on the exact URL).
- [ ] Tapping approve on a permission card unblocks the agent (live test: a command-permission prompt answered "allow" leads to tool_end success), and the permission_response event is persisted.
