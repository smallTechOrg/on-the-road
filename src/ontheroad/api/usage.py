"""Usage + error-audit endpoints (spec/api.md Phase 2, slice 2B).

Bearer auth is enforced by the app-wide middleware on /api/*.

    GET /api/usage/daily?days=30
        -> {"days":[{day,input_tokens,output_tokens,total_tokens,cost_usd}],
            "today_total": T}

    GET /api/errors?limit=50
        -> {"errors":[{id,ts,method,path,status,error_code,detail}]}
        newest-first audit of every 5xx response / unhandled exception
        captured by RequestLoggingMiddleware into request_errors.

The transcript/audit range query GET /api/sessions/{id}/events (type filter +
since_seq/limit paging) already exists in ontheroad.api.sessions (Phase 1).
"""

from __future__ import annotations

from typing import Any

import aiosqlite
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ontheroad.usage import daily_totals

router = APIRouter(prefix="/api", tags=["usage"])

MAX_ERRORS_LIMIT = 500


def api_error(
    status: int, code: str, message: str, detail: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail or {}}},
    )


def get_conn(request: Request) -> aiosqlite.Connection:
    db = getattr(request.app.state, "db", None)
    if db is None or db.conn is None:
        raise RuntimeError("database not wired on app.state")
    return db.conn


@router.get("/usage/daily")
async def usage_daily(request: Request, days: int = 30):
    if days < 1:
        return api_error(400, "bad_request", "days must be >= 1")
    return await daily_totals(get_conn(request), days=days)


@router.get("/errors")
async def request_errors(request: Request, limit: int = 50):
    if limit < 1:
        return api_error(400, "bad_request", "limit must be >= 1")
    limit = min(limit, MAX_ERRORS_LIMIT)
    conn = get_conn(request)
    cur = await conn.execute(
        "SELECT id, ts, method, path, status, error_code, detail "
        "FROM request_errors ORDER BY id DESC LIMIT ?",
        (limit,),
    )
    rows = await cur.fetchall()
    return {
        "errors": [
            {
                "id": row["id"],
                "ts": row["ts"],
                "method": row["method"],
                "path": row["path"],
                "status": row["status"],
                "error_code": row["error_code"],
                "detail": row["detail"],
            }
            for row in rows
        ]
    }
