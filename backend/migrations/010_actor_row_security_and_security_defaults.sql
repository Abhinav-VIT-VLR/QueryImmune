-- Bind actor-owned rows to trusted transaction-local context and close broad
-- future table grants in the security schema.
SET ROLE qi_owner;

-- 001 granted SELECT on every future security table. Remove that default and
-- re-grant reads only on relations used by the runtime repository.
ALTER DEFAULT PRIVILEGES IN SCHEMA security REVOKE SELECT ON TABLES FROM qi_runtime;
REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA security FROM qi_runtime;
GRANT SELECT ON security.query_logs, security.risk_events,
                security.behavior_profiles, security.action_role_policies
    TO qi_runtime;

-- Row ownership for the fixed appointments.mine query is established by the
-- doctor assignment. The other appointment actions are policy-authorized
-- shared aggregates and opt in through a separate transaction-local flag.
ALTER TABLE healthcare.doctors ENABLE ROW LEVEL SECURITY;
CREATE POLICY doctors_actor_scope ON healthcare.doctors
    FOR SELECT TO qi_runtime
    USING (
        user_id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid
        OR current_setting('queryimmune.shared_healthcare_read', true) = 'on'
    );

ALTER TABLE healthcare.appointments ENABLE ROW LEVEL SECURITY;
CREATE POLICY appointments_actor_scope ON healthcare.appointments
    FOR SELECT TO qi_runtime
    USING (
        doctor_id IN (
            SELECT d.id FROM healthcare.doctors d
            WHERE d.user_id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid
        )
        OR current_setting('queryimmune.shared_healthcare_read', true) = 'on'
    );

ALTER TABLE healthcare.patients ENABLE ROW LEVEL SECURITY;
CREATE POLICY patients_actor_scope ON healthcare.patients
    FOR SELECT TO qi_runtime
    USING (
        EXISTS (
            SELECT 1
            FROM healthcare.appointments a
            JOIN healthcare.doctors d ON d.id = a.doctor_id
            WHERE a.patient_id = patients.id
              AND d.user_id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid
        )
    );

-- Audit events and profiles are actor-owned too. Authorization policy rows are
-- shared configuration and remain readable by the runtime, not row-scoped.
ALTER TABLE security.query_logs ENABLE ROW LEVEL SECURITY;
CREATE POLICY query_logs_actor_scope ON security.query_logs
    FOR SELECT TO qi_runtime
    USING (actor_id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid);

ALTER TABLE security.risk_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY risk_events_actor_scope ON security.risk_events
    FOR SELECT TO qi_runtime
    USING (actor_id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid);

ALTER TABLE security.behavior_profiles ENABLE ROW LEVEL SECURITY;
CREATE POLICY behavior_profiles_actor_scope ON security.behavior_profiles
    FOR SELECT TO qi_runtime
    USING (actor_id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid);

-- SECURITY DEFINER audit, billing, and profile functions keep their explicit
-- EXECUTE grants; table SELECT revokes above do not change function privileges.
RESET ROLE;
