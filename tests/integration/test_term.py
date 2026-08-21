"""Integration test: PTY terminal WebSocket end-to-end."""

from __future__ import annotations

import json

import pytest
import websockets

from .conftest import TEST_TOKEN


@pytest.mark.timeout(30)
def test_term_echo(server):
    """Spawn a terminal, run echo, verify output."""
    import httpx

    # 1. Create a session
    with httpx.Client(
        base_url=server.base_url, headers=server.auth_headers(), timeout=10.0
    ) as client:
        resp = client.post("/api/sessions", json={"adapter": "echo"})
        assert resp.status_code == 201, resp.text
        session_id = resp.json()["id"]

    # 2. Connect to the terminal WebSocket and run echo
    import asyncio

    async def _run():
        url = (
            f"{server.ws_url}/api/sessions/{session_id}/term"
            f"?token={TEST_TOKEN}"
        )
        async with websockets.connect(url) as ws:
            # Send echo command
            await ws.send(
                json.dumps({"type": "input", "data": "echo otr-term-ok\n"})
            )

            # Read output until we see the marker
            accumulated = ""
            import time

            deadline = time.monotonic() + 5.0
            while time.monotonic() < deadline:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue
                frame = json.loads(raw)
                if frame.get("type") == "output":
                    accumulated += frame["data"]
                    if "otr-term-ok" in accumulated:
                        return accumulated

            raise AssertionError(
                f"Did not find 'otr-term-ok' in terminal output. Got: {accumulated!r}"
            )

    result = asyncio.run(_run())
    assert "otr-term-ok" in result
