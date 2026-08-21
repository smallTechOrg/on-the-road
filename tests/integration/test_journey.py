"""Full-journey integration: auth -> create session -> message -> WS stream.

Real uvicorn subprocess, real SQLite temp file, real EchoAdapter subprocess.
"""

from __future__ import annotations

import json
import time

import httpx
import pytest
from websockets.sync.client import connect as ws_connect

from .conftest import TEST_TOKEN, ServerProc


def wait_for_status(client: httpx.Client, session_id: str, status: str, timeout: float = 30.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        session = client.get(f"/api/sessions/{session_id}").json()
        if session["status"] == status:
            return session
        time.sleep(0.1)
    raise AssertionError(f"session never reached status {status!r}; last: {session}")


def collect_stream(url: str, until_type: str = "turn_end", timeout: float = 60.0) -> list[dict]:
    """Read event frames (those carrying a seq) until an event of until_type."""
    events: list[dict] = []
    with ws_connect(url, open_timeout=10) as ws:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            frame = json.loads(ws.recv(timeout=timeout))
            if "seq" not in frame:
                continue
            events.append(frame)
            if frame["type"] == until_type:
                return events
    raise AssertionError(f"never saw {until_type!r}; got {len(events)} events")


def assert_gap_free(events: list[dict], start_seq: int) -> None:
    seqs = [e["seq"] for e in events]
    assert seqs == list(range(start_seq, start_seq + len(seqs))), (
        f"seq stream has gaps/dupes: {seqs[:10]}...{seqs[-10:]}"
    )


def test_full_journey_auth_create_message_stream(server: ServerProc, client: httpx.Client):
    # --- auth: token check endpoint, and rejection without/with bad token
    assert client.get("/api/me").json() == {"ok": True}
    r = httpx.get(f"{server.base_url}/api/me")
    assert r.status_code == 401
    assert r.json()["error"]["code"] == "unauthorized"
    r = httpx.get(
        f"{server.base_url}/api/me", headers={"Authorization": "Bearer wrong-token"}
    )
    assert r.status_code == 401

    # --- empty session list
    assert client.get("/api/sessions").json() == {"sessions": []}

    # --- create an echo session
    r = client.post("/api/sessions", json={"adapter": "echo", "title": "journey"})
    assert r.status_code == 201, r.text
    session = r.json()
    sid = session["id"]
    assert session["adapter"] == "echo"
    assert session["title"] == "journey"
    assert session["status"] in ("starting", "running")
    assert session["last_seq"] >= 0

    # --- it shows up in the list
    listed = client.get("/api/sessions").json()["sessions"]
    assert [s["id"] for s in listed] == [sid]

    # --- send a message; deterministic echo streams it back over the WS
    text = "hello on the road, this is the integration journey"
    r = client.post(f"/api/sessions/{sid}/message", json={"text": text})
    assert r.status_code == 202, r.text
    user_seq = r.json()["seq"]
    assert user_seq >= 1

    url = f"{server.ws_url}/api/sessions/{sid}/stream?token={TEST_TOKEN}&since_seq=0"
    events = collect_stream(url)
    assert_gap_free(events, start_seq=1)

    by_type = {}
    for e in events:
        by_type.setdefault(e["type"], []).append(e)
    # the persisted user message is replayed with its exact seq and text
    user_events = by_type["user_message"]
    assert len(user_events) == 1
    assert user_events[0]["seq"] == user_seq
    assert user_events[0]["payload"]["text"] == text
    # deterministic echo: agent_text chunks concatenate to exactly the input
    echoed = "".join(e["payload"]["text"] for e in by_type["agent_text"])
    assert echoed == text
    # the fake tool call pair is present and correlated
    assert by_type["tool_start"][0]["payload"]["name"] == "echo"
    assert (
        by_type["tool_end"][0]["payload"]["tool_call_id"]
        == by_type["tool_start"][0]["payload"]["tool_call_id"]
    )
    assert by_type["turn_end"][0]["payload"]["stop_reason"] == "end_turn"

    # --- session settles to idle and last_seq matches the stream
    session = wait_for_status(client, sid, "idle")
    assert session["last_seq"] >= events[-1]["seq"]

    # --- events REST endpoint agrees with the WS stream, value for value
    body = client.get(f"/api/sessions/{sid}/events").json()
    assert body["last_seq"] == session["last_seq"]
    rest_by_seq = {e["seq"]: e for e in body["events"]}
    for e in events:
        assert rest_by_seq[e["seq"]]["type"] == e["type"]
        assert rest_by_seq[e["seq"]]["payload"] == e["payload"]


def test_create_session_error_paths(server: ServerProc, client: httpx.Client):
    # unknown adapter -> bad_request per spec error shape
    r = client.post("/api/sessions", json={"adapter": "nope"})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"

    # unknown session -> not_found
    r = client.get("/api/sessions/deadbeef")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    r = client.post("/api/sessions/deadbeef/message", json={"text": "hi"})
    assert r.status_code == 404

    # empty message -> bad_request
    r = client.post("/api/sessions", json={"adapter": "echo"})
    sid = r.json()["id"]
    r = client.post(f"/api/sessions/{sid}/message", json={"text": "   "})
    assert r.status_code == 400
    assert r.json()["error"]["code"] == "bad_request"


def test_ws_rejects_bad_token(server: ServerProc, client: httpx.Client):
    sid = client.post("/api/sessions", json={"adapter": "echo"}).json()["id"]
    url = f"{server.ws_url}/api/sessions/{sid}/stream?token=wrong"
    with ws_connect(url, open_timeout=10) as ws:
        frame = json.loads(ws.recv(timeout=10))
        assert frame["type"] == "error"
        assert frame["error"]["code"] == "unauthorized"
        with pytest.raises(Exception):
            while True:
                ws.recv(timeout=5)


def test_structured_log_line_for_run(server: ServerProc, client: httpx.Client):
    assert client.get("/api/me").json() == {"ok": True}
    time.sleep(0.5)  # let the log line flush
    lines = server.log_path.read_text().splitlines()
    parsed = []
    for line in lines:
        try:
            parsed.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # uvicorn's own non-JSON lines
    assert parsed, "no structured JSON log lines found"
    request_lines = [
        p for p in parsed if p.get("event") == "request" and p.get("path") == "/api/me"
    ]
    assert request_lines, f"no request log line for /api/me in {len(parsed)} JSON lines"
    line = request_lines[0]
    assert line["method"] == "GET"
    assert line["status"] == 200
    assert "ts" in line and "level" in line and "latency_ms" in line
    # startup line proves lifecycle logging too
    assert any(p.get("event") == "startup" for p in parsed)
