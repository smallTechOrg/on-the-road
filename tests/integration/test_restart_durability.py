"""Transcript survives a server-process restart; idle sessions auto-attach.

Kills the uvicorn process (SIGKILL — a crash, not a graceful stop), restarts
on the same SQLite file, and asserts the persisted event stream is intact
value-for-value and the session is resumable.
"""

from __future__ import annotations

import httpx

from .conftest import ServerProc
from .test_journey import wait_for_status


def test_transcript_survives_server_restart(server: ServerProc, client: httpx.Client):
    sid = client.post(
        "/api/sessions", json={"adapter": "echo", "title": "restart-me"}
    ).json()["id"]
    text = "written before the crash"
    client.post(f"/api/sessions/{sid}/message", json={"text": text})
    session = wait_for_status(client, sid, "idle")
    before = client.get(f"/api/sessions/{sid}/events").json()
    assert before["last_seq"] == session["last_seq"] > 0

    # --- crash + restart on the same DB
    server.kill()
    server.start()

    with httpx.Client(
        base_url=server.base_url, headers=server.auth_headers(), timeout=30.0
    ) as c2:
        # session survived, marked idle (its subprocess died with the server)
        session2 = c2.get(f"/api/sessions/{sid}").json()
        assert session2["status"] == "idle"
        assert session2["title"] == "restart-me"
        assert session2["last_seq"] == before["last_seq"]

        # transcript intact: identical events, value for value
        after = c2.get(f"/api/sessions/{sid}/events").json()
        assert after == before
        echoed = "".join(
            e["payload"]["text"]
            for e in after["events"]
            if e["type"] == "agent_text"
        )
        assert echoed == text


def test_message_to_idle_session_auto_attaches(server: ServerProc, client: httpx.Client):
    sid = client.post("/api/sessions", json={"adapter": "echo"}).json()["id"]
    client.post(f"/api/sessions/{sid}/message", json={"text": "first turn"})
    wait_for_status(client, sid, "idle")
    seq_before = client.get(f"/api/sessions/{sid}").json()["last_seq"]

    # restart -> the adapter subprocess is gone, session idle
    server.kill()
    server.start()

    with httpx.Client(
        base_url=server.base_url, headers=server.auth_headers(), timeout=30.0
    ) as c2:
        assert c2.get(f"/api/sessions/{sid}").json()["status"] == "idle"

        # messaging the idle session transparently reattaches (spec: attach
        # restarts the adapter; send_message on idle auto-attaches)
        text = "second turn after restart"
        r = c2.post(f"/api/sessions/{sid}/message", json={"text": text})
        assert r.status_code == 202, r.text
        assert r.json()["seq"] > seq_before

        session = wait_for_status(c2, sid, "idle")
        events = c2.get(
            f"/api/sessions/{sid}/events?since_seq={seq_before}"
        ).json()["events"]
        echoed = "".join(
            e["payload"]["text"] for e in events if e["type"] == "agent_text"
        )
        assert echoed == text
        # seq numbering continued monotonically across the restart — no reset
        assert [e["seq"] for e in events] == list(
            range(seq_before + 1, session["last_seq"] + 1)
        )
