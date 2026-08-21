"""On-demand headless screenshot endpoint (Phase 3, slice 3D).

Returns 501 if Playwright is not installed — it is a dev/optional dependency only.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field

from ontheroad.logging import get_logger

log = get_logger("ontheroad.preview.screenshot")

router = APIRouter(tags=["preview"])


def _api_error(
    status: int, code: str, message: str, detail: dict[str, Any] | None = None
) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail or {}}},
    )


class ScreenshotRequest(BaseModel):
    port: int = Field(..., gt=0, le=65535)
    path: str = Field(default="/")
    width: int = Field(default=1280, gt=0, le=7680)
    height: int = Field(default=720, gt=0, le=4320)
    full_page: bool = Field(default=False)


def _check_playwright_available() -> JSONResponse | None:
    """Return a 501 JSONResponse if Playwright is not importable, else None."""
    try:
        from playwright.async_api import async_playwright  # noqa: F401, PLC0415
    except ImportError:
        return _api_error(
            501,
            "not_implemented",
            "Screenshot requires Playwright. Install with: pip install playwright && playwright install chromium",
            {"reason": "playwright_not_installed"},
        )
    return None


async def _take_screenshot(req: ScreenshotRequest) -> bytes:
    from playwright.async_api import async_playwright  # noqa: PLC0415

    url = f"http://127.0.0.1:{req.port}{req.path}"

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            page = await browser.new_page(
                viewport={"width": req.width, "height": req.height},
            )
            await page.goto(url, wait_until="load")
            data = await page.screenshot(full_page=req.full_page, type="png")
        finally:
            await browser.close()

    return data


@router.post("/api/sessions/{session_id}/screenshot")
async def screenshot(session_id: str, body: ScreenshotRequest, request: Request):
    # Validate session exists.
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        return _api_error(503, "service_unavailable", "Session manager not available")

    session = await manager.get_session(session_id)
    if session is None:
        return _api_error(404, "not_found", f"Session {session_id} not found")

    # Check Playwright availability.
    pw_err = _check_playwright_available()
    if pw_err is not None:
        return pw_err

    # Take the screenshot.
    try:
        data = await asyncio.wait_for(_take_screenshot(body), timeout=30.0)
    except asyncio.TimeoutError:
        return _api_error(
            502,
            "upstream_error",
            "Screenshot timed out after 30 seconds",
            {"reason": "timeout"},
        )
    except Exception as exc:
        msg = str(exc)
        # Browser executable not installed.
        if "Executable doesn't exist" in msg or "executable doesn't exist" in msg.lower():
            return _api_error(
                501,
                "not_implemented",
                "Chromium browser not installed. Run: playwright install chromium",
                {"reason": "browser_not_installed"},
            )
        log.warning("screenshot_error", extra={"error": msg, "session_id": session_id})
        return _api_error(
            502,
            "upstream_error",
            f"Screenshot failed: {msg}",
            {"reason": "navigation_error"},
        )

    return Response(content=data, media_type="image/png")
