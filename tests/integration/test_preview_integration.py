"""Slice 2C/2B wiring integration: preview reverse proxy end-to-end.

Spins a throwaway local HTTP server (the "dev server" the agent would start)
and requests it THROUGH the running app at /preview/{sid}/{port}/..., using
each auth path the spec allows: Authorization header, ?token= query param,
and the cookie fallback set on the first ?token= hit. Roadmap gate: proxied
body content matches byte-for-byte.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from .conftest import ServerProc, TEST_TOKEN, free_port

HTML_BODY = b"<!doctype html><title>otr preview</title><h1>hello from the dev server</h1>"
# Binary payload including NUL/high bytes: byte-for-byte fidelity check.
BIN_BODY = bytes(range(256)) * 8


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 — http.server API
        if self.path.startswith("/data.bin"):
            body, ctype = BIN_BODY, "application/octet-stream"
        elif self.path == "/" or self.path.startswith("/index"):
            body, ctype = HTML_BODY, "text/html; charset=utf-8"
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # keep test output clean
        pass


@pytest.fixture
def dev_server():
    port = free_port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield port
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _echo_session(client: httpx.Client) -> str:
    r = client.post("/api/sessions", json={"adapter": "echo", "title": "preview"})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def test_preview_proxy_header_auth_byte_for_byte(
    server: ServerProc, client: httpx.Client, dev_server: int
):
    sid = _echo_session(client)
    r = client.get(f"/preview/{sid}/{dev_server}/")
    assert r.status_code == 200
    assert r.content == HTML_BODY  # byte-for-byte (roadmap gate)

    r = client.get(f"/preview/{sid}/{dev_server}/data.bin")
    assert r.status_code == 200
    assert r.content == BIN_BODY


def test_preview_proxy_query_token_then_cookie_fallback(
    server: ServerProc, dev_server: int, client: httpx.Client
):
    sid = _echo_session(client)
    # Browser-style client: no Authorization header, cookie jar enabled.
    with httpx.Client(base_url=server.base_url, timeout=30.0) as browser:
        # no header, no token -> 401 from the app-wide auth middleware
        r = browser.get(f"/preview/{sid}/{dev_server}/")
        assert r.status_code == 401

        # ?token= promoted into the Authorization header by the middleware
        r = browser.get(f"/preview/{sid}/{dev_server}/?token={TEST_TOKEN}")
        assert r.status_code == 200
        assert r.content == HTML_BODY
        # first ?token= hit sets the /preview-scoped cookie
        assert "otr_preview_token" in r.cookies

        # subresource-style request: cookie only (no header, no query param)
        r = browser.get(f"/preview/{sid}/{dev_server}/data.bin")
        assert r.status_code == 200
        assert r.content == BIN_BODY


def test_preview_proxy_error_paths(server: ServerProc, client: httpx.Client, dev_server: int):
    sid = _echo_session(client)
    # unknown session -> 404
    r = client.get(f"/preview/no-such-session/{dev_server}/")
    assert r.status_code == 404
    assert r.json()["error"]["code"] == "not_found"
    # out-of-range port -> 400
    r = client.get(f"/preview/{sid}/80/")
    assert r.status_code == 400
    # upstream 404 passes through the proxy untouched
    r = client.get(f"/preview/{sid}/{dev_server}/missing")
    assert r.status_code == 404
