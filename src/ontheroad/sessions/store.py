"""Event/session persistence queries (spec/data.md).

`seq` is assigned ONLY inside :meth:`EventStore.append`, under a per-session
asyncio write lock — this is the single point that makes the per-session event
stream gap/dupe-free (invariant: seqs are exactly 1..last_seq).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

import aiosqlite

ACTIVE_STATUSES = ("starting", "running", "waiting_input", "blocked")


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


def _session_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "adapter": row["adapter"],
        "adapter_session_ref": row["adapter_session_ref"],
        "workdir": row["workdir"],
        "status": row["status"],
        "owner": row["owner"],
        "vm_id": row["vm_id"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_seq": row["last_seq"],
    }


def _event_row_to_dict(row: aiosqlite.Row) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "type": row["type"],
        "payload": json.loads(row["payload"]),
        "ts": row["ts"],
    }


class EventStore:
    """All reads/writes for sessions + events over one aiosqlite connection.

    aiosqlite serializes statements on its worker thread, but seq assignment
    needs read-modify-write atomicity per session, hence the per-session
    asyncio.Lock held across the whole append transaction.
    """

    def __init__(self, conn: aiosqlite.Connection) -> None:
        self._conn = conn
        self._locks: dict[str, asyncio.Lock] = {}

    def lock_for(self, session_id: str) -> asyncio.Lock:
        # setdefault is safe: no await between lookup and insert (single loop).
        return self._locks.setdefault(session_id, asyncio.Lock())

    # ---------------------------------------------------------------- sessions

    async def create_session(
        self,
        adapter: str,
        title: str | None = None,
        workdir: str = ".",
    ) -> dict[str, Any]:
        session_id = uuid.uuid4().hex
        now = utcnow()
        final_title = title or f"Session {session_id[:8]}"
        await self._conn.execute(
            """INSERT INTO sessions
               (id, title, adapter, adapter_session_ref, workdir, status,
                owner, vm_id, created_at, updated_at, last_seq)
               VALUES (?, ?, ?, NULL, ?, 'starting', 'owner', 'local', ?, ?, 0)""",
            (session_id, final_title, adapter, workdir, now, now),
        )
        await self._conn.commit()
        return (await self.get_session(session_id))  # type: ignore[return-value]

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        cur = await self._conn.execute(
            "SELECT * FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cur.fetchone()
        return _session_row_to_dict(row) if row else None

    async def list_sessions(self) -> list[dict[str, Any]]:
        cur = await self._conn.execute(
            "SELECT * FROM sessions ORDER BY updated_at DESC, id"
        )
        return [_session_row_to_dict(r) for r in await cur.fetchall()]

    async def count_active(self) -> int:
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        cur = await self._conn.execute(
            f"SELECT COUNT(*) AS n FROM sessions WHERE status IN ({marks})",
            ACTIVE_STATUSES,
        )
        row = await cur.fetchone()
        return int(row["n"])

    async def update_session(self, session_id: str, **fields: Any) -> None:
        """Update whitelisted mutable session fields; bumps updated_at."""
        allowed = {"status", "adapter_session_ref", "title"}
        bad = set(fields) - allowed
        if bad:
            raise ValueError(f"non-updatable session fields: {bad}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        await self._conn.execute(
            f"UPDATE sessions SET {sets}, updated_at = ? WHERE id = ?",
            (*fields.values(), utcnow(), session_id),
        )
        await self._conn.commit()

    async def mark_active_sessions_idle(self) -> int:
        """Server (re)start: any session left active lost its subprocess."""
        marks = ",".join("?" for _ in ACTIVE_STATUSES)
        cur = await self._conn.execute(
            f"UPDATE sessions SET status = 'idle', updated_at = ? "
            f"WHERE status IN ({marks})",
            (utcnow(), *ACTIVE_STATUSES),
        )
        await self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------ events

    async def append(
        self, session_id: str, type_: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Persist one event; assigns seq = last_seq + 1 under the session lock."""
        async with self.lock_for(session_id):
            cur = await self._conn.execute(
                "SELECT last_seq FROM sessions WHERE id = ?", (session_id,)
            )
            row = await cur.fetchone()
            if row is None:
                raise KeyError(session_id)
            seq = int(row["last_seq"]) + 1
            ts = utcnow()
            await self._conn.execute(
                "INSERT INTO events (session_id, seq, type, payload, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, seq, type_, json.dumps(payload), ts),
            )
            await self._conn.execute(
                "UPDATE sessions SET last_seq = ?, updated_at = ? WHERE id = ?",
                (seq, ts, session_id),
            )
            await self._conn.commit()
            return {"seq": seq, "type": type_, "payload": payload, "ts": ts}

    async def events_since(
        self,
        session_id: str,
        since_seq: int = 0,
        limit: int | None = None,
        type_: str | None = None,
    ) -> list[dict[str, Any]]:
        """Events with seq > since_seq, ordered by seq (optional type filter/page)."""
        sql = "SELECT seq, type, payload, ts FROM events WHERE session_id = ? AND seq > ?"
        params: list[Any] = [session_id, since_seq]
        if type_ is not None:
            sql += " AND type = ?"
            params.append(type_)
        sql += " ORDER BY seq"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        cur = await self._conn.execute(sql, params)
        return [_event_row_to_dict(r) for r in await cur.fetchall()]

    async def last_seq(self, session_id: str) -> int:
        cur = await self._conn.execute(
            "SELECT last_seq FROM sessions WHERE id = ?", (session_id,)
        )
        row = await cur.fetchone()
        return int(row["last_seq"]) if row else 0
