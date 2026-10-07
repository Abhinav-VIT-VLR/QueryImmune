from __future__ import annotations

import os
import asyncio
from dataclasses import replace
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
    result = DeterministicPolicyPipeline(authorization_check=lambda *_: False).decide(
        ExecutionRequest(action="billing.summary"), actor({"doctor"})
    )
    assert result.state == "block"
    assert result.reason_codes == ("ROLE_NOT_ALLOWED",)


def test_unknown_operation_is_blocked():
    result = DeterministicPolicyPipeline().decide(ExecutionRequest(action="patients.delete"), actor({"admin"}))
    assert result.state == "block"
    assert result.reason_codes == ("UNSUPPORTED_ACTION",)


def test_broad_action_requires_review():
    result = DeterministicPolicyPipeline(authorization_check=lambda *_: True).decide(
        ExecutionRequest(action="appointments.mine", limit=50), actor({"doctor"})
    )
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

    with transaction(read_only=True, actor_context_id=UUID(seeded_doctor.user_id)) as conn:
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
    with transaction(read_only=True, actor_context_id=UUID(seeded_doctor.user_id)) as conn:
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


@pytest.mark.database
def test_action_authorization_is_read_from_postgres(seeded_doctor):
    """Removing a database permission changes the real API decision immediately."""
    import psycopg

    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("requires DATABASE_ADMIN_URL to safely mutate policy fixtures")
    with psycopg.connect(admin_url, autocommit=True) as conn:
        role_id = conn.execute("SELECT id FROM healthcare.roles WHERE name = 'doctor'").fetchone()[0]
        original = conn.execute(
            "SELECT enabled FROM security.action_role_policies "
            "WHERE action='appointments.mine' AND resource='patients' AND role_id=%s",
            (role_id,),
        ).fetchone()
        assert original == (True,)
        try:
            conn.execute(
                "DELETE FROM security.action_role_policies "
                "WHERE action='appointments.mine' AND resource='patients' AND role_id=%s",
                (role_id,),
            )
            with TestClient(app) as client:
                denied = client.post("/api/v1/requests", json={"action": "appointments.mine", "limit": 10})
            assert denied.status_code == 403
            assert denied.json()["decision"] == "block"
            assert denied.json()["reason_codes"] == ["ROLE_NOT_ALLOWED"]
        finally:
            conn.execute(
                "INSERT INTO security.action_role_policies(action, resource, role_id, enabled) "
                "VALUES ('appointments.mine', 'patients', %s, %s) "
                "ON CONFLICT (action, resource, role_id) DO UPDATE SET enabled=EXCLUDED.enabled",
                (role_id, original[0]),
            )
    with TestClient(app) as client:
        restored = client.post("/api/v1/requests", json={"action": "appointments.mine", "limit": 10})
    assert restored.status_code == 200
    assert restored.json()["decision"] == "allow"


