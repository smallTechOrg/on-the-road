"""Unit tests for the PTY terminal WebSocket endpoint."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ontheroad.main import create_app


@pytest.fixture
def app():
    return create_app()


@pytest.fixture
def client(app):
    return TestClient(app)


class TestTermAuth:
    """Auth and session validation on the term WebSocket."""

    def test_rejects_missing_token(self, client):
        with client.websocket_connect("/api/sessions/fake-id/term") as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert frame["error"]["code"] == "unauthorized"

    def test_rejects_bad_token(self, client):
        with client.websocket_connect(
            "/api/sessions/fake-id/term?token=wrong"
        ) as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert frame["error"]["code"] == "unauthorized"

    def test_rejects_nonexistent_session(self, client):
        """With a valid token but no session, we get not_found."""
        import os

        token = os.environ.get("ONTHEROAD_TOKEN", "")
        if len(token) < 32:
            pytest.skip("ONTHEROAD_TOKEN not set or too short")

        with client.websocket_connect(
            f"/api/sessions/nonexistent/term?token={token}"
        ) as ws:
            frame = ws.receive_json()
            assert frame["type"] == "error"
            assert frame["error"]["code"] in ("not_found", "internal")
