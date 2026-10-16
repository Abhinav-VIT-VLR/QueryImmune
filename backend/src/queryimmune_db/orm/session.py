"""Isolated SQLAlchemy engine/session support for administrative repositories."""
from __future__ import annotations

import os
from contextlib import contextmanager
from functools import lru_cache
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError, SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from queryimmune_db import env as _env  # noqa: F401


_ALLOWED_ORM_USERS = {"queryimmune_admin", "queryimmune_orm_admin"}


def _validated_url(value: str):
    try:
        url = make_url(value)
    except ArgumentError as exc:
        raise ValueError("DATABASE_ORM_ADMIN_URL is not a valid SQLAlchemy URL") from exc
    if url.drivername not in {"postgresql", "postgresql+psycopg"}:
        raise ValueError("SQLAlchemy administrative access requires PostgreSQL with Psycopg 3")
    if url.username not in _ALLOWED_ORM_USERS:
        raise ValueError("SQLAlchemy must use a dedicated administrative application login")
    return url.set(drivername="postgresql+psycopg")


def create_orm_engine(url: str | None = None) -> Engine:
    """Create an engine without connecting; production URLs come from the environment.

    The administrative application role must be separately provisioned with
    only the grants required by its specific operations. This function does not
    grant permissions or accept the gateway/bootstrap identities.
    """
    if url is None:
        url = os.environ.get("DATABASE_ORM_ADMIN_URL", "").strip()
        if not url:
            raise RuntimeError("DATABASE_ORM_ADMIN_URL is required for ORM operations")
    return create_engine(
        _validated_url(url),
        connect_args={"connect_timeout": 3},
        pool_pre_ping=True,
    )


@lru_cache(maxsize=1)
def _environment_engine() -> Engine:
    return create_orm_engine()


def create_session_factory(engine: Engine | None = None) -> sessionmaker[Session]:
    """Construct a session factory for an explicit engine or the ORM engine."""
    return sessionmaker(bind=engine or _environment_engine(), expire_on_commit=False)


def orm_database_available() -> bool:
    """Check ORM database connectivity without exposing driver errors."""
    try:
        with _environment_engine().connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except (SQLAlchemyError, RuntimeError, ValueError):
        return False


@contextmanager
def session_scope(
    factory: sessionmaker[Session] | None = None,
    *,
    actor_id: str | None = None,
    request_id: str | None = None,
) -> Iterator[Session]:
    """Commit or roll back one transaction with optional transaction-local context."""
    if (actor_id is None) != (request_id is None):
        raise ValueError("actor_id and request_id must be supplied together")
    session = (factory or create_session_factory())()
    try:
        with session.begin():
            if actor_id is not None and request_id is not None:
                session.execute(
                    text(
                        "SELECT set_config('queryimmune.actor_id', :actor_id, true), "
                        "set_config('queryimmune.request_id', :request_id, true)"
                    ),
                    {"actor_id": actor_id, "request_id": request_id},
                )
                session.execute(text("SET LOCAL statement_timeout = '5s'"))
                session.execute(text("SET LOCAL lock_timeout = '2s'"))
            yield session
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