@pytest.mark.database
def test_seeded_role_resource_policies_fail_closed(seeded_doctor):
    repository = HealthcareRepository()
    billing = repository.actor_by_subject("demo:billing-sam")
    receptionist = repository.actor_by_subject("demo:receptionist-jules")
    unknown_role = repository.actor_by_subject("demo:unconfigured-role")
    assert billing and "billing_staff" in billing.roles
    assert receptionist and "receptionist" in receptionist.roles
    assert unknown_role and "unknown_role" in unknown_role.roles

    assert repository.is_action_authorized(
        seeded_doctor.user_id, "appointments.mine", ("appointments", "patients", "doctors")
    )
    # Even forged in-memory role claims cannot change the role assignments read
    # by the authorization SQL.
    spoofed_admin = replace(seeded_doctor, roles=frozenset({"admin"}))
    spoofed_decision = DeterministicPolicyPipeline().decide(
        ExecutionRequest(action="billing.summary"), spoofed_admin
    )
    assert spoofed_decision.state == "block"
    assert spoofed_decision.reason_codes == ("ROLE_NOT_ALLOWED",)
    # The medical-record resource has no policy or broker action today, so every
    # role is denied and no PostgreSQL grant is broadened.
    assert not repository.is_action_authorized(
        billing.user_id, "medical_records.read", ("medical_records",)
    )
    assert not repository.is_action_authorized(
        receptionist.user_id, "medical_records.read", ("medical_records",)
    )
    assert not repository.is_action_authorized(
        seeded_doctor.user_id, "medical_records.read", ("medical_records",)
    )
    assert not repository.is_action_authorized(
        seeded_doctor.user_id, "appointments.mine", ("unknown_resource",)
    )
    assert not repository.is_action_authorized(
        seeded_doctor.user_id, "unknown.action", ("appointments",)
    )
    assert not repository.is_action_authorized(
        "00000000-0000-4000-8000-000000000000", "appointments.mine", ("appointments",)
    )

    app.dependency_overrides[get_current_actor] = lambda: unknown_role
    try:
        with TestClient(app) as client:
            response = client.post("/api/v1/requests", json={"action": "appointments.mine"})
        assert response.status_code == 403
        assert response.json()["reason_codes"] == ["ROLE_NOT_ALLOWED"]
        assert response.json()["execution_status"] == "not_run"
    finally:
        app.dependency_overrides.pop(get_current_actor, None)


@pytest.mark.database
def test_appointment_rows_are_isolated_by_database_actor_context(seeded_doctor):
    from queryimmune_db.connection import transaction

    repository = HealthcareRepository()
    actor_a = seeded_doctor
    actor_b = repository.actor_by_subject("demo:dr-grace")
    assert actor_b is not None
    actor_a_id = UUID(actor_a.user_id)
    actor_b_id = UUID(actor_b.user_id)

    with transaction(read_only=True, actor_context_id=actor_a_id) as conn:
        own_doctors = conn.execute("SELECT user_id FROM healthcare.doctors").fetchall()
        other_doctors = conn.execute(
            "SELECT id FROM healthcare.doctors WHERE user_id = %s", (actor_b_id,)
        ).fetchall()
        own_patients = conn.execute(
            """SELECT DISTINCT p.synthetic_patient_number
               FROM healthcare.appointments a
               JOIN healthcare.patients p ON p.id = a.patient_id
               JOIN healthcare.doctors d ON d.id = a.doctor_id
               WHERE d.user_id = %s""",
            (actor_a_id,),
        ).fetchall()
    assert own_doctors == [(actor_a_id,)]
    assert other_doctors == []
    assert own_patients

    with transaction(read_only=True, actor_context_id=actor_b_id) as conn:
        actor_b_patients = conn.execute(
            """SELECT DISTINCT p.synthetic_patient_number
               FROM healthcare.appointments a
               JOIN healthcare.patients p ON p.id = a.patient_id
               JOIN healthcare.doctors d ON d.id = a.doctor_id
               WHERE d.user_id = %s""",
            (actor_b_id,),
        ).fetchall()
    assert actor_b_patients
    assert {row[0] for row in own_patients}.isdisjoint({row[0] for row in actor_b_patients})

    # A wrong query predicate cannot escape the independently established actor context.
    wrong_actor_rows = repository.execute_action(
        "appointments.mine", actor_b_id, 25, None, actor_context_id=actor_a_id
    )
    assert wrong_actor_rows == []


