"""Env-driven settings — single source of config truth."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_hermes_python() -> str:
    primary = Path("~/.hermes/hermes-agent/venv/bin/python").expanduser()
    fallback = Path("~/.hermes/hermes-agent/.venv/bin/python").expanduser()
    if primary.exists():
        return str(primary)
    if fallback.exists():
        return str(fallback)
    return str(primary)


@dataclass(frozen=True)
class Settings:
    token: str = field(default_factory=lambda: os.environ.get("ONTHEROAD_TOKEN", ""))
    db_path: str = field(
        default_factory=lambda: os.environ.get("ONTHEROAD_DB_PATH", "./data/ontheroad.db")
    )
    port: int = field(default_factory=lambda: int(os.environ.get("ONTHEROAD_PORT", "8100")))
    hermes_python: str = field(
        default_factory=lambda: os.environ.get(
            "ONTHEROAD_HERMES_PYTHON", _default_hermes_python()
        )
    )
    test_hermes: bool = field(
        default_factory=lambda: os.environ.get("ONTHEROAD_TEST_HERMES", "0") == "1"
    )


def get_settings() -> Settings:
    return Settings()
