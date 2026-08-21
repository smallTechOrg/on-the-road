"""Slice 2B integration: usage rollup via the API + 5xx error audit.

Real uvicorn subprocess, real SQLite temp file (WAL — a second process-side
connection appends the usage event through the real EventStore, exercising
the transactional rollup path exactly as the SessionManager does), real echo
adapter session for the 5xx preview path.
"""

from __future__ import annotations

import asyncio
import time

import httpx

from ontheroad.db import Database
from ontheroad.sessions.store import EventStore

from .conftest import ServerProc, TEST_TOKEN


def _create_echo_session(client: httpx.Client) -> str:
    r = client.post("/api/sessions", json={"adapter": "echo", "title": "usage"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _append_usage(db_path, session_id: str, payload: dict) -> None:
    """Append a usage event through the real store (transactional rollup)."""

    async def _run() -> None:
        db = await Database(str(db_path)).open()
        try:
            store = EventStore(db.conn)
            event = await store.append(session_id, "usage", payload)
            assert event["type"] == "usage"
        finally:
            await db.close()

    asyncio.run(_run())


def test_usage_daily_reflects_summed_tokens(server: ServerProc, client: httpx.Client):
    sid = _create_echo_session(client)

    # baseline: empty rollup
    body = client.get("/api/usage/daily").json()
    assert body["today_total"] == 0

    _append_usage(
        server.db_path,
        sid,
        {"input_tokens": 120, "output_tokens": 30, "total_tokens": 150, "cost_usd": 0.02},
    )
    _append_usage(server.db_path, sid, {"input_tokens": 10, "output_tokens": 5})

    body = client.get("/api/usage/daily").json()
    assert body["today_total"] == 165  # 150 + (10 + 5 defaulted total)
    today = body["days"][0]
    assert today["input_tokens"] == 130
    assert today["output_tokens"] == 35
    assert today["total_tokens"] == 165
    assert today["cost_usd"] == 0.02

    # the persisted usage events are queryable on the audit range endpoint
    events = client.get(f"/api/sessions/{sid}/events", params={"type": "usage"}).json()
    assert len(events["events"]) == 2
    assert events["events"][0]["payload"]["total_tokens"] == 150

    # validation error path
    r = client.get("/api/usage/daily", params={"days": 0})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def _wait_for_error_row(client: httpx.Client, path: str, timeout: float = 10.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        errors = client.get("/api/errors").json()["errors"]
        for row in errors:
            if row["path"] == path:
                return row
        time.sleep(0.2)
    raise AssertionError(f"no request_errors row for {path}; got {errors}")


def test_5xx_is_audited_into_api_errors(server: ServerProc, client: httpx.Client):
    sid = _create_echo_session(client)

    assert client.get("/api/errors").json() == {"errors": []}

    # a 401 (4xx) must NOT be audited
    r = httpx.get(f"{server.base_url}/api/me")
    assert r.status_code == 401

    # preview proxy to a port nothing listens on -> 502 adapter_unavailable
    dead_port = 59999
    path = f"/preview/{sid}/{dead_port}/"
    r = client.get(path)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "adapter_unavailable"

    row = _wait_for_error_row(client, path)
    assert row["method"] == "GET"
    assert row["status"] == 502
    assert row["error_code"] == "adapter_unavailable"
    assert str(dead_port) in row["detail"]
    assert row["ts"]

    # still no 4xx rows
    errors = client.get("/api/errors").json()["errors"]
    assert all(e["status"] >= 500 for e in errors)
