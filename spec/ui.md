# UI — On The Road (mobile-first PWA)

Zero-build static app served single-origin at `/`. Installable PWA (manifest + service worker caching the shell only — never transcript data). Dark theme default. Every not-yet-functional control carries a visible "coming soon" chip (`.stub` class) so stubs are never mistaken for bugs.

## Screens

### 1. Token screen (first run / 401)
- One password-type input + "Connect". Validates via `GET /api/me`; on success stores the token in `localStorage` and proceeds. Error state: "Invalid token".

### 2. Home — session list
- App bar: "On The Road", daily token counter (Phase 1: stub chip "tokens — coming soon"; Phase 2: real `today_total`), notifications bell (stub until Phase 3).
- List of ALL sessions (running and idle), sorted by last activity: title, adapter badge (`Hermes` / `Echo (dev)` — Echo explicitly labelled "stub agent"), status pill (color-coded: running=green pulse, waiting_input/blocked=amber, idle=gray, error=red), last-activity time, last_seq count.
- FAB "+ New session" → sheet: adapter picker, optional title, optional workdir. Tap a session → chat view.
- Pull-to-refresh; list also refreshes on visibilitychange.

### 3. Chat view (the core screen)
- Header: session title, status line — current activity (latest tool/status event title), elapsed time in current turn, step counter (tool calls this turn). Back returns to Home.
- Mode toggle `Jam | Debug` (Phase 1: Debug real, Jam side labelled "coming soon"; Phase 2: both real, instant client-side switch, preference persisted per session in localStorage).
  - **Debug:** full raw event stream — every agent_text (markdown-rendered: headings, lists, fenced code blocks with horizontal scroll), agent_thought (dimmed), tool calls as collapsible rows (title + kind icon + status; expand for command/output payload), usage/status entries.
  - **Jam:** chat text prominent; consecutive tool events condensed to one summary row ("ran 6 tool calls — expand"); plan/milestone status events shown as timeline markers; thoughts hidden.
- Composer: multiline input, Send button, Stop button while a turn is running (calls `/cancel`).
- Permission requests render as an inline card with the option buttons (Phase 1: card shown with "auto-declined (safe default) — approvals coming soon" notice; Phase 2: buttons live).
- Chips row above composer: Preview (stub → Phase 2 opens `/preview/...` port picker), Git/PR (stub → Phase 2 panel with branch/status/PR links as tappable chips), Terminal, Diff (stubs → Phase 3 tabs).
- Connection indicator: subtle "live / reconnecting…" dot; reconnect is automatic with exponential backoff, resuming at `since_seq`, with no visible duplication or gap. Scroll pinned to bottom while streaming unless the user scrolled up ("jump to latest" pill appears).

### 4. Terminal / Diff tabs (Phase 3)
- Terminal: xterm.js full-height pane with an on-screen extra-keys row (Esc, Tab, Ctrl, arrows).
- Diff: file list with A/M/D badges; tap file → unified diff, word-wrapped, add/del coloring; file viewer for arbitrary paths.

## Interactions & states

- Offline / server unreachable: banner "reconnecting…"; composer disabled; nothing is lost — the agent continues server-side.
- Empty states: home with no sessions shows "No sessions yet — create one"; new session shows a hint ("Send a message to start the agent").
- Errors: adapter failures render as a red inline `error` event in the transcript, plus session status pill turning red — never a silent stall.
- Notifications (Phase 3): opt-in prompt from the bell; push on status → `blocked`/`waiting_input`/turn-complete-while-hidden; tapping the notification deep-links to the session.
