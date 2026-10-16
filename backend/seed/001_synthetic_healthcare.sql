-- Fully synthetic fixtures. All names and identifiers below are invented.
INSERT INTO healthcare.roles(name, description) VALUES
 ('admin', 'Synthetic demo administrator'), ('doctor', 'Synthetic care provider'),
 ('nurse', 'Synthetic nursing staff'), ('billing', 'Synthetic billing operator'),
 ('billing_staff', 'Synthetic billing staff'), ('receptionist', 'Synthetic reception staff'),
 ('analyst', 'Synthetic aggregate-only analyst'), ('unknown_role', 'Synthetic role without assigned permissions')
ON CONFLICT (name) DO NOTHING;

INSERT INTO healthcare.users(id, external_subject, display_name) VALUES
 ('10000000-0000-4000-8000-000000000001', 'demo:dr-ada', 'Dr Ada Example'),
 ('10000000-0000-4000-8000-000000000002', 'demo:nurse-lee', 'Nurse Lee Example'),
 ('10000000-0000-4000-8000-000000000003', 'demo:billing-sam', 'Sam Example Billing'),
 ('10000000-0000-4000-8000-000000000004', 'demo:analyst-riley', 'Riley Example Analyst'),
 ('10000000-0000-4000-8000-000000000005', 'demo:receptionist-jules', 'Jules Example Reception'),
 ('10000000-0000-4000-8000-000000000006', 'demo:unconfigured-role', 'Synthetic Unconfigured Role'),
 ('10000000-0000-4000-8000-000000000007', 'demo:dr-grace', 'Dr Grace Example'),
 ('10000000-0000-4000-8000-000000000008', 'demo:admin', 'Synthetic Demo Administrator')
ON CONFLICT (external_subject) DO NOTHING;
INSERT INTO healthcare.user_roles(user_id, role_id)
SELECT u.id, r.id FROM (VALUES
 ('demo:dr-ada', 'doctor'), ('demo:nurse-lee', 'nurse'),
 ('demo:billing-sam', 'billing'), ('demo:billing-sam', 'billing_staff'),
 ('demo:analyst-riley', 'analyst'), ('demo:receptionist-jules', 'receptionist'),
 ('demo:unconfigured-role', 'unknown_role'), ('demo:dr-grace', 'doctor'),
 ('demo:admin', 'admin')
) AS x(subject, role_name)
JOIN healthcare.users u ON u.external_subject = x.subject
JOIN healthcare.roles r ON r.name = x.role_name
ON CONFLICT DO NOTHING;

-- Action/resource permissions preserve the supported policy matrix. Role identity
-- comes from healthcare.roles and no medical-record permission is seeded: the
-- current runtime broker deliberately has no such action or PostgreSQL grant.
INSERT INTO security.action_role_policies(action, resource, role_id)
SELECT policy.action, policy.resource, r.id
FROM (VALUES
    ('appointments.mine', 'appointments', 'doctor'),
    ('appointments.mine', 'patients', 'doctor'),
    ('appointments.mine', 'doctors', 'doctor'),
    ('appointments.by_status', 'appointments', 'analyst'),
    ('appointments.by_status', 'doctors', 'analyst'),
    ('appointments.by_status', 'appointments', 'admin'),
    ('appointments.by_status', 'doctors', 'admin'),
    ('billing.summary', 'billing', 'billing'),
    ('billing.summary', 'billing', 'billing_staff'),
    ('billing.summary', 'billing', 'admin'),
    ('appointments.cardiology_today', 'appointments', 'doctor'),
    ('appointments.cardiology_today', 'doctors', 'doctor'),
    ('appointments.cardiology_today', 'appointments', 'analyst'),
    ('appointments.cardiology_today', 'doctors', 'analyst'),
    ('appointments.cardiology_today', 'appointments', 'admin'),
    ('appointments.cardiology_today', 'doctors', 'admin'),
    ('appointments.admin_crud', 'appointments', 'admin')
) AS policy(action, resource, role_name)
JOIN healthcare.roles r ON r.name = policy.role_name
ON CONFLICT (action, resource, role_id) DO NOTHING;

