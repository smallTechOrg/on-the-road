"""E2E smoke: headless Playwright against a LIVE server this script starts.

Journey (spec/roadmap.md Phase 1 gate): enter token -> create an "Echo (dev)"
session -> send "hello on the road" -> assert the echoed content renders in
the transcript DOM -> reload the page -> assert the transcript persists.

Run: uv run python tests/e2e/smoke.py
Exits nonzero on any failure; always kills the server it started.
"""

from __future__ import annotations

import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import httpx
from playwright.sync_api import expect, sync_playwright

REPO_ROOT = Path(__file__).resolve().parents[2]
TOKEN = "e2e-smoke-test-token-0123456789abcdef"  # >= 32 chars
MESSAGE = "hello on the road"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_server(db_path: str, port: int) -> subprocess.Popen:
    env = os.environ.copy()
    env["ONTHEROAD_TOKEN"] = TOKEN
    env["ONTHEROAD_DB_PATH"] = db_path
    env["ONTHEROAD_PORT"] = str(port)
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "ontheroad.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
        ],
        cwd=REPO_ROOT,
        env=env,
    )
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early rc={proc.returncode}")
        try:
            if httpx.get(f"http://127.0.0.1:{port}/healthz", timeout=1.0).status_code == 200:
                return proc
        except httpx.HTTPError:
            pass
        time.sleep(0.1)
    raise RuntimeError("server did not become healthy")


def wait_for_agent_echo(page, timeout_ms: int = 30_000) -> None:
    page.wait_for_function(
        """(msg) => {
            const norm = (s) => s.replace(/\\s+/g, ' ').trim();
            const parts = [...document.querySelectorAll('#transcript .evt-agent')]
                .map((el) => el.textContent).join(' ');
            // chunk boundaries may add/trim whitespace via markdown rendering
            return norm(parts).replace(/ /g, '').includes(norm(msg).replace(/ /g, ''));
        }""",
        arg=MESSAGE,
        timeout=timeout_ms,
    )


def run_journey(base_url: str) -> None:
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.goto(base_url)

            # --- token screen: paste token, connect
            expect(page.locator("#screen-token")).to_be_visible()
            page.fill("#token-input", TOKEN)
            page.click("#token-form button[type=submit]")

            # --- home: create an "Echo (dev)" session
            expect(page.locator("#screen-home")).to_be_visible()
            page.click("#fab-new")
            page.select_option("#ns-adapter", "echo")
            page.fill("#ns-title", "smoke")
            page.click("#ns-create")

            # --- chat: send the message, watch the echo stream into the DOM
            expect(page.locator("#screen-chat")).to_be_visible()
            page.fill("#composer-input", MESSAGE)
            page.click("#send-btn")
            # the AGENT's echoed reply streams in as .evt-agent chunk elements;
            # their joined text must contain the exact message (not the user's
            # own .evt-user bubble — that would pass before any echo arrives).
            wait_for_agent_echo(page)

            # --- reload: token persists, transcript persists
            page.reload()
            expect(page.locator("#screen-home")).to_be_visible(timeout=15_000)
            page.click(".session-card")
            expect(page.locator("#screen-chat")).to_be_visible()
            wait_for_agent_echo(page)
            print("SMOKE PASS: transcript rendered and survived a reload")
        finally:
            browser.close()


def main() -> int:
    port = free_port()
    with tempfile.TemporaryDirectory(prefix="otr-smoke-") as tmp:
        proc = start_server(os.path.join(tmp, "smoke.db"), port)
        try:
            run_journey(f"http://127.0.0.1:{port}")
            return 0
        finally:
            # ALWAYS kill the server, pass or fail.
            proc.send_signal(signal.SIGTERM)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=10)


if __name__ == "__main__":
    sys.exit(main())
