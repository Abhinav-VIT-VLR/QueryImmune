from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import String, create_engine, inspect, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, configure_mappers, mapped_column, sessionmaker

from queryimmune_db.orm.models import (
    ActionRolePolicy,
    AdminAuditEvent,
    Appointment,
    Base,
    BehaviorProfile,
    Billing,
    Doctor,
    MedicalRecord,
    Patient,
    QueryLog,
    RiskEvent,
    Role,
    User,
    UserRole,
)
from queryimmune_db.orm.session import create_orm_engine, orm_database_available, session_scope


def test_orm_mappings_cover_existing_tables_and_foreign_keys():
    configure_mappers()

    expected_tables = {
        "healthcare.users",
        "healthcare.roles",
        "healthcare.user_roles",
        "healthcare.patients",
        "healthcare.doctors",
        "healthcare.appointments",
        "healthcare.medical_records",
        "healthcare.billing",
        "security.query_logs",
        "security.behavior_profiles",
        "security.risk_events",
        "security.action_role_policies",
        "security.admin_audit_events",
        "qi_migrations.applied",
    }
    assert set(Base.metadata.tables) == expected_tables

    assert {column.name for column in UserRole.__table__.primary_key.columns} == {"user_id", "role_id"}
    assert {column.name for column in ActionRolePolicy.__table__.primary_key.columns} == {
        "action", "resource", "role_id",
    }
    assert User.doctor.property.uselist is False
    assert QueryLog.risk_event.property.uselist is False
    assert Appointment.patient.property.mapper.class_ is Patient
    assert Appointment.doctor.property.mapper.class_ is Doctor
    assert MedicalRecord.patient.property.mapper.class_ is Patient
    assert Billing.appointment.property.mapper.class_ is Appointment
    assert UserRole.role.property.mapper.class_ is Role
    assert RiskEvent.query_log.property.mapper.class_ is QueryLog
    assert BehaviorProfile.actor.property.mapper.class_ is User
    assert AdminAuditEvent.actor.property.mapper.class_ is User
    assert RiskEvent.__table__.c.profile_version.foreign_keys == set()

    foreign_keys = {
        (fk.parent.name, fk.target_fullname)
        for fk in Billing.__table__.foreign_keys
    }
    assert foreign_keys == {
        ("patient_id", "healthcare.patients.id"),
        ("appointment_id", "healthcare.appointments.id"),
    }
    assert len(inspect(UserRole).relationships) == 3
    assert {fk.target_fullname for fk in AdminAuditEvent.__table__.foreign_keys} == {
        "healthcare.users.id",
    }


def test_administrative_engine_requires_dedicated_postgres_role(monkeypatch):
    monkeypatch.delenv("DATABASE_ORM_ADMIN_URL", raising=False)
    with pytest.raises(RuntimeError, match="DATABASE_ORM_ADMIN_URL"):
        create_orm_engine()

    with pytest.raises(ValueError, match="dedicated administrative"):
        create_orm_engine("postgresql://queryimmune_runtime:secret@localhost/queryimmune")

    engine = create_orm_engine("postgresql://queryimmune_admin:secret@localhost/queryimmune")
    try:
        assert engine.url.drivername == "postgresql+psycopg"
        assert engine.url.username == "queryimmune_admin"
    finally:
        engine.dispose()


class _ProbeBase(DeclarativeBase):
    pass


class _Probe(_ProbeBase):
    __tablename__ = "session_probe"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    marker: Mapped[str] = mapped_column(String, nullable=False)


def test_session_scope_commits_on_success_and_rolls_back_on_exception():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    _ProbeBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        created_id = str(uuid4())
        with session_scope(factory) as session:
            session.add(_Probe(id=created_id, marker="committed"))

        with pytest.raises(RuntimeError, match="force rollback"):
            with session_scope(factory) as session:
                session.add(_Probe(id=str(uuid4()), marker="rolled back"))
                raise RuntimeError("force rollback")

        with session_scope(factory) as session:
            rows = session.scalars(select(_Probe)).all()
            assert [(row.id, row.marker) for row in rows] == [(created_id, "committed")]
    finally:
        engine.dispose()


def test_orm_database_health_checks_connectivity_without_leaking_errors(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    monkeypatch.setattr("queryimmune_db.orm.session._environment_engine", lambda: engine)
    assert orm_database_available()

    class UnavailableEngine:
        def connect(self):
            raise OperationalError("SELECT 1", {}, OSError("private connection detail"))

    monkeypatch.setattr(
        "queryimmune_db.orm.session._environment_engine",
        lambda: UnavailableEngine(),
    )
    assert not orm_database_available()
    engine.dispose()