@pytest.mark.database
def test_api_actor_change_changes_visible_appointments(seeded_doctor):
    from queryimmune_db.connection import transaction

    repository = HealthcareRepository()
    actor_a = seeded_doctor
    actor_b = repository.actor_by_subject("demo:dr-grace")
    assert actor_b is not None
    try:
        app.dependency_overrides[get_current_actor] = lambda: actor_a
        with TestClient(app) as client:
            response_a = client.post("/api/v1/requests", json={"action": "appointments.mine"})
        app.dependency_overrides[get_current_actor] = lambda: actor_b
        with TestClient(app) as client:
            response_b = client.post("/api/v1/requests", json={"action": "appointments.mine"})
    finally:
        app.dependency_overrides[get_current_actor] = lambda: actor_a

    assert response_a.status_code == response_b.status_code == 200
    patients_a = {row["synthetic_patient_number"] for row in response_a.json()["results"]}
    patients_b = {row["synthetic_patient_number"] for row in response_b.json()["results"]}
    assert patients_a and patients_b and patients_a.isdisjoint(patients_b)
    with transaction(read_only=True, actor_context_id=UUID(actor_a.user_id)) as conn:
        own_audit = conn.execute(
            "SELECT actor_id::text FROM security.query_logs WHERE request_id=%s",
            (UUID(response_a.json()["request_id"]),),
        ).fetchall()
        other_audit = conn.execute(
            "SELECT actor_id::text FROM security.query_logs WHERE request_id=%s",
            (UUID(response_b.json()["request_id"]),),
        ).fetchall()
    assert own_audit == [(actor_a.user_id,)]
    assert other_audit == []

    with TestClient(app) as client:
        forged = client.post("/api/v1/requests", json={
            "action": "appointments.mine", "actor_id": actor_b.user_id,
        })
    assert forged.status_code == 422


@pytest.mark.database
def test_shared_supported_aggregates_still_execute(seeded_doctor):
    repository = HealthcareRepository()
    for subject, action in (
        ("demo:analyst-riley", "appointments.by_status"),
        ("demo:billing-sam", "billing.summary"),
    ):
        authenticated_actor = repository.actor_by_subject(subject)
        assert authenticated_actor is not None
        app.dependency_overrides[get_current_actor] = lambda user=authenticated_actor: user
        with TestClient(app) as client:
            response = client.post("/api/v1/requests", json={"action": action})
        assert response.status_code == 200
        assert response.json()["decision"] == "allow"
        assert response.json()["execution_status"] == "succeeded"
    app.dependency_overrides[get_current_actor] = lambda: seeded_doctor


@pytest.mark.database
def test_actor_context_is_transaction_local(seeded_doctor):
    import psycopg
    from queryimmune_db.connection import database_url

    with psycopg.connect(database_url()) as conn:
        with conn.transaction():
            conn.execute(
                "SELECT set_config('queryimmune.actor_id', %s, true)",
                (seeded_doctor.user_id,),
            )
            visible = conn.execute("SELECT count(*) FROM healthcare.doctors").fetchone()[0]
            assert visible == 1
        with conn.transaction():
            setting = conn.execute(
                "SELECT current_setting('queryimmune.actor_id', true)"
            ).fetchone()[0]
            visible = conn.execute("SELECT count(*) FROM healthcare.doctors").fetchone()[0]
            assert setting in (None, "")
            assert visible == 0


def test_unsupported_medical_record_action_cannot_reach_execution():
    result = DeterministicPolicyPipeline(authorization_check=lambda *_: True).decide(
        ExecutionRequest(action="medical_records.read"), actor({"doctor"})
    )
    assert result.state == "block"
    assert result.reason_codes == ("UNSUPPORTED_ACTION",)


def test_sql_is_not_an_accepted_request_field():
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({"action": "appointments.mine", "sql": "SELECT 1"})


def test_client_cannot_assign_or_escalate_its_role():
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({"action": "billing.summary", "roles": ["admin"]})


def test_client_cannot_choose_database_actor_context():
    with pytest.raises(ValidationError):
        ExecutionRequest.model_validate({
            "action": "appointments.mine",
            "actor_id": "10000000-0000-4000-8000-000000000007",
        })


def test_health_and_openapi_endpoints():
    with TestClient(app) as client:
        assert client.get("/health/live").json() == {"status": "ok"}
        schema = client.get("/openapi.json").json()
    request_schema = schema["paths"]["/api/v1/requests"]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert request_schema["$ref"].endswith("ExecutionRequest")
    assert "sql" not in schema["components"]["schemas"]["ExecutionRequest"].get("properties", {})


