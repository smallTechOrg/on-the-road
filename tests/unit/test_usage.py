"""Slice 2B unit tests: usage rollup math + request_errors audit middleware.

Rollup runs against a real migrated SQLite temp file (production engine);
the middleware tests exercise a real FastAPI app in-process via ASGITransport
(so an unhandled route exception propagates through the middleware exactly as
it does under uvicorn's ServerErrorMiddleware).
"""

from __future__ import annotations

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from ontheroad.db import Database
from ontheroad.db.migrate import apply_migrations
from ontheroad.logging import RequestLoggingMiddleware
from ontheroad.usage.rollup import apply_usage, daily_totals, day_of, today_utc

async def _make_db(tmp_path) -> Database:
    db_path = str(tmp_path / "test.db")
    version = await apply_migrations(db_path)
    assert version >= 2  # 0002_usage.sql applied
    return await Database(db_path).open()


# ------------------------------------------------------------------- day_of


def test_day_of_buckets_iso_timestamp_by_utc_date():
    assert day_of("2026-08-16T23:59:59Z") == "2026-08-16"
    assert day_of("2026-01-02T00:00:00.123Z") == "2026-01-02"


# -------------------------------------------------------------- apply_usage


async def test_apply_usage_sums_same_day(tmp_path):
    db = await _make_db(tmp_path)
    try:
        await apply_usage(
            db.conn,
            "2026-08-16",
            {"input_tokens": 100, "output_tokens": 40, "total_tokens": 140, "cost_usd": 0.5},
        )
        await apply_usage(
            db.conn,
            "2026-08-16",
            {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15, "cost_usd": 0.25},
        )
        await db.conn.commit()
        cur = await db.conn.execute("SELECT * FROM usage_daily WHERE day = '2026-08-16'")
        row = await cur.fetchone()
        assert row["input_tokens"] == 110
        assert row["output_tokens"] == 45
        assert row["total_tokens"] == 155
        assert row["cost_usd"] == pytest.approx(0.75)
    finally:
        await db.close()


async def test_apply_usage_edge_payloads(tmp_path):
    """Missing total defaults to input+output; missing cost stays NULL; junk -> 0."""
    db = await _make_db(tmp_path)
    try:
        await apply_usage(db.conn, "2026-08-15", {"input_tokens": 7, "output_tokens": 3})
        await apply_usage(db.conn, "2026-08-15", {"input_tokens": "junk", "total_tokens": None})
        await apply_usage(db.conn, "2026-08-15", {})
        await db.conn.commit()
        cur = await db.conn.execute("SELECT * FROM usage_daily WHERE day = '2026-08-15'")
        row = await cur.fetchone()
        assert row["input_tokens"] == 7
        assert row["output_tokens"] == 3
        assert row["total_tokens"] == 10
        assert row["cost_usd"] is None
    finally:
        await db.close()


async def test_daily_totals_today_total_and_ordering(tmp_path):
    db = await _make_db(tmp_path)
    try:
        today = today_utc()
        await apply_usage(db.conn, "2020-01-01", {"total_tokens": 999})  # out of window
        await apply_usage(db.conn, today, {"input_tokens": 1, "output_tokens": 2})
        await db.conn.commit()
        body = await daily_totals(db.conn, days=30)
        assert body["today_total"] == 3
        days = [d["day"] for d in body["days"]]
        assert today in days
        assert "2020-01-01" not in days
    finally:
        await db.close()


# -------------------------------------------- request_errors audit middleware


def _audit_app(db: Database) -> FastAPI:
    app = FastAPI()

    @app.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    @app.get("/unavail")
    async def unavail():
        return JSONResponse(
            status_code=503,
            content={"error": {"code": "adapter_unavailable", "message": "down"}},
        )

    app.add_middleware(RequestLoggingMiddleware)
    app.state.db = db
    return app


async def _rows(db: Database) -> list:
    cur = await db.conn.execute(
        "SELECT ts, method, path, status, error_code, detail FROM request_errors ORDER BY id"
    )
    return await cur.fetchall()


async def test_audit_records_503_response(tmp_path):
    db = await _make_db(tmp_path)
    try:
        app = _audit_app(db)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/unavail")
        assert r.status_code == 503
        rows = await _rows(db)
        assert len(rows) == 1
        row = rows[0]
        assert row["method"] == "GET"
        assert row["path"] == "/unavail"
        assert row["status"] == 503
        assert row["error_code"] == "adapter_unavailable"
        assert "down" in row["detail"]
        assert row["ts"].endswith("Z")
    finally:
        await db.close()


async def test_audit_records_unhandled_exception_as_500(tmp_path):
    db = await _make_db(tmp_path)
    try:
        app = _audit_app(db)
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/boom")
        assert r.status_code == 500
        rows = await _rows(db)
        assert len(rows) == 1
        assert rows[0]["path"] == "/boom"
        assert rows[0]["status"] == 500
        assert "kaboom" in rows[0]["detail"]
    finally:
        await db.close()


async def test_audit_ignores_4xx(tmp_path):
    db = await _make_db(tmp_path)
    try:
        app = _audit_app(db)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
            r = await c.get("/nope")
        assert r.status_code == 404
        assert await _rows(db) == []
    finally:
        await db.close()


async def test_audit_failure_never_breaks_response(tmp_path):
    """Missing app.state.db (or a closed one) must not affect the response."""
    db = await _make_db(tmp_path)
    app = _audit_app(db)
    await db.close()  # audit insert will now fail
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as c:
        r = await c.get("/unavail")
    assert r.status_code == 503
    assert r.json()["error"]["code"] == "adapter_unavailable"
