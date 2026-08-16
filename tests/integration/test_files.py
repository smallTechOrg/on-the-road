"""Integration tests for files endpoints (Phase 3, slice 3C).

Requires a running server (the ``server``/``client`` fixtures from conftest).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from .conftest import TEST_TOKEN


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temp git repo with one committed file, then modify it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=repo, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=repo, check=True, capture_output=True,
    )
    hello = repo / "hello.txt"
    hello.write_text("hello world\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=repo, check=True, capture_output=True,
    )
    # Modify without committing so git diff shows changes
    hello.write_text("hello world\nmodified line\n")
    return repo


def test_diff_endpoint(client, server, git_repo):
    # Create a session with the git repo as workdir
    r = client.post("/api/sessions", json={
        "adapter": "echo",
        "title": "diff-test",
        "workdir": str(git_repo),
    })
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # Fetch diff
    r = client.get(f"/api/sessions/{sid}/files/diff")
    assert r.status_code == 200
    data = r.json()
    assert "hello.txt" in data["diff"]
    assert "hello.txt" in data["files_changed"]
    assert data["stats"]["insertions"] >= 1


def test_read_file(client, server, git_repo):
    r = client.post("/api/sessions", json={
        "adapter": "echo",
        "title": "read-test",
        "workdir": str(git_repo),
    })
    assert r.status_code == 201
    sid = r.json()["id"]

    r = client.get(f"/api/sessions/{sid}/files/read/hello.txt")
    assert r.status_code == 200
    data = r.json()
    assert data["path"] == "hello.txt"
    assert "modified line" in data["content"]
    assert data["lines"] >= 2


def test_tree_endpoint(client, server, git_repo):
    r = client.post("/api/sessions", json={
        "adapter": "echo",
        "title": "tree-test",
        "workdir": str(git_repo),
    })
    assert r.status_code == 201
    sid = r.json()["id"]

    r = client.get(f"/api/sessions/{sid}/files/tree")
    assert r.status_code == 200
    data = r.json()
    assert "hello.txt" in data["files"]
    assert data["count"] >= 1
