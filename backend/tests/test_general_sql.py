from __future__ import annotations

from datetime import datetime, timedelta, timezone
import pytest

from queryimmune_db.ai.intent import IntentFilter, QueryIntent
from queryimmune_db.ai.provider import DemoFixtureProvider
from queryimmune_db.application.nl2sql import NL2SQLPipeline, ValidatedIntentPolicy, execution_request
from queryimmune_db.domain.identity import Actor
from queryimmune_db.security.general_sql import GeneralSelectValidator


OWN_SQL = """SELECT a.scheduled_at, a.status, a.reason_category, p.synthetic_patient_number, d.specialty
FROM healthcare.appointments a
JOIN healthcare.patients p ON p.id = a.patient_id
JOIN healthcare.doctors d ON d.id = a.doctor_id
WHERE d.user_id = :actor_id
ORDER BY a.scheduled_at DESC LIMIT 5"""


def own_intent(**updates):
    value = {
        "request_status": "supported", "operation": "read", "action": "appointments.general_own",
        "tables": ["appointments", "patients", "doctors"],
        "columns": ["scheduled_at", "status", "reason_category", "synthetic_patient_number", "specialty"],
        "filters": [], "aggregations": [], "group_by": [],
        "ordering": [{"column": "scheduled_at", "direction": "desc"}],
        "limit": 5, "scope": "own_records", "sensitivity_expectations": ["standard"],
    }
    value.update(updates)
    return QueryIntent(**value)


def test_general_bounded_actor_scoped_select_is_valid():
    result = GeneralSelectValidator().validate("Show me my first 5 appointments", own_intent(), OWN_SQL)
    assert result.valid, result.reasons
    assert result.analysis and result.analysis.canonical_sql


def test_general_date_and_status_filters_must_match_intent():
    request = "Show completed appointments this week"
    today = datetime.now(timezone.utc).date()
    start = today - timedelta(days=today.weekday())
    end = start + timedelta(days=7)
    intent = own_intent(
        filters=[
            IntentFilter(column="status", operator="eq", value="completed"),
            IntentFilter(column="scheduled_at", operator="gte", value=start.isoformat()),
            IntentFilter(column="scheduled_at", operator="lt", value=end.isoformat()),
        ],
    )
    sql = OWN_SQL.replace("WHERE d.user_id = :actor_id", f"WHERE d.user_id = :actor_id AND a.status = 'completed' AND a.scheduled_at >= '{start.isoformat()}' AND a.scheduled_at < '{end.isoformat()}'")
    assert GeneralSelectValidator().validate(request, intent, sql).valid
    mismatch = GeneralSelectValidator().validate(request, intent, OWN_SQL)
    assert "FILTER_MISMATCH" in {reason.code for reason in mismatch.reasons}


def test_general_grouped_aggregate_is_supported_for_policy_gate():
    intent = QueryIntent(
        request_status="supported", operation="read", action="appointments.general_aggregate",
        tables=["appointments", "doctors"], columns=["specialty"],
        filters=[IntentFilter(column="specialty", operator="eq", value="Cardiology")],
        aggregations=["count"], group_by=["specialty"],
        ordering=[{"column": "appointment_count", "direction": "desc"}], limit=25,
        scope="aggregate", sensitivity_expectations=["standard"],
    )
    sql = "SELECT d.specialty, COUNT(*) AS appointment_count FROM healthcare.appointments a JOIN healthcare.doctors d ON d.id = a.doctor_id WHERE d.specialty = 'Cardiology' GROUP BY d.specialty HAVING COUNT(*) >= 3 ORDER BY appointment_count DESC LIMIT 25"
    result = GeneralSelectValidator().validate("Count appointments by specialty", intent, sql)
    assert result.valid, result.reasons


