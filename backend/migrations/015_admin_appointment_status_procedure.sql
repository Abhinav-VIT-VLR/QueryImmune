
-- Stored-procedure entry point for the audited admin appointment-status change.
-- Reuse the existing validated function so status validation, actor/request
-- authorization, missing-appointment handling, and the audit-triggered update
-- remain implemented in one place.

SET ROLE qi_owner;

CREATE PROCEDURE security.admin_update_appointment_status_proc(
    p_appointment_id uuid,
    p_new_status text
)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = pg_catalog, healthcare, security
AS $$
BEGIN
    -- Delegate validation, authorization and update to the existing function.
    PERFORM security.admin_update_appointment_status(
        p_appointment_id,
        p_new_status
    );

    RAISE NOTICE 'Appointment % status updated to %',
        p_appointment_id, p_new_status;
END
$$;

-- Prevent unrestricted execution.
REVOKE ALL ON PROCEDURE
    security.admin_update_appointment_status_proc(uuid, text)
FROM PUBLIC;

-- Allow only the ORM administrator role to execute the procedure.
GRANT EXECUTE ON PROCEDURE
    security.admin_update_appointment_status_proc(uuid, text)
TO qi_orm_admin;

RESET ROLE;
