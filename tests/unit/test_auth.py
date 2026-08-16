"""Slice 1A: bearer-token auth — constant-time compare, 401 vs 200, WS helper."""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import ontheroad.auth as auth
from ontheroad.auth import (
    WS_AUTH_CLOSE_CODE,
    AuthMiddleware,
    bearer_from_header,
    is_protected_path,
    token_matches,
    ws_token_ok,
)

TOKEN = "t" * 40


# ---------- token_matches ----------

def test_token_matches_true_and_false():
    assert token_matches(TOKEN, TOKEN) is True
    assert token_matches("wrong" * 8, TOKEN) is False
    assert token_matches(None, TOKEN) is False
    assert token_matches("", TOKEN) is False
    assert token_matches(TOKEN, "") is False


def test_token_matches_uses_constant_time_compare(monkeypatch):
    """The comparison must go through hmac.compare_digest (constant-time)."""
    calls = []
    real = auth.hmac.compare_digest

    def spy(a, b):
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    assert token_matches(TOKEN, TOKEN) is True
    assert calls == [(TOKEN.encode(), TOKEN.encode())]


# ---------- header parsing / path classification ----------

def test_bearer_from_header():
    assert bearer_from_header(f"Bearer {TOKEN}") == TOKEN
    assert bearer_from_header(f"bearer {TOKEN}") == TOKEN
    assert bearer_from_header(TOKEN) is None  # no scheme
    assert bearer_from_header("Basic abc") is None
    assert bearer_from_header("") is None
    assert bearer_from_header(None) is None


def test_is_protected_path():
    assert is_protected_path("/api/sessions")
    assert is_protected_path("/api/me")
    assert is_protected_path("/preview/abc/3000/")
    assert not is_protected_path("/healthz")
    assert not is_protected_path("/")
    assert not is_protected_path("/index.html")


# ---------- middleware 401 vs 200 ----------

@pytest.fixture()
def client():
    app = FastAPI()

    @app.get("/api/me")
    async def me():
        return {"ok": True}

    @app.get("/healthz")
    async def healthz():
        return {"status": "ok"}

    app.add_middleware(AuthMiddleware, token=TOKEN)
    return TestClient(app)


def test_missing_token_401_error_shape(client):
    resp = client.get("/api/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["error"]["code"] == "unauthorized"
    assert isinstance(body["error"]["message"], str)
    assert json.dumps(body)  # valid JSON error shape


def test_wrong_token_401(client):
    resp = client.get("/api/me", headers={"Authorization": "Bearer " + "x" * 40})
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_correct_token_200(client):
    resp = client.get("/api/me", headers={"Authorization": f"Bearer {TOKEN}"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_healthz_unauthenticated(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ---------- WS helper ----------

def test_ws_token_ok(monkeypatch):
    assert ws_token_ok(TOKEN, expected=TOKEN) is True
    assert ws_token_ok("nope", expected=TOKEN) is False
    assert ws_token_ok(None, expected=TOKEN) is False
    monkeypatch.setenv("ONTHEROAD_TOKEN", TOKEN)
    assert ws_token_ok(TOKEN) is True  # default expected comes from settings
    assert WS_AUTH_CLOSE_CODE == 4401
