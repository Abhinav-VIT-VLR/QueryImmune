-- Normalize the original synthetic doctor fixture so seeded behavior is interpretable by Phase 4.
UPDATE security.behavior_profiles bp
SET feature_summary = '{"actions":["appointments.mine","appointments.cardiology_today"],"tables":["appointments","patients","doctors"],"columns":["scheduled_at","status","reason_category","synthetic_patient_number","specialty","patient_id"],"operations":["read"],"scopes":["own_records","aggregate"],"sensitivities":["standard"],"roles":["doctor"],"signatures":["appointments.mine:appointments,doctors,patients:reason_category,scheduled_at,specialty,status,synthetic_patient_number::own_records:limit-standard","appointments.cardiology_today:appointments,doctors:patient_id:count:aggregate:limit-small"],"query_structure":["appointments.mine:appointments,doctors,patients:reason_category,scheduled_at,specialty,status,synthetic_patient_number::own_records:limit-standard"],"active_hours":["0","1","2","3","4","5","6","7","8","9","10","11","12","13","14","15","16","17","18","19","20","21","22","23"],"result_size_max":8,"frequency_sample_count":12}'::jsonb,
    updated_at = now()
FROM healthcare.users u
WHERE bp.actor_id = u.id AND u.external_subject = 'demo:dr-ada'
  AND bp.update_status = 'seeded' AND bp.feature_summary ? 'common_entities';
