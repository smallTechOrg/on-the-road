"""SQLite connection management (WAL). Skeleton — Phase 1 adds write locks."""

from __future__ import annotations

from pathlib import Path

import aiosqlite


async def connect(db_path: str) -> aiosqlite.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = await aiosqlite.connect(path)
    await conn.execute("PRAGMA journal_mode=WAL")
    await conn.execute("PRAGMA foreign_keys=ON")
    conn.row_factory = aiosqlite.Row
    return conn
