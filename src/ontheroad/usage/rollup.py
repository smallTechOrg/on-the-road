"""usage_daily rollup logic (spec/data.md).

The rollup is a materialized view of ``events WHERE type='usage'`` bucketed by
UTC date. It is updated transactionally with each usage-event append (the
store calls :func:`apply_usage` inside its append transaction, before commit)
and is always recomputable from events via :func:`recompute` — events are the
source of truth.

usage payload shape (spec/data.md):
    {"input_tokens": N, "output_tokens": N, "total_tokens": N, "cost_usd"?: F}
Missing token fields count as 0; a missing total defaults to input + output.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import aiosqlite


def today_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def day_of(ts: str) -> str:
    """UTC day bucket for an ISO-8601 UTC timestamp (spec: days are UTC dates)."""
    return ts[:10]


def _tokens(payload: dict[str, Any]) -> tuple[int, int, int, float | None]:
    def as_int(key: str) -> int:
        value = payload.get(key)
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0

    input_tokens = as_int("input_tokens")
    output_tokens = as_int("output_tokens")
    total = payload.get("total_tokens")
    try:
        total_tokens = int(total) if total is not None else input_tokens + output_tokens
    except (TypeError, ValueError):
        total_tokens = input_tokens + output_tokens
    cost = payload.get("cost_usd")
    try:
        cost_usd = float(cost) if cost is not None else None
    except (TypeError, ValueError):
        cost_usd = None
    return input_tokens, output_tokens, total_tokens, cost_usd


async def apply_usage(
    conn: aiosqlite.Connection, day: str, payload: dict[str, Any]
) -> None:
    """Fold one usage payload into usage_daily. Does NOT commit.

    Called by the store inside the usage-event append transaction so the
    rollup update commits (or rolls back) atomically with the event row.
    """
    input_tokens, output_tokens, total_tokens, cost_usd = _tokens(payload)
    await conn.execute(
        """INSERT INTO usage_daily (day, input_tokens, output_tokens, total_tokens, cost_usd)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(day) DO UPDATE SET
               input_tokens  = input_tokens  + excluded.input_tokens,
               output_tokens = output_tokens + excluded.output_tokens,
               total_tokens  = total_tokens  + excluded.total_tokens,
               cost_usd      = CASE
                   WHEN excluded.cost_usd IS NULL THEN cost_usd
                   ELSE COALESCE(cost_usd, 0) + excluded.cost_usd
               END""",
        (day, input_tokens, output_tokens, total_tokens, cost_usd),
    )


async def recompute(conn: aiosqlite.Connection) -> None:
    """Rebuild usage_daily from scratch out of events(type='usage'). Commits."""
    await conn.execute("DELETE FROM usage_daily")
    await conn.execute(
        """INSERT INTO usage_daily (day, input_tokens, output_tokens, total_tokens, cost_usd)
           SELECT substr(ts, 1, 10),
                  SUM(COALESCE(json_extract(payload, '$.input_tokens'), 0)),
                  SUM(COALESCE(json_extract(payload, '$.output_tokens'), 0)),
                  SUM(COALESCE(json_extract(payload, '$.total_tokens'),
                        COALESCE(json_extract(payload, '$.input_tokens'), 0)
                      + COALESCE(json_extract(payload, '$.output_tokens'), 0))),
                  SUM(json_extract(payload, '$.cost_usd'))
           FROM events
           WHERE type = 'usage'
           GROUP BY substr(ts, 1, 10)"""
    )
    await conn.commit()


async def daily_totals(
    conn: aiosqlite.Connection, days: int = 30
) -> dict[str, Any]:
    """`/api/usage/daily` response body (spec/api.md Phase 2)."""
    days = max(1, min(int(days), 365))
    today = today_utc()
    first_day = (
        datetime.now(timezone.utc) - timedelta(days=days - 1)
    ).strftime("%Y-%m-%d")
    cur = await conn.execute(
        "SELECT day, input_tokens, output_tokens, total_tokens, cost_usd "
        "FROM usage_daily WHERE day >= ? ORDER BY day DESC",
        (first_day,),
    )
    rows = await cur.fetchall()
    day_dicts = [
        {
            "day": row["day"],
            "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "total_tokens": row["total_tokens"],
            "cost_usd": row["cost_usd"],
        }
        for row in rows
    ]
    today_total = next(
        (d["total_tokens"] for d in day_dicts if d["day"] == today), 0
    )
    return {"days": day_dicts, "today_total": today_total}
