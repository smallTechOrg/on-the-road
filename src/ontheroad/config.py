"""Env-driven settings — single source of config truth."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

MIN_TOKEN_LENGTH = 32


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
    default_workdir: str = field(
        default_factory=lambda: os.environ.get("ONTHEROAD_DEFAULT_WORKDIR", os.getcwd())
    )
    hermes_python: str = field(
        default_factory=lambda: os.environ.get(
            "ONTHEROAD_HERMES_PYTHON", _default_hermes_python()
        )
    )
    test_hermes: bool = field(
        default_factory=lambda: os.environ.get("ONTHEROAD_TEST_HERMES", "0") == "1"
    )
    # Seconds to wait for an interactive answer to an agent permission request
    # before auto-approving the safest allow option so the jam keeps flowing.
    approval_timeout: float = field(
        default_factory=lambda: float(os.environ.get("ONTHEROAD_APPROVAL_TIMEOUT", "120"))
    )

    def validate(self) -> None:
        """Refuse to serve with a missing or weak token (spec: token-auth)."""
        if not self.token or len(self.token) < MIN_TOKEN_LENGTH:
            raise RuntimeError(
                "ONTHEROAD_TOKEN must be set to a string of at least "
                f"{MIN_TOKEN_LENGTH} characters. Set it in .env and restart."
            )


def get_settings() -> Settings:
    return Settings()
