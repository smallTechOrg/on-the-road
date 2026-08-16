"""App factory: routes, static mount, lifespan (runs migrations)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from ontheroad.config import get_settings
from ontheroad.db.migrate import apply_migrations
from ontheroad.logging import get_logger, setup_logging

STATIC_DIR = Path(__file__).parent / "static"

log = get_logger("ontheroad")


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging()
    settings = get_settings()
    version = await apply_migrations(settings.db_path)
    log.info("startup", extra={"schema_version": version, "port": settings.port})
    yield
    log.info("shutdown")


def create_app() -> FastAPI:
    app = FastAPI(title="On The Road", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
    return app


app = create_app()
