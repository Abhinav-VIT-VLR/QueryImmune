from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from queryimmune_db.orm.models import AdminAuditEvent, Appointment
from queryimmune_db.orm.session import session_scope


class AppointmentNotFound(Exception):
    pass


class AppointmentReferenceError(Exception):
    pass


class AppointmentConflict(Exception):
    pass


class AppointmentDeleteConflict(Exception):
    pass


def _sqlstate(error: IntegrityError) -> str | None:
    return getattr(error.orig, "sqlstate", None)


class AdminAppointmentRepository:
    """ORM access for appointment administration; never used by the SQL gateway."""

    def __init__(self, factory: sessionmaker[Session] | None = None):
        self.factory = factory

    def list_appointments(
        self,
        actor_id: UUID,
        request_id: UUID,
        *,
        limit: int,
        offset: int,
        status: str | None,
        patient_id: UUID | None,
        doctor_id: UUID | None,
        scheduled_from: datetime | None,
        scheduled_to: datetime | None,
    ) -> tuple[list[Appointment], int]:
        statement = select(Appointment)
        count_statement = select(func.count()).select_from(Appointment)
        filters = []
        if status is not None:
            filters.append(Appointment.status == status)
        if patient_id is not None:
            filters.append(Appointment.patient_id == patient_id)
        if doctor_id is not None:
            filters.append(Appointment.doctor_id == doctor_id)
        if scheduled_from is not None:
            filters.append(Appointment.scheduled_at >= scheduled_from)
        if scheduled_to is not None:
            filters.append(Appointment.scheduled_at <= scheduled_to)
        if filters:
            statement = statement.where(*filters)
            count_statement = count_statement.where(*filters)
        statement = statement.order_by(Appointment.scheduled_at.desc(), Appointment.id).limit(limit).offset(offset)
        with session_scope(self.factory, actor_id=str(actor_id), request_id=str(request_id)) as session:
            total = session.scalar(count_statement)
            items = list(session.scalars(statement).all())
        return items, int(total or 0)

    def get_appointment(self, actor_id: UUID, request_id: UUID, appointment_id: UUID) -> Appointment:
        with session_scope(self.factory, actor_id=str(actor_id), request_id=str(request_id)) as session:
            appointment = session.get(Appointment, appointment_id)
            if appointment is None:
                raise AppointmentNotFound
            return appointment

    def create_appointment(
        self, actor_id: UUID, request_id: UUID, values: dict[str, object],
    ) -> Appointment:
        try:
            with session_scope(self.factory, actor_id=str(actor_id), request_id=str(request_id)) as session:
                appointment = Appointment(**values)
                session.add(appointment)
                session.flush()
                session.refresh(appointment)
                return appointment
        except IntegrityError as exc:
            if _sqlstate(exc) == "23503":
                raise AppointmentReferenceError from exc
            raise AppointmentConflict from exc

    def update_appointment(
        self, actor_id: UUID, request_id: UUID, appointment_id: UUID, values: dict[str, object],
    ) -> Appointment:
        try:
            with session_scope(self.factory, actor_id=str(actor_id), request_id=str(request_id)) as session:
                appointment = session.scalar(
                    select(Appointment).where(Appointment.id == appointment_id).with_for_update()
                )
                if appointment is None:
                    raise AppointmentNotFound
                for field, value in values.items():
                    setattr(appointment, field, value)
                appointment.updated_at = func.now()
                session.flush()
                session.refresh(appointment)
                return appointment
        except IntegrityError as exc:
            if _sqlstate(exc) == "23503":
                raise AppointmentReferenceError from exc
            raise AppointmentConflict from exc

    def delete_appointment(self, actor_id: UUID, request_id: UUID, appointment_id: UUID) -> None:
        try:
            with session_scope(self.factory, actor_id=str(actor_id), request_id=str(request_id)) as session:
                appointment = session.scalar(
                    select(Appointment).where(Appointment.id == appointment_id).with_for_update()
                )
                if appointment is None:
                    raise AppointmentNotFound
                session.delete(appointment)
        except IntegrityError as exc:
            if _sqlstate(exc) == "23503":
                raise AppointmentDeleteConflict from exc
            raise AppointmentConflict from exc

    def list_audit_events(
        self,
        actor_id: UUID,
        request_id: UUID,
        *,
        limit: int,
        offset: int,
        appointment_id: UUID | None,
    ) -> tuple[list[AdminAuditEvent], int]:
        statement = select(AdminAuditEvent)
        count_statement = select(func.count()).select_from(AdminAuditEvent)
        if appointment_id is not None:
            statement = statement.where(AdminAuditEvent.appointment_id == appointment_id)
            count_statement = count_statement.where(AdminAuditEvent.appointment_id == appointment_id)
        statement = statement.order_by(AdminAuditEvent.created_at.desc(), AdminAuditEvent.id).limit(limit).offset(offset)
        with session_scope(self.factory, actor_id=str(actor_id), request_id=str(request_id)) as session:
            total = session.scalar(count_statement)
            items = list(session.scalars(statement).all())
        return items, int(total or 0)
