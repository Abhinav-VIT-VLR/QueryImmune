import pytest

from queryimmune_db.ai.intent import QueryIntent
from queryimmune_db.ai.provider import AIProviderError, DemoFixtureProvider
from queryimmune_db.security.sql_validation import IntentSqlValidator


@pytest.mark.parametrize(("scenario", "request_text", "expected_valid"), [
    ("normal", "How many patients visited cardiology today?", True),
    ("sensitive", "Show the medical history of every patient.", False),
    ("destructive", "Delete all patient records.", False),
    ("mismatch", "Show my appointments.", False),
    ("drift", "Show my appointments, capped at ten results.", True),
])
def test_demo_fixture_candidates_use_real_intent_sql_validator(scenario, request_text, expected_valid):
    provider = DemoFixtureProvider(scenario)
    intent = QueryIntent.model_validate_json(provider.complete("intent", {}, json_mode=True))
    candidate = provider.complete("SQL", {}, json_mode=False)
    result = IntentSqlValidator().validate(request_text, intent, candidate)
    assert result.valid is expected_valid
    if not expected_valid:
        assert result.reasons


def test_demo_provider_handles_a_general_custom_prompt_without_fixture_state():
    provider = DemoFixtureProvider(None)
    payload = {"request_untrusted": "Show me my first 5 appointments."}
    intent = QueryIntent.model_validate_json(provider.complete("intent", payload, json_mode=True))
    assert intent.request_status == "supported"
    assert intent.action == "appointments.general_own"
    assert intent.limit == 5
    candidate = provider.complete("SQL", {**payload, "intent": intent.model_dump(mode="json")}, json_mode=False)
    assert ":actor_id" in candidate
