from __future__ import annotations

import os
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from fastapi.testclient import TestClient

from queryimmune_db.api.admin_appointments import (
    get_admin_appointment_service,
    require_appointment_admin,
)
from queryimmune_db.api.app import app
from queryimmune_db.api.auth import get_current_actor
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.admin_appointments import (
    AppointmentDeleteConflict,
    AppointmentNotFound,
)
from queryimmune_db.repositories.healthcare import HealthcareRepository

ADMIN = Actor(
    "10000000-0000-4000-8000-000000000008",
    "demo:admin",
    "Synthetic Demo Administrator",
    frozenset({"admin"}),
)
PATIENT_ID = UUID("30000000-0000-4000-8000-000000000001")
DOCTOR_ID = UUID("20000000-0000-4000-8000-000000000001")
APPOINTMENT_ID = UUID("40000000-0000-4000-8000-000000000099")


def _appointment(**overrides):
    values = {
        "id": APPOINTMENT_ID,
        "patient_id": PATIENT_ID,
        "doctor_id": DOCTOR_ID,
        "scheduled_at": datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc),
        "status": "scheduled",
        "reason_category": "routine follow-up",
        "created_at": datetime(2026, 4, 1, 10, tzinfo=timezone.utc),
        "updated_at": datetime(2026, 4, 1, 10, tzinfo=timezone.utc),
    }
    values.update(overrides)
    return SimpleNamespace(**values)


class _FakeAdminService:
    def __init__(self):
        self.appointments = {APPOINTMENT_ID: _appointment()}
        self.list_filters = None

    def create_appointment(self, actor_id, request_id, values):
        result = _appointment(id=uuid4(), **values)
        self.appointments[result.id] = result
        return result

    def get_appointment(self, actor_id, request_id, appointment_id):
        if appointment_id not in self.appointments:
            raise AppointmentNotFound
        return self.appointments[appointment_id]

    def list_appointments(self, actor_id, request_id, **filters):
        self.list_filters = filters
        return list(self.appointments.values()), len(self.appointments)

    def update_appointment(self, actor_id, request_id, appointment_id, values):
        result = self.get_appointment(actor_id, request_id, appointment_id)
        for key, value in values.items():
            setattr(result, key, value)
        return result

    def delete_appointment(self, actor_id, request_id, appointment_id):
        if appointment_id not in self.appointments:
            raise AppointmentNotFound
        del self.appointments[appointment_id]

    def list_audit_events(self, actor_id, request_id, **filters):
        return [], 0


def test_admin_appointment_crud_routes_and_filtering():
    service = _FakeAdminService()
    app.dependency_overrides[get_current_actor] = lambda: ADMIN
    app.dependency_overrides[require_appointment_admin] = lambda: ADMIN
    app.dependency_overrides[get_admin_appointment_service] = lambda: service
    try:
        with TestClient(app) as client:
            created = client.post(
                "/api/v1/admin/appointments",
                json={
                    "patient_id": str(PATIENT_ID),
                    "doctor_id": str(DOCTOR_ID),
                    "scheduled_at": "2026-05-01T14:30:00Z",
                    "status": "scheduled",
                    "reason_category": "routine follow-up",
                },
            )
            assert created.status_code == 201
            assert created.json()["patient_id"] == str(PATIENT_ID)
            appointment_id = created.json()["id"]

            fetched = client.get(f"/api/v1/admin/appointments/{appointment_id}")
            assert fetched.status_code == 200
            assert fetched.json()["status"] == "scheduled"

            page = client.get(
                "/api/v1/admin/appointments",
                params={"status": "scheduled", "patient_id": str(PATIENT_ID), "limit": 10, "offset": 0},
            )
            assert page.status_code == 200
            assert page.json()["total"] == 2
            assert service.list_filters["status"] == "scheduled"
            assert service.list_filters["patient_id"] == PATIENT_ID

            updated = client.patch(
                f"/api/v1/admin/appointments/{appointment_id}",
                json={"status": "completed"},
            )
            assert updated.status_code == 200
            assert updated.json()["status"] == "completed"

            deleted = client.delete(f"/api/v1/admin/appointments/{appointment_id}")
            assert deleted.status_code == 204
            assert client.get(f"/api/v1/admin/appointments/{appointment_id}").status_code == 404

            audit = client.get("/api/v1/admin/appointments/audit")
            assert audit.status_code == 200
            assert audit.json() == {"items": [], "total": 0, "limit": 25, "offset": 0}
    finally:
        app.dependency_overrides.clear()


