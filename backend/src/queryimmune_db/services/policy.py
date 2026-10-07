from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Protocol

from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.domain.identity import Actor


@dataclass(frozen=True)
class Decision:
    state: str
    reason_codes: tuple[str, ...]
    risk_score: float
    message: str
    policy_version: str = "phase2-static-v1"
    model_version: str | None = None
    feature_snapshot: dict[str, object] = field(default_factory=dict)
    profile_version: int | None = None


class RequestPipeline(Protocol):
    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision: ...


class DeterministicPolicyPipeline:
    """Phase 2 hard policy; designed for later intent/risk providers to compose with."""

    _allowed_roles = {
        "appointments.mine": frozenset({"doctor"}),
        "appointments.by_status": frozenset({"analyst", "admin"}),
        "billing.summary": frozenset({"billing", "admin"}),
        "appointments.cardiology_today": frozenset({"doctor", "analyst", "admin"}),
    }

    def __init__(self, result_limit: int = 100, review_limit: int = 25):
        self.result_limit = result_limit
        self.review_limit = review_limit

    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision:
        metadata = {
            "action": request.action,
            "operation": "read",
            "tables": {
                "appointments.mine": ["appointments", "patients", "doctors"],
                "appointments.by_status": ["appointments", "doctors"],
                "billing.summary": ["billing"],
                "appointments.cardiology_today": ["appointments", "doctors"],
            }.get(request.action, []),
            "columns": {
                "appointments.mine": ["scheduled_at", "status", "reason_category", "synthetic_patient_number", "specialty"],
                "appointments.by_status": ["status", "specialty", "appointment_count"],
                "billing.summary": ["status", "currency", "invoice_count", "total_amount"],
                "appointments.cardiology_today": ["patient_id", "patient_count"],
            }.get(request.action, []),
            "semantic_signature": f"{request.action}:limit={request.limit}:date={'yes' if request.from_date else 'no'}",
            "scope": "own_records" if request.action == "appointments.mine" else "aggregate",
            "sensitivity": "sensitive" if request.action == "billing.summary" else "standard",
            "limit": request.limit,
        }
        allowed_roles = self._allowed_roles.get(request.action)
        if allowed_roles is None:
            return Decision("block", ("UNSUPPORTED_ACTION",), 100.0, "Action is not supported", feature_snapshot=metadata)
        if not actor.roles.intersection(allowed_roles):
            return Decision("block", ("ROLE_NOT_ALLOWED",), 100.0, "Role is not allowed to perform this action", feature_snapshot={**metadata, "allowed_roles": sorted(allowed_roles)})
        if request.limit > self.result_limit:
            return Decision("block", ("QUERY_LIMIT_EXCEEDED",), 100.0, "Requested result size exceeds the configured limit", feature_snapshot=metadata)
        if request.limit > self.review_limit:
            return Decision("review", ("SCOPE_TOO_BROAD",), 50.0, "Request requires review because its scope is broad", feature_snapshot=metadata)
        return Decision("allow", (), 0.0, "Request passed the deterministic security policy", feature_snapshot={**metadata, "allowed_roles": sorted(allowed_roles)})
