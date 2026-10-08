from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from queryimmune_db.api.config import Settings


class AIProviderError(RuntimeError):
    """Sanitized provider failure; never carries prompt or credential content."""


class AIModelOutputError(AIProviderError):
    """The provider responded, but its content was malformed or violated the output contract."""


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
                raise AIModelOutputError("model provider response exceeded configured size")
            try:
                envelope = json.loads(raw)
                content = envelope["choices"][0]["message"]["content"]
            except (ValueError, KeyError, IndexError, TypeError):
                raise AIModelOutputError("model provider returned malformed content") from None
            if not isinstance(content, str) or not content.strip():
                raise AIModelOutputError("model provider returned empty content")
            return content
        except AIProviderError:
            raise
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
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
            return self._general_complete(user_payload, json_mode=json_mode)
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
            return json.dumps({"request_status": "supported", **intents[self.scenario]})
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

    def _general_complete(self, user_payload: dict[str, object], *, json_mode: bool) -> str:
        request = str(user_payload.get("request_untrusted") or "")
        text = request.casefold()
        limit_match = re.search(r"\b(?:first|last|top|limit(?:\s+of)?)\s+(\d+)\b", text)
        limit = min(100, int(limit_match.group(1))) if limit_match else 25
        unsafe_records = bool(re.search(r"\b(medical\s+history|diagnos(?:is|es)|prescriptions?|lab\s+results?|date\s+of\s+birth|billing|invoices?)\b", text))
        destructive = bool(re.search(r"\b(delete|remove|erase|drop|alter|truncate|update|insert|cancel)\b|\b(book|schedule|reschedule)\s+(?:a\s+|an\s+)?(?:new\s+)?(?:appointment|visit)\b", text))
        ambiguous = bool(re.search(r"\b(that thing|that one|it from last week|the thing)\b", text))
        has_appointments = bool(re.search(r"\b(appointments?|visits?)\b", text)) or unsafe_records
        has_count = bool(re.search(r"\b(how many|count|number of)\b", text))
        personal_scope = bool(re.search(r"\b(my|mine|me|i)\b", text))
        doctor_leaderboard = bool(re.search(r"\b(which|what) doctors?\b.*\b(most|fewest|top)\b", text))
        per_doctor = bool(re.search(r"\b(each|per) doctor\b", text))
        has_appointments = has_appointments or doctor_leaderboard or per_doctor

        status_value = None
        for word in ("completed", "cancelled", "scheduled", "no_show"):
            if word.replace("_", " ") in text:
                status_value = word
                break
        specialty = "Cardiology" if "cardiology" in text else None
        now = datetime.now(timezone.utc).date()
        date_filters: list[dict[str, object]] = []
        if "tomorrow" in text:
            start, end = now + timedelta(days=1), now + timedelta(days=2)
        elif "today" in text:
            start, end = now, now + timedelta(days=1)
        elif "this week" in text or "this week" in text:
            start = now - timedelta(days=now.weekday())
            end = start + timedelta(days=7)
        elif "this month" in text:
            start = now.replace(day=1)
            end = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        else:
            start = end = None
        if start:
            date_filters = [
                {"column": "scheduled_at", "operator": "gte", "value": start.isoformat()},
                {"column": "scheduled_at", "operator": "lt", "value": end.isoformat()},
            ]

        if json_mode:
            if not has_appointments:
                status = "ambiguous" if ambiguous else "unsupported"
                operation = "read"
                action = "appointments.general_own"
                tables = ["appointments"]
                columns: list[str] = []
                filters: list[dict[str, object]] = []
                aggregations: list[str] = []
                group_by: list[str] = []
                ordering: list[dict[str, str]] = []
                scope = "unknown"
            else:
                status = "supported"
                operation = "delete" if destructive else "read"
                if unsafe_records:
                    status = "supported"
                cross_actor = (has_count or doctor_leaderboard or per_doctor) and not personal_scope
                action = "appointments.general_aggregate" if cross_actor else "appointments.general_own"
                scope = "aggregate" if cross_actor else "own_records"
                tables = ["appointments", "general_doctor_directory"] if (specialty or has_count or cross_actor) else ["appointments", "patients", "general_doctor_directory"]
                if unsafe_records:
                    tables = ["medical_records"] if "medical" in text or "diagnos" in text else ["billing"]
                grouped_doctors = cross_actor and (doctor_leaderboard or per_doctor)
                columns = (["summary"] if "medical_records" in tables else ["amount"] if "billing" in tables else []) if unsafe_records else ["display_name"] if grouped_doctors else [] if has_count else ["scheduled_at", "status", "reason_category", "synthetic_patient_number", "specialty"]
                filters = list(date_filters)
                if status_value:
                    filters.append({"column": "status", "operator": "eq", "value": status_value})
                if specialty:
                    filters.append({"column": "specialty", "operator": "eq", "value": specialty})
                aggregations = ["count"] if cross_actor or has_count else []
                group_by = ["display_name"] if grouped_doctors else []
                count_alias = "patient_count" if grouped_doctors and per_doctor and "patient" in text else "appointment_count"
                ordering = ([{"column": count_alias, "direction": "desc"}] if grouped_doctors else []) if cross_actor or has_count else [{"column": "scheduled_at", "direction": "desc"}]
                if destructive:
                    status = "supported"
                    scope = "all_records"
            payload = {
                "request_status": "ambiguous" if ambiguous else status,
                "operation": operation,
                "action": action,
                "tables": tables,
                "columns": columns,
                "filters": filters,
                "aggregations": aggregations,
                "group_by": group_by,
                "ordering": ordering,
                "limit": limit if status == "supported" else 25,
                "scope": scope,
                "sensitivity_expectations": ["restricted"] if unsafe_records and "medical_records" in tables else ["sensitive"] if unsafe_records else ["standard"],
            }
            return json.dumps(payload)

        intent = user_payload.get("intent") or {}
        if intent.get("request_status") != "supported":
            return ""
        if intent.get("operation") != "read":
            return "DELETE FROM healthcare.appointments"
        if "medical_records" in intent.get("tables", []):
            return "SELECT mr.summary FROM healthcare.medical_records mr LIMIT 25"
        if "billing" in intent.get("tables", []):
            return "SELECT b.amount FROM healthcare.billing b LIMIT 25"
        aggregate = intent.get("action") == "appointments.general_aggregate"
        own_aggregate = intent.get("action") == "appointments.general_own" and bool(intent.get("aggregations"))
        uses_directory = "general_doctor_directory" in intent.get("tables", [])
        distinct_patient_count = aggregate and bool(intent.get("group_by")) and "patient" in request.casefold()
        directory_source = "healthcare.general_doctor_directory d ON d.doctor_id = a.doctor_id"
        if aggregate or own_aggregate:
            if uses_directory:
                source = f"healthcare.appointments a JOIN {directory_source}"
                if "display_name" in intent.get("group_by", []):
                    count = "COUNT(DISTINCT a.patient_id)" if distinct_patient_count else "COUNT(*)"
                    alias = "patient_count" if distinct_patient_count else "appointment_count"
                    columns = f"d.display_name, {count} AS {alias}"
                elif "specialty" in intent.get("group_by", []):
                    columns = "d.specialty, COUNT(*) AS appointment_count"
                else:
                    columns = "COUNT(*) AS appointment_count"
            else:
                source = "healthcare.appointments a"
                columns = "COUNT(*) AS appointment_count"
        else:
            source = "healthcare.appointments a JOIN healthcare.patients p ON p.id = a.patient_id JOIN " + directory_source
            columns = "a.scheduled_at, a.status, a.reason_category, p.synthetic_patient_number, d.specialty"
        clauses = []
        if not aggregate:
            clauses.append("d.user_id = :actor_id")
        for item in intent.get("filters", []):
            column = item.get("column")
            relation_column = {
                "scheduled_at": "a.scheduled_at", "status": "a.status", "specialty": "d.specialty",
            }.get(column)
            if relation_column is None:
                continue
            op = {"eq": "=", "gte": ">=", "gt": ">", "lte": "<=", "lt": "<"}.get(item.get("operator"))
            if op is None:
                continue
            value = str(item.get("value", "")).replace("'", "''")
            clauses.append(f"{relation_column} {op} '{value}'")
        where = " WHERE " + " AND ".join(clauses) if clauses else ""
        group = " GROUP BY " + ", ".join("d." + col if col in {"specialty", "display_name"} else "a." + col for col in intent.get("group_by", [])) if intent.get("group_by") else ""
        disclosure_count = "COUNT(DISTINCT a.patient_id)" if distinct_patient_count else "COUNT(*)"
        disclosure_guard = f" HAVING {disclosure_count} >= 3" if aggregate else ""
        order = " ORDER BY a.scheduled_at DESC" if not (aggregate or own_aggregate) else (" ORDER BY " + str((intent.get("ordering") or [{}])[0].get("column", "appointment_count")) + " DESC" if intent.get("group_by") else "")
        return f"SELECT {columns} FROM {source}{where}{group}{disclosure_guard}{order} LIMIT {int(intent.get('limit') or 25)}"


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