def test_demo_console_is_served_same_origin_and_dashboard_requires_authentication():
    with TestClient(app) as client:
        page = client.get("/")
        script = client.get("/static/app.js")
        dashboard = client.get("/api/v1/dashboard")
    assert page.status_code == 200 and "Query intelligence" in page.text
    assert script.status_code == 200 and "api/v1/nl-requests" in script.text
    assert dashboard.status_code == 401


def test_execution_route_requires_authentication_and_returns_request_id():
    with TestClient(app) as client:
        response = client.post("/api/v1/requests", json={"action": "appointments.mine"})
    assert response.status_code == 401
    assert UUID(response.headers["x-request-id"])


def test_unhandled_errors_return_generic_message_with_request_id():
    from starlette.requests import Request
    from queryimmune_db.api.app import unhandled_error_handler
    scope = {"type": "http", "method": "GET", "path": "/", "headers": [],
             "query_string": b"", "server": ("test", 80), "client": ("test", 1), "scheme": "http"}
    request = Request(scope)
    request.state.request_id = "request-test-id"
    response = asyncio.run(unhandled_error_handler(request, RuntimeError("private SQL and secret")))
    payload = __import__("json").loads(response.body)
    assert response.status_code == 500
    assert payload["request_id"] == "request-test-id"
    assert "private SQL" not in response.body.decode()


def test_demo_token_issuer_is_local_opt_in_and_uses_existing_verifier(monkeypatch, capsys):
    from queryimmune_db.api.config import Settings
    from queryimmune_db.api.auth import Hs256Authenticator
    from queryimmune_db.api.dev_token import main
    monkeypatch.setenv("QUERYIMMUNE_DEMO_AUTH", "1")
    monkeypatch.setenv("AI_PROVIDER", "demo")
    monkeypatch.setenv("AUTH_JWT_HS256_SECRET", "a" * 64)
    monkeypatch.setenv("AUTH_JWT_ISSUER", "queryimmune-auth")
    monkeypatch.setenv("AUTH_JWT_AUDIENCE", "queryimmune-api")
    main()
    token = capsys.readouterr().out.strip()
    assert Hs256Authenticator(Settings.from_env()).subject_from_token(token) == "demo:dr-ada"
    monkeypatch.setenv("QUERYIMMUNE_DEMO_AUTH", "0")
    with pytest.raises(SystemExit):
        main()


@pytest.mark.database
def test_live_demo_scenarios_use_validator_gateway_and_postgres(seeded_doctor, monkeypatch):
    from importlib import import_module
    from queryimmune_db.connection import transaction

    app_module = import_module("queryimmune_db.api.app")
    monkeypatch.setattr(app_module, "settings", replace(app_module.settings, ai_provider="demo"))
    cases = [
        ("normal", "How many patients visited cardiology today?", "allow"),
        ("sensitive", "Show the medical history of every patient.", "block"),
        ("destructive", "Delete all patient records.", "block"),
        ("mismatch", "Show my appointments.", "block"),
    ]
    with TestClient(app) as client:
        for scenario, prompt, expected in cases:
            response = client.post("/api/v1/nl-requests", json={"request": prompt, "demo_scenario": scenario})
            body = response.json()
            assert body["decision"] == expected
            assert body["generated_sql"]
            if expected == "block":
                assert body["execution_status"] == "not_run"
            else:
                assert body["execution_status"] == "succeeded"
                assert body["results"][0]["patient_count"] >= 0

        drift_response = None
        for _ in range(10):
            drift_response = client.post("/api/v1/nl-requests", json={
                "request": "Show my first 10 appointments.", "demo_scenario": "drift",
            })
            assert drift_response.json()["decision"] == "allow"
        dashboard_response = client.get("/api/v1/dashboard")
        assert dashboard_response.status_code == 200
        assert dashboard_response.json()["behavior_profile"]["profile_version"] >= 2

    with transaction(read_only=True, actor_context_id=UUID(seeded_doctor.user_id)) as conn:
        logs = conn.execute("""SELECT count(*) FROM security.query_logs
            WHERE actor_id=%s AND decision IN ('allow','block')""", (UUID(seeded_doctor.user_id),)).fetchone()[0]
    assert logs >= 14
