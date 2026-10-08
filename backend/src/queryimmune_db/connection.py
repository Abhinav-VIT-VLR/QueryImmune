"""Small transaction-safe PostgreSQL connection helpers."""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Iterator
from uuid import UUID

import psycopg
from psycopg import Connection

from queryimmune_db import env as _env  # noqa: F401


def database_url() -> str:
    value = os.environ.get("DATABASE_URL")
    if not value:
        raise RuntimeError("DATABASE_URL is required")
    return value


@contextmanager
def transaction(
    *,
    read_only: bool = False,
    actor_context_id: UUID | str | None = None,
    allow_shared_healthcare_read: bool = False,
) -> Iterator[Connection]:
    """Yield a transaction with a trusted, transaction-local database actor context.

    The actor ID must come from the authenticated server-side Actor. The two
    custom settings are reset locally on every transaction so pooled/session
    state cannot carry an identity or shared-read mode into another request.
    """
    with psycopg.connect(database_url(), connect_timeout=3) as conn:
        with conn.transaction():
            if read_only:
                conn.execute("SET TRANSACTION READ ONLY")
            conn.execute("SET LOCAL search_path = healthcare, security, pg_catalog")
            conn.execute("SET LOCAL statement_timeout = '5s'")
            conn.execute("SET LOCAL lock_timeout = '2s'")
            actor_value = "" if actor_context_id is None else str(actor_context_id)
            conn.execute("SELECT set_config('queryimmune.actor_id', %s, true)", (actor_value,))
            shared_value = "on" if allow_shared_healthcare_read else "off"
            conn.execute("SELECT set_config('queryimmune.shared_healthcare_read', %s, true)", (shared_value,))
            yield conn


def database_health() -> dict[str, object]:
    """Return a minimal readiness result without exposing connection details."""
    try:
        with transaction(read_only=True) as conn:
            conn.execute("SELECT 1").fetchone()
        return {"status": "ok"}
    except (psycopg.Error, RuntimeError):
        return {"status": "unavailable"}
