from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field
from typing import Callable, Protocol

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
    """Deterministic action policy backed by PostgreSQL authorization rows."""

    # This describes the fixed data footprint of broker-supported actions. It is
    # not an authorization matrix; permissions for every pair live in PostgreSQL.
    _action_resources = {
        "appointments.mine": ("appointments", "patients", "doctors"),
        "appointments.by_status": ("appointments", "doctors"),
        "billing.summary": ("billing",),
        "appointments.cardiology_today": ("appointments", "doctors"),
    }

    def __init__(
        self,
        result_limit: int = 100,
        review_limit: int = 25,
        authorization_check: Callable[[str, str, tuple[str, ...]], bool] | None = None,
    ):
        self.result_limit = result_limit
        self.review_limit = review_limit
        self.authorization_check = authorization_check or _database_authorization_check

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
        resources = self._action_resources.get(request.action)
        if resources is None:
            return Decision("block", ("UNSUPPORTED_ACTION",), 100.0, "Action is not supported", feature_snapshot=metadata)
        try:
            authorized = self.authorization_check(actor.user_id, request.action, resources)
        except Exception:
            # A failed policy lookup can never become an implicit grant.
            return Decision("block", ("AUTHORIZATION_DATA_UNAVAILABLE",), 100.0,
                            "Authorization data is unavailable", feature_snapshot=metadata)
        if not authorized:
            return Decision("block", ("ROLE_NOT_ALLOWED",), 100.0,
                            "Role is not allowed to perform this action", feature_snapshot=metadata)
        if request.limit > self.result_limit:
            return Decision("block", ("QUERY_LIMIT_EXCEEDED",), 100.0, "Requested result size exceeds the configured limit", feature_snapshot=metadata)
        if request.limit > self.review_limit:
            return Decision("review", ("SCOPE_TOO_BROAD",), 50.0, "Request requires review because its scope is broad", feature_snapshot=metadata)
        return Decision("allow", (), 0.0, "Request passed the deterministic security policy", feature_snapshot=metadata)


def _database_authorization_check(actor_id: str, action: str, resources: tuple[str, ...]) -> bool:
    # Lazy import avoids coupling the policy module to repository initialization.
    from queryimmune_db.repositories.healthcare import HealthcareRepository

    return HealthcareRepository().is_action_authorized(actor_id, action, resources)
