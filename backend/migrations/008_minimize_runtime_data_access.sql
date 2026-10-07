-- Keep the API runtime login on the minimum columns needed for identity and
-- fixed appointment templates. Billing aggregation runs inside an owner function.
SET ROLE qi_owner;
CREATE INDEX IF NOT EXISTS billing_created_at_idx ON healthcare.billing(created_at);

REVOKE SELECT ON healthcare.users, healthcare.roles, healthcare.user_roles,
    healthcare.doctors, healthcare.appointments, healthcare.patients,
    healthcare.billing FROM qi_runtime;

GRANT SELECT (id, external_subject, display_name, status) ON healthcare.users TO qi_runtime;
GRANT SELECT (id, name) ON healthcare.roles TO qi_runtime;
GRANT SELECT (user_id, role_id) ON healthcare.user_roles TO qi_runtime;
GRANT SELECT (id, user_id, specialty) ON healthcare.doctors TO qi_runtime;
GRANT SELECT (patient_id, doctor_id, scheduled_at, status, reason_category)
    ON healthcare.appointments TO qi_runtime;
GRANT SELECT (id, synthetic_patient_number) ON healthcare.patients TO qi_runtime;

CREATE FUNCTION security.billing_summary(p_from_date date, p_limit integer)
RETURNS TABLE(status text, currency character, invoice_count bigint, total_amount numeric)
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
BEGIN
    IF p_limit < 1 OR p_limit > 100 THEN
        RAISE EXCEPTION 'billing summary limit is outside the supported range'
            USING ERRCODE = '22023';
    END IF;
    RETURN QUERY
    SELECT b.status::text, b.currency::character, count(*)::bigint, sum(b.amount)
    FROM healthcare.billing b
    WHERE p_from_date IS NULL OR b.created_at >= p_from_date::timestamptz
    GROUP BY b.status, b.currency
    ORDER BY b.status, b.currency
    LIMIT p_limit;
END;
$$;
REVOKE ALL ON FUNCTION security.billing_summary(date, integer) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION security.billing_summary(date, integer) TO qi_runtime;

RESET ROLE;
