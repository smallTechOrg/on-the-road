"""Unit tests for the screenshot endpoint."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import httpx
import pytest

AUTH = {"Authorization": "Bearer test-token-0123456789abcdef01234567"}


@pytest.fixture(autouse=True)
def _env(tmp_path):
    os.environ["ONTHEROAD_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["ONTHEROAD_TOKEN"] = "test-token-0123456789abcdef01234567"


def _make_client(*, with_manager=False):
    from ontheroad.main import create_app

    app = create_app()
    if with_manager:
        from unittest.mock import AsyncMock
        mgr = MagicMock()
        mgr.get_session = AsyncMock(return_value=None)
        app.state.session_manager = mgr
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(transport=transport, base_url="http://test"), app


@pytest.mark.anyio
async def test_missing_port_returns_422(_env):
    client, _ = _make_client()
    async with client:
        resp = await client.post(
            "/api/sessions/fake-session/screenshot", json={}, headers=AUTH
        )
        assert resp.status_code == 422


@pytest.mark.anyio
async def test_invalid_port_returns_422(_env):
    client, _ = _make_client()
    async with client:
        resp = await client.post(
            "/api/sessions/fake-session/screenshot", json={"port": -1}, headers=AUTH
        )
        assert resp.status_code == 422


@pytest.mark.anyio
async def test_port_zero_returns_422(_env):
    client, _ = _make_client()
    async with client:
        resp = await client.post(
            "/api/sessions/fake-session/screenshot", json={"port": 0}, headers=AUTH
        )
        assert resp.status_code == 422


@pytest.mark.anyio
async def test_port_too_large_returns_422(_env):
    client, _ = _make_client()
    async with client:
        resp = await client.post(
            "/api/sessions/fake-session/screenshot", json={"port": 99999}, headers=AUTH
        )
        assert resp.status_code == 422


@pytest.mark.anyio
async def test_session_not_found_returns_404(_env):
    client, app = _make_client(with_manager=True)
    async with client:
        resp = await client.post(
            "/api/sessions/nonexistent-session/screenshot",
            json={"port": 3000},
            headers=AUTH,
        )
        assert resp.status_code == 404
        body = resp.json()
        assert body["error"]["code"] == "not_found"


@pytest.mark.anyio
async def test_playwright_not_installed_returns_501(_env):
    """When Playwright import fails, endpoint returns 501."""
    client, app = _make_client(with_manager=True)
    # Make manager.get_session succeed for our fake session.
    from unittest.mock import AsyncMock
    app.state.session_manager.get_session = AsyncMock(return_value={"id": "test-session"})

    from fastapi.responses import JSONResponse

    mock_501 = JSONResponse(
        status_code=501,
        content={
            "error": {
                "code": "not_implemented",
                "message": "Screenshot requires Playwright.",
                "detail": {"reason": "playwright_not_installed"},
            }
        },
    )

    async with client:
        with patch(
            "ontheroad.preview.screenshot._check_playwright_available",
            return_value=mock_501,
        ):
            resp = await client.post(
                "/api/sessions/test-session/screenshot",
                json={"port": 3000},
                headers=AUTH,
            )
            assert resp.status_code == 501
            body = resp.json()
            assert body["error"]["code"] == "not_implemented"
            assert "playwright_not_installed" in str(body["error"]["detail"])


@pytest.mark.anyio
async def test_no_session_manager_returns_503(_env):
    """When session manager is not wired, returns 503."""
    client, _ = _make_client(with_manager=False)
    async with client:
        resp = await client.post(
            "/api/sessions/any/screenshot",
            json={"port": 3000},
            headers=AUTH,
        )
        assert resp.status_code == 503
