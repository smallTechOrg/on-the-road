"""Scaffold smoke: app boots, /healthz ok, static placeholder served."""

import os

from fastapi.testclient import TestClient


def _client(tmp_path):
    os.environ["ONTHEROAD_DB_PATH"] = str(tmp_path / "test.db")
    from ontheroad.main import create_app

    return TestClient(create_app())


def test_healthz_ok(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


def test_index_placeholder_served(tmp_path):
    with _client(tmp_path) as client:
        resp = client.get("/")
        assert resp.status_code == 200
        assert "On The Road" in resp.text
