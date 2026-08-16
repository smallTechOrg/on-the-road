"""Integration fixtures: boot the REAL server as a uvicorn subprocess.

Real SQLite temp file (production engine), real migrations (they run in the
app lifespan), real EchoAdapter subprocess. Server stdout/stderr is captured
to a log file so tests can assert structured JSON log lines.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TEST_TOKEN = "integration-test-token-0123456789abcdef"  # >= 32 chars
assert len(TEST_TOKEN) >= 32


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class ServerProc:
    """Handle for a live uvicorn subprocess of the real app."""

    def __init__(self, db_path: Path, log_path: Path, port: int):
        self.db_path = db_path
        self.log_path = log_path
        self.port = port
        self.proc: subprocess.Popen | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def ws_url(self) -> str:
        return f"ws://127.0.0.1:{self.port}"

    def auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {TEST_TOKEN}"}

    def start(self, timeout: float = 30.0) -> None:
        env = os.environ.copy()
        env["ONTHEROAD_TOKEN"] = TEST_TOKEN
        env["ONTHEROAD_DB_PATH"] = str(self.db_path)
        env["ONTHEROAD_PORT"] = str(self.port)
        log_file = open(self.log_path, "ab")
        self.proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "ontheroad.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
            ],
            cwd=REPO_ROOT,
            env=env,
            stdout=log_file,
            stderr=subprocess.STDOUT,
        )
        log_file.close()  # child holds its own fd
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                raise RuntimeError(
                    f"server exited early rc={self.proc.returncode}:\n"
                    + self.log_path.read_text()[-4000:]
                )
            try:
                r = httpx.get(f"{self.base_url}/healthz", timeout=1.0)
                if r.status_code == 200 and r.json()["status"] == "ok":
                    return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
        raise RuntimeError("server did not become healthy in time")

    def stop(self, timeout: float = 10.0) -> None:
        if self.proc is None or self.proc.poll() is not None:
            self.proc = None
            return
        self.proc.send_signal(signal.SIGTERM)
        try:
            self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout)
        self.proc = None

    def kill(self) -> None:
        """Hard-kill (simulates a crash/restart scenario)."""
        if self.proc is not None and self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait(timeout=10)
        self.proc = None


@pytest.fixture
def server(tmp_path: Path):
    """A running real server on a fresh temp SQLite DB. Restartable."""
    srv = ServerProc(
        db_path=tmp_path / "ontheroad-test.db",
        log_path=tmp_path / "server.log",
        port=free_port(),
    )
    srv.start()
    try:
        yield srv
    finally:
        srv.stop()


@pytest.fixture
def client(server: ServerProc):
    with httpx.Client(
        base_url=server.base_url, headers=server.auth_headers(), timeout=30.0
    ) as c:
        yield c
