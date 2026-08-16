-- 0001_init: sessions + events (per spec/data.md)

CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    adapter TEXT NOT NULL,
    adapter_session_ref TEXT,
    workdir TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'starting'
        CHECK (status IN ('starting', 'running', 'waiting_input', 'blocked', 'idle', 'error')),
    owner TEXT NOT NULL DEFAULT 'owner',
    vm_id TEXT NOT NULL DEFAULT 'local',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seq INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE events (
    session_id TEXT NOT NULL REFERENCES sessions(id),
    seq INTEGER NOT NULL,
    type TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts TEXT NOT NULL,
    PRIMARY KEY (session_id, seq)
);
