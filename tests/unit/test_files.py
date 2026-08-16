"""Unit tests for the files router (Phase 3, slice 3C)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from ontheroad.files.router import _is_path_safe, _parse_diff_stats, router


@pytest.fixture
def app_no_session():
    """App with files router but no valid sessions."""
    app = FastAPI()
    app.include_router(router)

    class FakeManager:
        async def get_session(self, session_id: str):
            return None

    app.state.session_manager = FakeManager()
    return app


@pytest.fixture
def client_no_session(app_no_session):
    return TestClient(app_no_session)


class TestPathSafety:
    def test_safe_path(self):
        assert _is_path_safe("src/main.py") is True

    def test_dotdot_rejected(self):
        assert _is_path_safe("../etc/passwd") is False

    def test_mid_dotdot_rejected(self):
        assert _is_path_safe("src/../../etc/passwd") is False

    def test_single_dot_ok(self):
        assert _is_path_safe("./src/main.py") is True


class TestDiffStatsParsing:
    def test_basic_diff(self):
        diff = (
            "diff --git a/foo.py b/foo.py\n"
            "--- a/foo.py\n"
            "+++ b/foo.py\n"
            "@@ -1,3 +1,4 @@\n"
            " line1\n"
            "-old\n"
            "+new\n"
            "+added\n"
        )
        result = _parse_diff_stats(diff)
        assert result["files_changed"] == ["foo.py"]
        assert result["stats"]["insertions"] == 2
        assert result["stats"]["deletions"] == 1

    def test_empty_diff(self):
        result = _parse_diff_stats("")
        assert result["files_changed"] == []
        assert result["stats"]["insertions"] == 0
        assert result["stats"]["deletions"] == 0

    def test_multiple_files(self):
        diff = (
            "diff --git a/a.py b/a.py\n"
            "+added\n"
            "diff --git a/b.py b/b.py\n"
            "-removed\n"
        )
        result = _parse_diff_stats(diff)
        assert result["files_changed"] == ["a.py", "b.py"]


class TestEndpoints404:
    def test_diff_unknown_session(self, client_no_session):
        r = client_no_session.get("/api/sessions/unknown/files/diff")
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "session_not_found"

    def test_read_unknown_session(self, client_no_session):
        r = client_no_session.get("/api/sessions/unknown/files/read/foo.py")
        assert r.status_code == 404

    def test_tree_unknown_session(self, client_no_session):
        r = client_no_session.get("/api/sessions/unknown/files/tree")
        assert r.status_code == 404

    def test_diff_file_unknown_session(self, client_no_session):
        r = client_no_session.get("/api/sessions/unknown/files/diff/foo.py")
        assert r.status_code == 404


class TestPathTraversal:
    """Path traversal is blocked by _is_path_safe (tested above).

    HTTP clients normalise '..' in URLs before they reach the endpoint,
    so we test the guard function directly rather than via HTTP.
    """

    def test_is_path_safe_blocks_traversal(self):
        assert _is_path_safe("../etc/passwd") is False
        assert _is_path_safe("foo/../../etc/passwd") is False
        assert _is_path_safe("normal/path.py") is True
