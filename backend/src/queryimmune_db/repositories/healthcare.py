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
            SELECT status, currency, count(*) AS invoice_count, sum(amount) AS total_amount
            FROM healthcare.billing
            WHERE (%s::date IS NULL OR created_at::date >= %s::date)
            GROUP BY status, currency ORDER BY status, currency LIMIT %s
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

    def write_decision(self, request_id: UUID, actor_id: UUID, decision: Decision, action: str, limit: int) -> None:
        query = self._queries.get(action, "UNSUPPORTED_ACTION")
        fingerprint = hashlib.sha256(query.encode("utf-8")).hexdigest()
        with transaction() as conn:
            conn.execute(
                "SELECT security.append_query_log(%s, %s, %s, %s, 'not_run', NULL, NULL)",
                (request_id, actor_id, fingerprint, decision.state),
            )
            conn.execute(
                """SELECT security.append_risk_event(
                       %s, %s, %s, %s, %s, %s, 'phase2-static-v1', NULL, NULL
                   )""",
                (request_id, actor_id, decision.risk_score, decision.state,
                 list(decision.reason_codes), Jsonb({"action": action, "limit": limit})),
            )

    def execute_action(self, action: str, actor_id: UUID, limit: int, from_date: date | None) -> list[dict[str, Any]]:
        query = self._queries.get(action)
        if query is None:
            raise ValueError("unsupported action cannot be executed")
        if action == "appointments.mine":
            params = (actor_id, from_date, from_date, limit)
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
                "SELECT security.finish_query_log(%s, %s, %s, %s)",
                (request_id, execution_status, duration_ms, row_count),
            )


def _json_value(value: Any) -> Any:
    if isinstance(value, (UUID, date)):
        return value.isoformat() if isinstance(value, date) else str(value)
    if isinstance(value, Decimal):
        return str(value)
    return value
