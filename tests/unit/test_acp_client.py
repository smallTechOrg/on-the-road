"""Unit tests for the hand-rolled ACP JSON-RPC client and HermesACPAdapter.

No real Hermes required: the client is tested against an in-process stream
pair (raw line assertions for correlation ids) and the adapter against a
scripted fake ACP peer subprocess.
"""

from __future__ import annotations

import asyncio
import json
import sys
import textwrap

import pytest

from ontheroad.adapters.base import AgentEvent
from ontheroad.adapters.hermes_acp import (
    ACPClient,
    ACPError,
    HermesACPAdapter,
    map_acp_update,
)

# -- in-process stream plumbing ------------------------------------------


class PipeWriter:
    """Writer facade feeding a StreamReader — an in-process fake stdio peer."""

    def __init__(self, reader: asyncio.StreamReader) -> None:
        self._reader = reader

    def write(self, data: bytes) -> None:
        self._reader.feed_data(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self._reader.feed_eof()


def make_client_and_peer(**kwargs):
    """Returns (client, peer_reader, writer_into_client, run_task_factory)."""
    to_client = asyncio.StreamReader()  # peer → client
    to_peer = asyncio.StreamReader()  # client → peer
    client = ACPClient(to_client, PipeWriter(to_peer), **kwargs)
    return client, to_peer, PipeWriter(to_client)


async def read_json_line(reader: asyncio.StreamReader) -> dict:
    return json.loads(await asyncio.wait_for(reader.readline(), 5.0))


# -- ACPClient ------------------------------------------------------------


async def test_request_response_correlation_out_of_order():
    client, peer_reader, peer_writer = make_client_and_peer()
    run = asyncio.create_task(client.run())
    try:
        t1 = asyncio.create_task(client.request("alpha", {"n": 1}))
        t2 = asyncio.create_task(client.request("beta", {"n": 2}))

        req1 = await read_json_line(peer_reader)
        req2 = await read_json_line(peer_reader)
        assert req1["jsonrpc"] == "2.0"
        assert req1["method"] == "alpha" and req1["params"] == {"n": 1}
        assert req2["method"] == "beta" and req2["params"] == {"n": 2}
        assert req1["id"] != req2["id"]

        # Respond in REVERSE order — each future must resolve by id, not order.
        peer_writer.write(json.dumps({"jsonrpc": "2.0", "id": req2["id"], "result": {"got": "beta"}}).encode() + b"\n")
        peer_writer.write(json.dumps({"jsonrpc": "2.0", "id": req1["id"], "result": {"got": "alpha"}}).encode() + b"\n")

        assert await asyncio.wait_for(t1, 5.0) == {"got": "alpha"}
        assert await asyncio.wait_for(t2, 5.0) == {"got": "beta"}
    finally:
        run.cancel()


async def test_error_response_raises_acp_error():
    client, peer_reader, peer_writer = make_client_and_peer()
    run = asyncio.create_task(client.run())
    try:
        t = asyncio.create_task(client.request("nope"))
        req = await read_json_line(peer_reader)
        peer_writer.write(
            json.dumps({"jsonrpc": "2.0", "id": req["id"], "error": {"code": -32601, "message": "method not found"}}).encode() + b"\n"
        )
        with pytest.raises(ACPError) as exc_info:
            await asyncio.wait_for(t, 5.0)
        assert exc_info.value.code == -32601
        assert exc_info.value.message == "method not found"
    finally:
        run.cancel()


async def test_notifications_have_no_id_and_incoming_ones_are_dispatched():
    received: list[tuple[str, dict]] = []

    async def on_note(method: str, params: dict) -> None:
        received.append((method, params))

    client, peer_reader, peer_writer = make_client_and_peer(notification_handler=on_note)
    run = asyncio.create_task(client.run())
    try:
        await client.notify("session/cancel", {"sessionId": "s1"})
        out = await read_json_line(peer_reader)
        assert out == {"jsonrpc": "2.0", "method": "session/cancel", "params": {"sessionId": "s1"}}
        assert "id" not in out

        peer_writer.write(json.dumps({"jsonrpc": "2.0", "method": "session/update", "params": {"x": 1}}).encode() + b"\n")
        await asyncio.sleep(0.05)
        assert received == [("session/update", {"x": 1})]
    finally:
        run.cancel()


async def test_incoming_request_is_answered_with_matching_id():
    async def handler(method: str, params: dict) -> dict:
        assert method == "session/request_permission"
        return {"outcome": {"outcome": "selected", "optionId": params["want"]}}

    client, peer_reader, peer_writer = make_client_and_peer(request_handler=handler)
    run = asyncio.create_task(client.run())
    try:
        peer_writer.write(
            json.dumps({"jsonrpc": "2.0", "id": 777, "method": "session/request_permission", "params": {"want": "reject"}}).encode() + b"\n"
        )
        resp = await read_json_line(peer_reader)
        assert resp["id"] == 777
        assert resp["result"] == {"outcome": {"outcome": "selected", "optionId": "reject"}}
    finally:
        run.cancel()


async def test_eof_fails_pending_requests():
    client, _peer_reader, peer_writer = make_client_and_peer()
    run = asyncio.create_task(client.run())
    t = asyncio.create_task(client.request("hang"))
    await asyncio.sleep(0.02)
    peer_writer.close()
    with pytest.raises(ACPError, match="connection closed"):
        await asyncio.wait_for(t, 5.0)
    await asyncio.wait_for(run, 5.0)


# -- notification → event mapping ----------------------------------------


@pytest.mark.parametrize(
    "update,expected_type",
    [
        ({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hi"}}, "agent_text"),
        ({"sessionUpdate": "agent_thought_chunk", "content": {"type": "text", "text": "hmm"}}, "agent_thought"),
        ({"sessionUpdate": "tool_call", "toolCallId": "t1", "status": "pending"}, "tool_start"),
        ({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "in_progress"}, "tool_update"),
        ({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "completed"}, "tool_end"),
        ({"sessionUpdate": "tool_call_update", "toolCallId": "t1", "status": "failed"}, "tool_end"),
        ({"sessionUpdate": "usage_update", "usage": {"inputTokens": 3}}, "usage"),
        ({"sessionUpdate": "plan", "entries": []}, "status"),
        ({"sessionUpdate": "current_mode_update", "modeId": "code"}, "status"),
        ({"sessionUpdate": "session_info_update"}, "status"),
        ({"sessionUpdate": "totally_new_kind", "x": 1}, "status"),  # forward compatible
    ],
)
def test_map_acp_update_types(update, expected_type):
    ev = map_acp_update(update)
    assert isinstance(ev, AgentEvent)
    assert ev.type == expected_type


def test_map_acp_update_extracts_text_and_preserves_raw():
    ev = map_acp_update({"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "hello"}})
    assert ev.payload == {"text": "hello"}
    raw = {"sessionUpdate": "totally_new_kind", "x": 1}
    assert map_acp_update(raw).payload == raw  # unknown kinds keep raw payload


# -- HermesACPAdapter against a scripted fake ACP peer -------------------

FAKE_PEER = textwrap.dedent(
    """
    import json, os, sys

    def send(obj):
        sys.stdout.write(json.dumps(obj) + "\\n")
        sys.stdout.flush()

    def log(m):
        p = os.environ.get("ACP_FAKE_LOG")
        if p:
            with open(p, "a") as f:
                f.write(m + "\\n")

    def update(sid, u):
        send({"jsonrpc": "2.0", "method": "session/update", "params": {"sessionId": sid, "update": u}})

    for line in sys.stdin:
        msg = json.loads(line)
        method = msg.get("method")
        mid = msg.get("id")
        if method:
            log(method)
        if method == "initialize":
            result = {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}}
            if os.environ.get("ACP_FAKE_AUTH"):
                result["authMethods"] = [{"id": "terminal-setup"}]
            send({"jsonrpc": "2.0", "id": mid, "result": result})
        elif method == "session/new":
            send({"jsonrpc": "2.0", "id": mid, "result": {"sessionId": "fake-sess-1"}})
        elif method == "session/load":
            if os.environ.get("ACP_FAKE_FAIL_LOAD"):
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "load unsupported"}})
            else:
                send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/resume":
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "session/prompt":
            sid = msg["params"]["sessionId"]
            log("prompt_text:" + msg["params"]["prompt"][0]["text"])
            update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "Hello "}})
            update(sid, {"sessionUpdate": "agent_message_chunk", "content": {"type": "text", "text": "world"}})
            update(sid, {"sessionUpdate": "tool_call", "toolCallId": "tc-1", "title": "run_command", "status": "pending", "kind": "execute"})
            update(sid, {"sessionUpdate": "tool_call_update", "toolCallId": "tc-1", "status": "completed"})
            update(sid, {"sessionUpdate": "usage_update", "usage": {"inputTokens": 10, "outputTokens": 5}})
            send({"jsonrpc": "2.0", "id": 9001, "method": "session/request_permission", "params": {
                "sessionId": sid,
                "toolCall": {"toolCallId": "tc-2", "title": "rm -rf /"},
                "options": [
                    {"optionId": "allow_once", "name": "Allow", "kind": "allow_once"},
                    {"optionId": "reject_once", "name": "Reject", "kind": "reject_once"},
                ],
            }})
            resp = json.loads(sys.stdin.readline())
            assert resp.get("id") == 9001
            log("permission_outcome:" + json.dumps(resp.get("result")))
            send({"jsonrpc": "2.0", "id": mid, "result": {"stopReason": "end_turn"}})
        elif method == "session/cancel":
            update(msg["params"]["sessionId"], {"sessionUpdate": "session_info_update", "cancelled": True})
    """
)


@pytest.fixture
def fake_peer_cmd(tmp_path):
    script = tmp_path / "fake_acp_peer.py"
    script.write_text(FAKE_PEER)
    return [sys.executable, str(script)]


async def collect(adapter, n_or_type, timeout=10.0):
    events = []

    async def _run():
        async for ev in adapter.events():
            events.append(ev)
            if ev.type == n_or_type:
                return

    await asyncio.wait_for(_run(), timeout)
    return events


async def test_hermes_adapter_full_turn_against_fake_peer(tmp_path, monkeypatch, fake_peer_cmd):
    log_path = tmp_path / "peer.log"
    monkeypatch.setenv("ACP_FAKE_LOG", str(log_path))
    adapter = HermesACPAdapter(command=fake_peer_cmd)
    try:
        ref = await adapter.start(tmp_path, None)
        assert ref == "fake-sess-1"

        await adapter.send_user_message("do the thing")
        events = await collect(adapter, "turn_end")
        types = [ev.type for ev in events]
        assert types == [
            "agent_text",
            "agent_text",
            "tool_start",
            "tool_end",
            "usage",
            "permission_request",
            "status",
            "turn_end",
        ]
        assert "".join(ev.payload["text"] for ev in events[:2]) == "Hello world"
        assert events[2].payload["toolCallId"] == "tc-1"
        assert events[3].payload["status"] == "completed"
        assert events[4].payload["usage"] == {"inputTokens": 10, "outputTokens": 5}

        perm = events[5].payload
        assert perm["request_id"] == "tc-2"
        assert [o["optionId"] for o in perm["options"]] == ["allow_once", "reject_once"]

        notice = events[6].payload["notice"]
        assert "Phase 1" in notice and "reject_once" in notice and "tc-2" in notice

        assert events[7].payload == {"stop_reason": "end_turn"}

        peer_log = log_path.read_text()
        assert "prompt_text:do the thing" in peer_log
        # Phase-1 auto-safe-reject actually reached the agent:
        assert '"optionId": "reject_once"' in peer_log
        assert '"outcome": "selected"' in peer_log
    finally:
        await adapter.stop()


async def test_hermes_adapter_cancel_sends_session_cancel(tmp_path, fake_peer_cmd):
    adapter = HermesACPAdapter(command=fake_peer_cmd)
    try:
        await adapter.start(tmp_path, None)
        await adapter.cancel()
        # peer answers session/cancel with a session_info_update → status event
        events = await collect(adapter, "status")
        assert events[-1].payload["cancelled"] is True
    finally:
        await adapter.stop()


async def test_hermes_adapter_resume_uses_session_load(tmp_path, monkeypatch, fake_peer_cmd):
    log_path = tmp_path / "peer.log"
    monkeypatch.setenv("ACP_FAKE_LOG", str(log_path))
    adapter = HermesACPAdapter(command=fake_peer_cmd)
    try:
        ref = await adapter.start(tmp_path, "fake-sess-1")
        assert ref == "fake-sess-1"
        methods = log_path.read_text().splitlines()
        assert methods == ["initialize", "session/load"]
    finally:
        await adapter.stop()


async def test_hermes_adapter_resume_falls_back_to_session_resume(tmp_path, monkeypatch, fake_peer_cmd):
    log_path = tmp_path / "peer.log"
    monkeypatch.setenv("ACP_FAKE_LOG", str(log_path))
    monkeypatch.setenv("ACP_FAKE_FAIL_LOAD", "1")
    adapter = HermesACPAdapter(command=fake_peer_cmd)
    try:
        ref = await adapter.start(tmp_path, "fake-sess-1")
        assert ref == "fake-sess-1"
        methods = log_path.read_text().splitlines()
        assert methods == ["initialize", "session/load", "session/resume"]
    finally:
        await adapter.stop()


async def test_hermes_adapter_auth_required_emits_actionable_error(tmp_path, monkeypatch, fake_peer_cmd):
    monkeypatch.setenv("ACP_FAKE_AUTH", "1")
    adapter = HermesACPAdapter(command=fake_peer_cmd)
    try:
        await adapter.start(tmp_path, None)
        events = await collect(adapter, "error")
        assert events[-1].type == "error"
        assert "hermes acp --setup" in events[-1].payload["message"]
    finally:
        await adapter.stop()


async def test_hermes_adapter_missing_binary_raises_and_emits_error(tmp_path):
    adapter = HermesACPAdapter(command=["/nonexistent/hermes-python", "-m", "acp_adapter.entry"])
    with pytest.raises(OSError):
        await adapter.start(tmp_path, None)
    events = await collect(adapter, "error")
    assert "failed to spawn" in events[-1].payload["message"]


def test_hermes_adapter_default_command_comes_from_settings(monkeypatch):
    monkeypatch.setenv("ONTHEROAD_HERMES_PYTHON", "/custom/venv/bin/python")
    adapter = HermesACPAdapter()
    assert adapter._command == ["/custom/venv/bin/python", "-m", "acp_adapter.entry"]
