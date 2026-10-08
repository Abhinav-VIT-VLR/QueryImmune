from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from queryimmune_db.ai.intent import QueryIntent, ValidationResult


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    limit: int = Field(default=25, ge=1, le=500)
    from_date: date | None = None


class NaturalLanguageRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    request: str = Field(min_length=3, max_length=2000)
    demo_scenario: Literal["normal", "sensitive", "destructive", "mismatch", "drift"] | None = None


class DecisionResponse(BaseModel):
    request_id: UUID
    decision: Literal["allow", "review", "block"]
    reason_codes: list[str]
    message: str
    execution_status: Literal["not_run", "succeeded", "failed"]
    risk_score: float | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    risk_analysis: dict[str, object] | None = None
    row_count: int | None = None
    results: list[dict[str, object]] | None = None


class NaturalLanguageResponse(BaseModel):
    request_id: UUID
    decision: Literal["allow", "review", "block"]
    outcome: Literal["security_decision", "unsupported_intent", "ambiguous_request", "ai_provider_unavailable", "validation_error"] = "security_decision"
    reason_codes: list[str]
    message: str
    intent: QueryIntent | None = None
    validation: ValidationResult | None = None
    provider_model: str | None = None
    generated_sql: str | None = None
    risk_score: float | None = None
    risk_level: Literal["LOW", "MEDIUM", "HIGH"] | None = None
    risk_analysis: dict[str, object] | None = None
    execution_status: Literal["not_run", "succeeded", "failed"]
    row_count: int | None = None
    results: list[dict[str, object]] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]


class DashboardResponse(BaseModel):
    history: list[dict[str, object]]
    risk_events: list[dict[str, object]]
    behavior_profile: dict[str, object] | None
    workload: list[dict[str, object]]
