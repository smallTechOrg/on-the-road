"""Integration tests for the screenshot endpoint."""

from __future__ import annotations


def test_screenshot_endpoint_exists(client, server):
    """The screenshot endpoint exists and returns a sensible error."""
    # Create a session first.
    resp = client.post("/api/sessions", json={"adapter": "echo"})
    assert resp.status_code == 201
    session_id = resp.json()["id"]

    # POST to screenshot with a port nothing is listening on.
    resp = client.post(
        f"/api/sessions/{session_id}/screenshot",
        json={"port": 9999},
    )

    # 501 = Playwright not installed (expected in CI/production image).
    # 502 = Playwright installed but nothing listening on port 9999.
    assert resp.status_code in (501, 502), f"Unexpected status: {resp.status_code}"

    body = resp.json()
    if resp.status_code == 501:
        assert body["error"]["code"] == "not_implemented"
        assert "playwright" in body["error"]["message"].lower()
    else:
        assert body["error"]["code"] == "upstream_error"


def test_screenshot_session_not_found(client, server):
    """Screenshot for a nonexistent session returns 404."""
    resp = client.post(
        "/api/sessions/nonexistent/screenshot",
        json={"port": 3000},
    )
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "not_found"


def test_screenshot_validation(client, server):
    """Invalid request body returns 422."""
    # Create a session.
    resp = client.post("/api/sessions", json={"adapter": "echo"})
    session_id = resp.json()["id"]

    # Missing required field.
    resp = client.post(
        f"/api/sessions/{session_id}/screenshot",
        json={},
    )
    assert resp.status_code == 422
