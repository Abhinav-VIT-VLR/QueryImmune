-- JOIN: appointments with synthetic patient and doctor context.
SELECT a.scheduled_at, a.status, p.synthetic_patient_number,
       d.specialty, u.display_name AS doctor_name
FROM healthcare.appointments a
JOIN healthcare.patients p ON p.id = a.patient_id
JOIN healthcare.doctors d ON d.id = a.doctor_id
LEFT JOIN healthcare.users u ON u.id = d.user_id
WHERE a.scheduled_at >= now() - interval '30 days'
ORDER BY a.scheduled_at DESC LIMIT 100;

-- Aggregation: appointment counts by status and specialty.
SELECT a.status, d.specialty, count(*) AS appointment_count
FROM healthcare.appointments a
JOIN healthcare.doctors d ON d.id = a.doctor_id
GROUP BY a.status, d.specialty ORDER BY appointment_count DESC;

-- Filtering: only synthetic patient's non-restricted records.
SELECT id, record_type, sensitivity, recorded_at
FROM healthcare.medical_records
WHERE patient_id = %s AND sensitivity = 'standard'
ORDER BY recorded_at DESC LIMIT 25;

-- Update inside an explicit transaction; runtime role intentionally cannot perform this.
BEGIN;
UPDATE healthcare.appointments
SET status = 'cancelled', updated_at = now()
WHERE id = %s AND status = 'scheduled';
COMMIT;

-- Atomic transaction demonstrating rollback on failed invariant/operation.
BEGIN;
UPDATE healthcare.billing SET status = 'paid', updated_at = now()
WHERE id = %s AND status = 'pending';
-- Application checks row count before COMMIT; ROLLBACK if it is not exactly one.
COMMIT;

-- Audit: recent decision and risk reasons without raw SQL or result rows.
SELECT q.request_id, q.actor_id, q.created_at, q.decision, q.execution_status,
       r.risk_score, r.reason_codes, r.policy_version
FROM security.query_logs q
JOIN security.risk_events r USING (request_id)
WHERE q.created_at >= now() - interval '7 days'
ORDER BY q.created_at DESC LIMIT 200;
