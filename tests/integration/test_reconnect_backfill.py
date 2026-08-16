"""Durability core: disconnect mid-stream, reconnect with since_seq=N and
receive exactly seqs N+1..latest — value-asserted, no gaps, no dupes.

The fixture turn generates >= 200 events (patterned text -> 220+ agent_text
chunks) so a partial or duplicated replay cannot pass by accident.
"""

from __future__ import annotations

import json
import time

import httpx
from websockets.sync.client import connect as ws_connect

from .conftest import TEST_TOKEN, ServerProc
from .test_journey import assert_gap_free, wait_for_status

# 220 distinct 8-char markers -> 220 agent_text chunks (echo child chunks by 8)
FIXTURE_TEXT = "".join(f"[{i:05d}]" for i in range(220))
MIN_FIXTURE_EVENTS = 200


def test_reconnect_backfill_exact_range(server: ServerProc, client: httpx.Client):
    sid = client.post(
        "/api/sessions", json={"adapter": "echo", "title": "backfill"}
    ).json()["id"]
    r = client.post(f"/api/sessions/{sid}/message", json={"text": FIXTURE_TEXT})
    assert r.status_code == 202

    # --- phase 1: connect live, read a partial prefix, then disconnect mid-stream
    url = f"{server.ws_url}/api/sessions/{sid}/stream?token={TEST_TOKEN}&since_seq=0"
    prefix: list[dict] = []
    with ws_connect(url, open_timeout=10) as ws:
        while len(prefix) < 40:
            frame = json.loads(ws.recv(timeout=30))
            if "seq" in frame:
                prefix.append(frame)
        # hard disconnect mid-turn (context exit closes the socket)
    assert_gap_free(prefix, start_seq=1)
    n = prefix[-1]["seq"]
    assert prefix[-1]["type"] != "turn_end", "disconnect must happen mid-stream"

    # --- agent keeps working while disconnected; wait for the turn to finish
    session = wait_for_status(client, sid, "idle", timeout=60.0)
    last_seq = session["last_seq"]
    assert last_seq >= MIN_FIXTURE_EVENTS, (
        f"fixture too small: last_seq={last_seq} < {MIN_FIXTURE_EVENTS}"
    )
    assert last_seq > n

    # --- phase 2: reconnect with since_seq=N -> exactly N+1..last_seq
    url = f"{server.ws_url}/api/sessions/{sid}/stream?token={TEST_TOKEN}&since_seq={n}"
    tail: list[dict] = []
    with ws_connect(url, open_timeout=10) as ws:
        while not tail or tail[-1]["seq"] < last_seq:
            frame = json.loads(ws.recv(timeout=30))
            if "seq" in frame:
                tail.append(frame)
    assert [e["seq"] for e in tail] == list(range(n + 1, last_seq + 1))

    # --- value assertions: prefix+tail reassemble the COMPLETE transcript
    full = prefix + tail
    assert_gap_free(full, start_seq=1)
    echoed = "".join(
        e["payload"]["text"] for e in full if e["type"] == "agent_text"
    )
    assert echoed == FIXTURE_TEXT

    # and the stream frames match the persisted events value-for-value
    rest = client.get(f"/api/sessions/{sid}/events").json()
    assert rest["last_seq"] == last_seq
    assert len(rest["events"]) == last_seq  # seqs are exactly 1..last_seq
    for ws_event, db_event in zip(full, rest["events"], strict=True):
        assert ws_event["seq"] == db_event["seq"]
        assert ws_event["type"] == db_event["type"]
        assert ws_event["payload"] == db_event["payload"]


def test_events_endpoint_pagination_and_filter(server: ServerProc, client: httpx.Client):
    sid = client.post("/api/sessions", json={"adapter": "echo"}).json()["id"]
    client.post(f"/api/sessions/{sid}/message", json={"text": "paginate me please"})
    session = wait_for_status(client, sid, "idle")
    last_seq = session["last_seq"]

    # paged range query: two pages stitch to the full ordered range
    page1 = client.get(f"/api/sessions/{sid}/events?since_seq=0&limit=3").json()["events"]
    assert [e["seq"] for e in page1] == [1, 2, 3]
    page2 = client.get(f"/api/sessions/{sid}/events?since_seq=3").json()["events"]
    assert [e["seq"] for e in page2] == list(range(4, last_seq + 1))

    # type filter returns only agent_text and their concatenation is the echo
    texts = client.get(f"/api/sessions/{sid}/events?type=agent_text").json()["events"]
    assert texts and all(e["type"] == "agent_text" for e in texts)
    assert "".join(e["payload"]["text"] for e in texts) == "paginate me please"
