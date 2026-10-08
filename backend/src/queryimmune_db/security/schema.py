"""Security-owned metadata for the separate general appointments SELECT path.

Keep this allowlist aligned with PostgreSQL column grants and RLS policies.
It is shared with the model context, but the model never grants access.
"""

GENERAL_QUERY_RELATIONS = {
    "appointments": {
        "columns": {"patient_id", "doctor_id", "scheduled_at", "status", "reason_category"},
        "sensitivity": "standard",
    },
    "doctors": {"columns": {"id", "user_id", "specialty"}, "sensitivity": "standard"},
    "patients": {"columns": {"id", "synthetic_patient_number"}, "sensitivity": "standard"},
    "general_doctor_directory": {
        "columns": {"doctor_id", "user_id", "display_name", "specialty"},
        "sensitivity": "standard",
    },
}

GENERAL_QUERY_RELATIONSHIPS = (
    ("appointments.doctor_id", "doctors.id"),
    ("appointments.patient_id", "patients.id"),
    ("appointments.doctor_id", "general_doctor_directory.doctor_id"),
)

GENERAL_QUERY_ACTIONS = {
    "own": "appointments.general_own",
    "aggregate": "appointments.general_aggregate",
}
