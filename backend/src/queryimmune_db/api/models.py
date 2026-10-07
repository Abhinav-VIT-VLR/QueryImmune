from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ExecutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    limit: int = Field(default=25, ge=1, le=500)
    from_date: date | None = None


class DecisionResponse(BaseModel):
    request_id: UUID
    decision: Literal["allow", "review", "block"]
    reason_codes: list[str]
    message: str
    execution_status: Literal["not_run", "succeeded", "failed"]
    row_count: int | None = None
    results: list[dict[str, object]] | None = None


class HealthResponse(BaseModel):
    status: Literal["ok", "unavailable"]
