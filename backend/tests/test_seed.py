from __future__ import annotations

import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy.exc import DBAPIError

from queryimmune_db import migrate

SEED_ADMIN_ID = UUID("10000000-0000-4000-8000-000000000008")
SEEDED_APPOINTMENT_IDS = (
    UUID("40000000-0000-4000-8000-000000000001"),
    UUID("40000000-0000-4000-8000-000000000002"),
    UUID("40000000-0000-4000-8000-000000000003"),
    UUID("40000000-0000-4000-8000-000000000004"),
)


@pytest.fixture
def postgres_admin_url(monkeypatch: pytest.MonkeyPatch) -> str:
    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("requires DATABASE_ADMIN_URL and a migrated PostgreSQL database")
    monkeypatch.setenv("DATABASE_ADMIN_URL", admin_url)
    return admin_url


@pytest.mark.database
def test_synthetic_seed_appointments_are_authorized_audited_and_idempotent(postgres_admin_url):
    import psycopg

    migrate.seed()
    with psycopg.connect(postgres_admin_url) as connection:
        appointments = connection.execute(
            "SELECT id FROM healthcare.appointments WHERE id = ANY(%s::uuid[])",
            (list(SEEDED_APPOINTMENT_IDS),),
        ).fetchall()
        create_events = connection.execute(
            """SELECT count(*)
               FROM security.admin_audit_events
               WHERE appointment_id = ANY(%s::uuid[])
                 AND actor_id = %s
                 AND action = 'create'""",
            (list(SEEDED_APPOINTMENT_IDS), SEED_ADMIN_ID),
        ).fetchone()[0]
        audit_count_before_rerun = connection.execute(
            "SELECT count(*) FROM security.admin_audit_events"
        ).fetchone()[0]

    assert {row[0] for row in appointments} == set(SEEDED_APPOINTMENT_IDS)
    assert create_events == len(SEEDED_APPOINTMENT_IDS)

    migrate.seed()
    with psycopg.connect(postgres_admin_url) as connection:
        audit_count_after_rerun = connection.execute(
            "SELECT count(*) FROM security.admin_audit_events"
        ).fetchone()[0]
        seeded_appointment_count = connection.execute(
            "SELECT count(*) FROM healthcare.appointments WHERE id = ANY(%s::uuid[])",
            (list(SEEDED_APPOINTMENT_IDS),),
        ).fetchone()[0]

    assert audit_count_after_rerun == audit_count_before_rerun
    assert seeded_appointment_count == len(SEEDED_APPOINTMENT_IDS)


@pytest.mark.database
def test_appointment_trigger_rejects_missing_admin_context(postgres_admin_url):
    import psycopg

    appointment_id = SEEDED_APPOINTMENT_IDS[0]
    with psycopg.connect(postgres_admin_url) as connection:
        before = connection.execute(
            "SELECT status FROM healthcare.appointments WHERE id = %s",
            (appointment_id,),
        ).fetchone()
        audit_count_before = connection.execute(
            "SELECT count(*) FROM security.admin_audit_events WHERE appointment_id = %s",
            (appointment_id,),
        ).fetchone()[0]
        assert before is not None

        with pytest.raises(psycopg.errors.InsufficientPrivilege) as error:
            with connection.transaction():
                connection.execute(
                    "UPDATE healthcare.appointments SET status = status WHERE id = %s",
                    (appointment_id,),
                )

        assert error.value.sqlstate == "42501"
        after = connection.execute(
            "SELECT status FROM healthcare.appointments WHERE id = %s",
            (appointment_id,),
        ).fetchone()
        audit_count_after = connection.execute(
            "SELECT count(*) FROM security.admin_audit_events WHERE appointment_id = %s",
            (appointment_id,),
        ).fetchone()[0]

    assert after == before
    assert audit_count_after == audit_count_before


@pytest.mark.database
def test_failed_seed_transaction_rolls_back_appointment_and_audit_then_reruns(postgres_admin_url):
    import psycopg

    failed_appointment_id = uuid4()
    seed_sql = (migrate.ROOT / "seed" / "001_synthetic_healthcare.sql").read_text(encoding="utf-8")
    seed_sql += f"""
INSERT INTO healthcare.appointments(
    id, patient_id, doctor_id, scheduled_at, status, reason_category
) VALUES (
    '{failed_appointment_id}',
    '30000000-0000-4000-8000-000000000001',
    '20000000-0000-4000-8000-000000000001',
    now(), 'scheduled', 'synthetic rollback test'
);
SELECT 1 / 0;
"""

    with pytest.raises(DBAPIError):
        migrate._execute_seed(seed_sql)

    with psycopg.connect(postgres_admin_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM healthcare.appointments WHERE id = %s",
            (failed_appointment_id,),
        ).fetchone()[0] == 0
        assert connection.execute(
            "SELECT count(*) FROM security.admin_audit_events WHERE appointment_id = %s",
            (failed_appointment_id,),
        ).fetchone()[0] == 0

    migrate.seed()
    with psycopg.connect(postgres_admin_url) as connection:
        assert connection.execute(
            "SELECT count(*) FROM healthcare.appointments WHERE id = ANY(%s::uuid[])",
            (list(SEEDED_APPOINTMENT_IDS),),
        ).fetchone()[0] == len(SEEDED_APPOINTMENT_IDS)
