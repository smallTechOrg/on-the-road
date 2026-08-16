"""Slice 1A: DB primitives — WAL, per-session write lock, monotonic seq.

The store surface (append/range/list) belongs to slice 1C
(ontheroad.sessions.store); these tests prove the primitives it builds on:
the lock serializes writers so seq assignment is exactly 1..N with no
gaps/dupes under concurrent appends.
"""

import asyncio
import json
import uuid

import pytest

from ontheroad.db import Database, connect
from ontheroad.db.migrate import apply_migrations

pytestmark = pytest.mark.asyncio


async def _make_db(tmp_path) -> Database:
    db_path = str(tmp_path / "test.db")
    version = await apply_migrations(db_path)
    assert version >= 1
    return await Database(db_path).open()


async def _insert_session(db: Database, session_id: str) -> None:
    await db.conn.execute(
        "INSERT INTO sessions (id, title, adapter, workdir, status, created_at, updated_at)"
        " VALUES (?, 'T', 'echo', '/tmp', 'idle', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
        (session_id,),
    )
    await db.conn.commit()


async def _append(db: Database, session_id: str, text: str) -> int:
    """Append one event assigning seq = last+1 under the per-session lock."""
    async with db.session_lock(session_id):
        cur = await db.conn.execute(
            "SELECT last_seq FROM sessions WHERE id = ?", (session_id,)
        )
        (last_seq,) = await cur.fetchone()
        seq = last_seq + 1
        # Yield mid-critical-section: without the lock this interleaving
        # produces duplicate seqs and the PK insert fails / gaps appear.
        await asyncio.sleep(0)
        await db.conn.execute(
            "INSERT INTO events (session_id, seq, type, payload, ts)"
            " VALUES (?, ?, 'user_message', ?, '2026-01-01T00:00:00Z')",
            (session_id, seq, json.dumps({"text": text})),
        )
        await db.conn.execute(
            "UPDATE sessions SET last_seq = ? WHERE id = ?", (seq, session_id)
        )
        await db.conn.commit()
        return seq


async def test_connect_enables_wal(tmp_path):
    conn = await connect(str(tmp_path / "wal.db"))
    try:
        cur = await conn.execute("PRAGMA journal_mode")
        (mode,) = await cur.fetchone()
        assert mode.lower() == "wal"
        cur = await conn.execute("PRAGMA foreign_keys")
        (fk,) = await cur.fetchone()
        assert fk == 1
    finally:
        await conn.close()


async def test_session_lock_is_stable_per_session(tmp_path):
    db = await _make_db(tmp_path)
    try:
        a1 = db.session_lock("a")
        a2 = db.session_lock("a")
        b = db.session_lock("b")
        assert a1 is a2
        assert a1 is not b
        assert isinstance(a1, asyncio.Lock)
    finally:
        await db.close()


async def test_concurrent_appends_yield_monotonic_gapless_seq(tmp_path):
    """50 concurrent appends → seqs are exactly 1..50, values asserted."""
    db = await _make_db(tmp_path)
    session_id = uuid.uuid4().hex
    try:
        await _insert_session(db, session_id)
        n = 50
        seqs = await asyncio.gather(
            *(_append(db, session_id, f"msg-{i}") for i in range(n))
        )
        assert sorted(seqs) == list(range(1, n + 1))
        assert len(set(seqs)) == n  # no dupes

        cur = await db.conn.execute(
            "SELECT seq FROM events WHERE session_id = ? ORDER BY seq", (session_id,)
        )
        stored = [row[0] for row in await cur.fetchall()]
        assert stored == list(range(1, n + 1))  # no holes, from 1

        cur = await db.conn.execute(
            "SELECT last_seq FROM sessions WHERE id = ?", (session_id,)
        )
        (last_seq,) = await cur.fetchone()
        assert last_seq == n
    finally:
        await db.close()


async def test_locks_isolate_sessions(tmp_path):
    """Appends to two sessions each get independent 1..N sequences."""
    db = await _make_db(tmp_path)
    s1, s2 = uuid.uuid4().hex, uuid.uuid4().hex
    try:
        await _insert_session(db, s1)
        await _insert_session(db, s2)
        await asyncio.gather(
            *(_append(db, s1, f"a{i}") for i in range(10)),
            *(_append(db, s2, f"b{i}") for i in range(7)),
        )
        for sid, n in ((s1, 10), (s2, 7)):
            cur = await db.conn.execute(
                "SELECT seq FROM events WHERE session_id = ? ORDER BY seq", (sid,)
            )
            assert [r[0] for r in await cur.fetchall()] == list(range(1, n + 1))
    finally:
        await db.close()
