"""Streaming reverse proxy: /preview/{session_id}/{port}/{path} → 127.0.0.1:{port}.

Auth (spec/api.md): /preview/* requires the bearer token. Browsers navigating
to a preview URL cannot set an Authorization header, so two fallbacks exist:

1. `?token=<ONTHEROAD_TOKEN>` query param — `PreviewTokenMiddleware` promotes
   it (or the cookie below) into an `Authorization: Bearer` header *before*
   the app-wide `AuthMiddleware` runs, so validation stays in one place
   (constant-time compare in ontheroad.auth — unmodified).
2. On the first authorized hit that carried a `?token=` param, the proxy sets
   an `otr_preview_token` cookie scoped to `/preview` (HttpOnly, SameSite=Lax)
   so subresource requests (css/js/img issued by the previewed page, which
   carry neither header nor query param) authenticate via the cookie.

Known limitation (documented per spec): proxied HTML is NOT rewritten. Apps
using absolute paths (`/static/app.css`) will request them relative to the
proxy root; well-behaved dev servers should honor the `X-Forwarded-Prefix`
header we set (`/preview/{sid}/{port}`), or use relative URLs. The UI also
offers a "direct :PORT" link as an escape hatch.
"""

from __future__ import annotations

from typing import Any, AsyncIterator
from urllib.parse import parse_qsl, urlencode

import httpx
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse

TOKEN_QUERY_PARAM = "token"
PREVIEW_COOKIE = "otr_preview_token"

PORT_MIN = 1024
PORT_MAX = 65535

# Hop-by-hop headers (RFC 9110 §7.6.1) plus fields the proxy owns.
_HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "proxy-connection",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}
_REQUEST_STRIP = _HOP_BY_HOP | {"host", "content-length", "authorization"}
_RESPONSE_STRIP = _HOP_BY_HOP

_ALL_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"]

router = APIRouter(tags=["preview"])


def _error(status: int, code: str, message: str, detail: dict | None = None) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": {"code": code, "message": message, "detail": detail or {}}},
    )


def port_valid(port: int) -> bool:
    return PORT_MIN <= port <= PORT_MAX


def strip_token_from_query(query: str) -> str:
    """Remove the auth `token` param before forwarding upstream (no leaks)."""
    if not query:
        return ""
    pairs = [(k, v) for k, v in parse_qsl(query, keep_blank_values=True) if k != TOKEN_QUERY_PARAM]
    return urlencode(pairs)


def strip_cookie(cookie_header: str, name: str) -> str:
    """Drop our auth cookie from a Cookie header; keep the app's own cookies."""
    parts = [p.strip() for p in cookie_header.split(";") if p.strip()]
    kept = [p for p in parts if not p.startswith(f"{name}=")]
    return "; ".join(kept)


def build_forward_headers(
    request_headers: list[tuple[str, str]], sid: str, port: int
) -> list[tuple[str, str]]:
    """Filter hop-by-hop/auth headers; set Host and X-Forwarded-Prefix."""
    out: list[tuple[str, str]] = []
    for name, value in request_headers:
        lname = name.lower()
        if lname in _REQUEST_STRIP or lname == "x-forwarded-prefix":
            continue
        if lname == "cookie":
            value = strip_cookie(value, PREVIEW_COOKIE)
            if not value:
                continue
        out.append((name, value))
    out.append(("host", f"127.0.0.1:{port}"))
    out.append(("x-forwarded-prefix", f"/preview/{sid}/{port}"))
    return out


def filter_response_headers(headers: httpx.Headers) -> list[tuple[str, str]]:
    return [(k, v) for k, v in headers.multi_items() if k.lower() not in _RESPONSE_STRIP]


async def _session_exists(request: Request, session_id: str) -> bool:
    manager = getattr(request.app.state, "session_manager", None)
    if manager is None:
        return False
    return (await manager.get_session(session_id)) is not None


def _preview_cookie_needed(request: Request) -> str | None:
    """Return the token to persist in a cookie, if this request used ?token=."""
    tok = request.query_params.get(TOKEN_QUERY_PARAM)
    if tok and request.cookies.get(PREVIEW_COOKIE) != tok:
        return tok
    return None


