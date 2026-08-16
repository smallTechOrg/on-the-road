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


class RequestLoggingMiddleware:
    """Pure-ASGI middleware emitting one JSON `request` line per HTTP request."""

    def __init__(self, app, logger: logging.Logger | None = None):
        self.app = app
        self.log = logger or get_logger("ontheroad.request")

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        start = time.perf_counter()
        status_holder = {"status": 0}

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                status_holder["status"] = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_wrapper)
        finally:
            latency_ms = round((time.perf_counter() - start) * 1000, 2)
            self.log.info(
                "request",
                extra={
                    "method": scope.get("method", ""),
                    "path": scope.get("path", ""),
                    "status": status_holder["status"],
                    "latency_ms": latency_ms,
                },
            )
