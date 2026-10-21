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
ROLE_ATTRIBUTES_QUERY = """SELECT rolcanlogin, rolinherit, rolsuper, rolcreatedb,
                                  rolcreaterole, rolreplication
                           FROM pg_roles WHERE rolname = %s"""


def admin_url() -> str:
    value = os.environ.get("DATABASE_ADMIN_URL")
    if not value:
        raise RuntimeError("DATABASE_ADMIN_URL is required for migrations")
    return value


def _verify_restricted_role(
    conn: psycopg.Connection,
    role_name: str,
    *,
    attributes: tuple[bool, ...] | None = None,
    login: bool | None = None,
    inherit: bool | None = None,
) -> None:
    if attributes is None:
        attributes = conn.execute(ROLE_ATTRIBUTES_QUERY, (role_name,)).fetchone()
    if attributes is None:
        raise RuntimeError(f"Required PostgreSQL role {role_name!r} does not exist")

    actual = dict(zip(
        ("LOGIN", "INHERIT", "SUPERUSER", "CREATEDB", "CREATEROLE", "REPLICATION"),
        attributes,
        strict=True,
    ))
    expected = {"SUPERUSER": False, "CREATEDB": False, "CREATEROLE": False, "REPLICATION": False}
    if login is not None:
        expected["LOGIN"] = login
    if inherit is not None:
        expected["INHERIT"] = inherit
    mismatches = [
        f"{name}={actual[name]} (expected {value})"
        for name, value in expected.items()
        if actual[name] is not value
    ]
    if mismatches:
        raise RuntimeError(
            f"PostgreSQL role {role_name!r} has unsafe or incorrect attributes: "
            + ", ".join(mismatches)
        )


def provision_database_roles() -> None:
    """Ensure migration-owned roles exist before applying any migrations.

    The migration ledger can outlive role state (for example after manual
    role removal or a partially restored database). Keep these prerequisites
    independent of whether the foundation migration is already recorded.
    """
    with psycopg.connect(admin_url(), autocommit=True, connect_timeout=5) as conn:
        for role_name in DATABASE_ROLES:
            attributes = conn.execute(ROLE_ATTRIBUTES_QUERY, (role_name,)).fetchone()
            if attributes is None:
                conn.execute(
                    sql.SQL("CREATE ROLE {} NOLOGIN NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                        sql.Identifier(role_name)
                    )
                )
            _verify_restricted_role(conn, role_name, attributes=attributes, login=False)
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
        attributes = conn.execute(ROLE_ATTRIBUTES_QUERY, (role_name,)).fetchone()
        if attributes is None:
            conn.execute(sql.SQL("CREATE ROLE {} LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION").format(sql.Identifier(role_name)))
        else:
            _verify_restricted_role(conn, role_name, attributes=attributes)
        conn.execute(
            sql.SQL("ALTER ROLE {} LOGIN INHERIT PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(password)
            )
        )
        _verify_restricted_role(conn, role_name, login=True, inherit=True)
        conn.execute(sql.SQL("GRANT qi_runtime TO {}").format(sql.Identifier(role_name)))
    print(f"provisioned runtime role {role_name}")


def provision_orm_admin_role() -> None:
    """Provision a distinct login for the narrowly scoped appointment ORM."""
    password = os.environ.get("DATABASE_ORM_ADMIN_PASSWORD")
    if not password:
        raise RuntimeError("DATABASE_ORM_ADMIN_PASSWORD is required to provision the ORM admin login")
    role_name = "queryimmune_orm_admin"
    with psycopg.connect(admin_url(), autocommit=True, connect_timeout=5) as conn:
        attributes = conn.execute(ROLE_ATTRIBUTES_QUERY, (role_name,)).fetchone()
        if attributes is None:
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN NOCREATEDB NOCREATEROLE NOREPLICATION").format(
                    sql.Identifier(role_name)
                )
            )
        else:
            _verify_restricted_role(conn, role_name, attributes=attributes)
        conn.execute(
            sql.SQL("ALTER ROLE {} WITH LOGIN INHERIT PASSWORD {}").format(
                sql.Identifier(role_name), sql.Literal(password)
            )
        )
        _verify_restricted_role(conn, role_name, login=True, inherit=True)
        memberships = conn.execute(
            """SELECT granted.rolname
               FROM pg_auth_members membership
               JOIN pg_roles granted ON granted.oid = membership.roleid
               WHERE membership.member = (SELECT oid FROM pg_roles WHERE rolname = %s)
                 AND granted.rolname <> 'qi_orm_admin'""",
            (role_name,),
        ).fetchall()
        for (membership,) in memberships:
            conn.execute(
                sql.SQL("REVOKE {} FROM {}").format(sql.Identifier(membership), sql.Identifier(role_name))
            )
        for object_type in ("TABLES", "SEQUENCES", "FUNCTIONS"):
            conn.execute(
                sql.SQL("REVOKE ALL PRIVILEGES ON ALL {} IN SCHEMA healthcare, security FROM {}").format(
                    sql.SQL(object_type), sql.Identifier(role_name)
                )
            )
        conn.execute(
            sql.SQL("REVOKE ALL ON SCHEMA healthcare, security FROM {}").format(sql.Identifier(role_name))
        )
        conn.execute(sql.SQL("GRANT qi_orm_admin TO {}").format(sql.Identifier(role_name)))
    print(f"provisioned ORM admin role {role_name}")


def _execute_seed(seed_sql: str) -> None:
    from sqlalchemy import create_engine
    from sqlalchemy.engine import make_url

    engine = create_engine(
        make_url(admin_url()).set(drivername="postgresql+psycopg"),
        connect_args={"connect_timeout": 5},
    )
    try:
        with session_scope(
            create_session_factory(engine),
            actor_id=SEED_ACTOR_ID,
            request_id=str(uuid4()),
        ) as session:
            session.connection().exec_driver_sql(seed_sql)
    finally:
        engine.dispose()


def seed() -> None:
    seed_sql = (ROOT / "seed" / "001_synthetic_healthcare.sql").read_text(encoding="utf-8")
    _execute_seed(seed_sql)
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
    if command == "setup":
        provision_orm_admin_role()
    if command in ("seed", "setup"):
        seed()


if __name__ == "__main__":
    main()
