"""Slice 1A: Settings env parsing + startup token validation."""

import pytest

from ontheroad.config import MIN_TOKEN_LENGTH, Settings, get_settings

TOKEN_OK = "a" * MIN_TOKEN_LENGTH


def test_defaults(monkeypatch):
    for var in (
        "ONTHEROAD_TOKEN",
        "ONTHEROAD_DB_PATH",
        "ONTHEROAD_PORT",
        "ONTHEROAD_DEFAULT_WORKDIR",
        "ONTHEROAD_TEST_HERMES",
    ):
        monkeypatch.delenv(var, raising=False)
    s = Settings()
    assert s.token == ""
    assert s.db_path == "./data/ontheroad.db"
    assert s.port == 8100
    assert s.test_hermes is False
    assert s.default_workdir  # falls back to cwd


def test_env_overrides(monkeypatch, tmp_path):
    monkeypatch.setenv("ONTHEROAD_TOKEN", TOKEN_OK)
    monkeypatch.setenv("ONTHEROAD_DB_PATH", str(tmp_path / "x.db"))
    monkeypatch.setenv("ONTHEROAD_PORT", "9000")
    monkeypatch.setenv("ONTHEROAD_DEFAULT_WORKDIR", str(tmp_path))
    monkeypatch.setenv("ONTHEROAD_HERMES_PYTHON", "/opt/hermes/python")
    monkeypatch.setenv("ONTHEROAD_TEST_HERMES", "1")
    s = get_settings()
    assert s.token == TOKEN_OK
    assert s.db_path == str(tmp_path / "x.db")
    assert s.port == 9000
    assert s.default_workdir == str(tmp_path)
    assert s.hermes_python == "/opt/hermes/python"
    assert s.test_hermes is True


def test_validate_rejects_missing_token(monkeypatch):
    monkeypatch.delenv("ONTHEROAD_TOKEN", raising=False)
    with pytest.raises(RuntimeError, match="ONTHEROAD_TOKEN"):
        Settings().validate()


def test_validate_rejects_short_token(monkeypatch):
    monkeypatch.setenv("ONTHEROAD_TOKEN", "a" * (MIN_TOKEN_LENGTH - 1))
    with pytest.raises(RuntimeError, match="32"):
        Settings().validate()


def test_validate_accepts_strong_token(monkeypatch):
    monkeypatch.setenv("ONTHEROAD_TOKEN", TOKEN_OK)
    Settings().validate()  # no raise
