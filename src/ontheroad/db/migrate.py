"""Migration runner: apply numbered SQL migrations; --verify prints schema version.

Usage:
    uv run python -m ontheroad.db.migrate            # apply pending migrations
    uv run python -m ontheroad.db.migrate --verify   # apply + print current version
"""

from __future__ import annotations

import argparse
import asyncio
import re
from pathlib import Path

import aiosqlite

from ontheroad.config import get_settings

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_MIGRATION_RE = re.compile(r"^(\d{4})_.+\.sql$")


def discover_migrations() -> list[tuple[int, Path]]:
    found = []
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        m = _MIGRATION_RE.match(path.name)
        if m:
            found.append((int(m.group(1)), path))
    return found


async def apply_migrations(db_path: str) -> int:
    """Apply pending migrations; return current schema version."""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        await conn.commit()
        cur = await conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations")
        (current,) = await cur.fetchone()
        for version, path in discover_migrations():
            if version <= current:
                continue
            await conn.executescript(path.read_text())
            await conn.execute(
                "INSERT INTO schema_migrations (version, applied_at) "
                "VALUES (?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
                (version,),
            )
            await conn.commit()
            current = version
        return current


async def current_version(db_path: str) -> int:
    async with aiosqlite.connect(db_path) as conn:
        cur = await conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_migrations "
            "WHERE EXISTS (SELECT 1 FROM sqlite_master WHERE name='schema_migrations')"
        )
        (version,) = await cur.fetchone()
        return version


def main() -> None:
    parser = argparse.ArgumentParser(description="Apply On The Road DB migrations")
    parser.add_argument("--verify", action="store_true", help="print current schema version")
    parser.add_argument("--db", default=None, help="override ONTHEROAD_DB_PATH")
    args = parser.parse_args()
    db_path = args.db or get_settings().db_path
    version = asyncio.run(apply_migrations(db_path))
    if args.verify:
        print(f"schema version: {version:04d}")
    else:
        print(f"migrated {db_path} to version {version:04d}")


if __name__ == "__main__":
    main()
