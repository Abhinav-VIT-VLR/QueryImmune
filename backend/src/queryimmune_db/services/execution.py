from __future__ import annotations

import logging
import time
from uuid import UUID

from fastapi import HTTPException
import psycopg

from queryimmune_db.api.models import DecisionResponse, ExecutionRequest
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository
from queryimmune_db.services.policy import RequestPipeline

logger = logging.getLogger(__name__)


class ExecutionService:
    def __init__(self, repository: HealthcareRepository, pipeline: RequestPipeline):
        self.repository = repository
        self.pipeline = pipeline

    def handle(self, request_id: UUID, request: ExecutionRequest, actor: Actor) -> DecisionResponse:
        decision = self.pipeline.decide(request, actor)
        try:
            self.repository.write_decision(request_id, UUID(actor.user_id), decision, request.action, request.limit)
        except (psycopg.Error, RuntimeError):
            logger.error("failed to persist security decision", extra={"request_id": str(request_id), "error_type": "database"})
            raise HTTPException(status_code=503, detail="Security decision could not be recorded") from None

        if decision.state != "allow":
            status_code = 202 if decision.state == "review" else 403
            response = DecisionResponse(
                request_id=request_id,
                decision=decision.state,
                reason_codes=list(decision.reason_codes),
                message=decision.message,
                execution_status="not_run",
            )
            if status_code == 403:
                # The response carries the audited decision while retaining HTTP authorization semantics.
                raise HTTPException(status_code=403, detail=response.model_dump(mode="json"))
            return response

        started = time.monotonic()
        try:
            rows = self.repository.execute_action(request.action, UUID(actor.user_id), request.limit, request.from_date)
        except (psycopg.Error, RuntimeError, ValueError):
            duration_ms = max(0, int((time.monotonic() - started) * 1000))
            try:
                self.repository.finish_execution(request_id, "failed", duration_ms, None)
            except psycopg.Error:
                logger.error("failed to finalize query audit", extra={"request_id": str(request_id), "error_type": "database"})
            logger.error("database action failed", extra={"request_id": str(request_id), "error_type": "database"})
            raise HTTPException(status_code=503, detail="Database action failed") from None
        duration_ms = max(0, int((time.monotonic() - started) * 1000))
        try:
            self.repository.finish_execution(request_id, "succeeded", duration_ms, len(rows))
        except psycopg.Error:
            logger.error("failed to finalize query audit", extra={"request_id": str(request_id), "error_type": "database"})
            raise HTTPException(status_code=503, detail="Execution audit could not be completed") from None
        return DecisionResponse(
            request_id=request_id,
            decision="allow",
            reason_codes=list(decision.reason_codes),
            message=decision.message,
            execution_status="succeeded",
            row_count=len(rows),
            results=rows,
        )
