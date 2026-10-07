-- Narrow the runtime SELECT grant introduced by the initial schema migration.
SET ROLE qi_owner;
REVOKE SELECT ON ALL TABLES IN SCHEMA healthcare FROM qi_runtime;
REVOKE USAGE, SELECT ON ALL SEQUENCES IN SCHEMA healthcare, security FROM qi_runtime;
GRANT SELECT ON healthcare.users, healthcare.roles, healthcare.user_roles,
                healthcare.doctors, healthcare.appointments,
                healthcare.patients, healthcare.billing TO qi_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA healthcare REVOKE SELECT ON TABLES FROM qi_runtime;
RESET ROLE;
