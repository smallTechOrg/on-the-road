"""Unit tests for the preview reverse proxy helpers + token middleware."""

from __future__ import annotations

import httpx
import pytest

from ontheroad.preview.proxy import (
    PREVIEW_COOKIE,
    PreviewTokenMiddleware,
    build_forward_headers,
    filter_response_headers,
    port_valid,
    strip_cookie,
    strip_token_from_query,
)

# ---------------------------------------------------------------- port bounds


@pytest.mark.parametrize("port,ok", [(1024, True), (8000, True), (65535, True),
                                     (1023, False), (0, False), (65536, False)])
def test_port_valid_bounds(port, ok):
    assert port_valid(port) is ok


# ------------------------------------------------------------- query handling


def test_strip_token_from_query_removes_only_token():
    assert strip_token_from_query("a=1&token=sekrit&b=two") == "a=1&b=two"


def test_strip_token_from_query_empty_and_token_only():
    assert strip_token_from_query("") == ""
    assert strip_token_from_query("token=sekrit") == ""


def test_strip_token_keeps_blank_values():
    assert strip_token_from_query("q=&token=x") == "q="


# ------------------------------------------------------------ header handling


def test_forward_headers_strip_hop_by_hop_and_set_host_prefix():
    inbound = [
        ("Host", "phone.example:8100"),
        ("Connection", "keep-alive"),
        ("Keep-Alive", "timeout=5"),
        ("Transfer-Encoding", "chunked"),
        ("Authorization", "Bearer secret"),
        ("Accept", "text/html"),
        ("X-Forwarded-Prefix", "/spoofed"),
    ]
    out = build_forward_headers(inbound, "sid123", 5173)
    lowered = {k.lower(): v for k, v in out}
    assert lowered["host"] == "127.0.0.1:5173"
    assert lowered["x-forwarded-prefix"] == "/preview/sid123/5173"
    assert lowered["accept"] == "text/html"
    for bad in ("connection", "keep-alive", "transfer-encoding", "authorization"):
        assert bad not in lowered


def test_forward_headers_strip_preview_cookie_only():
    out = build_forward_headers(
        [("Cookie", f"app=1; {PREVIEW_COOKIE}=tok; theme=dark")], "s", 3000
    )
    cookies = [v for k, v in out if k.lower() == "cookie"]
    assert cookies == ["app=1; theme=dark"]


def test_forward_headers_drop_cookie_header_when_only_ours():
    out = build_forward_headers([("Cookie", f"{PREVIEW_COOKIE}=tok")], "s", 3000)
    assert not [k for k, _ in out if k.lower() == "cookie"]


def test_strip_cookie_helper():
    assert strip_cookie(f"{PREVIEW_COOKIE}=x; a=b", PREVIEW_COOKIE) == "a=b"
    assert strip_cookie("a=b", PREVIEW_COOKIE) == "a=b"


def test_filter_response_headers_strips_hop_by_hop():
    headers = httpx.Headers(
        [
            ("content-type", "image/png"),
            ("transfer-encoding", "chunked"),
            ("connection", "close"),
            ("x-custom", "yes"),
        ]
    )
    out = dict(filter_response_headers(headers))
    assert out == {"content-type": "image/png", "x-custom": "yes"}


# --------------------------------------------------- PreviewTokenMiddleware


class _CaptureApp:
    def __init__(self):
        self.scope = None

    async def __call__(self, scope, receive, send):
        self.scope = scope


def _scope(path="/preview/s/8000/", query=b"", headers=None):
    return {
        "type": "http",
        "path": path,
        "query_string": query,
        "headers": headers or [],
    }


async def _run(scope):
    inner = _CaptureApp()
    await PreviewTokenMiddleware(inner)(scope, None, None)
    return dict(inner.scope["headers"])


async def test_middleware_promotes_query_token():
    headers = await _run(_scope(query=b"token=tok-123&x=1"))
    assert headers[b"authorization"] == b"Bearer tok-123"


async def test_middleware_promotes_cookie_token():
    headers = await _run(
        _scope(headers=[(b"cookie", f"a=b; {PREVIEW_COOKIE}=cookie-tok".encode())])
    )
    assert headers[b"authorization"] == b"Bearer cookie-tok"


async def test_middleware_prefers_existing_authorization_header():
    headers = await _run(
        _scope(query=b"token=other", headers=[(b"authorization", b"Bearer real")])
    )
    assert headers[b"authorization"] == b"Bearer real"


async def test_middleware_ignores_non_preview_paths():
    headers = await _run(_scope(path="/api/sessions", query=b"token=tok"))
    assert b"authorization" not in headers


async def test_middleware_no_token_leaves_scope_untouched():
    headers = await _run(_scope())
    assert b"authorization" not in headers
