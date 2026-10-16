-- Curated administrative reports and a validated appointment operation.
-- The query gateway remains read-only and does not use these objects.

SET ROLE qi_owner;

-- Indexes for reporting and date-based lookups.
CREATE INDEX query_logs_created_at_idx
    ON security.query_logs(created_at DESC);

CREATE INDEX risk_events_created_at_idx
    ON security.risk_events(created_at DESC);

CREATE INDEX appointments_scheduled_at_idx
    ON healthcare.appointments(scheduled_at DESC);


-- Daily query-execution summary.
CREATE VIEW security.query_execution_daily_summary
WITH (security_barrier = true)
AS
SELECT
    date_trunc('day', q.created_at) AS activity_day,
    q.decision,
    q.execution_status,
    count(*) AS request_count,
    sum(coalesce(q.row_count, 0)) AS returned_row_count,
    avg(q.duration_ms) AS average_duration_ms,
    min(q.duration_ms) AS minimum_duration_ms,
    max(q.duration_ms) AS maximum_duration_ms
FROM security.query_logs q
WHERE security.orm_admin_actor_allowed()
GROUP BY
    date_trunc('day', q.created_at),
    q.decision,
    q.execution_status;


-- Daily risk-event summary.
CREATE VIEW security.risk_event_daily_summary
WITH (security_barrier = true)
AS
SELECT
    date_trunc('day', r.created_at) AS activity_day,
    r.outcome,
    count(*) AS event_count,
    avg(r.risk_score) AS average_risk_score,
    min(r.risk_score) AS minimum_risk_score,
    max(r.risk_score) AS maximum_risk_score
FROM security.risk_events r
WHERE security.orm_admin_actor_allowed()
GROUP BY
    date_trunc('day', r.created_at),
    r.outcome;


-- Administrative appointment report aggregated by doctor.
CREATE VIEW healthcare.admin_appointment_report
WITH (security_barrier = true)
AS
SELECT
    d.id AS doctor_id,
    coalesce(
        u.display_name,
        d.synthetic_license_number
    ) AS doctor_name,
    d.specialty,
    count(a.id) AS appointment_count,
    count(a.id) FILTER (
        WHERE a.status = 'scheduled'
    ) AS scheduled_count,
    count(a.id) FILTER (
        WHERE a.status = 'completed'
    ) AS completed_count,
    min(a.scheduled_at) AS first_appointment_at,
    max(a.scheduled_at) AS last_appointment_at
FROM healthcare.doctors d
LEFT JOIN healthcare.users u
    ON u.id = d.user_id
LEFT JOIN healthcare.appointments a
    ON a.doctor_id = d.id
WHERE security.orm_admin_actor_allowed()
GROUP BY
    d.id,
    u.display_name,
    d.synthetic_license_number,
    d.specialty;


-- Administrative appointment details.
CREATE VIEW healthcare.admin_appointment_detail
WITH (security_barrier = true)
AS
SELECT
    a.id,
    a.patient_id,
    p.synthetic_patient_number,
    a.doctor_id,
    d.specialty,
    coalesce(
        u.display_name,
        d.synthetic_license_number
    ) AS doctor_name,
    a.scheduled_at,
    a.status,
    a.reason_category
FROM healthcare.appointments a
JOIN healthcare.patients p
    ON p.id = a.patient_id
JOIN healthcare.doctors d
    ON d.id = a.doctor_id
LEFT JOIN healthcare.users u
    ON u.id = d.user_id
WHERE security.orm_admin_actor_allowed();


-- IMPORTANT: Keep the first GRANT on one line because the
-- source-validation test searches for this exact SQL fragment.
GRANT SELECT ON security.query_execution_daily_summary TO qi_orm_admin;

GRANT SELECT ON security.risk_event_daily_summary TO qi_orm_admin;

GRANT SELECT ON healthcare.admin_appointment_report,
    healthcare.admin_appointment_detail TO qi_orm_admin;


-- Validated appointment-status update function.
CREATE FUNCTION security.admin_update_appointment_status(
    p_appointment_id uuid,
    p_new_status text
)
RETURNS healthcare.appointments
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, healthcare, security
AS $$
DECLARE
    v_appointment healthcare.appointments%ROWTYPE;
BEGIN
    IF p_appointment_id IS NULL THEN
        RAISE EXCEPTION 'appointment ID is required'
            USING ERRCODE = '22023';
    END IF;

    IF p_new_status IS NULL
       OR p_new_status NOT IN (
           'scheduled',
           'completed',
           'cancelled',
           'no_show'
       )
    THEN
        RAISE EXCEPTION 'unsupported appointment status'
            USING ERRCODE = '22023';
    END IF;

    IF nullif(
           current_setting('queryimmune.actor_id', true),
           ''
       ) IS NULL
       OR nullif(
           current_setting('queryimmune.request_id', true),
           ''
       ) IS NULL
       OR NOT security.orm_admin_actor_allowed()
    THEN
        RAISE EXCEPTION
            'active administrator request context is required'
            USING ERRCODE = '42501';
    END IF;

    -- Update the appointment while retaining the existing
    -- authorization and database-triggered audit behavior.
    UPDATE healthcare.appointments
       SET status = p_new_status,
           updated_at = now()
     WHERE id = p_appointment_id
     RETURNING * INTO v_appointment;

    IF NOT FOUND THEN
        RAISE EXCEPTION
            'appointment % does not exist',
            p_appointment_id
            USING ERRCODE = 'P0002';
    END IF;

    RETURN v_appointment;
END
$$;


-- Restrict direct execution to the administrator ORM role.
REVOKE ALL ON FUNCTION
    security.admin_update_appointment_status(uuid, text)
FROM PUBLIC;

GRANT EXECUTE ON FUNCTION
    security.admin_update_appointment_status(uuid, text)
TO qi_orm_admin;

RESET ROLE;