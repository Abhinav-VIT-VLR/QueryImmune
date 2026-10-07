from __future__ import annotations

import hashlib
from datetime import date
from decimal import Decimal
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

from queryimmune_db.connection import transaction
from queryimmune_db.domain.identity import Actor
from queryimmune_db.services.policy import Decision


class HealthcareRepository:
    """Uses trusted SQL templates and the restricted runtime connection only."""

    _queries: dict[str, str] = {
        "appointments.mine": """
            SELECT a.scheduled_at, a.status, a.reason_category,
                   p.synthetic_patient_number, d.specialty
            FROM healthcare.appointments a
            JOIN healthcare.patients p ON p.id = a.patient_id
            JOIN healthcare.doctors d ON d.id = a.doctor_id
            WHERE d.user_id = %s
              AND (%s::date IS NULL OR a.scheduled_at::date >= %s::date)
            ORDER BY a.scheduled_at DESC LIMIT %s
        """,
        "appointments.by_status": """
            SELECT a.status, d.specialty, count(*) AS appointment_count
            FROM healthcare.appointments a
            JOIN healthcare.doctors d ON d.id = a.doctor_id
            WHERE (%s::date IS NULL OR a.scheduled_at::date >= %s::date)
            GROUP BY a.status, d.specialty
            ORDER BY appointment_count DESC LIMIT %s
        """,
        "billing.summary": """
            SELECT status, currency, invoice_count, total_amount
            FROM security.billing_summary(%s::date, %s::integer)
        """,
        "appointments.cardiology_today": """
            SELECT count(DISTINCT a.patient_id) AS patient_count
            FROM healthcare.appointments a
            JOIN healthcare.doctors d ON d.id = a.doctor_id
            WHERE d.specialty = 'Cardiology'
              AND a.status = 'completed'
              AND a.scheduled_at::date = CURRENT_DATE
            LIMIT %s
        """,
    }

    def actor_by_subject(self, subject: str) -> Actor | None:
        with transaction(read_only=True) as conn:
            row = conn.execute(
                """SELECT u.id::text, u.external_subject, u.display_name, u.status,
                          coalesce(array_agg(r.name) FILTER (WHERE r.name IS NOT NULL), ARRAY[]::text[])
                   FROM healthcare.users u
                   LEFT JOIN healthcare.user_roles ur ON ur.user_id = u.id
                   LEFT JOIN healthcare.roles r ON r.id = ur.role_id
                   WHERE u.external_subject = %s
                   GROUP BY u.id""",
                (subject,),
            ).fetchone()
        if row is None or row[3] != "active":
            return None
        return Actor(user_id=row[0], subject=row[1], display_name=row[2], roles=frozenset(row[4]))

    def write_decision(
        self, request_id: UUID, actor_id: UUID, decision: Decision, action: str, limit: int,
        *, query_fingerprint: str | None = None,
    ) -> None:
        query = self._queries.get(action, "UNSUPPORTED_ACTION")
        fingerprint = query_fingerprint or hashlib.sha256(query.encode("utf-8")).hexdigest()
        features = {"action": action, "limit": limit, **decision.feature_snapshot}
        with transaction() as conn:
            conn.execute(
                "SELECT security.append_query_log(%s, %s, %s, %s, 'not_run', NULL, NULL)",
                (request_id, actor_id, fingerprint, decision.state),
            )
            conn.execute(
                """SELECT security.append_risk_event(
                       %s, %s, %s::numeric, %s::text, %s::text[], %s::jsonb,
                       %s::text, %s::text, %s::integer
                   )""",
                (request_id, actor_id, decision.risk_score, decision.state,
                 list(decision.reason_codes), Jsonb(features), decision.policy_version, decision.model_version,
                 decision.profile_version),
            )

    def execute_action(self, action: str, actor_id: UUID, limit: int, from_date: date | None) -> list[dict[str, Any]]:
        query = self._queries.get(action)
        if query is None:
            raise ValueError("unsupported action cannot be executed")
        if action == "appointments.mine":
            params = (actor_id, from_date, from_date, limit)
        elif action == "appointments.cardiology_today":
            params = (limit,)
        elif action == "billing.summary":
            params = (from_date, limit)
        else:
            params = (from_date, from_date, limit)
        with transaction(read_only=True) as conn:
            cursor = conn.execute(query, params)
            rows = cursor.fetchall()
            columns = [column.name for column in cursor.description or ()]
        return [{name: _json_value(value) for name, value in zip(columns, row)} for row in rows]

    def finish_execution(self, request_id: UUID, execution_status: str, duration_ms: int, row_count: int | None) -> None:
        with transaction() as conn:
            conn.execute(
                "SELECT security.finish_query_log(%s, %s::text, %s::integer, %s::integer)",
                (request_id, execution_status, duration_ms, row_count),
            )

    def dashboard(self, actor_id: UUID) -> dict[str, Any]:
        """Return only the authenticated actor's persisted audit and baseline metadata."""
        with transaction(read_only=True) as conn:
            history_rows = conn.execute("""SELECT q.request_id::text, q.created_at, q.decision,
                    q.execution_status, q.duration_ms, q.row_count, r.risk_score, r.reason_codes,
                    r.feature_snapshot, r.policy_version
                FROM security.query_logs q JOIN security.risk_events r USING (request_id)
                WHERE q.actor_id=%s ORDER BY q.created_at DESC LIMIT 50""", (actor_id,)).fetchall()
            profile_row = conn.execute("""SELECT profile_version, window_start, window_end,
                    approved_sample_count, confidence, feature_summary, update_status, updated_at
                FROM security.behavior_profiles WHERE actor_id=%s
                ORDER BY profile_version DESC LIMIT 1""", (actor_id,)).fetchone()
            workload_rows = conn.execute("""SELECT date_trunc('day', q.created_at)::date::text AS day,
                    coalesce(r.feature_snapshot->>'action', 'unknown') AS action,
                    count(*) AS requests, round(avg(r.risk_score), 2) AS mean_risk
                FROM security.query_logs q JOIN security.risk_events r USING (request_id)
                WHERE q.actor_id=%s AND q.created_at >= now() - interval '30 days'
                GROUP BY 1, 2 ORDER BY 1, 2""", (actor_id,)).fetchall()
        history = [{"request_id": row[0], "created_at": _json_value(row[1]), "decision": row[2],
                    "execution_status": row[3], "duration_ms": row[4], "row_count": row[5],
                    "risk_score": float(row[6]), "reason_codes": list(row[7]),
                    "action": row[8].get("action"), "risk_level": row[8].get("risk_level"),
                    "policy_version": row[9]} for row in history_rows]
        risk_events = [{"request_id": item["request_id"], "created_at": item["created_at"],
                        "decision": item["decision"], "risk_score": item["risk_score"],
                        "risk_level": item["risk_level"], "reason_codes": item["reason_codes"],
                        "action": item["action"]} for item in history]
        profile = None if profile_row is None else {
            "profile_version": profile_row[0], "window_start": _json_value(profile_row[1]),
            "window_end": _json_value(profile_row[2]), "approved_sample_count": profile_row[3],
            "confidence": float(profile_row[4]), "feature_summary": profile_row[5],
            "update_status": profile_row[6], "updated_at": _json_value(profile_row[7]),
        }
        workload = [{"day": row[0], "action": row[1], "requests": row[2], "mean_risk": float(row[3] or 0)} for row in workload_rows]
        return {"history": history, "risk_events": risk_events, "behavior_profile": profile, "workload": workload}


def _json_value(value: Any) -> Any:
    if isinstance(value, (UUID, date)):
        return value.isoformat() if isinstance(value, date) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value
