-- 0002_usage.sql — Phase 2 slice 2B: usage rollup + 5xx audit capture.

-- Materialized daily rollup of events(type='usage'); recomputable from events
-- (events are truth — see ontheroad.usage.rollup.recompute).
CREATE TABLE IF NOT EXISTS usage_daily (
    day           TEXT PRIMARY KEY,          -- YYYY-MM-DD (UTC)
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    total_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL                       -- NULL when the adapter reports no cost
);

-- Audit of every 5xx response / unhandled exception, recorded by
-- RequestLoggingMiddleware (async, best-effort — never blocks the request).
CREATE TABLE IF NOT EXISTS request_errors (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,                -- ISO-8601 UTC
    method     TEXT NOT NULL,
    path       TEXT NOT NULL,
    status     INTEGER NOT NULL,
    error_code TEXT,                         -- machine code from the error body, if any
    detail     TEXT                          -- truncated body / exception repr
);

CREATE INDEX IF NOT EXISTS idx_request_errors_ts ON request_errors (ts);
