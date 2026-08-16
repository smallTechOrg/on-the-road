"""PTY WebSocket endpoint: spawn a shell in a session's workdir.

WS /api/sessions/{session_id}/term?token=...

Auth uses the same accept-first, check-token pattern as the chat stream.
Output frames: {"type": "output", "data": "<text>"}
Input  frames: {"type": "input",  "data": "<text>"}
Resize frames: {"type": "resize", "cols": N, "rows": N}
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import os
import pty
import signal
import struct
import termios
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ontheroad.auth import token_matches
from ontheroad.config import get_settings

log = logging.getLogger("ontheroad.term")

router = APIRouter()

WS_CLOSE_AUTH = 4401
WS_CLOSE_INTERNAL = 1011


def _error_frame(code: str, message: str) -> dict[str, Any]:
    return {"type": "error", "error": {"code": code, "message": message, "detail": {}}}


async def _close_with_error(
    ws: WebSocket, code: str, message: str, close_code: int
) -> None:
    with contextlib.suppress(Exception):
        await ws.send_json(_error_frame(code, message))
    with contextlib.suppress(Exception):
        await ws.close(code=close_code)


@router.websocket("/api/sessions/{session_id}/term")
async def session_term(
    ws: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
) -> None:
    await ws.accept()

    settings = get_settings()
    if not token_matches(token, settings.token):
        await _close_with_error(ws, "unauthorized", "invalid token", WS_CLOSE_AUTH)
        return

    manager = getattr(ws.app.state, "session_manager", None)
    if manager is None:
        await _close_with_error(
            ws, "internal", "session manager not available", WS_CLOSE_INTERNAL
        )
        return

    session = await manager.get_session(session_id)
    if session is None:
        await _close_with_error(
            ws, "not_found", f"session {session_id} not found", WS_CLOSE_INTERNAL
        )
        return

    workdir = session.get("workdir", ".")

    # Spawn PTY via fork
    pid, master_fd = pty.fork()
    if pid == 0:
        # Child process — exec shell
        try:
            os.chdir(workdir)
        except OSError:
            pass
        shell = os.environ.get("SHELL", "/bin/sh")
        os.execvp(shell, [shell, "-l"])
        # unreachable
        os._exit(1)  # pragma: no cover

    # Parent — set master_fd non-blocking
    os.set_blocking(master_fd, False)

    loop = asyncio.get_event_loop()
    closed = asyncio.Event()

    async def read_pty() -> None:
        """Read from PTY fd and send to WebSocket."""
        try:
            while not closed.is_set():
                future: asyncio.Future[bytes] = loop.create_future()

                def _on_readable() -> None:
                    if future.done():
                        return
                    try:
                        data = os.read(master_fd, 4096)
                        if not data:
                            future.set_exception(EOFError())
                        else:
                            future.set_result(data)
                    except OSError as exc:
                        future.set_exception(exc)

                loop.add_reader(master_fd, _on_readable)
                try:
                    data = await future
                finally:
                    loop.remove_reader(master_fd)

                await ws.send_json({"type": "output", "data": data.decode("utf-8", errors="replace")})
        except (EOFError, OSError, WebSocketDisconnect):
            pass
        finally:
            closed.set()

    async def write_pty() -> None:
        """Read from WebSocket and write to PTY fd."""
        try:
            while not closed.is_set():
                frame = await ws.receive_json()
                ftype = frame.get("type")
                if ftype == "input":
                    raw = frame.get("data", "")
                    if raw:
                        os.write(master_fd, raw.encode("utf-8"))
                elif ftype == "resize":
                    cols = frame.get("cols", 80)
                    rows = frame.get("rows", 24)
                    try:
                        winsize = struct.pack("HHHH", rows, cols, 0, 0)
                        fcntl.ioctl(master_fd, termios.TIOCSWINSZ, winsize)
                    except OSError:
                        pass
        except (WebSocketDisconnect, Exception):
            pass
        finally:
            closed.set()

    reader = asyncio.create_task(read_pty())
    writer = asyncio.create_task(write_pty())
    try:
        done, pending = await asyncio.wait(
            {reader, writer}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
    finally:
        # Cleanup: kill child, close fd
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGTERM)
        with contextlib.suppress(OSError):
            os.close(master_fd)
        with contextlib.suppress(ChildProcessError, OSError):
            os.waitpid(pid, os.WNOHANG)
        with contextlib.suppress(Exception):
            await ws.close()
