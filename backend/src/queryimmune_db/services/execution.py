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
    def __init__(self, repository: HealthcareRepository, pipeline: RequestPipeline, profile_updater=None):
        self.repository = repository
        self.pipeline = pipeline
        self.profile_updater = profile_updater

    def handle(
        self,
        request_id: UUID,
        request: ExecutionRequest,
        actor: Actor,
        *,
        query_fingerprint: str | None = None,
        raise_on_block: bool = True,
    ) -> DecisionResponse:
        decision = self.pipeline.decide(request, actor)
        try:
            self.repository.write_decision(
                request_id, UUID(actor.user_id), decision, request.action, request.limit,
                query_fingerprint=query_fingerprint,
            )
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
                risk_score=decision.risk_score,
                risk_level=decision.feature_snapshot.get("risk_level") or ("HIGH" if decision.risk_score >= 60 else "MEDIUM" if decision.risk_score >= 25 else "LOW"),
                risk_analysis={
                    **{key: decision.feature_snapshot[key] for key in ("behavior", "risk_factors", "risk_reasons", "profile_version") if key in decision.feature_snapshot},
                    "risk_factors": decision.feature_snapshot.get("risk_factors", {"hard_policy": decision.risk_score}),
                },
            )
            if status_code == 403 and raise_on_block:
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
        if self.profile_updater is not None:
            try:
                self.profile_updater.observe_success(actor.user_id, decision.feature_snapshot, len(rows))
            except (psycopg.Error, RuntimeError, ValueError):
                # Profile maintenance is isolated from successful query execution and remains fail closed.
                logger.warning("behavior profile update deferred", extra={"request_id": str(request_id), "error_type": "profile_update"})
        return DecisionResponse(
            request_id=request_id,
            decision="allow",
            reason_codes=list(decision.reason_codes),
            message=decision.message,
            execution_status="succeeded",
            row_count=len(rows),
            results=rows,
            risk_score=decision.risk_score,
            risk_level=decision.feature_snapshot.get("risk_level") or ("HIGH" if decision.risk_score >= 60 else "MEDIUM" if decision.risk_score >= 25 else "LOW"),
            risk_analysis={
                **{key: decision.feature_snapshot[key] for key in ("behavior", "risk_factors", "risk_reasons", "profile_version") if key in decision.feature_snapshot},
                "risk_factors": decision.feature_snapshot.get("risk_factors", {"hard_policy": decision.risk_score}),
            },
        )
