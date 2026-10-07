# Database

PostgreSQL is the system of record. Migrations are applied in filename order from `backend/migrations/`; fixtures are deterministic and synthetic in `backend/seed/001_synthetic_healthcare.sql`.

## Schemas and entities

`healthcare`: `users`, `roles`, `user_roles`, `patients`, `doctors`, `appointments`, `medical_records`, and `billing`.

`security`: `query_logs`, `risk_events`, and versioned `behavior_profiles`.

Primary/foreign keys, unique identifiers, enumerated-state checks, nonnegative amounts/counts, positive profile versions, JSON object checks, and time-window constraints are in migration 001. Important indexes cover user-role lookup, patient names, patient/doctor appointment timelines, medical record timelines, billing patient/date, status/due date and aggregate date filtering, query logs by actor/date and decision/date, risk events by actor/outcome/date, and behavior profiles by actor/version. The medical-record index exists for the modeled relation, but the runtime role does not receive SELECT on that table.

## Roles and transactions

- `queryimmune_bootstrap`: local PostgreSQL bootstrap superuser used only by the migration/seed job.
- `qi_owner`: NOLOGIN schema/table/function owner.
- `qi_runtime`: NOLOGIN runtime privilege group.
- `queryimmune_runtime`: restricted LOGIN member of `qi_runtime` provisioned using the generated runtime password. Migration 008 replaces broad healthcare table grants with column-level access; names/date of birth and raw billing rows are not accessible to this role.

Only ALLOW reaches execution. The repository selects a fixed SQL template and binds values. Billing totals use the `security.billing_summary(date, limit)` SECURITY DEFINER function, which returns only grouped totals and validates its row bound; runtime cannot select individual billing rows. Executor transactions are read-only, use fixed search path, five-second statement timeout, two-second lock timeout, and rollback on errors. Decision audit is persisted before query execution; successful/failed completion is recorded separately. `query_logs` stores a fingerprint and row metadata, not result rows.

`append_behavior_profile` enforces sample eligibility, optimistic profile version, category provenance, window bounds, derived confidence, and audited row-size bounds. Migration 007 removes result-size statistics that cannot be supported by recent eligible audit history before installing the hardened function.

## SQL examples

Representative JOIN, aggregation, filtering, update, transaction, and audit queries are in [backend/sql/representative_queries.sql](backend/sql/representative_queries.sql). The update/transaction examples are for database-system demonstration; the runtime query gateway does not expose write execution.

## Local verification

Use the Compose setup in [SETUP.md](SETUP.md). With `DATABASE_URL` set to the host-side runtime connection, the database tests verify seed counts, index presence, read-only transactions, and direct mutation/medical-record privilege denial. They were skipped in the final review because PostgreSQL/Docker was unavailable.
