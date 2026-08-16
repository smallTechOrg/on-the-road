# Capability: Notifications

## What It Does
Sends a Web Push notification to the phone when a session becomes blocked/needs input or finishes a turn while the app is hidden, deep-linking back to the session.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| PushSubscription JSON | JSON | browser Push API via bell opt-in | yes (once) |
| session status transitions | events | SessionManager | continuous |
| VAPID key pair | env (`ONTHEROAD_VAPID_PUBLIC/PRIVATE`) | .env | yes (Phase 3) |

## Outputs
| Output | Type | Destination |
|---|---|---|
| push message {title, body, session_id} | Web Push | phone OS notification |
| stored subscriptions | SQLite | push_subscriptions table |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| Browser push service (endpoint URL) | pywebpush send | 404/410 → delete subscription; other errors logged, never crash the event path |

## Business Rules
- Triggers: status → `blocked` or `waiting_input`; `turn_end` (agent done). Debounce: max one push per session per 30 s.
- Push contains no transcript content beyond a one-line title (privacy on lock screen). Tapping opens `/#/session/{id}`.
- Fully opt-in; the bell shows enabled/disabled state; unsubscribe supported.

## Success Criteria
- [ ] Integration: stored subscription + synthetic `blocked` event → exactly one push payload generated with the correct session_id (send function called with asserted payload against a stub endpoint; real browser delivery verified in the human test).
- [ ] Expired subscription (410) is pruned from the table.
- [ ] Human test: phone locked, Hermes finishes → notification arrives and deep-links to the session.
