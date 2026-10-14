from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

AppointmentStatus = Literal["scheduled", "completed", "cancelled", "no_show"]
AuditAction = Literal["create", "update", "delete"]


class AppointmentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    patient_id: UUID
    doctor_id: UUID
    scheduled_at: datetime
    status: AppointmentStatus = "scheduled"
    reason_category: str = Field(min_length=1, max_length=80)

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("scheduled_at must include a timezone")
        return value


class AppointmentUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    patient_id: UUID | None = None
    doctor_id: UUID | None = None
    scheduled_at: datetime | None = None
    status: AppointmentStatus | None = None
    reason_category: str | None = Field(default=None, min_length=1, max_length=80)

    @model_validator(mode="after")
    def require_changes(self) -> AppointmentUpdate:
        changes = self.model_dump(exclude_unset=True)
        if not changes:
            raise ValueError("at least one appointment field must be provided")
        if any(value is None for value in changes.values()):
            raise ValueError("appointment fields cannot be null")
        return self

    @field_validator("scheduled_at")
    @classmethod
    def require_timezone(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("scheduled_at must include a timezone")
        return value


class AppointmentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    patient_id: UUID
    doctor_id: UUID
    scheduled_at: datetime
    status: AppointmentStatus
    reason_category: str
    created_at: datetime
    updated_at: datetime


class AppointmentPage(BaseModel):
    items: list[AppointmentRead]
    total: int
    limit: int
    offset: int


class AppointmentAuditRead(BaseModel):
    id: UUID
    request_id: UUID
    actor_id: UUID
    appointment_id: UUID
    action: AuditAction
    before_data: dict[str, Any] | None
    after_data: dict[str, Any] | None
    created_at: datetime


class AppointmentAuditPage(BaseModel):
    items: list[AppointmentAuditRead]
    total: int
    limit: int
    offset: int
