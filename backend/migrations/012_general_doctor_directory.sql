-- A narrow, invoker-security directory view exposes clinician display names
-- only through doctor rows already constrained by the existing doctors RLS.
SET ROLE qi_owner;

CREATE VIEW healthcare.general_doctor_directory
WITH (security_invoker = true)
AS
SELECT d.id AS doctor_id, d.user_id, u.display_name, d.specialty
FROM healthcare.doctors d
JOIN healthcare.users u ON u.id = d.user_id;

REVOKE ALL ON healthcare.general_doctor_directory FROM PUBLIC;
GRANT SELECT ON healthcare.general_doctor_directory TO qi_runtime;

RESET ROLE;
