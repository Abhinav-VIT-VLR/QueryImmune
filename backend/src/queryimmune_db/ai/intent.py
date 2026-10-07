from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class IntentFilter(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    column: str = Field(min_length=1, max_length=80, pattern=r"^[a-z_][a-z0-9_.]*$")
    operator: Literal["eq", "gte", "gt", "lte", "lt"]
    value: str | int | float | bool


class IntentOrder(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    column: str = Field(min_length=1, max_length=80, pattern=r"^[a-z_][a-z0-9_.]*$")
    direction: Literal["asc", "desc"]


class QueryIntent(BaseModel):
    """Validated model output describing the requested database operation."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    operation: Literal["read", "insert", "update", "delete"]
    action: str = Field(min_length=1, max_length=80, pattern=r"^[a-z][a-z0-9_.-]*$")
    tables: list[str] = Field(min_length=1, max_length=8)
    columns: list[str] = Field(max_length=32)
    filters: list[IntentFilter] = Field(max_length=16)
    aggregations: list[Literal["count", "sum", "avg", "min", "max"]] = Field(max_length=8)
    group_by: list[str] = Field(max_length=16)
    ordering: list[IntentOrder] = Field(max_length=8)
    limit: int = Field(ge=1, le=500)
    scope: Literal["own_records", "bounded", "aggregate", "all_records", "unknown"]
    sensitivity_expectations: list[Literal["standard", "sensitive", "restricted"]] = Field(max_length=3)


class MismatchReason(BaseModel):
    code: str
    message: str
    expected: str | None = None
    actual: str | None = None


class SQLAnalysis(BaseModel):
    operation: str
    tables: list[str]
    columns: list[str]
    projected_columns: list[str]
    filters: list[IntentFilter]
    aggregations: list[str]
    group_by: list[str]
    ordering: list[IntentOrder]
    limit: int | None
    canonical_fingerprint: str


class ValidationResult(BaseModel):
    valid: bool
    reasons: list[MismatchReason]
    analysis: SQLAnalysis | None = None

