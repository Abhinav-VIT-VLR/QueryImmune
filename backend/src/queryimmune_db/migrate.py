"""Apply ordered SQL migrations and optionally load deterministic demo fixtures."""

from __future__ import annotations

import argparse
import os
from uuid import uuid4

import psycopg
from psycopg import sql

from queryimmune_db import env as _env  # noqa: F401
from queryimmune_db.paths import project_root
from queryimmune_db.orm.session import create_session_factory, session_scope

ROOT = project_root()
MIGRATIONS = ROOT / "migrations"
DATABASE_ROLES = ("qi_owner", "qi_runtime", "qi_orm_admin")
SEED_ACTOR_ID = "10000000-0000-4000-8000-000000000008"


def admin_url() -> str:
    value = os.environ.get("DATABASE_ADMIN_URL")
    if not value:
        raise RuntimeError("DATABASE_ADMIN_URL is required for migrations")
    return value


def provision_database_roles() -> None:
    """Ensure migration-owned roles exist before applying any migrations.

    The migration ledger can outlive role state (for example after manual
    role removal or a partially restored database). Keep these prerequisites
    independent of whether the foundation migration is already recorded.
    """
    with psycopg.connect(admin_url(), autocommit=True, connect_timeout=5) as conn:
        for role_name in DATABASE_ROLES:
            exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)).fetchone()
            if not exists:
                conn.execute(
                    sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                        sql.Identifier(role_name)
                    )
                )
            else:
                conn.execute(
                    sql.SQL("ALTER ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                        sql.Identifier(role_name)
                    )
                )
        # Verify the roles exist before any SQL migration can GRANT to them.
        existing_roles = {
            row[0]
            for row in conn.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s)",
                (list(DATABASE_ROLES),),
            ).fetchall()
        }
        missing_roles = sorted(set(DATABASE_ROLES) - existing_roles)
        if missing_roles:
            raise RuntimeError(
                "Failed to provision required PostgreSQL privilege roles: "
                + ", ".join(missing_roles)
            )
    print("provisioned database privilege roles: " + ", ".join(DATABASE_ROLES))


def apply_migrations() -> None:
    with psycopg.connect(admin_url(), autocommit=True, connect_timeout=5) as conn:
        conn.execute("CREATE SCHEMA IF NOT EXISTS qi_migrations")
        conn.execute("""CREATE TABLE IF NOT EXISTS qi_migrations.applied (
            version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now()
        )""")
        for path in sorted(MIGRATIONS.glob("*.sql")):
            if conn.execute("SELECT 1 FROM qi_migrations.applied WHERE version = %s", (path.name,)).fetchone():
                continue
            with conn.transaction():
                conn.execute(path.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO qi_migrations.applied(version) VALUES (%s)", (path.name,))
            print(f"applied {path.name}")


def provision_runtime_role() -> None:
    """Provision LOGIN role with a secret supplied only through the environment."""
    password = os.environ.get("DATABASE_RUNTIME_PASSWORD")
    role_name = os.environ.get("DATABASE_RUNTIME_ROLE", "queryimmune_runtime")
    if not password:
        raise RuntimeError("DATABASE_RUNTIME_PASSWORD is required to provision the runtime login")
    if not role_name.replace("_", "").isalnum():
        raise RuntimeError("DATABASE_RUNTIME_ROLE must contain only letters, digits, and underscores")
    if role_name == "qi_runtime":
        raise RuntimeError("DATABASE_RUNTIME_ROLE must be a login distinct from the qi_runtime privilege role")
    with psycopg.connect(admin_url(), autocommit=True, connect_timeout=5) as conn:
        exists = conn.execute("SELECT 1 FROM pg_roles WHERE rolname = %s", (role_name,)).fetchone()
        if not exists:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION").format(sql.Identifier(role_name)))
        conn.execute(sql.SQL("ALTER ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role_name), sql.Literal(password)))
        conn.execute(sql.SQL("GRANT qi_runtime TO {}").format(sql.Identifier(role_name)))
    print(f"provisioned runtime role {role_name}")


def seed() -> None:
    with psycopg.connect(admin_url(), autocommit=True, connect_timeout=5) as conn:
        conn.execute((ROOT / "seed" / "001_synthetic_healthcare.sql").read_text(encoding="utf-8"))
    print("loaded deterministic synthetic healthcare fixtures")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("migrate", "provision-runtime", "seed", "setup"))
    command = parser.parse_args().command
    if command in ("migrate", "setup"):
        provision_database_roles()
        apply_migrations()
    elif command == "provision-runtime":
        provision_database_roles()
    if command in ("provision-runtime", "setup"):
        provision_runtime_role()
    if command in ("seed", "setup"):
        seed()


if __name__ == "__main__":
    main()
