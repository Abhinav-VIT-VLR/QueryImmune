-- Run with psql as queryimmune_orm_admin after migrations and seed:
--   psql "$DATABASE_ORM_ADMIN_URL" -f backend/sql/advanced_dbms_demo.sql
-- The login is intentionally limited; the request context below models the
-- verified actor context established by the API. Do not use this credential
-- for AI-generated SQL or expose it to clients.

\echo 'Admin reports (the views return aggregates, not raw query or risk rows)'
BEGIN;
SELECT set_config('queryimmune.actor_id', '10000000-0000-4000-8000-000000000008', true);
SELECT set_config('queryimmune.request_id', gen_random_uuid()::text, true);

SELECT activity_day::date, decision, execution_status, request_count,
       returned_row_count, round(average_duration_ms, 2) AS avg_duration_ms,
       minimum_duration_ms, maximum_duration_ms
FROM security.query_execution_daily_summary
WHERE activity_day >= now() - interval '30 days'
ORDER BY activity_day DESC, decision, execution_status
LIMIT 50;

SELECT activity_day::date, outcome, event_count,
       round(average_risk_score, 2) AS avg_risk,
       minimum_risk_score, maximum_risk_score
FROM security.risk_event_daily_summary
ORDER BY activity_day DESC, outcome
LIMIT 50;

SELECT specialty, count(*) AS doctors,
       sum(appointment_count) AS appointments,
       avg(appointment_count) AS avg_per_doctor,
       min(appointment_count) AS min_per_doctor,
       max(appointment_count) AS max_per_doctor
FROM healthcare.admin_appointment_report
GROUP BY specialty
HAVING sum(appointment_count) > 0
ORDER BY appointments DESC, specialty;

-- LEFT JOIN retains doctors who have no appointments.
SELECT d.doctor_id, d.specialty, count(a.id) AS appointment_count
FROM healthcare.admin_appointment_report d
LEFT JOIN healthcare.admin_appointment_detail a ON a.doctor_id = d.doctor_id
GROUP BY d.doctor_id, d.specialty
ORDER BY appointment_count DESC, d.specialty;

-- RIGHT JOIN has a meaningful outer-join use here: retain every doctor,
-- including doctors with no appointment, by putting doctors on the right.
SELECT d.doctor_id, d.specialty, count(a.id) AS appointment_count
FROM healthcare.admin_appointment_detail a
RIGHT JOIN healthcare.admin_appointment_report d ON d.doctor_id = a.doctor_id
GROUP BY d.doctor_id, d.specialty
ORDER BY appointment_count DESC, d.specialty;

-- Multi-table joins over restricted report views and RLS-protected audit rows.
SELECT a.id, a.scheduled_at, a.status, a.synthetic_patient_number,
       r.specialty, r.doctor_name, count(e.id) AS audit_change_count
FROM healthcare.admin_appointment_detail a
INNER JOIN healthcare.admin_appointment_report r ON r.doctor_id = a.doctor_id
LEFT JOIN security.admin_audit_events e ON e.appointment_id = a.id
WHERE a.scheduled_at >= now() - interval '30 days'
GROUP BY a.id, a.scheduled_at, a.status, a.synthetic_patient_number,
         r.specialty, r.doctor_name
ORDER BY a.scheduled_at DESC
LIMIT 25;

-- Scalar subquery compares each doctor with the average appointment count.
SELECT report.doctor_id, report.doctor_name, report.appointment_count
FROM healthcare.admin_appointment_report report
WHERE report.appointment_count > (
    SELECT avg(other.appointment_count)
    FROM healthcare.admin_appointment_report other
);

-- Correlated subquery: each doctor is tested against their own appointments.
SELECT d.doctor_id, d.specialty
FROM healthcare.admin_appointment_report d
WHERE EXISTS (
    SELECT 1
    FROM healthcare.admin_appointment_detail a
    WHERE a.doctor_id = d.doctor_id AND a.status = 'scheduled'
)
ORDER BY d.specialty;
COMMIT;

\echo 'Transactional DML: INSERT, a savepoint recovery, function UPDATE, DELETE'
BEGIN;
SELECT set_config('queryimmune.actor_id', '10000000-0000-4000-8000-000000000008', true);
SELECT set_config('queryimmune.request_id', gen_random_uuid()::text, true);

INSERT INTO healthcare.appointments(
    patient_id, doctor_id, scheduled_at, status, reason_category
) VALUES (
    '30000000-0000-4000-8000-000000000001',
    '20000000-0000-4000-8000-000000000001',
    now() + interval '45 days',
    'scheduled',
    'DBMS demonstration'
)
RETURNING id AS demo_appointment_id \gset
\echo 'Created appointment:' :demo_appointment_id

SAVEPOINT before_invalid_reference;
-- Intentionally violates patient_id FK. psql reports the error and leaves the
-- transaction failed until the savepoint rollback below.
INSERT INTO healthcare.appointments(
    patient_id, doctor_id, scheduled_at, status, reason_category
) VALUES (
    'ffffffff-ffff-4fff-8fff-ffffffffffff',
    '20000000-0000-4000-8000-000000000001',
    now() + interval '46 days',
    'scheduled',
    'invalid FK demonstration'
);
ROLLBACK TO SAVEPOINT before_invalid_reference;

SAVEPOINT before_direct_update;
UPDATE healthcare.appointments
SET status = 'cancelled', updated_at = now()
WHERE id = :'demo_appointment_id'::uuid
RETURNING id, status;
ROLLBACK TO SAVEPOINT before_direct_update;

SAVEPOINT before_invalid_status;
-- The function raises SQLSTATE 22023 for this unsupported value.
SELECT security.admin_update_appointment_status(
    :'demo_appointment_id'::uuid, 'pending'
);
ROLLBACK TO SAVEPOINT before_invalid_status;

SAVEPOINT before_status_change;
SELECT (security.admin_update_appointment_status(
    :'demo_appointment_id'::uuid, 'completed'
)).status AS status_inside_savepoint;
ROLLBACK TO SAVEPOINT before_status_change;

SELECT (security.admin_update_appointment_status(
    :'demo_appointment_id'::uuid, 'completed'
)).status AS committed_status;

DELETE FROM healthcare.appointments
WHERE id = :'demo_appointment_id'::uuid
RETURNING id AS deleted_appointment_id;

-- The audit trigger runs in this same transaction. Inspect the synthetic audit
-- rows before rolling back so neither the appointment nor its audit events persist.
SELECT action, count(*) AS event_count
FROM security.admin_audit_events
WHERE appointment_id = :'demo_appointment_id'::uuid
GROUP BY action
ORDER BY action;
ROLLBACK;

\echo 'Full transaction rollback: the status and trigger audit event both revert'
BEGIN;
SELECT set_config('queryimmune.actor_id', '10000000-0000-4000-8000-000000000008', true);
SELECT set_config('queryimmune.request_id', gen_random_uuid()::text, true);
SELECT (security.admin_update_appointment_status(
    '40000000-0000-4000-8000-000000000002', 'cancelled'
)).status AS temporary_status;
ROLLBACK;

SELECT id, status
FROM healthcare.appointments
WHERE id = '40000000-0000-4000-8000-000000000002';

\echo 'Successful completion. Inspect EXPLAIN plans for the date-filtered reports if desired.'
