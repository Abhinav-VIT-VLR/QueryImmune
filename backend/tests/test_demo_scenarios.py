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


def test_demo_provider_refuses_to_select_a_scenario_implicitly():
    provider = DemoFixtureProvider(None)
    with pytest.raises(AIProviderError):
        provider.complete("intent", {}, json_mode=True)
