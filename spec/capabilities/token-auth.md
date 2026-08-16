# Capability: Token Auth

## What It Does
Gates every API and preview surface behind a single strong bearer token so the phone can safely reach the server over any network.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| ONTHEROAD_TOKEN | string ≥32 chars | server env (.env) | yes |
| Authorization header / ?token= WS param | string | client | yes (per request) |

## Outputs
| Output | Type | Destination |
|---|---|---|
| 401 error shape / WS close 4401 | JSON / close code | client |
| pass-through | — | downstream handlers |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| none | — | — |

## Business Rules
- Constant-time comparison; token never logged or echoed.
- Static shell and /healthz are unauthenticated; every /api/* and /preview/* route is not.
- Server refuses to start if ONTHEROAD_TOKEN is unset or <32 chars.
- Client stores token in localStorage; a 401 anywhere returns the UI to the token screen.

## Success Criteria
- [ ] Request without/with-wrong token → 401 with `{"error":{"code":"unauthorized",...}}`; WS upgrade closes 4401.
- [ ] Correct token passes on REST, WS, and preview routes.
- [ ] Server exits with a clear message when the token is missing/short.