INSERT INTO healthcare.doctors(id, user_id, synthetic_license_number, specialty) VALUES
 ('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'SYN-LIC-ADA-001', 'Family Medicine'),
 ('20000000-0000-4000-8000-000000000002', NULL, 'SYN-LIC-JAM-002', 'Cardiology')
ON CONFLICT (synthetic_license_number) DO NOTHING;
UPDATE healthcare.doctors SET user_id = '10000000-0000-4000-8000-000000000007'
WHERE synthetic_license_number = 'SYN-LIC-JAM-002' AND user_id IS NULL;

INSERT INTO healthcare.patients(id, synthetic_patient_number, given_name, family_name, date_of_birth, sex_at_birth) VALUES
 ('30000000-0000-4000-8000-000000000001', 'SYN-PAT-0001', 'Jordan', 'Example', '1988-04-12', 'unknown'),
 ('30000000-0000-4000-8000-000000000002', 'SYN-PAT-0002', 'Taylor', 'Sample', '1975-09-23', 'female'),
 ('30000000-0000-4000-8000-000000000003', 'SYN-PAT-0003', 'Morgan', 'Demo', '1999-01-07', 'male'),
 ('30000000-0000-4000-8000-000000000004', 'SYN-PAT-0004', 'Casey', 'Fiction', '1962-12-30', 'unknown')
ON CONFLICT (synthetic_patient_number) DO NOTHING;

INSERT INTO healthcare.appointments(id, patient_id, doctor_id, scheduled_at, status, reason_category) VALUES
 ('40000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', now() - interval '14 days', 'completed', 'routine follow-up'),
 ('40000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', now() + interval '3 days', 'scheduled', 'cardiology review'),
 ('40000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', '20000000-0000-4000-8000-000000000001', now() - interval '2 days', 'completed', 'wellness visit'),
 ('40000000-0000-4000-8000-000000000004', '30000000-0000-4000-8000-000000000004', '20000000-0000-4000-8000-000000000002', now() + interval '10 days', 'scheduled', 'follow-up')
ON CONFLICT (id) DO NOTHING;

INSERT INTO healthcare.medical_records(id, patient_id, author_doctor_id, record_type, sensitivity, summary, recorded_at) VALUES
 ('50000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 'visit_note', 'standard', 'Synthetic routine follow-up; patient reports improved sleep.', now() - interval '14 days'),
 ('50000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', 'diagnosis', 'sensitive', 'Synthetic example diagnosis summary for access-control demonstrations.', now() - interval '30 days'),
 ('50000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', '20000000-0000-4000-8000-000000000001', 'lab_summary', 'standard', 'Synthetic lab summary: values within illustrative reference ranges.', now() - interval '2 days')
ON CONFLICT (id) DO NOTHING;

INSERT INTO healthcare.billing(id, patient_id, appointment_id, amount, currency, status, due_at) VALUES
 ('60000000-0000-4000-8000-000000000001', '30000000-0000-4000-8000-000000000001', '40000000-0000-4000-8000-000000000001', 125.00, 'USD', 'paid', CURRENT_DATE - 7),
 ('60000000-0000-4000-8000-000000000002', '30000000-0000-4000-8000-000000000002', NULL, 240.50, 'USD', 'pending', CURRENT_DATE + 14),
 ('60000000-0000-4000-8000-000000000003', '30000000-0000-4000-8000-000000000003', '40000000-0000-4000-8000-000000000003', 0.00, 'USD', 'waived', NULL)
ON CONFLICT (id) DO NOTHING;

INSERT INTO security.behavior_profiles(actor_id, profile_version, window_start, window_end, approved_sample_count, confidence, feature_summary, update_status)
SELECT u.id, 1, now() - interval '90 days', now(), 12, 0.65,
       '{"actions":["appointments.mine","appointments.cardiology_today"],"tables":["appointments","patients","doctors"],"columns":["scheduled_at","status","reason_category","synthetic_patient_number","specialty","patient_id"],"operations":["read"],"scopes":["own_records","aggregate"],"sensitivities":["standard"],"roles":["doctor"],"signatures":["appointments.mine:appointments,doctors,patients:reason_category,scheduled_at,specialty,status,synthetic_patient_number::own_records:limit-standard","appointments.cardiology_today:appointments,doctors:patient_id:count:aggregate:limit-small"],"query_structure":["appointments.mine:appointments,doctors,patients:reason_category,scheduled_at,specialty,status,synthetic_patient_number::own_records:limit-standard"],"active_hours":["0","1","2","3","4","5","6","7","8","9","10","11","12","13","14","15","16","17","18","19","20","21","22","23"],"result_size_max":8,"frequency_sample_count":12}'::jsonb, 'seeded'
FROM healthcare.users u WHERE u.external_subject = 'demo:dr-ada'
ON CONFLICT (actor_id, profile_version) DO NOTHING;
