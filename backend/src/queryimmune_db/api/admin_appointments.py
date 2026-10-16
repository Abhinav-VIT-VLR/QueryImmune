from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Annotated

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import SQLAlchemyError

from queryimmune_db.api.admin_models import (
    AppointmentAuditPage,
    AppointmentAuditRead,
    AppointmentCreate,
    AppointmentPage,
    AppointmentRead,
    AppointmentStatus,
    AppointmentUpdate,
)
from queryimmune_db.api.auth import get_current_actor
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.admin_appointments import (
    AdminAppointmentRepository,
    AppointmentConflict,
    AppointmentDeleteConflict,
    AppointmentNotFound,
    AppointmentReferenceError,
)
from queryimmune_db.repositories.healthcare import HealthcareRepository
from queryimmune_db.services.admin_appointments import AdminAppointmentService

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1/admin/appointments", tags=["appointment administration"])


def get_admin_appointment_service() -> AdminAppointmentService:
    return AdminAppointmentService(AdminAppointmentRepository())


def require_appointment_admin(actor: Actor = Depends(get_current_actor)) -> Actor:
    try:
        allowed = HealthcareRepository().is_action_authorized(
            actor.user_id, "appointments.admin_crud", ("appointments",),
        )
    except (psycopg.Error, RuntimeError) as exc:
        raise HTTPException(status_code=503, detail="Authorization service is unavailable") from exc
    if not allowed:
        raise HTTPException(status_code=403, detail="Administrator access required")
    return actor


def _actor_id(actor: Actor) -> uuid.UUID:
    try:
        return uuid.UUID(actor.user_id)
    except ValueError as exc:
        raise HTTPException(status_code=403, detail="Administrator identity is invalid") from exc


def _request_id(request: Request) -> uuid.UUID:
    return uuid.UUID(request.state.request_id)


def _raise_operation_error(exc: Exception, request_id: uuid.UUID) -> None:
    if isinstance(exc, AppointmentNotFound):
        raise HTTPException(status_code=404, detail="Appointment not found") from exc
    if isinstance(exc, AppointmentReferenceError):
        raise HTTPException(status_code=422, detail="Referenced patient or doctor does not exist") from exc
    if isinstance(exc, AppointmentDeleteConflict):
        raise HTTPException(status_code=409, detail="Appointment is referenced by another record") from exc
    if isinstance(exc, AppointmentConflict):
        raise HTTPException(status_code=409, detail="Appointment conflicts with a database constraint") from exc
    if isinstance(exc, SQLAlchemyError):
        logger.error(
            "admin appointment database operation failed",
            extra={"request_id": str(request_id), "error_type": type(exc).__name__},
        )
        raise HTTPException(status_code=503, detail="Appointment data is unavailable") from exc
    raise exc


def _require_timezone(value: datetime | None, name: str) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise HTTPException(status_code=422, detail=f"{name} must include a timezone")


