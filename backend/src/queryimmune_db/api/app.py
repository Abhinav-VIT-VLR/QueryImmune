from __future__ import annotations

import time
import uuid
import logging

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from queryimmune_db.api.auth import get_current_actor
from queryimmune_db.api.config import Settings
from queryimmune_db.api.logging import configure_logging
from queryimmune_db.api.models import DecisionResponse, ExecutionRequest, HealthResponse
from queryimmune_db.connection import database_health
from queryimmune_db.domain.identity import Actor
from queryimmune_db.repositories.healthcare import HealthcareRepository
from queryimmune_db.services.execution import ExecutionService
from queryimmune_db.services.policy import DeterministicPolicyPipeline

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
        pipeline=DeterministicPolicyPipeline(settings.result_limit, settings.review_limit),
    )
    return service.handle(uuid.UUID(request.state.request_id), body, actor)

