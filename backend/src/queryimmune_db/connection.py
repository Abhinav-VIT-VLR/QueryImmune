"""Small transaction-safe PostgreSQL connection helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator

import psycopg
from psycopg import Connection

from queryimmune_db import env as _env  # noqa: F401


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value


@contextmanager
def transaction(*, read_only: bool = False) -> Iterator[Connection]:
    """Yield a connection in one atomic transaction; exceptions always roll back."""
    with psycopg.connect(database_url(), connect_timeout=3) as conn:
        with conn.transaction():
            conn.execute("SET LOCAL search_path = healthcare, security, pg_catalog")
            conn.execute("SET LOCAL statement_timeout = '5s'")
            conn.execute("SET LOCAL lock_timeout = '2s'")
            if read_only:
                conn.execute("SET TRANSACTION READ ONLY")
            yield conn


def database_health() -> dict[str, object]:
    """Return a minimal readiness result without exposing connection details."""
    try:
        with transaction(read_only=True) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except (psycopg.Error, RuntimeError):
        return {"status": "unavailable"}