@router.get("/audit", response_model=AppointmentAuditPage)
def list_appointment_audit(
    request: Request,
    actor: Annotated[Actor, Depends(require_appointment_admin)],
    service: Annotated[AdminAppointmentService, Depends(get_admin_appointment_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    appointment_id: uuid.UUID | None = None,
) -> AppointmentAuditPage:
    actor_id, request_id = _actor_id(actor), _request_id(request)
    try:
        events, total = service.list_audit_events(
            actor_id, request_id, limit=limit, offset=offset, appointment_id=appointment_id,
        )
        return AppointmentAuditPage(
            items=[AppointmentAuditRead.model_validate(event) for event in events],
            total=total, limit=limit, offset=offset,
        )
    except (AppointmentNotFound, AppointmentReferenceError, AppointmentDeleteConflict, AppointmentConflict, SQLAlchemyError) as exc:
        _raise_operation_error(exc, request_id)


@router.get("", response_model=AppointmentPage)
def list_appointments(
    request: Request,
    actor: Annotated[Actor, Depends(require_appointment_admin)],
    service: Annotated[AdminAppointmentService, Depends(get_admin_appointment_service)],
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    offset: Annotated[int, Query(ge=0, le=100000)] = 0,
    status_filter: Annotated[AppointmentStatus | None, Query(alias="status")] = None,
    patient_id: uuid.UUID | None = None,
    doctor_id: uuid.UUID | None = None,
    scheduled_from: datetime | None = None,
    scheduled_to: datetime | None = None,
) -> AppointmentPage:
    _require_timezone(scheduled_from, "scheduled_from")
    _require_timezone(scheduled_to, "scheduled_to")
    if scheduled_from is not None and scheduled_to is not None and scheduled_from > scheduled_to:
        raise HTTPException(status_code=422, detail="scheduled_from must not be later than scheduled_to")
    actor_id, request_id = _actor_id(actor), _request_id(request)
    try:
        items, total = service.list_appointments(
            actor_id,
            request_id,
            limit=limit,
            offset=offset,
            status=status_filter,
            patient_id=patient_id,
            doctor_id=doctor_id,
            scheduled_from=scheduled_from,
            scheduled_to=scheduled_to,
        )
        return AppointmentPage(
            items=[AppointmentRead.model_validate(item) for item in items],
            total=total, limit=limit, offset=offset,
        )
    except (AppointmentNotFound, AppointmentReferenceError, AppointmentDeleteConflict, AppointmentConflict, SQLAlchemyError) as exc:
        _raise_operation_error(exc, request_id)


@router.post("", response_model=AppointmentRead, status_code=status.HTTP_201_CREATED)
def create_appointment(
    body: AppointmentCreate,
    request: Request,
    actor: Annotated[Actor, Depends(require_appointment_admin)],
    service: Annotated[AdminAppointmentService, Depends(get_admin_appointment_service)],
) -> AppointmentRead:
    request_id = _request_id(request)
    try:
        appointment = service.create_appointment(
            _actor_id(actor), request_id, body.model_dump(),
        )
        return AppointmentRead.model_validate(appointment)
    except (AppointmentNotFound, AppointmentReferenceError, AppointmentDeleteConflict, AppointmentConflict, SQLAlchemyError) as exc:
        _raise_operation_error(exc, request_id)


@router.get("/{appointment_id}", response_model=AppointmentRead)
def get_appointment(
    appointment_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_appointment_admin)],
    service: Annotated[AdminAppointmentService, Depends(get_admin_appointment_service)],
) -> AppointmentRead:
    request_id = _request_id(request)
    try:
        appointment = service.get_appointment(_actor_id(actor), request_id, appointment_id)
        return AppointmentRead.model_validate(appointment)
    except (AppointmentNotFound, AppointmentReferenceError, AppointmentDeleteConflict, AppointmentConflict, SQLAlchemyError) as exc:
        _raise_operation_error(exc, request_id)


@router.patch("/{appointment_id}", response_model=AppointmentRead)
def update_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentUpdate,
    request: Request,
    actor: Annotated[Actor, Depends(require_appointment_admin)],
    service: Annotated[AdminAppointmentService, Depends(get_admin_appointment_service)],
) -> AppointmentRead:
    request_id = _request_id(request)
    try:
        appointment = service.update_appointment(
            _actor_id(actor), request_id, appointment_id, body.model_dump(exclude_unset=True),
        )
        return AppointmentRead.model_validate(appointment)
    except (AppointmentNotFound, AppointmentReferenceError, AppointmentDeleteConflict, AppointmentConflict, SQLAlchemyError) as exc:
        _raise_operation_error(exc, request_id)


@router.delete("/{appointment_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_appointment(
    appointment_id: uuid.UUID,
    request: Request,
    actor: Annotated[Actor, Depends(require_appointment_admin)],
    service: Annotated[AdminAppointmentService, Depends(get_admin_appointment_service)],
) -> Response:
    request_id = _request_id(request)
    try:
        service.delete_appointment(_actor_id(actor), request_id, appointment_id)
    except (AppointmentNotFound, AppointmentReferenceError, AppointmentDeleteConflict, AppointmentConflict, SQLAlchemyError) as exc:
        _raise_operation_error(exc, request_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
