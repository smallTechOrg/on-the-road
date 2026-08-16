"""WebSocket stream endpoint (spec/api.md, Phase 1).

WS /api/sessions/{id}/stream?token=...&since_seq=N

Auth via ?token= (browsers cannot set headers on WS upgrade). On connect the
client receives persisted events N+1..latest and then live fan-out as ONE
strictly-increasing seq stream with no gaps or duplicates — the race-free
subscribe-before-read-backfill scheme lives in ``SessionManager.stream`` (see
its docstring); this module only ships frames and handles inbound
user_message/ping frames.

Fatal errors send a final {"type": "error", ...} frame then close with 1011
(4401 for auth failures), per spec conventions.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from ontheroad.auth import token_matches
from ontheroad.config import get_settings
from ontheroad.sessions.manager import (
    AdapterUnavailable,
    SessionManager,
    SessionNotFound,
)

log = logging.getLogger("ontheroad.stream")

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


@router.websocket("/api/sessions/{session_id}/stream")
async def session_stream(
    ws: WebSocket,
    session_id: str,
    token: str | None = Query(default=None),
    since_seq: int = Query(default=0, ge=0),
) -> None:
    # Accept first so we can deliver a structured error frame before closing.
    await ws.accept()

    settings = get_settings()
    if not token_matches(token, settings.token):
        await _close_with_error(ws, "unauthorized", "invalid token", WS_CLOSE_AUTH)
        return

    manager: SessionManager | None = getattr(ws.app.state, "session_manager", None)
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

    send_lock = asyncio.Lock()  # sender + pong replies share one socket

    async def send_events() -> None:
        async for event in manager.stream(session_id, since_seq=since_seq):
            async with send_lock:
                await ws.send_json(event)

    async def receive_frames() -> None:
        while True:
            frame = await ws.receive_json()
            ftype = frame.get("type")
            if ftype == "ping":
                async with send_lock:
                    await ws.send_json({"type": "pong"})
            elif ftype == "user_message":
                text = frame.get("text", "")
                if not isinstance(text, str) or not text.strip():
                    continue
                try:
                    await manager.send_message(session_id, text)
                except (SessionNotFound, AdapterUnavailable) as exc:
                    async with send_lock:
                        await ws.send_json(
                            _error_frame("adapter_unavailable", str(exc))
                        )
            # unknown frame types ignored (forward compatibility)

    sender = asyncio.create_task(send_events())
    receiver = asyncio.create_task(receive_frames())
    try:
        done, pending = await asyncio.wait(
            {sender, receiver}, return_when=asyncio.FIRST_COMPLETED
        )
        for task in pending:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        for task in done:
            exc = task.exception()
            if exc is not None and not isinstance(exc, WebSocketDisconnect):
                log.exception("stream error for %s", session_id, exc_info=exc)
                await _close_with_error(
                    ws, "internal", "stream failed", WS_CLOSE_INTERNAL
                )
                return
    finally:
        for task in (sender, receiver):
            if not task.done():
                task.cancel()
    with contextlib.suppress(Exception):
        await ws.close()
