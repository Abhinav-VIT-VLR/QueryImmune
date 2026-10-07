-- QueryImmune database foundation. Apply with a trusted bootstrap/migration role.
DO $$ BEGIN
    CREATE ROLE qi_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;
DO $$ BEGIN
    CREATE ROLE qi_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

CREATE SCHEMA IF NOT EXISTS healthcare AUTHORIZATION qi_owner;
CREATE SCHEMA IF NOT EXISTS security AUTHORIZATION qi_owner;
REVOKE ALL ON SCHEMA healthcare, security FROM PUBLIC;
GRANT USAGE ON SCHEMA healthcare TO qi_runtime;
GRANT USAGE ON SCHEMA security TO qi_runtime;

SET ROLE qi_owner;

CREATE TABLE healthcare.users (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    external_subject text NOT NULL UNIQUE,
    display_name text NOT NULL CHECK (length(trim(display_name)) BETWEEN 1 AND 120),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'suspended', 'disabled')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE healthcare.roles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name text NOT NULL UNIQUE CHECK (name IN ('admin', 'doctor', 'nurse', 'billing', 'analyst')),
    description text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE healthcare.user_roles (
    user_id uuid NOT NULL REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    role_id uuid NOT NULL REFERENCES healthcare.roles(id) ON DELETE RESTRICT,
    assigned_by uuid REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    assigned_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (user_id, role_id)
);
CREATE INDEX user_roles_role_user_idx ON healthcare.user_roles(role_id, user_id);

