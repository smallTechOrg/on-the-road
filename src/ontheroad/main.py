"""App factory: routes, static mount, auth + logging middleware, lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager
from importlib import metadata
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ontheroad.auth import AuthMiddleware
from ontheroad.config import get_settings
from ontheroad.db import Database
from ontheroad.db.migrate import apply_migrations
from ontheroad.logging import RequestLoggingMiddleware, get_logger, setup_logging

STATIC_DIR = Path(__file__).parent / "static"

log = get_logger("ontheroad")


def _version() -> str:
    try:
        return metadata.version("ontheroad")
    except metadata.PackageNotFoundError:
        return "0.0.0-dev"


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    settings.validate()  # refuse to serve without a strong ONTHEROAD_TOKEN
    schema_version = await apply_migrations(settings.db_path)
    db = await Database(settings.db_path).open()
    app.state.settings = settings
    app.state.db = db

    # SessionManager wiring point (slice 1C owns ontheroad.sessions).
    manager = None
    try:
        from ontheroad.sessions.manager import SessionManager  # noqa: PLC0415
        from ontheroad.sessions.store import EventStore  # noqa: PLC0415
    except ImportError:
        log.warning("session_manager_unavailable")
    else:
        manager = SessionManager(EventStore(db.conn))
        await manager.startup()
    app.state.session_manager = manager

    log.info("startup", extra={"schema_version": schema_version, "port": settings.port})
    try:
        yield
    finally:
        if manager is not None:
            await manager.shutdown()
        await db.close()
        log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="On The Road", lifespan=lifespan)

    version = _version()

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok", "version": version}

    @app.get("/api/me")
    async def me() -> dict:
        # Auth middleware gates /api/*; reaching here means the token is valid.
        return {"ok": True}

    # API routers (slice 1C owns ontheroad.api). Imported here — not at module
    # top level — so slice-1A modules stay importable/testable before 1C lands.
    try:
        from ontheroad.api import sessions as api_sessions  # noqa: PLC0415
        from ontheroad.api import stream as api_stream  # noqa: PLC0415
    except ImportError:
        log.warning("api_routers_unavailable")
    else:
        # Routers declare full spec/api.md paths (e.g. /api/sessions).
        app.include_router(api_sessions.router)
        app.include_router(api_stream.router)

    # Phase-2 routers (guarded like the above so sibling slices land in any
    # order; once Phase 2 is complete these imports MUST all succeed — a
    # warning here on a finished build is a bug, not noise).
    preview_available = False
    try:
        from ontheroad.api import usage as api_usage  # noqa: PLC0415
    except ImportError:
        log.warning("usage_router_unavailable")
    else:
        app.include_router(api_usage.router)

    try:
        from ontheroad.preview import proxy as preview_proxy  # noqa: PLC0415
    except ImportError:
        log.warning("preview_router_unavailable")
    else:
        app.include_router(preview_proxy.router)
        preview_available = True

    try:
        from ontheroad.api import approvals as api_approvals  # noqa: PLC0415
    except ImportError:
        log.warning("approvals_router_unavailable")
    else:
        app.include_router(api_approvals.router)

    try:
        from ontheroad.term import pty_ws as term_ws  # noqa: PLC0415
    except ImportError:
        log.warning("term_router_unavailable")
    else:
        app.include_router(term_ws.router)

    try:
        from ontheroad.preview import screenshot as screenshot_mod  # noqa: PLC0415
    except ImportError:
        log.warning("screenshot_router_unavailable")
    else:
        app.include_router(screenshot_mod.router)

    try:
        from ontheroad.files import router as files_router  # noqa: PLC0415
    except ImportError:
        log.warning("files_router_unavailable")
    else:
        app.include_router(files_router.router)

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")

    # Middleware (outermost first at request time = LAST added): request
    # logging wraps everything so 401s/5xx are logged + audited too; the
    # preview token middleware runs BEFORE (outside) auth so it can promote
    # ?token=/cookie into an Authorization header; auth gates /api/* and
    # /preview/*.
    app.add_middleware(AuthMiddleware)
    if preview_available:
        from ontheroad.preview.proxy import PreviewTokenMiddleware  # noqa: PLC0415

        app.add_middleware(PreviewTokenMiddleware)
    app.add_middleware(RequestLoggingMiddleware)
    return app


app = create_app()