def test_general_doctor_comparison_requires_a_minimum_distinct_patient_group():
    intent = QueryIntent(
        request_status="supported", operation="read", action="appointments.general_aggregate",
        tables=["appointments", "general_doctor_directory"], columns=["display_name"],
        filters=[], aggregations=["count"], group_by=["display_name"],
        ordering=[{"column": "patient_count", "direction": "desc"}], limit=25,
        scope="aggregate", sensitivity_expectations=["standard"],
    )
    sql = "SELECT d.display_name, COUNT(DISTINCT a.patient_id) AS patient_count FROM healthcare.appointments a JOIN healthcare.general_doctor_directory d ON d.doctor_id = a.doctor_id GROUP BY d.display_name HAVING COUNT(DISTINCT a.patient_id) >= 3 ORDER BY patient_count DESC LIMIT 25"
    result = GeneralSelectValidator().validate("How many patients did each doctor see?", intent, sql)
    assert result.valid, result.reasons
    no_threshold = sql.replace(" HAVING COUNT(DISTINCT a.patient_id) >= 3", "")
    result = GeneralSelectValidator().validate("How many patients did each doctor see?", intent, no_threshold)
    assert not result.valid
    assert "AGGREGATE_DISCLOSURE_RISK" in {reason.code for reason in result.reasons}
    wrong_measure = sql.replace("COUNT(DISTINCT a.patient_id)", "COUNT(*)")
    result = GeneralSelectValidator().validate("How many patients did each doctor see?", intent, wrong_measure)
    assert not result.valid
    assert "REQUEST_AGGREGATION_MISMATCH" in {reason.code for reason in result.reasons}


@pytest.mark.parametrize(("sql", "intent", "expected"), [
    ("SELECT * FROM healthcare.appointments a WHERE a.doctor_id = 'x' LIMIT 5", own_intent(), "WILDCARD_PROJECTION"),
    (OWN_SQL.replace("WHERE d.user_id = :actor_id", "WHERE TRUE"), own_intent(), "MISSING_ACTOR_SCOPE"),
    (OWN_SQL.replace("LIMIT 5", "LIMIT 5000"), own_intent(limit=100), "LIMIT_MISMATCH"),
    ("SELECT mr.summary FROM healthcare.medical_records mr LIMIT 5", own_intent(tables=["medical_records"]), "UNAUTHORIZED_RELATION"),
    (OWN_SQL + "; DELETE FROM healthcare.appointments", own_intent(), "MULTIPLE_STATEMENTS"),
    (OWN_SQL.replace("WHERE d.user_id = :actor_id", "WHERE d.user_id = :actor_id OR TRUE"), own_intent(), "UNSUPPORTED_FILTER"),
    (OWN_SQL.replace("WHERE d.user_id = :actor_id", "WHERE d.user_id = :actor_id AND pg_sleep(3) IS NULL"), own_intent(), "UNSAFE_FUNCTION"),
    (OWN_SQL.replace("JOIN healthcare.doctors d ON d.id = a.doctor_id", "JOIN healthcare.doctors d ON d.id = a.patient_id"), own_intent(), "UNSAFE_JOIN"),
    (OWN_SQL.replace("d.user_id = :actor_id", "d.user_id = :other_actor"), own_intent(), "UNSAFE_PARAMETER"),
])
def test_general_query_rejects_unsafe_shapes(sql, intent, expected):
    result = GeneralSelectValidator().validate("Show me my first 5 appointments", intent, sql)
    assert not result.valid
    assert expected in {reason.code for reason in result.reasons}


def test_explicit_other_provider_scope_is_blocked():
    result = GeneralSelectValidator().validate("Show me another doctor's appointments", own_intent(), OWN_SQL)
    assert not result.valid
    assert "SCOPE_MISMATCH" in {reason.code for reason in result.reasons}


def test_unsupported_or_ambiguous_intents_cannot_execute():
    validator = GeneralSelectValidator()
    for status in ("unsupported", "ambiguous"):
        result = validator.validate("Show me that thing", own_intent(request_status=status), OWN_SQL)
        assert not result.valid
        assert "UNSUPPORTED_OR_AMBIGUOUS_REQUEST" in {reason.code for reason in result.reasons}


@pytest.mark.parametrize(("prompt", "behavior_action"), [
    ("Show me my first 5 appointments.", "appointments.mine"),
    ("Which doctors have the most appointments?", "appointments.by_status"),
])
def test_general_query_risk_features_share_the_existing_action_family(prompt, behavior_action):
    analysis = NL2SQLPipeline(DemoFixtureProvider(None)).analyze(prompt)
    assert analysis.validation.valid
    policy = ValidatedIntentPolicy(analysis, result_limit=100, review_limit=25,
                                   authorization_check=lambda *_: True)
    actor = Actor("10000000-0000-4000-8000-000000000001", "test:actor", "Test Actor", frozenset({"doctor"}))
    decision = policy.decide(execution_request(analysis.intent), actor)
    assert decision.feature_snapshot["behavior_action"] == behavior_action
