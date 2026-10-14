from __future__ import annotations

from datetime import datetime
from uuid import UUID

from queryimmune_db.orm.models import AdminAuditEvent, Appointment
from queryimmune_db.repositories.admin_appointments import AdminAppointmentRepository


class AdminAppointmentService:
    def __init__(self, repository: AdminAppointmentRepository | None = None):
        self.repository = repository or AdminAppointmentRepository()

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
        return self.repository.list_appointments(
            actor_id,
            request_id,
            limit=limit,
            offset=offset,
            status=status,
            patient_id=patient_id,
            doctor_id=doctor_id,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
        )

    def get_appointment(self, actor_id: UUID, request_id: UUID, appointment_id: UUID) -> Appointment:
        return self.repository.get_appointment(actor_id, request_id, appointment_id)

    def create_appointment(
        self, actor_id: UUID, request_id: UUID, values: dict[str, object],
    ) -> Appointment:
        return self.repository.create_appointment(actor_id, request_id, values)

    def update_appointment(
        self, actor_id: UUID, request_id: UUID, appointment_id: UUID, values: dict[str, object],
    ) -> Appointment:
        return self.repository.update_appointment(actor_id, request_id, appointment_id, values)

    def delete_appointment(self, actor_id: UUID, request_id: UUID, appointment_id: UUID) -> None:
        self.repository.delete_appointment(actor_id, request_id, appointment_id)

    def list_audit_events(
        self, actor_id: UUID, request_id: UUID, *, limit: int, offset: int, appointment_id: UUID | None,
    ) -> tuple[list[AdminAuditEvent], int]:
        return self.repository.list_audit_events(
            actor_id, request_id, limit=limit, offset=offset, appointment_id=appointment_id,
        )
