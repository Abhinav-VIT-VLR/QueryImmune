from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from queryimmune_db.api.config import Settings


class AIProviderError(RuntimeError):
    """Sanitized provider failure; never carries prompt or credential content."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class LLMProvider(Protocol):
    @property
    def model_version(self) -> str: ...

    def complete(self, system_prompt: str, user_payload: dict[str, object], *, json_mode: bool) -> str: ...


@dataclass(frozen=True)
class OpenAICompatibleProvider:
    base_url: str
    api_key: str = field(repr=False)
    model: str
    timeout_seconds: float = 20
    max_response_bytes: int = 16384

    @property
    def model_version(self) -> str:
        return self.model

    def complete(self, system_prompt: str, user_payload: dict[str, object], *, json_mode: bool) -> str:
        body: dict[str, object] = {
            "model": self.model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(user_payload, separators=(",", ":"))},
            ],
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        request = Request(
            f"{self.base_url}/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            opener = build_opener(_NoRedirect)
            with opener.open(request, timeout=self.timeout_seconds) as response:
                raw = response.read(self.max_response_bytes + 1)
            if len(raw) > self.max_response_bytes:
                raise AIProviderError("model response exceeded configured size")
            envelope = json.loads(raw)
            content = envelope["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                raise AIProviderError("model response was empty or malformed")
            return content
        except AIProviderError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError, ValueError, KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise AIProviderError("model provider request failed") from None


@dataclass(frozen=True)
class DemoFixtureProvider:
    """Fixed untrusted candidates for demos; all decisions still use normal validation and PostgreSQL."""
    scenario: str | None

    @property
    def model_version(self) -> str:
        return "queryimmune-demo-fixtures-v1"

    def complete(self, system_prompt: str, user_payload: dict[str, object], *, json_mode: bool) -> str:
        if self.scenario is None:
            raise AIProviderError("Select a preset scenario when using the demo fixture provider")
        action_mine = {
            "operation": "read", "action": "appointments.mine",
            "tables": ["appointments", "patients", "doctors"],
            "columns": ["scheduled_at", "status", "reason_category", "synthetic_patient_number", "specialty"],
            "filters": [], "aggregations": [], "group_by": [],
            "ordering": [{"column": "scheduled_at", "direction": "desc"}],
            "limit": 25, "scope": "own_records", "sensitivity_expectations": ["standard"],
        }
        action_drift = {**action_mine, "limit": 10}
        intents = {
            "normal": {"operation": "read", "action": "appointments.cardiology_today",
                "tables": ["appointments", "doctors"], "columns": ["patient_id"], "filters": [],
                "aggregations": ["count"], "group_by": [], "ordering": [], "limit": 1,
                "scope": "aggregate", "sensitivity_expectations": ["standard"]},
            "sensitive": {**action_mine, "tables": ["medical_records"], "columns": ["summary"],
                "scope": "all_records", "sensitivity_expectations": ["restricted"]},
            "destructive": {**action_mine, "operation": "delete"},
            "mismatch": action_mine,
            "drift": action_drift,
        }
        if json_mode:
            return json.dumps(intents[self.scenario])
        sql_candidates = {
            "normal": """SELECT COUNT(DISTINCT a.patient_id) AS patient_count
FROM healthcare.appointments AS a
JOIN healthcare.doctors AS d ON d.id = a.doctor_id
WHERE d.specialty = 'Cardiology' AND a.status = 'completed'
AND a.scheduled_at::date = CURRENT_DATE LIMIT 1""",
            "sensitive": "SELECT mr.summary FROM healthcare.medical_records AS mr LIMIT 25",
            "destructive": "DELETE FROM healthcare.appointments",
            "mismatch": "SELECT b.status, b.currency, COUNT(*) AS invoice_count, SUM(b.amount) AS total_amount FROM healthcare.billing AS b GROUP BY b.status, b.currency ORDER BY b.status, b.currency LIMIT 25",
            "drift": "SELECT a.scheduled_at, a.status, a.reason_category, p.synthetic_patient_number, d.specialty FROM healthcare.appointments AS a JOIN healthcare.patients AS p ON p.id = a.patient_id JOIN healthcare.doctors AS d ON d.id = a.doctor_id WHERE d.user_id = :actor_id ORDER BY a.scheduled_at DESC LIMIT 10",
        }
        return sql_candidates[self.scenario]


def configured_provider(settings: Settings, *, demo_scenario: str | None = None) -> LLMProvider | None:
    if settings.ai_provider == "disabled":
        return None
    if settings.ai_provider == "demo":
        return DemoFixtureProvider(demo_scenario)
    if not settings.ai_base_url or not settings.ai_api_key or not settings.ai_model:
        raise AIProviderError("model provider configuration is incomplete")
    return OpenAICompatibleProvider(
        base_url=settings.ai_base_url,
        api_key=settings.ai_api_key,
        model=settings.ai_model,
        timeout_seconds=settings.ai_timeout_seconds,
        max_response_bytes=settings.ai_max_response_bytes,
    )
