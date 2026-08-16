"""Bearer-token auth (skeleton — Phase 1 slice 1A wires the middleware)."""

from __future__ import annotations

import hmac


def token_matches(presented: str | None, expected: str) -> bool:
    """Constant-time bearer token comparison."""
    if not presented or not expected:
        return False
    return hmac.compare_digest(presented.encode(), expected.encode())
