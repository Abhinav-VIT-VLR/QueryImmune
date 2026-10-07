from __future__ import annotations

from dataclasses import dataclass, replace
import hashlib
from typing import Callable

from queryimmune_db.ai.intent import QueryIntent, ValidationResult
from queryimmune_db.ai.provider import LLMProvider
from queryimmune_db.ai.service import NL2SQLService
from queryimmune_db.ai.context import SchemaContextRetriever
from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.domain.identity import Actor
from queryimmune_db.security.sql_validation import IntentSqlValidator
from queryimmune_db.services.policy import Decision, DeterministicPolicyPipeline


@dataclass(frozen=True)
class NL2SQLAnalysis:
    intent: QueryIntent
    validation: ValidationResult
    model_version: str
    candidate_fingerprint: str
    candidate_sql: str


class NL2SQLPipeline:
    """Generate intent/candidate SQL and validate the candidate before gateway policy."""

    def __init__(
        self,
        provider: LLMProvider,
        context: SchemaContextRetriever | None = None,
        validator: IntentSqlValidator | None = None,
    ):
        self.provider = provider
        self.context = context or SchemaContextRetriever()
        self.validator = validator or IntentSqlValidator()
        self.analyzer = NL2SQLService(provider, self.context)

    def analyze(self, request_text: str) -> NL2SQLAnalysis:
        intent, context = self.analyzer.extract_intent(request_text)
        return self.generate_and_validate(request_text, intent, context)

    def extract_intent(self, request_text: str):
        return self.analyzer.extract_intent(request_text)

    def generate_and_validate(self, request_text: str, intent: QueryIntent, context: dict[str, object]) -> NL2SQLAnalysis:
        candidate_sql = self.analyzer.generate_sql(request_text, intent, context)
        validation = self.validator.validate(request_text, intent, candidate_sql)
        candidate_fingerprint = validation.analysis.canonical_fingerprint if validation.analysis else hashlib.sha256(candidate_sql.encode("utf-8")).hexdigest()
        return NL2SQLAnalysis(
            intent=intent, validation=validation, model_version=self.provider.model_version,
            candidate_fingerprint=candidate_fingerprint, candidate_sql=candidate_sql,
        )


class ValidatedIntentPolicy:
    """Preserves deterministic role/risk restrictions and vetoes every mismatch first."""

    def __init__(
        self,
        analysis: NL2SQLAnalysis,
        result_limit: int,
        review_limit: int,
        authorization_check: Callable[[str, str, tuple[str, ...]], bool] | None = None,
    ):
        self.analysis = analysis
        self.static_policy = DeterministicPolicyPipeline(result_limit, review_limit, authorization_check)

    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision:
        validation = self.analysis.validation
        intent = self.analysis.intent
        features = {
            "validated": validation.valid,
            "intent_action": intent.action,
            "intent_operation": intent.operation,
            "intent_tables": intent.tables,
            "intent_columns": intent.columns,
            "mismatch_codes": [reason.code for reason in validation.reasons],
            "sql_fingerprint": validation.analysis.canonical_fingerprint if validation.analysis else None,
            "candidate_fingerprint": self.analysis.candidate_fingerprint,
            "action": intent.action,
            "operation": intent.operation,
            "tables": intent.tables,
            "columns": intent.columns,
            "scope": intent.scope,
            "sensitivity": "restricted" if "restricted" in intent.sensitivity_expectations else "sensitive" if "sensitive" in intent.sensitivity_expectations else "standard",
            "semantic_signature": ":".join((intent.action, ",".join(sorted(intent.tables)), ",".join(sorted(intent.columns)), ",".join(sorted(intent.aggregations)), intent.scope, f"limit-{_limit_bucket(intent.limit)}")),
        }
        if not validation.valid:
            return Decision(
                "block", tuple(reason.code for reason in validation.reasons), 100.0,
                "Generated SQL did not match the requested action", "phase3-intent-sql-v1",
                self.analysis.model_version, features,
            )
        decision = self.static_policy.decide(request, actor)
        features["limit"] = intent.limit
        return replace(
            decision,
            policy_version="phase3-intent-sql-v1",
            model_version=self.analysis.model_version,
            feature_snapshot=features,
        )


class FixedDecisionPolicy:
    """Persists fail-closed AI/provider decisions through the same audit gateway."""

    def __init__(self, decision: Decision):
        self.decision = decision

    def decide(self, request: ExecutionRequest, actor: Actor) -> Decision:
        return self.decision


def execution_request(intent: QueryIntent) -> ExecutionRequest:
    from datetime import date

    date_filter = None
    date_column = "scheduled_at" if intent.action.startswith("appointments.") else "created_at"
    for item in intent.filters:
        if item.column.rsplit(".", 1)[-1] == date_column and item.operator == "gte":
            date_filter = date.fromisoformat(str(item.value))
    return ExecutionRequest(action=intent.action, limit=intent.limit, from_date=date_filter)


def _limit_bucket(limit: int) -> str:
    return "small" if limit <= 10 else "standard" if limit <= 25 else "broad"
