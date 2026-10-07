from __future__ import annotations

import time
import uuid
import logging
import hashlib
from dataclasses import replace

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import psycopg

from queryimmune_db.api.auth import get_current_actor
from queryimmune_db.api.config import Settings
from queryimmune_db.api.logging import configure_logging
from queryimmune_db.api.models import (
    DecisionResponse,
    DashboardResponse,
    ExecutionRequest,
    HealthResponse,
    NaturalLanguageRequest,
    NaturalLanguageResponse,
)
from queryimmune_db.paths import project_root
from queryimmune_db.ai.provider import AIProviderError, configured_provider
from queryimmune_db.application.nl2sql import (
    FixedDecisionPolicy,
    NL2SQLPipeline,
    ValidatedIntentPolicy,
    execution_request,
)
from queryimmune_db.connection import database_health
from queryimmune_db.behavior import BehaviorAwarePipeline, ControlledProfileUpdater
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository
from queryimmune_db.services.execution import ExecutionService
from queryimmune_db.services.policy import Decision, DeterministicPolicyPipeline

settings = Settings.from_env()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="QueryImmune API",
    version="0.2.0",
    description="Policy-enforced PostgreSQL query gateway. Clients submit typed actions, never SQL.",
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.middleware("http")
async def request_context(request: Request, call_next):
    request_id = str(uuid.uuid4())
    request.state.request_id = request_id
    started = time.monotonic()
    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id
    logger.info("http request", extra={
        "request_id": request_id,
        "method": request.method,
        "path": request.url.path,
        "status_code": response.status_code,
        "duration_ms": max(0, int((time.monotonic() - started) * 1000)),
    })
    return response


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=422, content={
        "error": {"code": "VALIDATION_ERROR", "message": "Request does not match the API schema"},
        "request_id": getattr(request.state, "request_id", None),
    })


@app.exception_handler(HTTPException)
async def http_error_handler(request: Request, exc: HTTPException):
    detail = exc.detail
    if isinstance(detail, dict) and "request_id" in detail:
        content = detail
    else:
        content = {
            "error": {"code": "HTTP_ERROR", "message": detail if isinstance(detail, str) else "Request denied"},
            "request_id": getattr(request.state, "request_id", None),
        }
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception):
    # Keep unexpected failures useful to correlate, without returning exception,
    # SQL, provider, or connection details to the caller.
    logger.error("unhandled request failure", extra={
        "request_id": getattr(request.state, "request_id", None),
        "error_type": type(exc).__name__,
    })
    return JSONResponse(status_code=500, content={
        "error": {"code": "INTERNAL_ERROR", "message": "The request could not be completed"},
        "request_id": getattr(request.state, "request_id", None),
    })


@app.get("/health/live", response_model=HealthResponse, tags=["health"])
def liveness() -> HealthResponse:
    return HealthResponse(status="ok")


@app.get("/health/ready", response_model=HealthResponse, tags=["health"])
def readiness() -> HealthResponse:
    return HealthResponse(status=database_health()["status"])


@app.post("/api/v1/requests", response_model=DecisionResponse, tags=["execution"])
def execute_request(
    body: ExecutionRequest,
    request: Request,
    actor: Actor = Depends(get_current_actor),
) -> DecisionResponse:
    service = ExecutionService(
        repository=HealthcareRepository(),
        pipeline=BehaviorAwarePipeline(DeterministicPolicyPipeline(settings.result_limit, settings.review_limit)),
        profile_updater=ControlledProfileUpdater(),
    )
    return service.handle(uuid.UUID(request.state.request_id), body, actor)


