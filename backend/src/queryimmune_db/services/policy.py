from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.domain.identity import Actor


@dataclass(frozen=True)
class Decision:
    state: str
    reason_codes: tuple[str, ...]
    risk_score: float
    message: str


class RequestPipeline(Protocol):
    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision: ...


class DeterministicPolicyPipeline:
    """Phase 2 hard policy; designed for later intent/risk providers to compose with."""

    _allowed_roles = {
        "appointments.mine": frozenset({"doctor"}),
        "appointments.by_status": frozenset({"analyst", "admin"}),
        "billing.summary": frozenset({"billing", "admin"}),
    }

    def __init__(self, result_limit: int = 100, review_limit: int = 25):
        self.result_limit = result_limit
        self.review_limit = review_limit

    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision:
        allowed_roles = self._allowed_roles.get(request.action)
        if allowed_roles is None:
            return Decision("block", ("UNSUPPORTED_ACTION",), 100.0, "Action is not supported")
        if not actor.roles.intersection(allowed_roles):
            return Decision("block", ("ROLE_NOT_ALLOWED",), 100.0, "Role is not allowed to perform this action")
        if request.limit > self.result_limit:
            return Decision("block", ("QUERY_LIMIT_EXCEEDED",), 100.0, "Requested result size exceeds the configured limit")
        if request.limit > self.review_limit:
            return Decision("review", ("SCOPE_TOO_BROAD",), 50.0, "Request requires review because its scope is broad")
        return Decision("allow", (), 0.0, "Request passed the deterministic security policy")
