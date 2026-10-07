-- Fully synthetic fixtures. All names and identifiers below are invented.
BEGIN;
INSERT INTO healthcare.roles(name, description) VALUES
 ('admin', 'Synthetic demo administrator'), ('doctor', 'Synthetic care provider'),
 ('nurse', 'Synthetic nursing staff'), ('billing', 'Synthetic billing operator'),
 ('analyst', 'Synthetic aggregate-only analyst')
ON CONFLICT (name) DO NOTHING;

INSERT INTO healthcare.users(id, external_subject, display_name) VALUES
 ('10000000-0000-4000-8000-000000000001', 'demo:dr-ada', 'Dr Ada Example'),
 ('10000000-0000-4000-8000-000000000002', 'demo:nurse-lee', 'Nurse Lee Example'),
 ('10000000-0000-4000-8000-000000000003', 'demo:billing-sam', 'Sam Example Billing'),
 ('10000000-0000-4000-8000-000000000004', 'demo:analyst-riley', 'Riley Example Analyst')
ON CONFLICT (external_subject) DO NOTHING;
INSERT INTO healthcare.user_roles(user_id, role_id)
SELECT u.id, r.id FROM (VALUES
 ('demo:dr-ada', 'doctor'), ('demo:nurse-lee', 'nurse'),
 ('demo:billing-sam', 'billing'), ('demo:analyst-riley', 'analyst')
) AS x(subject, role_name)
JOIN healthcare.users u ON u.external_subject = x.subject
JOIN healthcare.roles r ON r.name = x.role_name
ON CONFLICT DO NOTHING;

INSERT INTO healthcare.doctors(id, user_id, synthetic_license_number, specialty) VALUES
 ('20000000-0000-4000-8000-000000000001', '10000000-0000-4000-8000-000000000001', 'SYN-LIC-ADA-001', 'Family Medicine'),
 ('20000000-0000-4000-8000-000000000002', NULL, 'SYN-LIC-JAM-002', 'Cardiology')
ON CONFLICT (synthetic_license_number) DO NOTHING;

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
       '{"common_entities":["appointments","patients"],"typical_rows":8,"synthetic_seed":true}'::jsonb, 'seeded'
FROM healthcare.users u WHERE u.external_subject = 'demo:dr-ada'
ON CONFLICT (actor_id, profile_version) DO NOTHING;
COMMIT;
