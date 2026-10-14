"""SQLAlchemy mappings for the existing PostgreSQL schema.

These mappings describe database-owned tables; they do not create or migrate
them. Schema changes remain the responsibility of SQL migrations.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CHAR,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON

_JSONB = JSONB().with_variant(JSON(), "sqlite")
_TEXT_ARRAY = ARRAY(Text()).with_variant(JSON(), "sqlite")


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("length(trim(display_name)) BETWEEN 1 AND 120"),
        CheckConstraint("status IN ('active', 'suspended', 'disabled')"),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    external_subject: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'active'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    role_assignments: Mapped[list[UserRole]] = relationship(back_populates="user", foreign_keys="UserRole.user_id")
    role_grants_made: Mapped[list[UserRole]] = relationship(back_populates="assigned_by_user", foreign_keys="UserRole.assigned_by")
    doctor: Mapped[Doctor | None] = relationship(back_populates="user", uselist=False)
    query_logs: Mapped[list[QueryLog]] = relationship(back_populates="actor")
    behavior_profiles: Mapped[list[BehaviorProfile]] = relationship(back_populates="actor")
    risk_events: Mapped[list[RiskEvent]] = relationship(back_populates="actor")
    admin_audit_events: Mapped[list[AdminAuditEvent]] = relationship(back_populates="actor")


class Role(Base):
    __tablename__ = "roles"
    __table_args__ = (
        CheckConstraint("name ~ '^[a-z][a-z0-9_]{0,62}$'"),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    name: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    user_assignments: Mapped[list[UserRole]] = relationship(back_populates="role")
    action_policies: Mapped[list[ActionRolePolicy]] = relationship(back_populates="role")


class UserRole(Base):
    __tablename__ = "user_roles"
    __table_args__ = (
        Index("user_roles_role_user_idx", "role_id", "user_id"),
        {"schema": "healthcare"},
    )

    user_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT"), primary_key=True
    )
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.roles.id", ondelete="RESTRICT"), primary_key=True
    )
    assigned_by: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT")
    )
    assigned_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    user: Mapped[User] = relationship(back_populates="role_assignments", foreign_keys=[user_id])
    role: Mapped[Role] = relationship(back_populates="user_assignments")
    assigned_by_user: Mapped[User | None] = relationship(back_populates="role_grants_made", foreign_keys=[assigned_by])


class Patient(Base):
    __tablename__ = "patients"
    __table_args__ = (
        CheckConstraint("date_of_birth <= CURRENT_DATE"),
        CheckConstraint("sex_at_birth IN ('female', 'male', 'intersex', 'unknown')"),
        CheckConstraint("synthetic_patient_number LIKE 'SYN-%'"),
        Index("patients_name_idx", "family_name", "given_name"),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    synthetic_patient_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    given_name: Mapped[str] = mapped_column(Text, nullable=False)
    family_name: Mapped[str] = mapped_column(Text, nullable=False)
    date_of_birth: Mapped[date] = mapped_column(Date, nullable=False)
    sex_at_birth: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    appointments: Mapped[list[Appointment]] = relationship(back_populates="patient")
    medical_records: Mapped[list[MedicalRecord]] = relationship(back_populates="patient")
    billing_records: Mapped[list[Billing]] = relationship(back_populates="patient")


class Doctor(Base):
    __tablename__ = "doctors"
    __table_args__ = (
        CheckConstraint("synthetic_license_number LIKE 'SYN-%'"),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    user_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT"), unique=True
    )
    synthetic_license_number: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    specialty: Mapped[str] = mapped_column(Text, nullable=False)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    user: Mapped[User | None] = relationship(back_populates="doctor")
    appointments: Mapped[list[Appointment]] = relationship(back_populates="doctor")
    authored_medical_records: Mapped[list[MedicalRecord]] = relationship(back_populates="author_doctor")


class Appointment(Base):
    __tablename__ = "appointments"
    __table_args__ = (
        CheckConstraint("status IN ('scheduled', 'completed', 'cancelled', 'no_show')"),
        CheckConstraint("length(trim(reason_category)) BETWEEN 1 AND 80"),
        Index("appointments_patient_time_idx", "patient_id", text("scheduled_at DESC")),
        Index("appointments_doctor_time_idx", "doctor_id", text("scheduled_at DESC")),
        Index("appointments_status_time_idx", "status", "scheduled_at"),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    patient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.patients.id", ondelete="RESTRICT"), nullable=False
    )
    doctor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.doctors.id", ondelete="RESTRICT"), nullable=False
    )
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason_category: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    patient: Mapped[Patient] = relationship(back_populates="appointments")
    doctor: Mapped[Doctor] = relationship(back_populates="appointments")
    billing_records: Mapped[list[Billing]] = relationship(back_populates="appointment")


class MedicalRecord(Base):
    __tablename__ = "medical_records"
    __table_args__ = (
        CheckConstraint("record_type IN ('visit_note', 'lab_summary', 'diagnosis', 'prescription')"),
        CheckConstraint("sensitivity IN ('standard', 'sensitive', 'restricted')"),
        CheckConstraint("length(trim(summary)) BETWEEN 1 AND 4000"),
        Index("medical_records_patient_time_idx", "patient_id", text("recorded_at DESC")),
        Index("medical_records_author_time_idx", "author_doctor_id", text("recorded_at DESC")),
        Index("medical_records_sensitivity_idx", "sensitivity", text("recorded_at DESC")),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    patient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.patients.id", ondelete="RESTRICT"), nullable=False
    )
    author_doctor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.doctors.id", ondelete="RESTRICT"), nullable=False
    )
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    sensitivity: Mapped[str] = mapped_column(Text, nullable=False)
    summary: Mapped[str] = mapped_column(Text, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    patient: Mapped[Patient] = relationship(back_populates="medical_records")
    author_doctor: Mapped[Doctor] = relationship(back_populates="authored_medical_records")


class Billing(Base):
    __tablename__ = "billing"
    __table_args__ = (
        CheckConstraint("amount >= 0"),
        CheckConstraint("currency ~ '^[A-Z]{3}$'"),
        CheckConstraint("status IN ('pending', 'paid', 'waived', 'refunded')"),
        Index("billing_patient_time_idx", "patient_id", text("created_at DESC")),
        Index("billing_status_due_idx", "status", "due_at", postgresql_where=text("status = 'pending'")),
        Index("billing_created_at_idx", "created_at"),
        {"schema": "healthcare"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    patient_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.patients.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.appointments.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, server_default=text("'USD'"))
    status: Mapped[str] = mapped_column(Text, nullable=False)
    due_at: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    patient: Mapped[Patient] = relationship(back_populates="billing_records")
    appointment: Mapped[Appointment | None] = relationship(back_populates="billing_records")


class QueryLog(Base):
    __tablename__ = "query_logs"
    __table_args__ = (
        CheckConstraint("query_fingerprint ~ '^[0-9a-f]{64}$'"),
        CheckConstraint("decision IN ('allow', 'review', 'block')"),
        CheckConstraint("execution_status IN ('not_run', 'succeeded', 'failed')"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0"),
        CheckConstraint("row_count IS NULL OR row_count >= 0"),
        CheckConstraint(
            "(decision = 'allow' AND execution_status IN ('not_run', 'succeeded', 'failed')) "
            "OR (decision IN ('review', 'block') AND execution_status = 'not_run')"
        ),
        Index("query_logs_actor_time_idx", "actor_id", text("created_at DESC")),
        Index("query_logs_decision_time_idx", "decision", text("created_at DESC")),
        {"schema": "security"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, unique=True, server_default=text("gen_random_uuid()"))
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    query_fingerprint: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    execution_status: Mapped[str] = mapped_column(Text, nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    row_count: Mapped[int | None] = mapped_column(Integer)

    actor: Mapped[User] = relationship(back_populates="query_logs")
    risk_event: Mapped[RiskEvent | None] = relationship(back_populates="query_log", uselist=False)


class BehaviorProfile(Base):
    __tablename__ = "behavior_profiles"
    __table_args__ = (
        CheckConstraint("profile_version > 0"),
        CheckConstraint("approved_sample_count >= 0"),
        CheckConstraint("confidence BETWEEN 0 AND 1"),
        CheckConstraint("jsonb_typeof(feature_summary) = 'object'"),
        CheckConstraint("update_status IN ('seeded', 'active', 'quarantined', 'superseded')"),
        CheckConstraint("window_end >= window_start"),
        UniqueConstraint("actor_id", "profile_version"),
        Index("behavior_profiles_actor_version_idx", "actor_id", text("profile_version DESC")),
        {"schema": "security"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT"), nullable=False
    )
    profile_version: Mapped[int] = mapped_column(Integer, nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    approved_sample_count: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4), nullable=False)
    feature_summary: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False)
    update_status: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    actor: Mapped[User] = relationship(back_populates="behavior_profiles")


class RiskEvent(Base):
    __tablename__ = "risk_events"
    __table_args__ = (
        CheckConstraint("risk_score BETWEEN 0 AND 100"),
        CheckConstraint("outcome IN ('allow', 'review', 'block')"),
        CheckConstraint("jsonb_typeof(feature_snapshot) = 'object'"),
        CheckConstraint("length(policy_version) BETWEEN 1 AND 80"),
        CheckConstraint("profile_version IS NULL OR profile_version > 0"),
        Index("risk_events_outcome_time_idx", "outcome", text("created_at DESC")),
        Index("risk_events_actor_time_idx", "actor_id", text("created_at DESC")),
        {"schema": "security"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    request_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("security.query_logs.request_id", ondelete="RESTRICT"), nullable=False, unique=True
    )
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT"), nullable=False
    )
    risk_score: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason_codes: Mapped[list[str]] = mapped_column(_TEXT_ARRAY, nullable=False, server_default=text("'{}'"))
    feature_snapshot: Mapped[dict[str, Any]] = mapped_column(_JSONB, nullable=False, server_default=text("'{}'"))
    policy_version: Mapped[str] = mapped_column(Text, nullable=False)
    model_version: Mapped[str | None] = mapped_column(Text)
    profile_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    actor: Mapped[User] = relationship(back_populates="risk_events")
    query_log: Mapped[QueryLog] = relationship(back_populates="risk_event")


class ActionRolePolicy(Base):
    __tablename__ = "action_role_policies"
    __table_args__ = (
        CheckConstraint("action ~ '^[a-z][a-z0-9_.]{0,127}$'"),
        CheckConstraint("resource ~ '^[a-z][a-z0-9_.]{0,127}$'"),
        Index(
            "action_role_policies_role_idx",
            "role_id",
            "action",
            "resource",
            postgresql_where=text("enabled"),
        ),
        {"schema": "security"},
    )

    action: Mapped[str] = mapped_column(Text, primary_key=True)
    resource: Mapped[str] = mapped_column(Text, primary_key=True)
    role_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.roles.id", ondelete="RESTRICT"), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    role: Mapped[Role] = relationship(back_populates="action_policies")


class AdminAuditEvent(Base):
    __tablename__ = "admin_audit_events"
    __table_args__ = (
        CheckConstraint("action IN ('create', 'update', 'delete')"),
        CheckConstraint("before_data IS NULL OR jsonb_typeof(before_data) = 'object'"),
        CheckConstraint("after_data IS NULL OR jsonb_typeof(after_data) = 'object'"),
        CheckConstraint(
            "(action = 'create' AND before_data IS NULL AND after_data IS NOT NULL) OR "
            "(action = 'update' AND before_data IS NOT NULL AND after_data IS NOT NULL) OR "
            "(action = 'delete' AND before_data IS NOT NULL AND after_data IS NULL)"
        ),
        Index("admin_audit_events_actor_time_idx", "actor_id", text("created_at DESC")),
        Index("admin_audit_events_appointment_time_idx", "appointment_id", text("created_at DESC")),
        Index("admin_audit_events_request_idx", "request_id"),
        {"schema": "security"},
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))
    request_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    actor_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("healthcare.users.id", ondelete="RESTRICT"), nullable=False
    )
    appointment_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    before_data: Mapped[dict[str, Any] | None] = mapped_column(_JSONB)
    after_data: Mapped[dict[str, Any] | None] = mapped_column(_JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    actor: Mapped[User] = relationship(back_populates="admin_audit_events")


class AppliedMigration(Base):
    __tablename__ = "applied"
    __table_args__ = {"schema": "qi_migrations"}

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))