def test_admin_appointment_routes_reject_invalid_requests():
    service = _FakeAdminService()
    app.dependency_overrides[get_current_actor] = lambda: ADMIN
    app.dependency_overrides[require_appointment_admin] = lambda: ADMIN
    app.dependency_overrides[get_admin_appointment_service] = lambda: service
    try:
        with TestClient(app) as client:
            invalid_status = client.post(
                "/api/v1/admin/appointments",
                json={
                    "patient_id": str(PATIENT_ID),
                    "doctor_id": str(DOCTOR_ID),
                    "scheduled_at": "2026-05-01T14:30:00Z",
                    "status": "pending",
                    "reason_category": "routine",
                },
            )
            assert invalid_status.status_code == 422

            naive_time = client.post(
                "/api/v1/admin/appointments",
                json={
                    "patient_id": str(PATIENT_ID),
                    "doctor_id": str(DOCTOR_ID),
                    "scheduled_at": "2026-05-01T14:30:00",
                    "reason_category": "routine",
                },
            )
            assert naive_time.status_code == 422

            null_update = client.patch(
                f"/api/v1/admin/appointments/{APPOINTMENT_ID}", json={"status": None},
            )
            assert null_update.status_code == 422
    finally:
        app.dependency_overrides.clear()


def test_admin_routes_check_database_authorization(monkeypatch):
    observed = []

    def denied(self, actor_id, action, resources):
        observed.append((actor_id, action, resources))
        return False

    monkeypatch.setattr(HealthcareRepository, "is_action_authorized", denied)
    app.dependency_overrides[get_current_actor] = lambda: ADMIN
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/appointments")
        assert response.status_code == 403
        assert observed == [(ADMIN.user_id, "appointments.admin_crud", ("appointments",))]
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    ("authenticated_actor", "authorized", "expected_status"),
    [
        (ADMIN, True, 200),
        (
            Actor(
                "10000000-0000-4000-8000-000000000009",
                "demo:dr-ada",
                "Synthetic Demo Doctor",
                frozenset({"doctor"}),
            ),
            False,
            403,
        ),
    ],
)
def test_admin_route_enforces_database_role_policy(
    monkeypatch, authenticated_actor, authorized, expected_status,
):
    service = _FakeAdminService()
    calls = []

    def authorize(self, actor_id, action, resources):
        calls.append((actor_id, action, resources))
        return authorized

    monkeypatch.setattr(HealthcareRepository, "is_action_authorized", authorize)
    app.dependency_overrides[get_current_actor] = lambda: authenticated_actor
    app.dependency_overrides[get_admin_appointment_service] = lambda: service
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/appointments")
        assert response.status_code == expected_status
        assert calls == [
            (authenticated_actor.user_id, "appointments.admin_crud", ("appointments",))
        ]
        if authorized:
            assert response.json()["total"] == 1
    finally:
        app.dependency_overrides.clear()


def test_admin_route_maps_restricted_delete_to_conflict():
    class DeleteConflictService(_FakeAdminService):
        def delete_appointment(self, actor_id, request_id, appointment_id):
            raise AppointmentDeleteConflict

    app.dependency_overrides[get_current_actor] = lambda: ADMIN
    app.dependency_overrides[require_appointment_admin] = lambda: ADMIN
    app.dependency_overrides[get_admin_appointment_service] = DeleteConflictService
    try:
        with TestClient(app) as client:
            response = client.delete(f"/api/v1/admin/appointments/{APPOINTMENT_ID}")
        assert response.status_code == 409
    finally:
        app.dependency_overrides.clear()


