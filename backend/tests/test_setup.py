from pathlib import Path
import sys
from uuid import uuid4

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import create_env


def test_environment_generator_creates_random_secrets_and_never_overwrites(monkeypatch):
    destination = REPOSITORY_ROOT / f".env-generator-test-{uuid4().hex}"
    monkeypatch.setattr(create_env, "DESTINATION", destination)
    try:
        create_env.main()
        values = dict(line.split("=", 1) for line in destination.read_text(encoding="utf-8").splitlines() if "=" in line)
        assert len(values["POSTGRES_PASSWORD"]) >= 64
        assert len(values["DATABASE_RUNTIME_PASSWORD"]) >= 64
        assert len(values["DATABASE_ORM_ADMIN_PASSWORD"]) >= 64
        assert len(values["AUTH_JWT_HS256_SECRET"]) >= 64
        prior = destination.read_text(encoding="utf-8")
        with pytest.raises(SystemExit):
            create_env.main()
        assert destination.read_text(encoding="utf-8") == prior
    finally:
        destination.unlink(missing_ok=True)


def test_compose_defines_database_migration_and_api_dependency_chain():
    compose = (REPOSITORY_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:" in compose and "migrate:" in compose and "api:" in compose
    assert "service_healthy" in compose
    assert "service_completed_successfully" in compose
    assert "DATABASE_RUNTIME_PASSWORD" in compose
    assert "DATABASE_ORM_ADMIN_URL" in compose
    assert "DATABASE_ORM_ADMIN_URL: postgresql+psycopg://queryimmune_orm_admin:" in compose
    assert "http://127.0.0.1:8000/health/ready" in compose
    assert "json.load(response).get('status') == 'ok'" in compose


def test_setup_bootstraps_privilege_roles_before_migrations(monkeypatch):
    from queryimmune_db import migrate

    calls = []
    monkeypatch.setattr(migrate, "provision_database_roles", lambda: calls.append("roles"))
    monkeypatch.setattr(migrate, "apply_migrations", lambda: calls.append("migrations"))
    monkeypatch.setattr(migrate, "provision_runtime_role", lambda: calls.append("runtime"))
    monkeypatch.setattr(migrate, "provision_orm_admin_role", lambda: calls.append("orm-admin"))
    monkeypatch.setattr(migrate, "seed", lambda: calls.append("seed"))
    monkeypatch.setattr(sys, "argv", ["queryimmune_db.migrate", "setup"])

    migrate.main()

    assert calls == ["roles", "migrations", "runtime", "orm-admin", "seed"]


def test_runtime_login_inherits_the_canonical_privilege_role():
    from queryimmune_db import migrate

    migrate_source = (REPOSITORY_ROOT / "backend" / "src" / "queryimmune_db" / "migrate.py").read_text(encoding="utf-8")
    foundation = (REPOSITORY_ROOT / "backend" / "migrations" / "001_database_foundation.sql").read_text(encoding="utf-8")
    compose = (REPOSITORY_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'DATABASE_ROLES = ("qi_owner", "qi_runtime", "qi_orm_admin")' in migrate_source
    assert 'os.environ.get("DATABASE_RUNTIME_ROLE", "queryimmune_runtime")' in migrate_source
    assert 'GRANT qi_runtime TO {}' in migrate_source
    assert "CREATE ROLE qi_runtime NOLOGIN" in foundation
    assert "DATABASE_URL: postgresql://queryimmune_runtime:" in compose
    assert "DATABASE_ORM_ADMIN_URL: postgresql+psycopg://queryimmune_orm_admin:" in compose
    assert migrate.ROOT == REPOSITORY_ROOT / "backend"
    assert migrate.MIGRATIONS.is_dir()
    assert (migrate.ROOT / "seed").is_dir()


class _FakeQueryResult:
    def __init__(self, row=None, rows=()):
        self.row = row
        self.rows = list(rows)

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class _FakeRoleConnection:
    def __init__(self, roles):
        self.roles = roles
        self.statements = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def execute(self, query, params=None):
        statement = query if isinstance(query, str) else query.as_string()
        self.statements.append(statement)
        if statement.lstrip().lower().startswith("select rolcanlogin"):
            return _FakeQueryResult(row=self.roles.get(params[0]))
        if statement.lstrip().startswith("SELECT granted.rolname"):
            return _FakeQueryResult(rows=())
        return _FakeQueryResult()


def _safe_role_attributes(*, login=False, inherit=True, superuser=False):
    return (login, inherit, superuser, False, False, False)


def _assert_alter_role_has_no_privileged_attributes(connection):
    import re

    forbidden = re.compile(r"\b(?:NO)?(?:SUPERUSER|CREATEDB|CREATEROLE|REPLICATION)\b", re.I)
    alterations = [
        statement for statement in connection.statements
        if statement.lstrip().upper().startswith("ALTER ROLE")
    ]
    assert all(forbidden.search(statement) is None for statement in alterations)


def test_existing_privilege_roles_are_verified_without_alter_role(monkeypatch):
    from queryimmune_db import migrate

    connection = _FakeRoleConnection({
        name: _safe_role_attributes() for name in migrate.DATABASE_ROLES
    })
    monkeypatch.setattr(migrate, "admin_url", lambda: "postgresql://unused")
    monkeypatch.setattr(migrate.psycopg, "connect", lambda *args, **kwargs: connection)

    migrate.provision_database_roles()

    assert not any(
        statement.lstrip().upper().startswith("ALTER ROLE")
        for statement in connection.statements
    )
    assert sum(statement == migrate.ROLE_ATTRIBUTES_QUERY for statement in connection.statements) == len(migrate.DATABASE_ROLES)


def test_existing_unsafe_privilege_role_fails_with_diagnostic(monkeypatch):
    from queryimmune_db import migrate

    roles = {name: _safe_role_attributes() for name in migrate.DATABASE_ROLES}
    roles["qi_runtime"] = _safe_role_attributes(superuser=True)
    connection = _FakeRoleConnection(roles)
    monkeypatch.setattr(migrate, "admin_url", lambda: "postgresql://unused")
    monkeypatch.setattr(migrate.psycopg, "connect", lambda *args, **kwargs: connection)

    with pytest.raises(RuntimeError, match="qi_runtime.*SUPERUSER=True"):
        migrate.provision_database_roles()

    assert not any(
        statement.lstrip().upper().startswith("ALTER ROLE")
        for statement in connection.statements
    )


@pytest.mark.parametrize(
    ("provision", "role_name", "password_env"),
    [
        ("provision_runtime_role", "queryimmune_runtime", "DATABASE_RUNTIME_PASSWORD"),
        ("provision_orm_admin_role", "queryimmune_orm_admin", "DATABASE_ORM_ADMIN_PASSWORD"),
    ],
)
def test_existing_login_roles_use_only_login_inherit_and_password_alterations(
    monkeypatch, provision, role_name, password_env
):
    from queryimmune_db import migrate

    connection = _FakeRoleConnection({role_name: _safe_role_attributes(login=True)})
    monkeypatch.setattr(migrate, "admin_url", lambda: "postgresql://unused")
    monkeypatch.setattr(migrate.psycopg, "connect", lambda *args, **kwargs: connection)
    monkeypatch.setenv(password_env, "test-only-role-password")

    getattr(migrate, provision)()

    alterations = [
        statement for statement in connection.statements
        if statement.lstrip().upper().startswith("ALTER ROLE")
    ]
    assert len(alterations) == 1
    assert "LOGIN" in alterations[0].upper()
    assert "INHERIT" in alterations[0].upper()
    assert "PASSWORD 'test-only-role-password'" in alterations[0]
    _assert_alter_role_has_no_privileged_attributes(connection)