@app.post("/api/v1/nl-requests", response_model=NaturalLanguageResponse, tags=["execution"])
def execute_natural_language_request(
    body: NaturalLanguageRequest,
    request: Request,
    response: Response,
    actor: Actor = Depends(get_current_actor),
) -> NaturalLanguageResponse:
    request_id = uuid.UUID(request.state.request_id)
    repository = HealthcareRepository()
    if body.demo_scenario is not None and settings.ai_provider != "demo":
        raise HTTPException(status_code=400, detail="Demo scenarios require AI_PROVIDER=demo")
    provider_model: str | None = None
    try:
        provider = configured_provider(settings, demo_scenario=body.demo_scenario)
        if provider is None:
            raise AIProviderError("AI provider is disabled")
        provider_model = provider.model_version
        pipeline = NL2SQLPipeline(provider)
        intent, schema_context = pipeline.extract_intent(body.request)
    except AIProviderError:
        return _audit_nl_failure(request_id, actor, repository, response, provider_model)

    intent_request = ExecutionRequest(action=intent.action, limit=intent.limit)
    preliminary = DeterministicPolicyPipeline(settings.result_limit, settings.review_limit).decide(intent_request, actor)
    if preliminary.state != "allow":
        decision = replace(
            preliminary,
            policy_version="phase3-intent-sql-v1",
            model_version=provider_model,
            feature_snapshot={"intent_action": intent.action, "intent_operation": intent.operation, "sql_generated": False},
        )
        result = ExecutionService(repository, FixedDecisionPolicy(decision)).handle(
            request_id, intent_request, actor,
            query_fingerprint=hashlib.sha256(b"PRE_SQL_POLICY_BLOCK").hexdigest(),
            raise_on_block=False,
        )
        response.status_code = 202 if result.decision == "review" else 403
        return NaturalLanguageResponse(
            request_id=request_id, decision=result.decision, reason_codes=result.reason_codes,
            message=result.message, intent=intent, provider_model=provider_model,
            execution_status="not_run",
        )

    try:
        analysis = pipeline.generate_and_validate(body.request, intent, schema_context)
    except AIProviderError:
        return _audit_nl_failure(request_id, actor, repository, response, provider_model)

    validation = analysis.validation
    if validation.valid:
        gateway_request = execution_request(intent)
    else:
        # Preserve a typed placeholder request; the validation pipeline hard-blocks it before execution.
        gateway_request = ExecutionRequest(action=intent.action, limit=intent.limit)
    gateway = ExecutionService(
        repository,
        BehaviorAwarePipeline(ValidatedIntentPolicy(analysis, settings.result_limit, settings.review_limit)),
        profile_updater=ControlledProfileUpdater(),
    )
    result = gateway.handle(
        request_id, gateway_request, actor,
        query_fingerprint=analysis.candidate_fingerprint,
        raise_on_block=False,
    )
    response.status_code = 403 if result.decision == "block" else 202 if result.decision == "review" else 200
    return NaturalLanguageResponse(
        request_id=request_id,
        decision=result.decision,
        reason_codes=result.reason_codes,
        message=result.message,
        intent=intent,
        validation=validation,
        provider_model=analysis.model_version,
        generated_sql=analysis.candidate_sql,
        risk_score=result.risk_score,
        risk_level=result.risk_level,
        risk_analysis=result.risk_analysis,
        execution_status=result.execution_status,
        row_count=result.row_count,
        results=result.results,
    )


@app.get("/api/v1/dashboard", response_model=DashboardResponse, tags=["audit"])
def dashboard(actor: Actor = Depends(get_current_actor)) -> DashboardResponse:
    try:
        data = HealthcareRepository().dashboard(uuid.UUID(actor.user_id))
    except (psycopg.Error, RuntimeError):
        # Do not expose connection details or audit contents in database failures.
        raise HTTPException(status_code=503, detail="Dashboard data is unavailable") from None
    return DashboardResponse(**data)


@app.get("/api/v1/demo/config", tags=["demo"])
def demo_configuration() -> dict[str, bool]:
    """Expose whether explicitly configured deterministic fixtures are available."""
    return {"enabled": settings.ai_provider == "demo"}


def _audit_nl_failure(request_id, actor, repository, response, model_version: str | None) -> NaturalLanguageResponse:
    decision = Decision(
        "block", ("AI_PROVIDER_UNAVAILABLE",), 100.0,
        "Natural-language processing is unavailable", "phase3-intent-sql-v1",
        model_version, {"validated": False, "mismatch_codes": ["AI_PROVIDER_UNAVAILABLE"]},
    )
    gateway = ExecutionService(repository, FixedDecisionPolicy(decision))
    result = gateway.handle(
        request_id, ExecutionRequest(action="ai.unavailable", limit=25), actor,
        query_fingerprint=hashlib.sha256(b"NO_SQL_CANDIDATE").hexdigest(), raise_on_block=False,
    )
    response.status_code = 503
    return NaturalLanguageResponse(
        request_id=request_id, decision=result.decision, reason_codes=result.reason_codes,
        message=result.message, provider_model=model_version, execution_status="not_run",
    )


WEB_ROOT = project_root() / "web"
app.mount("/static", StaticFiles(directory=WEB_ROOT), name="static")


@app.get("/", include_in_schema=False)
def demo_ui() -> FileResponse:
    return FileResponse(WEB_ROOT / "index.html")

