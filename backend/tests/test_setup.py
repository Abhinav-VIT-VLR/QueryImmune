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


def test_setup_bootstraps_privilege_roles_before_migrations(monkeypatch):
    from queryimmune_db import migrate

    calls = []
    monkeypatch.setattr(migrate, "provision_database_roles", lambda: calls.append("roles"))
    monkeypatch.setattr(migrate, "apply_migrations", lambda: calls.append("migrations"))
    monkeypatch.setattr(migrate, "provision_runtime_role", lambda: calls.append("runtime"))
    monkeypatch.setattr(migrate, "seed", lambda: calls.append("seed"))
    monkeypatch.setattr(sys, "argv", ["queryimmune_db.migrate", "setup"])

    migrate.main()

    assert calls == ["roles", "migrations", "runtime", "seed"]


def test_runtime_login_inherits_the_canonical_privilege_role():
    from queryimmune_db import migrate

    migrate_source = (REPOSITORY_ROOT / "backend" / "src" / "queryimmune_db" / "migrate.py").read_text(encoding="utf-8")
    foundation = (REPOSITORY_ROOT / "backend" / "migrations" / "001_database_foundation.sql").read_text(encoding="utf-8")
    compose = (REPOSITORY_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")

    assert 'DATABASE_ROLES = ("qi_owner", "qi_runtime")' in migrate_source
    assert 'os.environ.get("DATABASE_RUNTIME_ROLE", "queryimmune_runtime")' in migrate_source
    assert 'GRANT qi_runtime TO {}' in migrate_source
    assert "CREATE ROLE qi_runtime NOLOGIN" in foundation
    assert "DATABASE_URL: postgresql://queryimmune_runtime:" in compose
    assert migrate.ROOT == REPOSITORY_ROOT / "backend"
    assert migrate.MIGRATIONS.is_dir()
    assert (migrate.ROOT / "seed").is_dir()