@router.api_route("/preview/{session_id}/{port}", methods=_ALL_METHODS)
async def preview_redirect(request: Request, session_id: str, port: str) -> Response:
    """Redirect /preview/{sid}/{port} → trailing slash, preserving the query."""
    url = f"/preview/{session_id}/{port}/"
    if request.url.query:
        url += f"?{request.url.query}"
    return RedirectResponse(url, status_code=307)


@router.api_route("/preview/{session_id}/{port}/{path:path}", methods=_ALL_METHODS)
async def preview_proxy(
    request: Request, session_id: str, port: int, path: str
) -> Response:
    if not port_valid(port):
        return _error(
            400,
            "bad_request",
            f"port must be between {PORT_MIN} and {PORT_MAX}",
            {"port": port},
        )
    if not await _session_exists(request, session_id):
        return _error(404, "not_found", f"session {session_id} not found")

    query = strip_token_from_query(request.url.query)
    target = f"http://127.0.0.1:{port}/{path}" + (f"?{query}" if query else "")
    headers = build_forward_headers(request.headers.items(), session_id, port)
    cookie_token = _preview_cookie_needed(request)

    client = httpx.AsyncClient(timeout=httpx.Timeout(30.0, read=None), follow_redirects=False)
    upstream: httpx.Response | None = None
    try:
        req = client.build_request(
            request.method, target, headers=headers, content=request.stream()
        )
        upstream = await client.send(req, stream=True)
    except httpx.ConnectError:
        await client.aclose()
        return _error(
            502,
            "adapter_unavailable",
            f"nothing is listening on 127.0.0.1:{port}",
            {"port": port},
        )
    except httpx.HTTPError as exc:
        await client.aclose()
        return _error(502, "adapter_unavailable", f"proxy error: {exc}", {"port": port})

    async def body() -> AsyncIterator[bytes]:
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await upstream.aclose()
            await client.aclose()

    response = StreamingResponse(
        body(),
        status_code=upstream.status_code,
        headers=dict_multi(filter_response_headers(upstream.headers)),
    )
    if cookie_token is not None:
        # First authorized hit via ?token= — persist so subresources work.
        response.set_cookie(
            PREVIEW_COOKIE,
            cookie_token,
            path="/preview",
            httponly=True,
            samesite="lax",
        )
    return response


def dict_multi(items: list[tuple[str, str]]) -> dict[str, str]:
    """Collapse multi-value headers (e.g. Set-Cookie) into comma-joined values.

    Starlette's StreamingResponse takes a mapping; duplicate keys are rare on
    the proxied path and comma-joining is the RFC 9110 §5.3 combination rule.
    """
    out: dict[str, str] = {}
    for k, v in items:
        out[k] = f"{out[k]}, {v}" if k in out else v
    return out


class PreviewTokenMiddleware:
    """Promote `?token=` / preview cookie into an Authorization header.

    Pure ASGI; must be added AFTER `AuthMiddleware` in `create_app()` (later
    `add_middleware` = outer = runs first), so the app-wide auth middleware —
    reused unmodified — performs the actual constant-time validation.
    Only /preview/* HTTP requests without an existing header are touched.
    """

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope.get("path", "").startswith("/preview/"):
            headers = list(scope.get("headers", []))
            if not any(name == b"authorization" for name, _ in headers):
                token = self._extract_token(scope, headers)
                if token:
                    headers.append((b"authorization", b"Bearer " + token.encode("latin-1")))
                    scope = dict(scope)
                    scope["headers"] = headers
        await self.app(scope, receive, send)

    @staticmethod
    def _extract_token(scope, headers: list[tuple[bytes, bytes]]) -> str | None:
        query = scope.get("query_string", b"").decode("latin-1")
        for key, value in parse_qsl(query, keep_blank_values=True):
            if key == TOKEN_QUERY_PARAM and value:
                return value
        for name, value in headers:
            if name == b"cookie":
                for part in value.decode("latin-1").split(";"):
                    part = part.strip()
                    if part.startswith(f"{PREVIEW_COOKIE}="):
                        tok = part[len(PREVIEW_COOKIE) + 1 :]
                        if tok:
                            return tok
        return None
