"""Structured JSON logging to stdout + request logging middleware.

Every log line is one JSON object with at minimum `ts`, `level`, `event`;
requests add `method, path, status, latency_ms` (spec/architecture.md
Conventions). No print(), no secrets/token values in logs.
"""

from __future__ import annotations

import json
import logging
import sys
import time

_RESERVED = set(
    logging.LogRecord("", 0, "", 0, "", (), None).__dict__.keys()
) | {"message", "asctime"}

SUMMARY_LIMIT = 200


def truncate_summary(text: str, limit: int = SUMMARY_LIMIT) -> str:
    """Truncate input/output summaries to the conventions' 200-char cap."""
    if len(text) <= limit:
        return text
    return text[:limit]


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        line: dict = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname.lower(),
            "event": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED:
                line[key] = value
        if record.exc_info:
            line["error"] = self.formatException(record.exc_info)
        return json.dumps(line, default=str)


def setup_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# Cap for the response-body excerpt persisted with a 5xx audit row.
ERROR_DETAIL_LIMIT = 2000


def _utcnow_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _extract_error_code(body: bytes) -> str | None:
    """Pull the machine code out of a spec error body, if the body is one."""
    try:
        parsed = json.loads(body)
        code = parsed.get("error", {}).get("code")
        return code if isinstance(code, str) else None
    except (ValueError, AttributeError):
        return None


class RequestLoggingMiddleware:
    """Pure-ASGI middleware emitting one JSON `request` line per HTTP request.

    Additionally audits every 5xx response and every unhandled exception into
    the `request_errors` table (migration 0002) via `app.state.db` — the
    user-visible fix for 503s missing from `/api/errors` analytics. The audit
    write is best-effort: any failure is logged and never breaks the response.
    4xx responses are NOT recorded.
    """

    def __init__(self, app, logger: logging.Logger | None = None):
        self.app = app
        self.log = logger or get_logger("ontheroad.request")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"status": 0}
        body_parts: list[bytes] = []
        body_len = 0

        async def send_wrapper(message):
            nonlocal body_len
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            elif (
                message["type"] == "http.response.body"
                and status_holder["status"] >= 500
                and body_len < ERROR_DETAIL_LIMIT
            ):
                chunk = message.get("body", b"")
                if chunk:
                    body_parts.append(chunk[: ERROR_DETAIL_LIMIT - body_len])
                    body_len += len(body_parts[-1])
            await send(message)

        exc: BaseException | None = None
        try:
            await self.app(scope, receive, send_wrapper)
        except BaseException as e:  # noqa: BLE001 — audited then re-raised
            exc = e
            raise
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            status = status_holder["status"]
            if exc is not None and status < 500:
                # Unhandled exception before any response started: the outer
                # ServerErrorMiddleware will answer 500.
                status = 500
            self.log.info(
                "request",
                extra={
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status,
                    "latency_ms": latency_ms,
                },
            )
            if status >= 500:
                await self._audit(scope, status, b"".join(body_parts), exc)

    async def _audit(self, scope, status: int, body: bytes, exc: BaseException | None) -> None:
        """Persist one request_errors row. Never raises."""
        try:
            app = scope.get("app")
            db = getattr(getattr(app, "state", None), "db", None)
            conn = getattr(db, "conn", None)
            if conn is None:
                return
            if exc is not None:
                error_code = _extract_error_code(body) or "internal"
                detail = repr(exc)[:ERROR_DETAIL_LIMIT]
            else:
                error_code = _extract_error_code(body)
                detail = body.decode("utf-8", errors="replace")[:ERROR_DETAIL_LIMIT]
            await conn.execute(
                "INSERT INTO request_errors (ts, method, path, status, error_code, detail)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (
                    _utcnow_iso(),
                    scope.get("method", ""),
                    scope.get("path", ""),
                    status,
                    error_code,
                    detail,
                ),
            )
            await conn.commit()
        except Exception:  # noqa: BLE001 — audit must never break the response
            self.log.warning("request_error_audit_failed", exc_info=True)
