"""SQLite connection management: WAL mode + per-session write-lock primitives.

Per spec/architecture.md Layout, this package owns connection management (WAL,
per-session write lock); event/session queries (append, range since_seq, list)
live in `ontheroad.sessions.store` (slice 1C), which appends only while holding
`Database.session_lock(session_id)` — that single serialized writer per session
is what makes seq assignment gap/dupe-free.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite


async def connect(db_path: str) -> aiosqlite.Connection:
    """Open an aiosqlite connection with WAL + foreign keys, Row factory."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    await conn.execute("PRAGMA busy_timeout=5000")
    conn.row_factory = aiosqlite.Row
    return conn


class Database:
    """One shared connection + one asyncio write lock per session id.

    All writes for a given session MUST run inside
    `async with db.session_lock(session_id):` — seq numbers are assigned only
    under that lock (by the store's append), guaranteeing per-session
    monotonic 1..last_seq with no holes.
    """

    def __init__(self, db_path: str):
        self.db_path = db_path
        self.conn: aiosqlite.Connection | None = None
        self._locks: dict[str, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    async def open(self) -> "Database":
        if self.conn is None:
            self.conn = await connect(self.db_path)
        return self

    async def close(self) -> None:
        if self.conn is not None:
            await self.conn.close()
            self.conn = None

    def session_lock(self, session_id: str) -> asyncio.Lock:
        """Return the (lazily-created) write lock for a session.

        Lock creation is synchronous and atomic under the event loop — no
        await between check and insert — so concurrent callers always get
        the same Lock object for the same session id.
        """
        lock = self._locks.get(session_id)
        if lock is None:
            lock = self._locks.setdefault(session_id, asyncio.Lock())
        return lock

    async def __aenter__(self) -> "Database":
        return await self.open()

    async def __aexit__(self, *exc) -> None:
        await self.close()
