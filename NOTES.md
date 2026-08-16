# NOTES.md — harness-improvement journal

- 2026-08-16 18:49 — Run start (first invocation: spec + scaffold + Phase 1). Branch feature/on-the-road-20260816-1849-v0.1. Remote origin present (github.com/smallTechOrg/on-the-road).
- 2026-08-16 ~20:10 — Phase 1 complete. All 5 slices (1A-1E) VERIFIED first pass; no BLOCKED loops. Gate: 78 tests + Playwright smoke green. Friction log: (1) slice 1A had to guard router imports inside create_app since 1C built concurrently — worked as designed; (2) slice 1E needed 3 small glue fixes (main.py wiring, /api/me, adapter factory) — expected integration cost of parallel slices; (3) phase-gate auditor misread `git status` as being on main (we were on the feature branch) — harmless but note auditors should report branch name, not assume.
