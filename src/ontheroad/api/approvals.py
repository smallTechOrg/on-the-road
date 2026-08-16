"""Approvals endpoint (spec/api.md Phase 2, slice 2D).

    POST /api/sessions/{id}/approvals/{request_id}  body {"option_id": "..."}
        -> 202 {} ; persists a permission_response event and forwards the
           decision to the session's adapter.
        -> 404 not_found for an unknown session or unknown/already-answered
           request_id.

Bearer auth is enforced by the app-wide middleware on /api/*.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from ontheroad.sessions.manager import (
    SessionManager,
    SessionNotFound,
    UnknownPermissionRequest,
)

router = APIRouter(prefix="/api/sessions", tags=["approvals"])


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


class ApprovalBody(BaseModel):
    option_id: str


@router.post("/{session_id}/approvals/{request_id}", status_code=202)
async def respond_approval(
    request: Request, session_id: str, request_id: str, body: ApprovalBody
):
    manager = get_manager(request)
    try:
        await manager.respond_permission(session_id, request_id, body.option_id)
    except SessionNotFound:
        return api_error(404, "not_found", f"unknown session {session_id!r}")
    except UnknownPermissionRequest:
        return api_error(
            404,
            "not_found",
            f"no pending permission request {request_id!r} for session {session_id!r}",
        )
    return {}
