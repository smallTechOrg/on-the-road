"""Opt-in live test: drives the REAL HermesACPAdapter over ACP stdio.

Skipped unless ONTHEROAD_TEST_HERMES=1 (requires hermes-agent installed with
an LLM key in ~/.hermes/.env — burns real tokens, so it is never part of the
default gate; see spec/roadmap.md Phase 1 assumption).

Run: ONTHEROAD_TEST_HERMES=1 uv run pytest tests/live -q
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("ONTHEROAD_TEST_HERMES") != "1",
    reason="live Hermes test: set ONTHEROAD_TEST_HERMES=1 to run",
)

TURN_TIMEOUT = 180.0


async def _minimal_turn(tmp_path: Path) -> None:
    from ontheroad.adapters.hermes_acp import HermesACPAdapter

    adapter = HermesACPAdapter()
    ref = await adapter.start(tmp_path, resume_ref=None)
    assert ref, "session/new must return an adapter-side session ref"
    try:
        await adapter.send_user_message(
            "Reply with exactly the single word: pong"
        )
        text_parts: list[str] = []
        saw_turn_end = False
        agen = adapter.events()
        while not saw_turn_end:
            event = await asyncio.wait_for(agen.__anext__(), timeout=TURN_TIMEOUT)
            if event.type == "agent_text":
                text_parts.append(event.payload.get("text", ""))
            elif event.type == "turn_end":
                saw_turn_end = True
            elif event.type == "error":
                pytest.fail(f"hermes error event: {event.payload}")
        assert saw_turn_end
        full_text = "".join(text_parts)
        assert full_text.strip(), "expected non-empty streamed assistant reply"
        assert "pong" in full_text.lower()
    finally:
        await adapter.stop()


async def test_hermes_minimal_turn(tmp_path: Path) -> None:
    hermes_python = os.environ.get(
        "ONTHEROAD_HERMES_PYTHON",
        str(Path("~/.hermes/hermes-agent/venv/bin/python").expanduser()),
    )
    if not Path(hermes_python).exists():
        pytest.fail(
            f"ONTHEROAD_TEST_HERMES=1 but hermes python not found at {hermes_python}"
        )
    await _minimal_turn(tmp_path)
