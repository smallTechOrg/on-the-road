"""Integration: git surfacing + interactive permission approvals (slice 2D).

Real uvicorn subprocess, real SQLite temp file, real EchoAdapter subprocess.
The echo child's synthetic triggers make the flow deterministic:

* "!git"        -> a git_status event with a fixture PR url;
* "!permission" -> a permission_request (request_id "echo-perm-1") that holds
  the turn open until the approvals endpoint answers it.
"""

from __future__ import annotations

import json
import time

import httpx
from websockets.sync.client import connect as ws_connect

from .conftest import TEST_TOKEN, ServerProc

GIT_FIXTURE_URL = "https://github.com/example/on-the-road/pull/7"


def create_echo_session(client: httpx.Client) -> str:
    r = client.post("/api/sessions", json={"adapter": "echo", "title": "2d"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def stream_url(server: ServerProc, session_id: str, since_seq: int = 0) -> str:
    return (
        f"{server.ws_url}/api/sessions/{session_id}/stream"
        f"?token={TEST_TOKEN}&since_seq={since_seq}"
    )


def recv_until(ws, until_type: str, timeout: float = 30.0) -> list[dict]:
    events: list[dict] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = json.loads(ws.recv(timeout=timeout))
        if "seq" not in frame:
            continue
        events.append(frame)
        if frame["type"] == until_type:
            return events
    raise AssertionError(f"never saw {until_type!r}; got {[e['type'] for e in events]}")


def persisted(client: httpx.Client, session_id: str, type_: str) -> list[dict]:
    r = client.get(f"/api/sessions/{session_id}/events", params={"type": type_})
    assert r.status_code == 200
    return r.json()["events"]


def test_git_status_event_streams_and_persists(server: ServerProc, client: httpx.Client):
    session_id = create_echo_session(client)
    with ws_connect(stream_url(server, session_id), open_timeout=10) as ws:
        assert client.post(
            f"/api/sessions/{session_id}/message", json={"text": "!git please"}
        ).status_code == 202
        events = recv_until(ws, "turn_end")
    git = [e for e in events if e["type"] == "git_status"]
    assert len(git) == 1
    assert git[0]["payload"]["pr_url"] == GIT_FIXTURE_URL
    assert git[0]["payload"]["branch"] == "feature/echo-fixture"
    # persisted (transcript durability): same event via the range query
    stored = persisted(client, session_id, "git_status")
    assert [e["payload"]["pr_url"] for e in stored] == [GIT_FIXTURE_URL]


def test_permission_approve_flow_end_to_end(server: ServerProc, client: httpx.Client):
    session_id = create_echo_session(client)
    with ws_connect(stream_url(server, session_id), open_timeout=10) as ws:
        client.post(f"/api/sessions/{session_id}/message", json={"text": "hi !permission"})
        events = recv_until(ws, "permission_request")
        perm = events[-1]["payload"]
        assert perm["request_id"] == "echo-perm-1"
        option_ids = [o["optionId"] for o in perm["options"]]
        assert "allow_once" in option_ids and "reject_once" in option_ids

        # unknown request_id -> 404
        r = client.post(
            f"/api/sessions/{session_id}/approvals/nope-1", json={"option_id": "allow_once"}
        )
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"

        # unknown session -> 404
        r = client.post(
            "/api/sessions/no-such-session/approvals/echo-perm-1",
            json={"option_id": "allow_once"},
        )
        assert r.status_code == 404

        # approve -> 202, turn completes with an ack
        r = client.post(
            f"/api/sessions/{session_id}/approvals/echo-perm-1",
            json={"option_id": "allow_once"},
        )
        assert r.status_code == 202, r.text
        tail = recv_until(ws, "turn_end")

    types = [e["type"] for e in tail]
    assert "permission_response" in types
    resp = next(e for e in tail if e["type"] == "permission_response")
    assert resp["payload"]["request_id"] == "echo-perm-1"
    assert resp["payload"]["option_id"] == "allow_once"
    assert resp["payload"]["source"] == "user"
    ack = "".join(
        e["payload"]["text"] for e in tail if e["type"] == "agent_text"
    )
    assert "allow_once" in ack

    # decision persisted to the transcript
    stored = persisted(client, session_id, "permission_response")
    assert [e["payload"]["option_id"] for e in stored] == ["allow_once"]

    # answering again -> 404 (already answered / no longer pending)
    r = client.post(
        f"/api/sessions/{session_id}/approvals/echo-perm-1",
        json={"option_id": "allow_once"},
    )
    assert r.status_code == 404


def test_permission_deny_flow_completes_turn(server: ServerProc, client: httpx.Client):
    session_id = create_echo_session(client)
    with ws_connect(stream_url(server, session_id), open_timeout=10) as ws:
        client.post(f"/api/sessions/{session_id}/message", json={"text": "!permission"})
        recv_until(ws, "permission_request")
        r = client.post(
            f"/api/sessions/{session_id}/approvals/echo-perm-1",
            json={"option_id": "reject_once"},
        )
        assert r.status_code == 202, r.text
        tail = recv_until(ws, "turn_end")
    resp = next(e for e in tail if e["type"] == "permission_response")
    assert resp["payload"]["option_id"] == "reject_once"
    ack = "".join(e["payload"]["text"] for e in tail if e["type"] == "agent_text")
    assert "reject_once" in ack
    stored = persisted(client, session_id, "permission_response")
    assert stored and stored[-1]["payload"]["option_id"] == "reject_once"