def test_postgresql_admin_crud_rls_and_audit_integration():
    if not os.environ.get("DATABASE_URL") or not os.environ.get("DATABASE_ORM_ADMIN_URL"):
        pytest.skip("requires migrated PostgreSQL runtime and ORM admin URLs")

    from queryimmune_db.orm.session import create_orm_engine, create_session_factory, session_scope
    from queryimmune_db.repositories.admin_appointments import (
        AdminAppointmentRepository,
        AppointmentNotFound,
    )

    admin = HealthcareRepository().actor_by_subject("demo:admin")
    doctor = HealthcareRepository().actor_by_subject("demo:dr-ada")
    if admin is None or doctor is None:
        pytest.skip("synthetic demo admin/doctor fixture is missing")
    engine = create_orm_engine(os.environ["DATABASE_ORM_ADMIN_URL"])
    repository = AdminAppointmentRepository(create_session_factory(engine))
    request_ids = [uuid4(), uuid4(), uuid4()]
    appointment_id = None
    deleted = False
    try:
        created = repository.create_appointment(
            UUID(admin.user_id),
            request_ids[0],
            {
                "patient_id": PATIENT_ID,
                "doctor_id": DOCTOR_ID,
                "scheduled_at": datetime(2027, 8, 20, 15, tzinfo=timezone.utc),
                "status": "scheduled",
                "reason_category": "integration test",
            },
        )
        appointment_id = created.id
        assert repository.get_appointment(UUID(admin.user_id), request_ids[0], created.id).id == created.id

        with pytest.raises(AppointmentNotFound):
            repository.get_appointment(UUID(doctor.user_id), request_ids[0], created.id)

        with pytest.raises(RuntimeError, match="rollback trigger audit with appointment"):
            with session_scope(create_session_factory(engine), actor_id=admin.user_id, request_id=str(request_ids[1])) as session:
                session.execute(
                    text("SELECT security.admin_update_appointment_status(:id, 'cancelled')"),
                    {"id": created.id},
                )
                raise RuntimeError("rollback trigger audit with appointment")
        after_rollback = repository.get_appointment(UUID(admin.user_id), request_ids[1], created.id)
        assert after_rollback.status == "scheduled"
        rollback_events, rollback_total = repository.list_audit_events(
            UUID(admin.user_id), request_ids[1], limit=10, offset=0, appointment_id=created.id,
        )
        assert rollback_total == 1
        assert [event.action for event in rollback_events] == ["create"]

        with session_scope(create_session_factory(engine), actor_id=admin.user_id, request_id=str(request_ids[1])) as session:
            changed = session.execute(
                text("SELECT (security.admin_update_appointment_status(:id, :status)).status"),
                {"id": created.id, "status": "completed"},
            ).scalar_one()
        assert changed == "completed"

        updated = repository.update_appointment(
            UUID(admin.user_id), request_ids[1], created.id, {"status": "cancelled"},
        )
        assert updated.status == "cancelled"

        with session_scope(create_session_factory(engine), actor_id=admin.user_id, request_id=str(request_ids[2])) as session:
            execution_summary = session.execute(
                text("SELECT count(*) FROM security.query_execution_daily_summary")
            ).scalar_one()
            risk_summary = session.execute(
                text("SELECT count(*) FROM security.risk_event_daily_summary")
            ).scalar_one()
            report_count = session.execute(
                text("SELECT appointment_count FROM healthcare.admin_appointment_report WHERE doctor_id = :id"),
                {"id": DOCTOR_ID},
            ).scalar_one()
        assert execution_summary >= 0
        assert risk_summary >= 0
        assert report_count >= 1

        events, total = repository.list_audit_events(
            UUID(admin.user_id), request_ids[2], limit=10, offset=0, appointment_id=created.id,
        )
        assert total == 3
        assert [event.action for event in events].count("update") == 2

        repository.delete_appointment(UUID(admin.user_id), request_ids[2], created.id)
        deleted = True
        events, total = repository.list_audit_events(
            UUID(admin.user_id), request_ids[2], limit=10, offset=0, appointment_id=created.id,
        )
        assert total == 4
        assert [event.action for event in events].count("delete") == 1
    finally:
        if appointment_id is not None and not deleted:
            repository.delete_appointment(UUID(admin.user_id), uuid4(), appointment_id)
        engine.dispose()
