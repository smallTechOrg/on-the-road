"""REST endpoints for sessions (spec/api.md, Phase 1).

Bearer auth is enforced by the app-wide middleware (slice 1A); this module
implements the routes and the spec's error shape.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ontheroad.sessions.manager import (
    AdapterUnavailable,
    SessionConflict,
    SessionManager,
    SessionNotFound,
)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])

KNOWN_ADAPTERS = ("hermes", "echo")


def api_error(
    status: int, code: str, message: str, detail: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail or {}}},
    )


def get_manager(request: Request) -> SessionManager:
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        raise RuntimeError("session_manager not wired on app.state")
    return manager


def default_workdir() -> str:
    return os.environ.get("ONTHEROAD_DEFAULT_WORKDIR", os.getcwd())


class CreateSessionBody(BaseModel):
    adapter: str
    title: str | None = None
    workdir: str | None = None


class MessageBody(BaseModel):
    text: str


@router.get("")
async def list_sessions(request: Request) -> dict[str, Any]:
    sessions = await get_manager(request).list_sessions()
    return {"sessions": sessions}


@router.post("")
async def create_session(request: Request, body: CreateSessionBody):
    if body.adapter not in KNOWN_ADAPTERS:
        return api_error(
            400,
            "bad_request",
            f"unknown adapter '{body.adapter}'",
            {"known_adapters": list(KNOWN_ADAPTERS)},
        )
    manager = get_manager(request)
    try:
        session = await manager.create_session(
            adapter=body.adapter,
            title=body.title,
            workdir=body.workdir or default_workdir(),
        )
    except SessionConflict as exc:
        return api_error(409, "session_conflict", str(exc))
    except AdapterUnavailable as exc:
        return api_error(502, "adapter_unavailable", str(exc))
    return JSONResponse(status_code=201, content=session)


@router.get("/{session_id}")
async def get_session(request: Request, session_id: str):
    session = await get_manager(request).get_session(session_id)
    if session is None:
        return api_error(404, "not_found", f"session {session_id} not found")
    return session


@router.post("/{session_id}/attach")
async def attach_session(request: Request, session_id: str):
    manager = get_manager(request)
    try:
        session = await manager.attach(session_id)
    except SessionNotFound:
        return api_error(404, "not_found", f"session {session_id} not found")
    except AdapterUnavailable as exc:
        return api_error(502, "adapter_unavailable", str(exc))
    return session


@router.post("/{session_id}/message")
async def send_message(request: Request, session_id: str, body: MessageBody):
    if not body.text.strip():
        return api_error(400, "bad_request", "text must be non-empty")
    manager = get_manager(request)
    try:
        seq = await manager.send_message(session_id, body.text)
    except SessionNotFound:
        return api_error(404, "not_found", f"session {session_id} not found")
    except AdapterUnavailable as exc:
        return api_error(502, "adapter_unavailable", str(exc))
    return JSONResponse(status_code=202, content={"seq": seq})


@router.post("/{session_id}/cancel")
async def cancel_turn(request: Request, session_id: str):
    manager = get_manager(request)
    try:
        await manager.cancel(session_id)
    except SessionNotFound:
        return api_error(404, "not_found", f"session {session_id} not found")
    return JSONResponse(status_code=202, content={})


@router.get("/{session_id}/events")
async def list_events(
    request: Request,
    session_id: str,
    since_seq: int = 0,
    limit: int | None = None,
    type: str | None = None,
):
    manager = get_manager(request)
    session = await manager.get_session(session_id)
    if session is None:
        return api_error(404, "not_found", f"session {session_id} not found")
    events = await manager.store.events_since(
        session_id, since_seq=since_seq, limit=limit, type_=type
    )
    return {"events": events, "last_seq": session["last_seq"]}
