"""Unit tests for the adapter layer: AgentEvent, registry, EchoAdapter subprocess."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from ontheroad.adapters import (
    AgentAdapter,
    AgentEvent,
    EchoAdapter,
    HermesACPAdapter,
    available_adapters,
    get_adapter_class,
)

# -- AgentEvent -----------------------------------------------------------


def test_agent_event_is_frozen_with_default_ts():
    ev = AgentEvent("agent_text", {"text": "hi"})
    assert ev.type == "agent_text"
    assert ev.payload == {"text": "hi"}
    assert ev.ts > 0
    with pytest.raises(dataclasses.FrozenInstanceError):
        ev.type = "status"  # type: ignore[misc]


def test_agent_event_has_no_seq_field():
    # Adapters never number events; seq is assigned by the store at persist time.
    assert "seq" not in {f.name for f in dataclasses.fields(AgentEvent)}


# -- Registry -------------------------------------------------------------


def test_registry_contains_both_builtin_adapters():
    assert available_adapters() == ["echo", "hermes"]
    assert get_adapter_class("echo") is EchoAdapter
    assert get_adapter_class("hermes") is HermesACPAdapter
    assert issubclass(EchoAdapter, AgentAdapter)
    assert issubclass(HermesACPAdapter, AgentAdapter)


def test_registry_unknown_name_raises_keyerror():
    with pytest.raises(KeyError, match="unknown adapter"):
        get_adapter_class("codex")


# -- EchoAdapter (real subprocess) ---------------------------------------


async def collect_until_turn_end(adapter: AgentAdapter, timeout: float = 10.0) -> list[AgentEvent]:
    events: list[AgentEvent] = []

    async def _collect():
        async for ev in adapter.events():
            events.append(ev)
            if ev.type in ("turn_end", "error"):
                return

    await asyncio.wait_for(_collect(), timeout)
    return events


async def test_echo_streams_message_back_deterministically(tmp_path):
    adapter = EchoAdapter()
    try:
        ref = await adapter.start(tmp_path, None)
        assert ref.startswith("echo-")
        await adapter.send_user_message("hello on the road")
        events = await collect_until_turn_end(adapter)

        types = [ev.type for ev in events]
        # ready status, working status, fake tool pair, chunks, turn_end — in order
        assert types[0] == "status" and events[0].payload == {"state": "ready"}
        assert types[1] == "status" and events[1].payload == {"state": "working"}
        assert types[2] == "tool_start"
        assert events[2].payload["name"] == "echo"
        assert events[2].payload["args"] == {"text": "hello on the road"}
        assert types[3] == "tool_end"
        assert events[3].payload["status"] == "completed"
        assert events[3].payload["tool_call_id"] == events[2].payload["tool_call_id"]

        chunks = [ev.payload["text"] for ev in events if ev.type == "agent_text"]
        assert chunks == ["hello on", " the roa", "d"]
        assert "".join(chunks) == "hello on the road"

        assert types[-1] == "turn_end"
        assert events[-1].payload == {"stop_reason": "end_turn"}
        # agent_text chunks all sit between tool_end and turn_end
        assert types[4:-1] == ["agent_text"] * 3
    finally:
        await adapter.stop()


async def test_echo_empty_message_still_yields_tool_pair_and_turn_end(tmp_path):
    adapter = EchoAdapter()
    try:
        await adapter.start(tmp_path, None)
        await adapter.send_user_message("")
        events = await collect_until_turn_end(adapter)
        types = [ev.type for ev in events]
        assert "agent_text" not in types
        assert types[-1] == "turn_end"
        assert "tool_start" in types and "tool_end" in types
    finally:
        await adapter.stop()


async def test_echo_resume_ref_is_reused(tmp_path):
    adapter = EchoAdapter()
    try:
        ref = await adapter.start(tmp_path, "echo-previous-ref")
        assert ref == "echo-previous-ref"
    finally:
        await adapter.stop()


async def test_echo_cancel_mid_turn_yields_cancelled_turn_end(tmp_path):
    adapter = EchoAdapter()
    try:
        await adapter.start(tmp_path, None)
        # 320 chars → 40 chunks * 50ms = ~2s: plenty of time to cancel mid-stream.
        await adapter.send_user_message("x" * 320)

        events: list[AgentEvent] = []

        async def _collect():
            chunks_seen = 0
            async for ev in adapter.events():
                events.append(ev)
                if ev.type == "agent_text":
                    chunks_seen += 1
                    if chunks_seen == 2:
                        await adapter.cancel()
                if ev.type == "turn_end":
                    return

        await asyncio.wait_for(_collect(), 10.0)
        assert events[-1].type == "turn_end"
        assert events[-1].payload == {"stop_reason": "cancelled"}
        chunk_count = sum(1 for ev in events if ev.type == "agent_text")
        assert 2 <= chunk_count < 40  # streaming was interrupted before completion
    finally:
        await adapter.stop()


async def test_echo_subprocess_death_emits_error_event(tmp_path):
    adapter = EchoAdapter()
    try:
        await adapter.start(tmp_path, None)
        assert adapter._proc is not None
        adapter._proc.kill()
        events = await collect_until_turn_end(adapter)
        assert events[-1].type == "error"
        assert "exited unexpectedly" in events[-1].payload["message"]
    finally:
        await adapter.stop()
