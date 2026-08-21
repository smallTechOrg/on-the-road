# Capability: Preview Proxy

## What It Does
Makes the agent's dev servers reachable from the phone: a streaming reverse proxy at a stable URL per session/port, plus direct port links; on-demand headless screenshots arrive in Phase 3 (see terminal-view.md).

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| session id, port, path | route params | user taps Preview chip / enters port | yes |
| any HTTP method/headers/body | HTTP | phone browser | yes |

## Outputs
| Output | Type | Destination |
|---|---|---|
| proxied response (streamed) | HTTP | phone browser |
| direct link `http://<vm-host>:<port>` | anchor | user (for private-net/Tailscale access) |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| `127.0.0.1:{port}` on the VM | streamed httpx request, all methods | 502 `adapter_unavailable` with hint "is the dev server running on port P?" |

## Business Rules
- URL shape `/preview/{session_id}/{port}/{path}` is stable across reconnects/restarts; bearer auth enforced (token via cookie set by the PWA so plain `<a>` navigation works). > **Assumed:** on first preview open the PWA sets an HttpOnly-equivalent cookie `otr_token` scoped to path `/preview` since header auth is impossible for full-page navigation.
- `X-Forwarded-Prefix`/Host headers set so well-behaved apps generate correct links; apps with absolute paths may still prefer the direct port link — both are offered.
- WebSocket upgrade pass-through for HMR is best-effort; failure degrades to plain HTTP. > **Assumed:** HMR WS proxying is nice-to-have, not gated.

## Success Criteria
- [ ] Integration: fixture HTTP server on a random port → proxied GET body matches origin byte-for-byte; POST body round-trips.
- [ ] Unreachable port → 502 with the documented error shape.
- [ ] Preview chip in the chat view opens the proxied URL for a user-entered port; direct-link anchor is also rendered.
