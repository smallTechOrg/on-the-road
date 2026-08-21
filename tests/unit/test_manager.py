"""Unit tests for SessionManager + EventStore (slice 1C).

Uses a real SQLite file (production engine) via the real migration runner, and
an in-file FakeAdapter implementing the spec'd AgentAdapter surface — the real
adapters are slice 1B; integration happens in 1E.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from ontheroad import db
from ontheroad.db.migrate import apply_migrations
from ontheroad.sessions.manager import (
    AdapterUnavailable,
    SessionConflict,
    SessionManager,
    SessionNotFound,
)
from ontheroad.sessions.store import EventStore


@dataclass
class FakeEvent:
    type: str
    payload: dict
    ts: str = "2026-08-16T00:00:00+00:00"


class FakeAdapter:
    """Implements the AgentAdapter interface from spec/architecture.md."""

    name = "echo"

    def __init__(self) -> None:
        self.started_with: tuple[Path, str | None] | None = None
        self.messages: list[str] = []
        self.cancelled = False
        self.stopped = False
        self._queue: asyncio.Queue = asyncio.Queue()

    async def start(self, workdir: Path, resume_ref: str | None) -> str:
        self.started_with = (workdir, resume_ref)
        return resume_ref or "fake-ref-1"

    async def send_user_message(self, text: str) -> None:
        self.messages.append(text)

    async def events(self):
        while True:
            ev = await self._queue.get()
            if ev is None:
                return
            yield ev

    def emit(self, type_: str, payload: dict) -> None:
        self._queue.put_nowait(FakeEvent(type_, payload))

    def end_stream(self) -> None:
        self._queue.put_nowait(None)

    async def respond_permission(self, request_id: str, option_id: str) -> None:
        pass

    async def cancel(self) -> None:
        self.cancelled = True

    async def stop(self) -> None:
        self.stopped = True


class FailingAdapter(FakeAdapter):
    async def start(self, workdir: Path, resume_ref: str | None) -> str:
        raise RuntimeError("spawn failed: no such binary")


@dataclass
class Harness:
    db_path: str
    conn: Any
    store: EventStore
    manager: SessionManager
    adapters: list[FakeAdapter] = field(default_factory=list)


async def make_harness(db_path: str, adapter_cls=FakeAdapter) -> Harness:
    await apply_migrations(db_path)
    conn = await db.connect(db_path)
    store = EventStore(conn)
    adapters: list[FakeAdapter] = []

    def factory(name: str) -> FakeAdapter:
        a = adapter_cls()
        adapters.append(a)
        return a

    manager = SessionManager(store, adapter_factory=factory)
    await manager.startup()
    return Harness(db_path, conn, store, manager, adapters)


async def wait_for(predicate, timeout: float = 2.0) -> None:
    async def _poll():
        while not await predicate():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(_poll(), timeout)


@pytest.fixture
async def h(tmp_path):
    harness = await make_harness(str(tmp_path / "test.db"))
    yield harness
    await harness.manager.shutdown()
    await harness.conn.close()


async def test_create_session_spawns_adapter_and_persists_status(h: Harness):
    session = await h.manager.create_session("echo", title="my jam", workdir="/tmp/w")
    assert session["status"] == "running"
    assert session["title"] == "my jam"
    assert session["adapter"] == "echo"
    assert session["adapter_session_ref"] == "fake-ref-1"
    assert session["owner"] == "owner" and session["vm_id"] == "local"
    # adapter got the workdir and no resume ref on first start
    assert h.adapters[0].started_with == (Path("/tmp/w"), None)
    # a status event was persisted as seq 1
    events = await h.store.events_since(session["id"], 0)
    assert [(e["seq"], e["type"]) for e in events] == [(1, "status")]
    assert events[0]["payload"] == {"status": "running"}


async def test_default_title(h: Harness):
    session = await h.manager.create_session("echo", workdir="/tmp/w")
    assert session["title"] == f"Session {session['id'][:8]}"


async def test_send_message_persists_then_forwards(h: Harness):
    session = await h.manager.create_session("echo", workdir="/tmp/w")
    seq = await h.manager.send_message(session["id"], "hello on the road")
    assert seq == 2  # seq 1 was the running status event
    events = await h.store.events_since(session["id"], 0)
    assert events[1]["type"] == "user_message"
    assert events[1]["payload"] == {"text": "hello on the road"}
    assert h.adapters[0].messages == ["hello on the road"]


async def test_adapter_events_pumped_with_monotonic_seq(h: Harness):
    session = await h.manager.create_session("echo", workdir="/tmp/w")
    sid = session["id"]
    await h.manager.send_message(sid, "go")
    adapter = h.adapters[0]
    adapter.emit("agent_text", {"text": "chunk-1"})
    adapter.emit("tool_start", {"tool_call_id": "t1", "title": "ls", "kind": "execute"})
    adapter.emit("turn_end", {"stop_reason": "end_turn"})
    await wait_for(lambda: _seq_reached(h, sid, 6))

    events = await h.store.events_since(sid, 0)
    assert [e["seq"] for e in events] == [1, 2, 3, 4, 5, 6]  # no gaps, from 1
    assert [e["type"] for e in events] == [
        "status",        # running
        "user_message",
        "agent_text",
        "tool_start",
        "turn_end",
        "status",        # idle after turn_end
    ]
    assert events[5]["payload"] == {"status": "idle"}
    refreshed = await h.store.get_session(sid)
    assert refreshed["status"] == "idle"
    assert refreshed["last_seq"] == 6


async def _seq_reached(h: Harness, sid: str, n: int) -> bool:
    return await h.store.last_seq(sid) >= n


async def test_restart_marks_sessions_idle_and_attach_resumes_ref(tmp_path):
    db_path = str(tmp_path / "test.db")
    h1 = await make_harness(db_path)
    session = await h1.manager.create_session("echo", workdir="/tmp/w")
    sid = session["id"]
    await h1.manager.send_message(sid, "before restart")
    assert (await h1.store.get_session(sid))["status"] == "running"
    await h1.conn.close()  # simulate process death (no graceful shutdown)

    # "Restarted" server: fresh manager over the same DB file
    h2 = await make_harness(db_path)
    revived = await h2.store.get_session(sid)
    assert revived["status"] == "idle"  # survived restart as idle
    # transcript fully intact
    events = await h2.store.events_since(sid, 0)
    assert [e["seq"] for e in events] == [1, 2]
    assert events[1]["payload"] == {"text": "before restart"}

    attached = await h2.manager.attach(sid)
    assert attached["status"] == "running"
    # resume used the STORED adapter_session_ref
    assert h2.adapters[0].started_with == (Path("/tmp/w"), "fake-ref-1")
    await h2.manager.shutdown()
    await h2.conn.close()


async def test_adapter_start_failure_marks_error(tmp_path):
    h = await make_harness(str(tmp_path / "t.db"), adapter_cls=FailingAdapter)
    with pytest.raises(AdapterUnavailable):
        await h.manager.create_session("echo", workdir="/tmp/w")
    sessions = await h.store.list_sessions()
    assert len(sessions) == 1
    assert sessions[0]["status"] == "error"
    events = await h.store.events_since(sessions[0]["id"], 0)
    assert events[0]["type"] == "error"
    assert "spawn failed" in events[0]["payload"]["message"]
    await h.conn.close()


async def test_cancel_reaches_adapter_and_unknown_session_raises(h: Harness):
    session = await h.manager.create_session("echo", workdir="/tmp/w")
    await h.manager.cancel(session["id"])
    assert h.adapters[0].cancelled is True
    with pytest.raises(SessionNotFound):
        await h.manager.cancel("nope")
    with pytest.raises(SessionNotFound):
        await h.manager.send_message("nope", "hi")


async def test_soft_cap_five_concurrent_sessions(h: Harness):
    for _ in range(5):
        await h.manager.create_session("echo", workdir="/tmp/w")
    with pytest.raises(SessionConflict):
        await h.manager.create_session("echo", workdir="/tmp/w")


async def test_concurrent_appends_are_gap_and_dupe_free(h: Harness):
    session = await h.manager.create_session("echo", workdir="/tmp/w")
    sid = session["id"]

    async def producer(tag: str):
        for i in range(50):
            await h.store.append(sid, "agent_text", {"text": f"{tag}-{i}"})

    await asyncio.gather(producer("a"), producer("b"), producer("c"), producer("d"))
    events = await h.store.events_since(sid, 0)
    # 1 status + 200 appended: exactly 1..201, no holes, no dupes
    assert [e["seq"] for e in events] == list(range(1, 202))
    assert await h.store.last_seq(sid) == 201


async def test_list_sessions_sorted_by_updated_at_desc(h: Harness):
    s1 = await h.manager.create_session("echo", title="first", workdir="/tmp/w")
    await asyncio.sleep(0.01)
    await h.manager.create_session("echo", title="second", workdir="/tmp/w")
    await asyncio.sleep(0.01)
    await h.manager.send_message(s1["id"], "bump")  # touches updated_at
    titles = [s["title"] for s in await h.manager.list_sessions()]
    assert titles == ["first", "second"]
