-- Separate ORM capability for audited, admin-only appointment maintenance.
SET ROLE qi_owner;

CREATE FUNCTION security.orm_admin_actor_allowed() RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
    SELECT EXISTS (
        SELECT 1
        FROM healthcare.users u
        JOIN healthcare.user_roles ur ON ur.user_id = u.id
        JOIN healthcare.roles r ON r.id = ur.role_id
        WHERE u.id = nullif(current_setting('queryimmune.actor_id', true), '')::uuid
          AND u.status = 'active'
          AND r.name = 'admin'
    )
$$;
REVOKE ALL ON FUNCTION security.orm_admin_actor_allowed() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION security.orm_admin_actor_allowed() TO qi_orm_admin;

GRANT USAGE ON SCHEMA healthcare, security TO qi_orm_admin;
GRANT SELECT, INSERT, UPDATE, DELETE ON healthcare.appointments TO qi_orm_admin;

CREATE POLICY appointments_orm_admin_select ON healthcare.appointments
    FOR SELECT TO qi_orm_admin
    USING (security.orm_admin_actor_allowed());
CREATE POLICY appointments_orm_admin_insert ON healthcare.appointments
    FOR INSERT TO qi_orm_admin
    WITH CHECK (security.orm_admin_actor_allowed());
CREATE POLICY appointments_orm_admin_update ON healthcare.appointments
    FOR UPDATE TO qi_orm_admin
    USING (security.orm_admin_actor_allowed())
    WITH CHECK (security.orm_admin_actor_allowed());
CREATE POLICY appointments_orm_admin_delete ON healthcare.appointments
    FOR DELETE TO qi_orm_admin
    USING (security.orm_admin_actor_allowed());

CREATE TABLE security.admin_audit_events (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    request_id uuid NOT NULL,
    actor_id uuid NOT NULL REFERENCES healthcare.users(id) ON DELETE RESTRICT,
    appointment_id uuid NOT NULL,
    action text NOT NULL CHECK (action IN ('create', 'update', 'delete')),
    before_data jsonb CHECK (before_data IS NULL OR jsonb_typeof(before_data) = 'object'),
    after_data jsonb CHECK (after_data IS NULL OR jsonb_typeof(after_data) = 'object'),
    created_at timestamptz NOT NULL DEFAULT now(),
    CHECK (
        (action = 'create' AND before_data IS NULL AND after_data IS NOT NULL) OR
        (action = 'update' AND before_data IS NOT NULL AND after_data IS NOT NULL) OR
        (action = 'delete' AND before_data IS NOT NULL AND after_data IS NULL)
    )
);
CREATE INDEX admin_audit_events_actor_time_idx
    ON security.admin_audit_events(actor_id, created_at DESC);
CREATE INDEX admin_audit_events_appointment_time_idx
    ON security.admin_audit_events(appointment_id, created_at DESC);
CREATE INDEX admin_audit_events_request_idx ON security.admin_audit_events(request_id);

ALTER TABLE security.admin_audit_events ENABLE ROW LEVEL SECURITY;
CREATE POLICY admin_audit_events_admin_read ON security.admin_audit_events
    FOR SELECT TO qi_orm_admin
    USING (security.orm_admin_actor_allowed());
GRANT SELECT ON security.admin_audit_events TO qi_orm_admin;

CREATE FUNCTION security.audit_appointment_admin_change() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER
SET search_path = pg_catalog, healthcare, security
AS $$
DECLARE
    v_actor_id uuid := nullif(current_setting('queryimmune.actor_id', true), '')::uuid;
    v_request_id uuid := nullif(current_setting('queryimmune.request_id', true), '')::uuid;
BEGIN
    IF v_actor_id IS NULL OR v_request_id IS NULL
       OR NOT security.orm_admin_actor_allowed() THEN
        RAISE EXCEPTION 'admin actor and request context are required'
            USING ERRCODE = '42501';
    END IF;

    IF TG_OP = 'INSERT' THEN
        INSERT INTO security.admin_audit_events(
            request_id, actor_id, appointment_id, action, before_data, after_data
        ) VALUES (v_request_id, v_actor_id, NEW.id, 'create', NULL, to_jsonb(NEW));
        RETURN NEW;
    ELSIF TG_OP = 'UPDATE' THEN
        INSERT INTO security.admin_audit_events(
            request_id, actor_id, appointment_id, action, before_data, after_data
        ) VALUES (v_request_id, v_actor_id, NEW.id, 'update', to_jsonb(OLD), to_jsonb(NEW));
        RETURN NEW;
    ELSE
        INSERT INTO security.admin_audit_events(
            request_id, actor_id, appointment_id, action, before_data, after_data
        ) VALUES (v_request_id, v_actor_id, OLD.id, 'delete', to_jsonb(OLD), NULL);
        RETURN OLD;
    END IF;
END
$$;
REVOKE ALL ON FUNCTION security.audit_appointment_admin_change() FROM PUBLIC;
CREATE TRIGGER appointments_admin_audit
    AFTER INSERT OR UPDATE OR DELETE ON healthcare.appointments
    FOR EACH ROW EXECUTE FUNCTION security.audit_appointment_admin_change();

RESET ROLE;
