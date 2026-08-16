# Capability: Terminal, Diff & Screenshot

## What It Does
Phone-side inspection of the VM: an interactive terminal in the session workdir, a mobile-friendly diff/file viewer for the agent's branch, and on-demand headless screenshots of previewed dev servers.

## Inputs
| Input | Type | Source | Required |
|---|---|---|---|
| WS `/api/sessions/{id}/term` + keystrokes/resize | bytes/JSON | xterm.js client | terminal |
| diff/file requests (path) | route/query | user | viewer |
| screenshot request {port, path} | JSON | user tap | screenshot |

## Outputs
| Output | Type | Destination |
|---|---|---|
| PTY byte stream | WS | xterm.js pane |
| `{branch, files[], patch}` and `{path, content}` | JSON | diff/file viewer |
| PNG image | image/png | phone |

## External Calls
| System | Operation | On Failure |
|---|---|---|
| PTY (`ptyprocess`, login shell, cwd=workdir) | spawn/read/write/TIOCSWINSZ | WS close 1011; pane shows "terminal exited" |
| git in workdir | `git diff` / `git status` (read-only) | error shape `bad_request` if not a repo |
| Playwright headless Chromium (server-side) | goto `http://127.0.0.1:{port}{path}`, screenshot | 502 with hint; 15 s timeout |

## Business Rules
- Terminal is the user's shell in the session workdir — separate from, and non-interfering with, the agent's own terminal sessions.
- File reads are jailed to the workdir (path traversal rejected), read-only, 512 KB cap with `truncated` flag.
- Screenshot renders through the same localhost port the proxy uses; no external egress.
- POSIX-only (VM is Linux; dev Mac fine).

## Success Criteria
- [ ] Terminal round-trip: type `echo otr-ok` → `otr-ok` appears in the PTY stream (integration + human test).
- [ ] Fixture repo with one modified line → diff endpoint returns a patch containing exactly that hunk; viewer renders A/M/D badges.
- [ ] Path traversal (`?path=../../etc/passwd`) → 400.
- [ ] Screenshot of a fixture page returns a decodable PNG > 10 KB whose pixel dimensions match the requested viewport.