CREATE TABLE healthcare.patients (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    synthetic_patient_number text NOT NULL UNIQUE,
    given_name text NOT NULL,
    family_name text NOT NULL,
    date_of_birth date NOT NULL CHECK (date_of_birth <= CURRENT_DATE),
    sex_at_birth text NOT NULL CHECK (sex_at_birth IN ('female', 'male', 'intersex', 'unknown')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    CHECK (synthetic_patient_number LIKE 'SYN-%')
);
CREATE INDEX patients_name_idx ON healthcare.patients(family_name, given_name);

CREATE TABLE healthcare.doctors (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id uuid UNIQUE REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    synthetic_license_number text NOT NULL UNIQUE,
    specialty text NOT NULL,
    active boolean NOT NULL DEFAULT true,
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (synthetic_license_number LIKE 'SYN-%')
);

CREATE TABLE healthcare.appointments (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id uuid NOT NULL REFERENCES healthcare.patients(id) ON DELETE RESTRICT,
    doctor_id uuid NOT NULL REFERENCES healthcare.doctors(id) ON DELETE RESTRICT,
    scheduled_at timestamptz NOT NULL,
    status text NOT NULL CHECK (status IN ('scheduled', 'completed', 'cancelled', 'no_show')),
    reason_category text NOT NULL CHECK (length(trim(reason_category)) BETWEEN 1 AND 80),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX appointments_patient_time_idx ON healthcare.appointments(patient_id, scheduled_at DESC);
CREATE INDEX appointments_doctor_time_idx ON healthcare.appointments(doctor_id, scheduled_at DESC);
CREATE INDEX appointments_status_time_idx ON healthcare.appointments(status, scheduled_at);

CREATE TABLE healthcare.medical_records (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id uuid NOT NULL REFERENCES healthcare.patients(id) ON DELETE RESTRICT,
    author_doctor_id uuid NOT NULL REFERENCES healthcare.doctors(id) ON DELETE RESTRICT,
    record_type text NOT NULL CHECK (record_type IN ('visit_note', 'lab_summary', 'diagnosis', 'prescription')),
    sensitivity text NOT NULL CHECK (sensitivity IN ('standard', 'sensitive', 'restricted')),
    summary text NOT NULL CHECK (length(trim(summary)) BETWEEN 1 AND 4000),
    recorded_at timestamptz NOT NULL DEFAULT now(),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX medical_records_patient_time_idx ON healthcare.medical_records(patient_id, recorded_at DESC);
CREATE INDEX medical_records_author_time_idx ON healthcare.medical_records(author_doctor_id, recorded_at DESC);
CREATE INDEX medical_records_sensitivity_idx ON healthcare.medical_records(sensitivity, recorded_at DESC);

CREATE TABLE healthcare.billing (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    patient_id uuid NOT NULL REFERENCES healthcare.patients(id) ON DELETE RESTRICT,
    appointment_id uuid REFERENCES healthcare.appointments(id) ON DELETE RESTRICT,
    amount numeric(12, 2) NOT NULL CHECK (amount >= 0),
    currency char(3) NOT NULL DEFAULT 'USD' CHECK (currency ~ '^[A-Z]{3}$'),
    status text NOT NULL CHECK (status IN ('pending', 'paid', 'waived', 'refunded')),
    due_at date,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX billing_patient_time_idx ON healthcare.billing(patient_id, created_at DESC);
CREATE INDEX billing_status_due_idx ON healthcare.billing(status, due_at) WHERE status = 'pending';

CREATE TABLE security.query_logs (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    actor_id uuid NOT NULL REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    created_at timestamptz NOT NULL DEFAULT now(),
    query_fingerprint char(64) NOT NULL CHECK (query_fingerprint ~ '^[0-9a-f]{64}$'),
    decision text NOT NULL CHECK (decision IN ('allow', 'review', 'block')),
    execution_status text NOT NULL CHECK (execution_status IN ('not_run', 'succeeded', 'failed')),
    duration_ms integer CHECK (duration_ms IS NULL OR duration_ms >= 0),
    row_count integer CHECK (row_count IS NULL OR row_count >= 0),
    CHECK ((decision = 'allow' AND execution_status IN ('not_run', 'succeeded', 'failed')) OR
           (decision IN ('review', 'block') AND execution_status = 'not_run'))
);
CREATE INDEX query_logs_actor_time_idx ON security.query_logs(actor_id, created_at DESC);
CREATE INDEX query_logs_decision_time_idx ON security.query_logs(decision, created_at DESC);

CREATE TABLE security.behavior_profiles (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    actor_id uuid NOT NULL REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    profile_version integer NOT NULL CHECK (profile_version > 0),
    window_start timestamptz NOT NULL,
    window_end timestamptz NOT NULL,
    approved_sample_count integer NOT NULL DEFAULT 0 CHECK (approved_sample_count >= 0),
    confidence numeric(5, 4) NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    feature_summary jsonb NOT NULL CHECK (jsonb_typeof(feature_summary) = 'object'),
    update_status text NOT NULL CHECK (update_status IN ('seeded', 'active', 'quarantined', 'superseded')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (actor_id, profile_version),
    CHECK (window_end >= window_start)
);
CREATE INDEX behavior_profiles_actor_version_idx ON security.behavior_profiles(actor_id, profile_version DESC);

CREATE TABLE security.risk_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL UNIQUE REFERENCES security.query_logs(request_id) ON DELETE RESTRICT,
    actor_id uuid NOT NULL REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    risk_score numeric(5, 2) NOT NULL CHECK (risk_score BETWEEN 0 AND 100),
    outcome text NOT NULL CHECK (outcome IN ('allow', 'review', 'block')),
    reason_codes text[] NOT NULL DEFAULT ARRAY[]::text[],
    feature_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(feature_snapshot) = 'object'),
    policy_version text NOT NULL CHECK (length(policy_version) BETWEEN 1 AND 80),
    model_version text,
    profile_version integer CHECK (profile_version IS NULL OR profile_version > 0),
    created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX risk_events_outcome_time_idx ON security.risk_events(outcome, created_at DESC);
CREATE INDEX risk_events_actor_time_idx ON security.risk_events(actor_id, created_at DESC);

-- Runtime may append audit data only through narrow SECURITY DEFINER functions.
CREATE FUNCTION security.append_query_log(
    p_request_id uuid, p_actor_id uuid, p_query_fingerprint text, p_decision text,
    p_execution_status text, p_duration_ms integer, p_row_count integer
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
DECLARE v_id uuid;
BEGIN
    INSERT INTO security.query_logs(request_id, actor_id, query_fingerprint, decision,
                                    execution_status, duration_ms, row_count)
    VALUES (p_request_id, p_actor_id, p_query_fingerprint, p_decision,
            p_execution_status, p_duration_ms, p_row_count)
    RETURNING id INTO v_id;
    RETURN v_id;
END;
$$;

CREATE FUNCTION security.append_risk_event(
    p_request_id uuid, p_actor_id uuid, p_risk_score numeric, p_outcome text,
    p_reason_codes text[], p_feature_snapshot jsonb, p_policy_version text,
    p_model_version text, p_profile_version integer
) RETURNS uuid
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
DECLARE v_id uuid;
BEGIN
    INSERT INTO security.risk_events(request_id, actor_id, risk_score, outcome, reason_codes,
                                     feature_snapshot, policy_version, model_version, profile_version)
    SELECT q.request_id, q.actor_id, p_risk_score, p_outcome, p_reason_codes,
           p_feature_snapshot, p_policy_version, p_model_version, p_profile_version
    FROM security.query_logs q
    WHERE q.request_id = p_request_id AND q.actor_id = p_actor_id AND q.decision = p_outcome
    RETURNING id INTO v_id;
    IF v_id IS NULL THEN
        RAISE EXCEPTION 'risk event must match an existing query decision and actor'
            USING ERRCODE = '23503';
    END IF;
    RETURN v_id;
END;
$$;
REVOKE ALL ON FUNCTION security.append_query_log(uuid, uuid, text, text, text, integer, integer) FROM PUBLIC;
REVOKE ALL ON FUNCTION security.append_risk_event(uuid, uuid, numeric, text, text[], jsonb, text, text, integer) FROM PUBLIC;

-- Runtime may read clinical data, profiles and audit history, but cannot mutate them directly.
GRANT SELECT ON ALL TABLES IN SCHEMA healthcare TO qi_runtime;
GRANT SELECT ON security.behavior_profiles TO qi_runtime;
GRANT SELECT ON security.query_logs, security.risk_events TO qi_runtime;
GRANT EXECUTE ON FUNCTION security.append_query_log(uuid, uuid, text, text, text, integer, integer) TO qi_runtime;
GRANT EXECUTE ON FUNCTION security.append_risk_event(uuid, uuid, numeric, text, text[], jsonb, text, text, integer) TO qi_runtime;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA healthcare, security TO qi_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA healthcare GRANT SELECT ON TABLES TO qi_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA security GRANT SELECT ON TABLES TO qi_runtime;
RESET ROLE;
