from __future__ import annotations

import os
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from queryimmune_db.api.app import app
from queryimmune_db.api.auth import get_current_actor
from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository
from queryimmune_db.services.policy import DeterministicPolicyPipeline


def actor(roles: set[str]) -> Actor:
    return Actor("10000000-0000-4000-8000-000000000001", "test:actor", "Synthetic Test Actor", frozenset(roles))


def test_role_policy_blocks_unauthorized_action():
    result = DeterministicPolicyPipeline().decide(ExecutionRequest(action="billing.summary"), actor({"doctor"}))
    assert result.state == "block"
    assert result.reason_codes == ("ROLE_NOT_ALLOWED",)


def test_unknown_operation_is_blocked():
    result = DeterministicPolicyPipeline().decide(ExecutionRequest(action="patients.delete"), actor({"admin"}))
    assert result.state == "block"
    assert result.reason_codes == ("UNSUPPORTED_ACTION",)


def test_broad_action_requires_review():
    result = DeterministicPolicyPipeline().decide(ExecutionRequest(action="appointments.mine", limit=50), actor({"doctor"}))
    assert result.state == "review"
    assert result.reason_codes == ("SCOPE_TOO_BROAD",)


@pytest.fixture
def seeded_doctor():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("requires a migrated, seeded PostgreSQL database")
    pytest.importorskip("psycopg")
    current = HealthcareRepository().actor_by_subject("demo:dr-ada")
    if current is None:
        pytest.skip("synthetic demo:dr-ada seed is missing")
    app.dependency_overrides[get_current_actor] = lambda: current
    yield current
    app.dependency_overrides.pop(get_current_actor, None)


@pytest.mark.database
def test_allowed_action_executes_and_audit_is_created(seeded_doctor):
    from queryimmune_db.connection import transaction

    with TestClient(app) as client:
        response = client.post("/api/v1/requests", json={"action": "appointments.mine", "limit": 10})
    assert response.status_code == 200
    result = response.json()
    assert result["decision"] == "allow"
    assert result["execution_status"] == "succeeded"
    assert isinstance(result["results"], list)
    assert response.headers["x-request-id"] == result["request_id"]

    with transaction(read_only=True) as conn:
        log = conn.execute(
            "SELECT decision, execution_status, row_count FROM security.query_logs WHERE request_id = %s",
            (UUID(result["request_id"]),),
        ).fetchone()
        risk = conn.execute("SELECT outcome FROM security.risk_events WHERE request_id = %s", (UUID(result["request_id"]),)).fetchone()
    assert log == ("allow", "succeeded", result["row_count"])
    assert risk == ("allow",)


@pytest.mark.database
def test_blocked_request_is_audited_and_never_executes(seeded_doctor):
    from queryimmune_db.connection import transaction

    with TestClient(app) as client:
        response = client.post("/api/v1/requests", json={"action": "billing.summary"})
    assert response.status_code == 403
    body = response.json()
    assert body["decision"] == "block"
    assert body["execution_status"] == "not_run"
    with transaction(read_only=True) as conn:
        log = conn.execute("SELECT decision, execution_status FROM security.query_logs WHERE request_id = %s", (UUID(body["request_id"]),)).fetchone()
        risk = conn.execute("SELECT outcome FROM security.risk_events WHERE request_id = %s", (UUID(body["request_id"]),)).fetchone()
    assert log == ("block", "not_run")
    assert risk == ("block",)


@pytest.mark.database
def test_unauthorized_operation_fails_without_execution(seeded_doctor):
    with TestClient(app) as client:
        response = client.post("/api/v1/requests", json={"action": "appointments.by_status"})
    assert response.status_code == 403
    assert response.json()["reason_codes"] == ["ROLE_NOT_ALLOWED"]
    assert response.json()["execution_status"] == "not_run"


def test_sql_is_not_an_accepted_request_field():
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({"action": "appointments.mine", "sql": "SELECT 1"})


def test_health_and_openapi_endpoints():
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        schema = client.get("/openapi.json").json()
    request_schema = schema["paths"]["/api/v1/requests"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("ExecutionRequest")
    assert "sql" not in schema["components"]["schemas"]["ExecutionRequest"].get("properties", {})


def test_execution_route_requires_authentication_and_returns_request_id():
    with TestClient(app) as client:
        response = client.post("/api/v1/requests", json={"action": "appointments.mine"})
    assert response.status_code == 401
    assert UUID(response.headers["x-request-id"])
