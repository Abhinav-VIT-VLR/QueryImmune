from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from queryimmune_db.api import auth
from queryimmune_db.api.app import app
from queryimmune_db.api.config import Settings
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository


@pytest.fixture(autouse=True)
def clear_login_throttle():
    with auth._login_failure_lock:
        auth._login_failures.clear()
    yield
    with auth._login_failure_lock:
        auth._login_failures.clear()


def configured_login(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_HS256_SECRET", "s" * 64)
    monkeypatch.setenv("AUTH_JWT_ISSUER", "queryimmune-auth")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "queryimmune-api")
    monkeypatch.setenv("AUTH_LOGIN_USERNAME", "professor-demo")
    monkeypatch.setenv("AUTH_LOGIN_PASSWORD_HASH", auth.hash_login_password("correct horse battery staple"))
    monkeypatch.setenv("AUTH_LOGIN_SUBJECT", "demo:dr-ada")


def test_login_issues_short_lived_token_for_active_database_identity(monkeypatch):
    configured_login(monkeypatch)
    expected = Actor("user-id", "demo:dr-ada", "Dr Ada Example", frozenset({"doctor"}))
    monkeypatch.setattr(HealthcareRepository, "actor_by_subject", lambda self, subject: expected)

    response = TestClient(app).post("/api/v1/auth/login", json={
        "username": "professor-demo", "password": "correct horse battery staple",
    })

    assert response.status_code == 200
    payload = response.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == auth.ACCESS_TOKEN_TTL_SECONDS
    assert auth.Hs256Authenticator(Settings.from_env()).subject_from_token(payload["access_token"]) == expected.subject


def test_login_rejects_invalid_credentials_without_database_lookup(monkeypatch):
    configured_login(monkeypatch)
    monkeypatch.setattr(
        HealthcareRepository,
        "actor_by_subject",
        lambda *args: pytest.fail("database identity must not be queried for bad credentials"),
    )

    response = TestClient(app).post("/api/v1/auth/login", json={
        "username": "professor-demo", "password": "wrong password value",
    })

    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid username or password"


def test_password_hash_is_salted_and_verified():
    encoded = auth.hash_login_password("correct horse battery staple")
    assert encoded != auth.hash_login_password("correct horse battery staple")
    assert auth.verify_login_password("correct horse battery staple", encoded)
    assert not auth.verify_login_password("incorrect password", encoded)
    assert not auth.verify_login_password("password", "malformed")


def test_issued_token_expires_and_verifier_rejects_it(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_HS256_SECRET", "s" * 64)
    settings = Settings.from_env()
    token = auth.issue_access_token("demo:dr-ada", settings, now=1_000)
    monkeypatch.setattr(auth.time, "time", lambda: 1_000 + auth.ACCESS_TOKEN_TTL_SECONDS)

    with pytest.raises(ValueError, match="invalid bearer token"):
        auth.Hs256Authenticator(settings).subject_from_token(token)


def test_verifier_rejects_bad_signature_issuer_and_audience(monkeypatch):
    monkeypatch.setenv("AUTH_JWT_HS256_SECRET", "s" * 64)
    settings = Settings.from_env()
    token = auth.issue_access_token("demo:dr-ada", settings)
    verifier = auth.Hs256Authenticator(settings)
    tampered = token[:-1] + ("A" if token[-1] != "A" else "B")

    with pytest.raises(ValueError):
        verifier.subject_from_token(tampered)

    monkeypatch.setenv("AUTH_JWT_ISSUER", "unexpected-issuer")
    with pytest.raises(ValueError):
        auth.Hs256Authenticator(Settings.from_env()).subject_from_token(token)

    monkeypatch.setenv("AUTH_JWT_ISSUER", settings.auth_issuer)
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "unexpected-audience")
    with pytest.raises(ValueError):
        auth.Hs256Authenticator(Settings.from_env()).subject_from_token(token)


def test_current_user_returns_database_roles_from_verified_token(monkeypatch):
    configured_login(monkeypatch)
    actor = Actor("user-id", "demo:dr-ada", "Dr Ada Example", frozenset({"doctor"}))
    monkeypatch.setattr(HealthcareRepository, "actor_by_subject", lambda self, subject: actor)
    token = auth.issue_access_token(actor.subject, Settings.from_env())

    response = TestClient(app).get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json() == {
        "subject": "demo:dr-ada", "display_name": "Dr Ada Example", "roles": ["doctor"],
    }


def test_appointment_admin_permission_is_enforced_server_side(monkeypatch):
    from queryimmune_db.api.admin_appointments import require_appointment_admin

    monkeypatch.setattr(HealthcareRepository, "is_action_authorized", lambda *args: False)
    actor = Actor("user-id", "demo:dr-ada", "Dr Ada Example", frozenset({"doctor"}))

    with pytest.raises(HTTPException) as error:
        require_appointment_admin(actor)

    assert error.value.status_code == 403
