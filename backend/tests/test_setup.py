from pathlib import Path
import sys

import pytest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts import create_env


def test_environment_generator_creates_random_secrets_and_never_overwrites(tmp_path, monkeypatch):
    destination = tmp_path / ".env"
    monkeypatch.setattr(create_env, "DESTINATION", destination)
    create_env.main()
    values = dict(line.split("=", 1) for line in destination.read_text(encoding="utf-8").splitlines() if "=" in line)
    assert len(values["POSTGRES_PASSWORD"]) >= 64
    assert len(values["DATABASE_RUNTIME_PASSWORD"]) >= 64
    assert len(values["AUTH_JWT_HS256_SECRET"]) >= 64
    prior = destination.read_text(encoding="utf-8")
    with pytest.raises(SystemExit):
        create_env.main()
    assert destination.read_text(encoding="utf-8") == prior


def test_compose_defines_database_migration_and_api_dependency_chain():
    compose = (REPOSITORY_ROOT / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "postgres:" in compose and "migrate:" in compose and "api:" in compose
    assert "service_healthy" in compose
    assert "service_completed_successfully" in compose
    assert "DATABASE_RUNTIME_PASSWORD" in compose
