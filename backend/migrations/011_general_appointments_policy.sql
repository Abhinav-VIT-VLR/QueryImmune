-- General NL read access is a separate action surface. Permissions remain
-- database-backed, default-closed, and scoped by actor/RLS or aggregate role.
SET ROLE qi_owner;

INSERT INTO security.action_role_policies(action, resource, role_id)
SELECT policy.action, policy.resource, roles.id
FROM (VALUES
    ('appointments.general_own', 'appointments', 'doctor'),
    ('appointments.general_own', 'patients', 'doctor'),
    ('appointments.general_own', 'doctors', 'doctor'),
    ('appointments.general_aggregate', 'appointments', 'analyst'),
    ('appointments.general_aggregate', 'doctors', 'analyst'),
    ('appointments.general_aggregate', 'appointments', 'admin'),
    ('appointments.general_aggregate', 'doctors', 'admin')
) AS policy(action, resource, role_name)
JOIN healthcare.roles ON roles.name = policy.role_name
ON CONFLICT (action, resource, role_id) DO NOTHING;

RESET ROLE;
