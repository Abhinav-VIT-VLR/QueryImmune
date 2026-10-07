from __future__ import annotations

import os
from pathlib import Path
from uuid import uuid4

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


def test_actor_rls_and_security_default_privileges_are_migrated():
    migration = (ROOT / "migrations" / "010_actor_row_security_and_security_defaults.sql").read_text(encoding="utf-8").lower()
    assert "alter table healthcare.doctors enable row level security" in migration
    assert "alter table healthcare.appointments enable row level security" in migration
    assert "alter table healthcare.patients enable row level security" in migration
    assert "alter table security.query_logs enable row level security" in migration
    assert "alter table security.risk_events enable row level security" in migration
    assert "alter table security.behavior_profiles enable row level security" in migration
    assert "alter default privileges in schema security revoke select on tables from qi_runtime" in migration
    assert "revoke all privileges on all tables in schema security from qi_runtime" in migration
    assert "security.action_role_policies" in migration


def test_authorization_migration_is_role_referenced_and_runtime_read_only():
    migration = (ROOT / "migrations" / "009_database_action_authorization.sql").read_text(encoding="utf-8").lower()
    seed = (ROOT / "seed" / "001_synthetic_healthcare.sql").read_text(encoding="utf-8").lower()
    assert "create table security.action_role_policies" in migration
    assert "role_id uuid not null references healthcare.roles(id)" in migration
    assert "action text not null" in migration and "resource text not null" in migration
    assert "enabled boolean not null" in migration
    assert "grant select on security.action_role_policies to qi_runtime" in migration
    assert "grant insert" not in migration and "grant update" not in migration
    assert "appointments.mine', 'patients', 'doctor'" in seed
    assert "medical_records.read" not in seed
    assert "billing_staff" in seed and "receptionist" in seed


@pytest.mark.database
def test_live_database_constraints_indexes_seed_and_readonly_transaction() -> None:
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not configured")
    psycopg = pytest.importorskip("psycopg")
    from queryimmune_db.connection import database_health, transaction

    assert database_health()["status"] == "ok"
    with transaction(read_only=True, actor_context_id="10000000-0000-4000-8000-000000000001") as conn:
        assert conn.execute("SELECT count(*) FROM healthcare.patients").fetchone()[0] >= 1
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


@pytest.mark.database
def test_security_runtime_access_is_explicit_read_only_and_default_closed():
    if not os.environ.get("DATABASE_URL"):
        pytest.skip("DATABASE_URL is not configured")
    from queryimmune_db.connection import transaction

    runtime_relations = (
        "security.query_logs",
        "security.risk_events",
        "security.behavior_profiles",
        "security.action_role_policies",
    )
    with transaction(read_only=True, actor_context_id="10000000-0000-4000-8000-000000000001") as conn:
        assert conn.execute(
            "SELECT has_schema_privilege(current_user, 'security', 'USAGE')"
        ).fetchone()[0]
        for relation in runtime_relations:
            can_select, can_insert, can_update, can_delete = conn.execute(
                """SELECT has_table_privilege(current_user, %s, 'SELECT'),
                          has_table_privilege(current_user, %s, 'INSERT'),
                          has_table_privilege(current_user, %s, 'UPDATE'),
                          has_table_privilege(current_user, %s, 'DELETE')""",
                (relation, relation, relation, relation),
            ).fetchone()
            assert can_select
            assert not can_insert and not can_update and not can_delete
        runtime_functions = (
            "security.append_query_log(uuid,uuid,text,text,text,integer,integer)",
            "security.append_risk_event(uuid,uuid,numeric,text,text[],jsonb,text,text,integer)",
            "security.finish_query_log(uuid,text,integer,integer)",
            "security.billing_summary(date,integer)",
            "security.append_behavior_profile(uuid,integer,integer,numeric,timestamptz,timestamptz,jsonb)",
        )
        for function in runtime_functions:
            assert conn.execute(
                "SELECT has_function_privilege(current_user, %s, 'EXECUTE')", (function,)
            ).fetchone()[0]

    admin_url = os.environ.get("DATABASE_ADMIN_URL")
    if not admin_url:
        pytest.skip("DATABASE_ADMIN_URL is required to verify future default privileges")
    import psycopg
    from psycopg import sql

    probe_name = f"default_privilege_probe_{uuid4().hex}"
    with psycopg.connect(admin_url, autocommit=True) as conn:
        conn.execute("SET ROLE qi_owner")
        conn.execute(sql.SQL("CREATE TABLE security.{} (id integer)").format(sql.Identifier(probe_name)))
        try:
            can_select = conn.execute(
                "SELECT has_table_privilege('queryimmune_runtime', %s, 'SELECT')",
                (f"security.{probe_name}",),
            ).fetchone()[0]
            assert not can_select
        finally:
            conn.execute(sql.SQL("DROP TABLE security.{}").format(sql.Identifier(probe_name)))
            conn.execute("RESET ROLE")

