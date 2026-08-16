"""EchoAdapter — deterministic dev stub agent, a REAL subprocess.

Spawns ``python -m ontheroad.adapters.echo_child`` and speaks its trivial
newline-JSON line protocol; used as the deterministic test double and as a
selectable "Echo (dev)" agent in the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import uuid
from pathlib import Path
from typing import AsyncIterator

from .base import AgentAdapter, AgentEvent, register_adapter

log = logging.getLogger("ontheroad.adapters.echo")


@register_adapter
class EchoAdapter(AgentAdapter):
    name = "echo"

    def __init__(self) -> None:
        self._proc: asyncio.subprocess.Process | None = None
        self._queue: asyncio.Queue[AgentEvent] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._stderr_task: asyncio.Task | None = None
        self._stopping = False
        self._ref: str | None = None

    async def start(self, workdir: Path, resume_ref: str | None) -> str:
        self._proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "ontheroad.adapters.echo_child",
            cwd=str(workdir),
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        self._reader_task = asyncio.create_task(self._read_stdout())
        self._stderr_task = asyncio.create_task(self._drain_stderr())
        self._ref = resume_ref or f"echo-{uuid.uuid4().hex}"
        return self._ref

    async def send_user_message(self, text: str) -> None:
        await self._send({"type": "user_message", "text": text})

    async def events(self) -> AsyncIterator[AgentEvent]:
        while True:
            yield await self._queue.get()

    async def respond_permission(self, request_id: str, option_id: str) -> None:
        # The echo agent never requests permissions.
        return None

    async def cancel(self) -> None:
        await self._send({"type": "cancel"})

    async def stop(self) -> None:
        self._stopping = True
        proc = self._proc
        if proc is None:
            return
        try:
            await self._send({"type": "stop"})
        except (ConnectionError, RuntimeError, OSError):
            pass
        try:
            await asyncio.wait_for(proc.wait(), timeout=2.0)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
        for task in (self._reader_task, self._stderr_task):
            if task is not None:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        self._proc = None

    # -- internals --------------------------------------------------------

    async def _send(self, msg: dict) -> None:
        assert self._proc is not None and self._proc.stdin is not None, "adapter not started"
        self._proc.stdin.write(json.dumps(msg).encode() + b"\n")
        await self._proc.stdin.drain()

    async def _read_stdout(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        stdout = self._proc.stdout
        while True:
            line = await stdout.readline()
            if not line:
                break
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                self._queue.put_nowait(
                    AgentEvent("error", {"message": f"echo: bad event line: {line[:200]!r}"})
                )
                continue
            self._queue.put_nowait(
                AgentEvent(str(data.get("type", "status")), dict(data.get("payload") or {}))
            )
        if not self._stopping:
            self._queue.put_nowait(
                AgentEvent("error", {"message": "echo subprocess exited unexpectedly"})
            )

    async def _drain_stderr(self) -> None:
        assert self._proc is not None and self._proc.stderr is not None
        stderr = self._proc.stderr
        while True:
            line = await stderr.readline()
            if not line:
                break
            log.info("echo stderr", extra={"output_summary": line.decode(errors="replace")[:200]})
