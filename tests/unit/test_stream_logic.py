"""Unit tests for the stream/backfill logic (slice 1C).

Verifies the durability contract with exact seq-value asserts: a subscriber
connecting with since_seq=N receives exactly N+1..latest then live events, in
one strictly-increasing stream with no gaps or duplicates — including under a
forced race between backfill read and live broadcast, simulated disconnect/
reconnect, and concurrent producers.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import pytest

from ontheroad import db
from ontheroad.db.migrate import apply_migrations
from ontheroad.sessions.manager import SessionManager
from ontheroad.sessions.store import EventStore


class NullAdapter:
    """Minimal AgentAdapter surface; emits nothing (tests drive the store)."""

    name = "echo"

    async def start(self, workdir: Path, resume_ref: str | None) -> str:
        return "ref"

    async def send_user_message(self, text: str) -> None:
        pass

    async def events(self):
        await asyncio.Event().wait()
        yield  # pragma: no cover

    async def respond_permission(self, request_id: str, option_id: str) -> None:
        pass

    async def cancel(self) -> None:
        pass

    async def stop(self) -> None:
        pass


@dataclass
class Ctx:
    conn: object
    store: EventStore
    manager: SessionManager
    sid: str


@pytest.fixture
async def ctx(tmp_path):
    db_path = str(tmp_path / "stream.db")
    await apply_migrations(db_path)
    conn = await db.connect(db_path)
    store = EventStore(conn)
    manager = SessionManager(store, adapter_factory=lambda name: NullAdapter())
    await manager.startup()
    session = await manager.create_session("echo", workdir="/tmp/w")
    yield Ctx(conn, store, manager, session["id"])
    await manager.shutdown()
    await conn.close()


async def publish(ctx: Ctx, n: int, start_label: int = 0) -> None:
    for i in range(n):
        await ctx.manager._persist_and_broadcast(
            ctx.sid, "agent_text", {"i": start_label + i}
        )


async def collect(ctx: Ctx, since_seq: int, until_seq: int) -> list[dict]:
    """Consume ctx.manager.stream until seq until_seq is received."""
    out: list[dict] = []
    async def run():
        async for ev in ctx.manager.stream(ctx.sid, since_seq=since_seq):
            out.append(ev)
            if ev["seq"] >= until_seq:
                return
    await asyncio.wait_for(run(), timeout=5)
    return out


async def test_backfill_exact_range_then_live(ctx: Ctx):
    # session creation persisted seq 1 (status); add 9 more → last_seq 10
    await publish(ctx, 9)
    assert await ctx.store.last_seq(ctx.sid) == 10

    consumer = asyncio.create_task(collect(ctx, since_seq=4, until_seq=12))
    await asyncio.sleep(0.05)  # let backfill drain
    await publish(ctx, 2, start_label=100)  # live events → seq 11, 12
    events = await consumer

    assert [e["seq"] for e in events] == [5, 6, 7, 8, 9, 10, 11, 12]
    assert events[-2]["payload"] == {"i": 100}
    assert events[-1]["payload"] == {"i": 101}


async def test_since_seq_zero_returns_full_transcript(ctx: Ctx):
    await publish(ctx, 5)
    events = await collect(ctx, since_seq=0, until_seq=6)
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]
    assert events[0]["type"] == "status"


async def test_disconnect_reconnect_exact_missed_range(ctx: Ctx):
    # Long fixture: 200 events beyond the status event → last_seq 201
    await publish(ctx, 200)

    # Client 1 consumes through seq K=87 then "disconnects" (generator dropped)
    first = await collect(ctx, since_seq=0, until_seq=87)
    assert first[-1]["seq"] == 87

    # While disconnected, the agent keeps producing: seqs 202..251
    await publish(ctx, 50, start_label=1000)

    # Reconnect with since_seq = highest rendered → exactly 88..251
    second = await collect(ctx, since_seq=87, until_seq=251)
    assert [e["seq"] for e in second] == list(range(88, 252))
    # value-assert boundaries of the offline-produced tail
    assert second[-50]["payload"] == {"i": 1000}
    assert second[-1]["payload"] == {"i": 1049}


async def test_race_between_backfill_and_live_broadcast_no_dupes(ctx: Ctx, monkeypatch):
    """Force the worst-case race: an event is appended+broadcast AFTER the
    subscriber queue registers but BEFORE the backfill read runs, so it lands
    in BOTH the backfill result and the live queue. The stream must yield it
    exactly once."""
    await publish(ctx, 4)  # last_seq = 5
    real_events_since = ctx.store.events_since
    fired = False

    async def racing_events_since(session_id, since_seq=0, **kw):
        nonlocal fired
        if not fired:
            fired = True
            # subscriber is already registered; this broadcast goes to its
            # queue AND will be included in the read below (seq 6)
            await ctx.manager._persist_and_broadcast(
                ctx.sid, "agent_text", {"i": "raced"}
            )
        return await real_events_since(session_id, since_seq, **kw)

    monkeypatch.setattr(ctx.store, "events_since", racing_events_since)

    consumer = asyncio.create_task(collect(ctx, since_seq=2, until_seq=7))
    await asyncio.sleep(0.05)
    monkeypatch.setattr(ctx.store, "events_since", real_events_since)
    await ctx.manager._persist_and_broadcast(ctx.sid, "agent_text", {"i": "after"})
    events = await consumer

    seqs = [e["seq"] for e in events]
    assert seqs == [3, 4, 5, 6, 7]  # 6 appears exactly once, no gap before 7
    assert events[3]["payload"] == {"i": "raced"}
    assert events[4]["payload"] == {"i": "after"}


async def test_concurrent_producers_and_consumer_one_ordered_stream(ctx: Ctx):
    consumer = asyncio.create_task(collect(ctx, since_seq=0, until_seq=201))
    await asyncio.sleep(0.01)

    async def producer(tag):
        for i in range(50):
            await ctx.manager._persist_and_broadcast(
                ctx.sid, "agent_text", {"p": tag, "i": i}
            )

    await asyncio.gather(*(producer(t) for t in "abcd"))
    events = await consumer
    # status event (1) + 200 produced = exactly 1..201, strictly increasing
    assert [e["seq"] for e in events] == list(range(1, 202))


async def test_two_subscribers_each_receive_every_event_once(ctx: Ctx):
    c1 = asyncio.create_task(collect(ctx, since_seq=0, until_seq=21))
    c2 = asyncio.create_task(collect(ctx, since_seq=0, until_seq=21))
    await asyncio.sleep(0.01)
    await publish(ctx, 20)
    e1, e2 = await c1, await c2
    assert [e["seq"] for e in e1] == list(range(1, 22))
    assert [e["seq"] for e in e2] == list(range(1, 22))


async def test_unsubscribe_on_generator_exit(ctx: Ctx):
    gen = ctx.manager.stream(ctx.sid, since_seq=0)
    ev = await gen.__anext__()
    assert ev["seq"] == 1
    assert len(ctx.manager._subscribers.get(ctx.sid, set())) == 1
    await gen.aclose()
    assert ctx.manager._subscribers.get(ctx.sid) is None
