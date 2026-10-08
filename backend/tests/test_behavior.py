from datetime import datetime, timedelta, timezone

from queryimmune_db.api.models import ExecutionRequest
from queryimmune_db.behavior import (
    BehaviorAnalyzer, BehaviorAssessment, BehaviorAwarePipeline,
    ControlledProfileUpdater, ExplainableRiskEngine,
)
from queryimmune_db.domain.identity import Actor
from queryimmune_db.services.policy import Decision


NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
PROFILE = {"approved_sample_count": 40, "profile_version": 3, "feature_summary": {
    "actions": ["appointments.mine"], "tables": ["appointments", "doctors", "patients"],
    "signatures": ["appointments.mine"], "result_size_p95": 8,
}}


def test_normal_behavior_is_low_risk_allow():
    features = {"action": "appointments.mine", "tables": ["appointments", "doctors", "patients"],
                "semantic_signature": "appointments.mine", "scope": "own_records", "sensitivity": "standard"}
    behavior = BehaviorAnalyzer().analyze(features, PROFILE, [], NOW)
    result = ExplainableRiskEngine().assess(features, behavior)
    assert result.level == "LOW" and result.state == "allow"
    assert behavior.similarity == 1.0


def test_unusual_action_and_query_shape_increase_risk():
    features = {"action": "billing.summary", "tables": ["billing"], "semantic_signature": "new-shape",
                "scope": "aggregate", "sensitivity": "standard"}
    result = ExplainableRiskEngine().assess(features, BehaviorAnalyzer().analyze(features, PROFILE, [], NOW))
    assert result.state == "review"
    assert "UNSEEN_ACTION_PATTERN" in result.reasons


def test_sensitive_request_contributes_explainable_risk():
    features = {"action": "medical.records", "scope": "bounded", "sensitivity": "restricted"}
    result = ExplainableRiskEngine().assess(features, BehaviorAssessment(0, 1, False, "none", 20, ()))
    assert result.level == "HIGH" and result.state == "block"
    assert result.factors["data_sensitivity"] == 60


def test_destructive_request_is_high_risk():
    result = ExplainableRiskEngine().assess({"operation": "delete", "scope": "bounded"},
        BehaviorAssessment(0, 1, False, "none", 20, ()))
    assert result.level == "HIGH" and result.state == "block"


def test_intent_mismatch_is_high_risk():
    result = ExplainableRiskEngine().assess({"validated": False, "mismatch_codes": ["OPERATION_MISMATCH"]},
        BehaviorAssessment(0, None, False, "none", 0, ()))
    assert result.score == 100 and result.state == "block"
    assert "INTENT_SQL_MISMATCH" in result.reasons


def test_sustained_workload_change_is_detected_as_gradual_drift():
    history = []
    for i in range(20):
        history.append({"created_at": NOW - timedelta(days=14, hours=i),
                        "feature_snapshot": {"action": "appointments.mine"}})
    for i in range(12):
        history.append({"created_at": NOW - timedelta(days=2, hours=i),
                        "feature_snapshot": {"action": "billing.summary"}})
    features = {"action": "billing.summary", "tables": ["billing"], "semantic_signature": "billing.summary",
                "scope": "aggregate", "sensitivity": "standard"}
    behavior = BehaviorAnalyzer().analyze(features, PROFILE, history, NOW)
    result = ExplainableRiskEngine().assess(features, behavior)
    assert behavior.drift and behavior.drift_kind == "gradual"
    assert result.state != "block"


def test_empty_summary_on_mature_profile_is_safe_during_drift_check():
    result = BehaviorAnalyzer().analyze({"action": "appointments.mine"},
        {"approved_sample_count": 20, "feature_summary": {}}, [], NOW)
    assert result.similarity is None
    assert "BASELINE_WARMUP" in result.reasons


def test_behavior_risk_keeps_authorized_role_context():
    from queryimmune_db.application.nl2sql import NL2SQLAnalysis, ValidatedIntentPolicy
    from queryimmune_db.ai.intent import QueryIntent, ValidationResult
    from queryimmune_db.domain.identity import Actor
    intent = QueryIntent(request_status="supported", operation="read", action="appointments.cardiology_today",
        tables=["appointments", "doctors"], columns=["patient_id"], filters=[], aggregations=["count"],
        group_by=[], ordering=[], limit=1, scope="aggregate", sensitivity_expectations=["standard"])
    analysis = NL2SQLAnalysis(intent, ValidationResult(valid=True, reasons=[]), "test", "fingerprint", "")
    decision = ValidatedIntentPolicy(analysis, 100, 25, authorization_check=lambda *_: True).decide(ExecutionRequest(action=intent.action, limit=1),
        Actor("user", "subject", "Doctor", frozenset({"doctor"})))
    assert decision.state == "allow"
    assert decision.feature_snapshot["action"] == "appointments.cardiology_today"
    assert "allowed_roles" not in decision.feature_snapshot


def test_profile_updater_rejects_poisoning_inputs_and_requires_sample_gate():
    class Repository:
        def __init__(self): self.writes = []
        def load(self, actor_id, *, actor_context_id): return None, []
        def eligible_sample_count(self, actor_id, *, actor_context_id): return 9
        def append_profile(self, *args): self.writes.append(args)
    repo = Repository()
    updater = ControlledProfileUpdater(repo)
    updater.observe_success("actor", {"risk_level": "HIGH", "risk_reasons": [], "action": "billing.summary"}, actor_context_id="actor")
    updater.observe_success("actor", {"risk_level": "LOW", "risk_reasons": ["UNSEEN_ACTION_PATTERN"], "action": "billing.summary"}, actor_context_id="actor")
    updater.observe_success("actor", {"risk_level": "LOW", "risk_reasons": ["BASELINE_WARMUP"], "action": "appointments.mine"}, actor_context_id="actor")
    assert repo.writes == []


def test_profile_updater_rejects_impossible_result_size_poisoning():
    class Repository:
        def __init__(self): self.writes = []
        def load(self, actor_id, *, actor_context_id): return None, []
        def eligible_sample_count(self, actor_id, *, actor_context_id): return 10
        def append_profile(self, *args): self.writes.append(args)
    repo = Repository()
    updater = ControlledProfileUpdater(repo)
    features = {"risk_level": "LOW", "risk_reasons": ["BASELINE_WARMUP"], "action": "appointments.mine"}
    updater.observe_success("actor", features, result_size=101, actor_context_id="actor")
    assert repo.writes == []


def test_hard_policy_block_cannot_be_overridden_by_behavior():
    class Deny:
        def decide(self, request, actor):
            return Decision("block", ("ROLE_NOT_ALLOWED",), 100, "Denied")
    class Repository:
        def load(self, actor_id): raise AssertionError("hard veto must short-circuit learning")
    actor = Actor(user_id="00000000-0000-0000-0000-000000000001", subject="x", display_name="x", roles=frozenset({"patient"}))
    decision = BehaviorAwarePipeline(Deny(), Repository()).decide(ExecutionRequest(action="billing.summary"), actor)
    assert decision.state == "block" and decision.reason_codes == ("ROLE_NOT_ALLOWED",)
