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


def _integration_conftest():
    """Load tests/integration/conftest.py by path (tests/ is not a package)."""
    import importlib.util

    path = Path(__file__).resolve().parents[1] / "integration" / "conftest.py"
    spec = importlib.util.spec_from_file_location("otr_integration_conftest", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_hermes_usage_persisted_and_daily_total(tmp_path: Path) -> None:
    """Phase 2 gate: at least one persisted usage event with token count > 0,
    and /api/usage/daily returns that exact summed value."""
    import time

    import httpx

    ic = _integration_conftest()
    srv = ic.ServerProc(
        db_path=tmp_path / "live.db",
        log_path=tmp_path / "server.log",
        port=ic.free_port(),
    )
    srv.start()
    try:
        with httpx.Client(
            base_url=srv.base_url, headers=srv.auth_headers(), timeout=60.0
        ) as client:
            r = client.post(
                "/api/sessions", json={"adapter": "hermes", "title": "live-usage"}
            )
            assert r.status_code == 201, r.text
            sid = r.json()["id"]
            r = client.post(
                f"/api/sessions/{sid}/message",
                json={"text": "Reply with exactly the single word: pong"},
            )
            assert r.status_code == 202, r.text

            # wait for at least one persisted usage event with tokens > 0
            usage_events: list[dict] = []
            deadline = time.monotonic() + TURN_TIMEOUT
            while time.monotonic() < deadline:
                usage_events = client.get(
                    f"/api/sessions/{sid}/events", params={"type": "usage"}
                ).json()["events"]
                if usage_events:
                    break
                time.sleep(1.0)
            assert usage_events, "no persisted usage event within the turn timeout"

            def total_of(ev: dict) -> int:
                p = ev["payload"]
                total = p.get("total_tokens")
                if total is None:
                    total = (p.get("input_tokens") or 0) + (p.get("output_tokens") or 0)
                return int(total)

            summed = sum(total_of(e) for e in usage_events)
            assert summed > 0, f"usage events carry no tokens: {usage_events}"

            body = client.get("/api/usage/daily").json()
            assert body["today_total"] == summed, (
                f"/api/usage/daily today_total={body['today_total']} != "
                f"summed usage events {summed}"
            )
    finally:
        srv.stop()


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
