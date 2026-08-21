# Capability: Usage Metering

## What It Does
Captures token usage per agent turn from adapter `usage` events and shows a running daily token total across all sessions, plus a queryable audit trail of costs.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| usage events (input/output/total tokens, cost?) | AgentEvent | HermesACPAdapter (ACP usage_update) | per turn |
| days query param | int | client | no (default 30) |

## Outputs
| Output | Type | Destination |
|---|---|---|
| usage_daily rollup rows | SQLite | usage_daily table |
| `/api/usage/daily` totals + today_total | JSON | home-screen counter |
| cost/audit entries | events query | `/api/sessions/{id}/events?type=usage` |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| none (derives from the event stream) | — | rollup update failure logs + is recomputable from events |

## Business Rules
- Rollup updated in the same transaction as the usage-event append; always recomputable from events (events are truth).
- Days bucketed by UTC date. Adapters that report no usage (Echo) simply contribute zero.
- Counter on home screen refreshes with the session list.

## Success Criteria
- [ ] Live test: one real Hermes turn produces a usage event with total_tokens > 0, and `/api/usage/daily` today_total equals the sum of all persisted usage events for today (value equality, multi-session fixture so a partial sum fails).
- [ ] Deleting the rollup table row and recomputing from events yields the identical total.
- [ ] Home screen shows the total and it increases after a turn (E2E-asserted).
