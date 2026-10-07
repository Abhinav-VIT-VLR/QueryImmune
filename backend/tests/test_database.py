from __future__ import annotations

import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_migration_has_required_entities_constraints_and_indexes() -> None:
    migration = (ROOT / "migrations" / "001_database_foundation.sql").read_text(encoding="utf-8").lower()
    for table in ("users", "roles", "user_roles", "patients", "doctors", "appointments", "medical_records", "billing", "query_logs", "behavior_profiles", "risk_events"):
        assert f"create table healthcare.{table}" in migration or f"create table security.{table}" in migration
    assert "primary key" in migration and "references" in migration
    assert " not null" in migration and " unique" in migration and " check (" in migration
    assert "create index" in migration
    assert "grant execute on function security.append_query_log" in migration
    assert "grant execute on function security.append_risk_event" in migration
    assert "grant select, insert on security.query_logs, security.risk_events to qi_runtime" not in migration
    assert "grant select on all tables in schema healthcare to qi_runtime" in migration
    assert "grant insert, update, delete" not in migration


def test_seed_is_synthetic_and_repeatable() -> None:
    seed = (ROOT / "seed" / "001_synthetic_healthcare.sql").read_text(encoding="utf-8").lower()
    assert "synthetic fixtures" in seed
    assert "on conflict" in seed
    assert "@" not in seed


def test_execution_migrations_limit_runtime_access():
    finish = (ROOT / "migrations" / "002_execution_audit.sql").read_text(encoding="utf-8").lower()
    narrow = (ROOT / "migrations" / "003_narrow_runtime_reads.sql").read_text(encoding="utf-8").lower()
    assert "security.finish_query_log" in finish
    assert "execution_status = 'not_run'" in finish
    assert "revoke select on all tables in schema healthcare from qi_runtime" in narrow
    assert "healthcare.medical_records" not in narrow


def test_profile_learning_migration_bounds_observed_statistics():
    migration = (ROOT / "migrations" / "007_harden_profile_statistics.sql").read_text(encoding="utf-8").lower()
    assert "v_observed_max" in migration
    assert "statistics exceed observed bounds" in migration
    assert "90 days" in migration
    assert "one-time repair" in migration


def test_runtime_grants_hide_patient_demographics_and_raw_billing_rows():
    migration = (ROOT / "migrations" / "008_minimize_runtime_data_access.sql").read_text(encoding="utf-8").lower()
    assert "grant select (id, synthetic_patient_number) on healthcare.patients" in migration
    assert "security.billing_summary" in migration
    assert "revoke select on healthcare.users" in migration
    assert "billing_created_at_idx on healthcare.billing(created_at)" in migration


@pytest.mark.database
def test_live_database_constraints_indexes_seed_and_readonly_transaction() -> None:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    from queryimmune_db.connection import database_health, transaction

    assert database_health()["status"] == "ok"
    with transaction(read_only=True) as conn:
        assert conn.execute("SELECT count(*) FROM healthcare.patients").fetchone()[0] >= 4
        assert conn.execute("SELECT count(*) FROM pg_indexes WHERE schemaname IN ('healthcare','security')").fetchone()[0] >= 10
        with pytest.raises(psycopg.errors.ReadOnlySqlTransaction):
            conn.execute("UPDATE healthcare.patients SET family_name = family_name WHERE false")


@pytest.mark.database
def test_runtime_cannot_mutate_clinical_tables():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    from queryimmune_db.connection import transaction

    with transaction() as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("UPDATE healthcare.patients SET family_name = family_name WHERE false")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT summary FROM healthcare.medical_records LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT date_of_birth FROM healthcare.patients LIMIT 1")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("SELECT amount FROM healthcare.billing LIMIT 1")
        assert conn.execute("SELECT status, currency, invoice_count, total_amount FROM security.billing_summary(NULL, 25)").fetchall()
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            with conn.transaction():
                conn.execute("INSERT INTO security.query_logs(actor_id, query_fingerprint, decision, execution_status) SELECT id, repeat('a', 64), 'block', 'not_run' FROM healthcare.users LIMIT 1")

