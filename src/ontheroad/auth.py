"""Bearer-token auth: constant-time compare, HTTP middleware, WS token helper.

REST: `Authorization: Bearer <token>` on every /api/* and /preview/* route.
WebSocket: the stream endpoint (slice 1C) calls `ws_token_ok()` with the
`?token=` query param and closes with code 4401 on failure.

/healthz and static files are unauthenticated (the PWA shell contains no data).
"""

from __future__ import annotations

import hmac
import json

from ontheroad.config import get_settings

WS_AUTH_CLOSE_CODE = 4401

_PROTECTED_PREFIXES = ("/api/", "/preview/")

UNAUTHORIZED_BODY = {
    "error": {
        "code": "unauthorized",
        "message": "Missing or invalid bearer token.",
        "detail": {},
    }
}


def token_matches(presented: str | None, expected: str) -> bool:
    """Constant-time bearer token comparison. Never log either value."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())


def is_protected_path(path: str) -> bool:
    return path.startswith(_PROTECTED_PREFIXES) or path in ("/api", "/preview")


def bearer_from_header(authorization: str | None) -> str | None:
    """Extract the token from an `Authorization: Bearer <token>` header value."""
    if not authorization:
        return None
    scheme, _, credentials = authorization.partition(" ")
    if scheme.lower() != "bearer" or not credentials:
        return None
    return credentials.strip()


def ws_token_ok(token: str | None, expected: str | None = None) -> bool:
    """Validate a WebSocket `?token=` query param (used by slice 1C).

    Callers close the socket with code `WS_AUTH_CLOSE_CODE` (4401) on False.
    """
    if expected is None:
        expected = get_settings().token
    return token_matches(token, expected)


class AuthMiddleware:
    """Pure-ASGI middleware gating /api/* and /preview/* HTTP requests.

    WebSocket scopes are passed through: WS auth happens in the endpoint via
    `ws_token_ok` so it can close with 4401 (middleware can't send a proper
    close code before accept in a uniform way across servers).
    """

    def __init__(self, app, token: str | None = None):
        self.app = app
        self._token = token

    @property
    def token(self) -> str:
        if self._token is not None:
            return self._token
        return get_settings().token

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_protected_path(scope.get("path", "")):
            await self.app(scope, receive, send)
            return

        authorization = None
        for name, value in scope.get("headers", []):
            if name == b"authorization":
                authorization = value.decode("latin-1")
                break

        if token_matches(bearer_from_header(authorization), self.token):
            await self.app(scope, receive, send)
            return

        body = json.dumps(UNAUTHORIZED_BODY).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
