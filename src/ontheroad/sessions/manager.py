"""SessionManager: lifecycle, persist-then-broadcast, race-free streaming.

Durability core (spec/capabilities/transcript-durability.md):

* every event is persisted via ``EventStore.append`` (which assigns the
  per-session monotonic ``seq`` under the store's write lock) BEFORE any
  subscriber sees it;
* append + fan-out happen under a per-session publish lock so subscriber
  queues receive frames in strict seq order;
* :meth:`stream` implements the race-free reconnect protocol —
  **subscribe-before-read-backfill**:

    1. register the subscriber queue (from this instant every new append is
       queued for us, in order);
    2. read the persisted backfill ``since_seq+1 .. <read time>`` and yield it;
    3. drain the queue, discarding any frame whose seq <= the highest seq
       already yielded (an event appended between steps 1 and 2 appears in
       both the backfill and the queue — the dedupe drops the queued copy).

  Result: the client sees exactly one strictly-increasing seq stream with no
  gaps and no duplicates, by construction.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, AsyncIterator, Callable

from ontheroad.sessions.store import ACTIVE_STATUSES, EventStore

log = logging.getLogger("ontheroad.sessions")

SOFT_SESSION_CAP = 5


class SessionNotFound(Exception):
    pass


class SessionConflict(Exception):
    pass


class AdapterUnavailable(Exception):
    pass


def _default_adapter_factory(name: str) -> Any:
    """Instantiate an adapter from the registry (ontheroad.adapters.base).

    Importing the ``ontheroad.adapters`` package registers the built-ins.
    """
    from ontheroad.adapters import get_adapter_class

    try:
        cls = get_adapter_class(name)
    except KeyError as exc:
        raise AdapterUnavailable(f"unknown adapter: {name}") from exc
    return cls()


class SessionManager:
    """Owns adapters + subscriber fan-out; single instance per server process."""

    def __init__(
        self,
        store: EventStore,
        adapter_factory: Callable[[str], Any] | None = None,
    ) -> None:
        self._store = store
        self._adapter_factory = adapter_factory or _default_adapter_factory
        self._adapters: dict[str, Any] = {}
        self._pumps: dict[str, asyncio.Task] = {}
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._pub_locks: dict[str, asyncio.Lock] = {}

    @property
    def store(self) -> EventStore:
        return self._store

    # ------------------------------------------------------------- lifecycle

    async def startup(self) -> None:
        """After server restart: subprocesses died with us — sessions go idle."""
        n = await self._store.mark_active_sessions_idle()
        if n:
            log.info("restart: marked %d session(s) idle", n)

    async def shutdown(self) -> None:
        for session_id in list(self._adapters):
            await self._stop_adapter(session_id, final_status="idle")

    async def create_session(
        self,
        adapter: str,
        title: str | None = None,
        workdir: str = ".",
    ) -> dict[str, Any]:
        if await self._store.count_active() >= SOFT_SESSION_CAP:
            raise SessionConflict(
                f"soft cap of {SOFT_SESSION_CAP} concurrent sessions reached"
            )
        session = await self._store.create_session(adapter, title, workdir)
        await self._start_adapter(session)
        return (await self._store.get_session(session["id"]))  # type: ignore[return-value]

    async def attach(self, session_id: str) -> dict[str, Any]:
        """(Re)start the adapter for an idle/error session, resuming via the
        stored adapter_session_ref (Hermes: ACP session/load)."""
        session = await self._require(session_id)
        if session_id in self._adapters and session["status"] in ACTIVE_STATUSES:
            return session  # already attached & live
        await self._start_adapter(session)
        return (await self._require(session_id))

    async def _start_adapter(self, session: dict[str, Any]) -> None:
        session_id = session["id"]
        try:
            adapter = self._adapter_factory(session["adapter"])
            from pathlib import Path

            ref = await adapter.start(
                Path(session["workdir"]), session["adapter_session_ref"]
            )
        except Exception as exc:  # spawn/handshake failure → error status
            await self._store.update_session(session_id, status="error")
            await self._persist_and_broadcast(
                session_id, "error", {"message": f"adapter start failed: {exc}"}
            )
            raise AdapterUnavailable(str(exc)) from exc
        self._adapters[session_id] = adapter
        await self._store.update_session(
            session_id, status="running", adapter_session_ref=ref
        )
        await self._persist_and_broadcast(session_id, "status", {"status": "running"})
        self._pumps[session_id] = asyncio.create_task(
            self._pump_events(session_id, adapter)
        )

    async def _stop_adapter(self, session_id: str, final_status: str) -> None:
        adapter = self._adapters.pop(session_id, None)
        pump = self._pumps.pop(session_id, None)
        if pump is not None:
            pump.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await pump
        if adapter is not None:
            with contextlib.suppress(Exception):
                await adapter.stop()
        await self._store.update_session(session_id, status=final_status)

    # --------------------------------------------------------------- actions

    async def send_message(self, session_id: str, text: str) -> int:
        session = await self._require(session_id)
        if session_id not in self._adapters:
            # idle/error session: transparently reattach so "drop back in and
            # keep talking" just works.
            session = await self.attach(session_id)
        event = await self._persist_and_broadcast(
            session_id, "user_message", {"text": text}
        )
        if session["status"] != "running":
            await self._store.update_session(session_id, status="running")
            await self._persist_and_broadcast(
                session_id, "status", {"status": "running"}
            )
        await self._adapters[session_id].send_user_message(text)
        return event["seq"]

    async def cancel(self, session_id: str) -> None:
        await self._require(session_id)
        adapter = self._adapters.get(session_id)
        if adapter is not None:
            await adapter.cancel()

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        return await self._store.get_session(session_id)

    async def list_sessions(self) -> list[dict[str, Any]]:
        return await self._store.list_sessions()

    async def _require(self, session_id: str) -> dict[str, Any]:
        session = await self._store.get_session(session_id)
        if session is None:
            raise SessionNotFound(session_id)
        return session

    # -------------------------------------------- persist-then-broadcast core

    def _pub_lock(self, session_id: str) -> asyncio.Lock:
        return self._pub_locks.setdefault(session_id, asyncio.Lock())

    async def _persist_and_broadcast(
        self, session_id: str, type_: str, payload: dict[str, Any]
    ) -> dict[str, Any]:
        """Durability first: append (assigns seq) and only then fan out.
        Held under the publish lock so queues see strict seq order."""
        async with self._pub_lock(session_id):
            event = await self._store.append(session_id, type_, payload)
            for queue in self._subscribers.get(session_id, set()):
                queue.put_nowait(event)
            return event

    async def _pump_events(self, session_id: str, adapter: Any) -> None:
        """Consume the adapter's normalized stream; persist + broadcast each."""
        try:
            async for ev in adapter.events():
                payload = dict(ev.payload)
                await self._persist_and_broadcast(session_id, ev.type, payload)
                if ev.type == "turn_end":
                    await self._store.update_session(session_id, status="idle")
                    await self._persist_and_broadcast(
                        session_id, "status", {"status": "idle"}
                    )
            # stream ended: adapter subprocess exited
            self._adapters.pop(session_id, None)
            await self._store.update_session(session_id, status="idle")
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("adapter pump failed for %s", session_id)
            self._adapters.pop(session_id, None)
            await self._store.update_session(session_id, status="error")
            with contextlib.suppress(Exception):
                await self._persist_and_broadcast(
                    session_id, "error", {"message": str(exc)}
                )

    # ------------------------------------------------------------- streaming

    def subscribe(self, session_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(session_id, set()).add(queue)
        return queue

    def unsubscribe(self, session_id: str, queue: asyncio.Queue) -> None:
        subs = self._subscribers.get(session_id)
        if subs is not None:
            subs.discard(queue)
            if not subs:
                self._subscribers.pop(session_id, None)

    async def stream(
        self, session_id: str, since_seq: int = 0
    ) -> AsyncIterator[dict[str, Any]]:
        """One ordered event stream: persisted backfill since_seq+1.. then live.

        Race-free by subscribe-before-read-backfill + seq dedupe (see module
        docstring). Yields event dicts {seq, type, payload, ts} with strictly
        increasing, gap-free seq.
        """
        queue = self.subscribe(session_id)
        try:
            last_sent = since_seq
            for event in await self._store.events_since(session_id, since_seq):
                yield event
                last_sent = event["seq"]
            while True:
                event = await queue.get()
                if event["seq"] <= last_sent:
                    continue  # overlap with backfill window — dedupe
                last_sent = event["seq"]
                yield event
        finally:
            self.unsubscribe(session_id, queue)
