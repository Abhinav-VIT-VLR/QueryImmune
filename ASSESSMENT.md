# QueryImmune DBMS Assessment and Demonstration Guide

This guide describes the implementation present in this repository and gives
a practical demonstration sequence. It distinguishes automated tests and
source checks from PostgreSQL behavior that has not been exercised against a
live database. QueryImmune uses synthetic healthcare data and is a security
prototype, not a production healthcare or compliance product.

## 1. Project introduction and objectives

QueryImmune is a PostgreSQL query-defense prototype for AI-assisted
applications. It accepts typed or natural-language requests, checks the
resulting intent and untrusted SQL candidate, applies action/role/scope and
workload-risk controls, records a decision, and executes only a fixed,
parameterized query template after an ALLOW decision.

The implementation demonstrates:

- PostgreSQL relational design, constraints, foreign keys, RLS, grants,
  routines, views, triggers, indexes, and transactions.
- SQLGlot-based PostgreSQL AST validation of a candidate query.
- Database-backed action/resource/role authorization.
- A restricted Psycopg runtime connection for the AI-query path.
- An isolated SQLAlchemy connection for administrator-only appointment CRUD.
- Audit records for query decisions and administrative appointment writes.

All supplied fixture records are synthetic. The model provider has no database
credential and the generated SQL candidate is not sent directly to PostgreSQL.

## 2. Problem statement

An AI-generated query can be syntactically valid and still request data outside
the caller's permitted role or scope, access sensitive relations, return too
many rows, or attempt a mutation. QueryImmune places a deterministic policy
and database privilege boundary between an untrusted candidate and execution.
The database schema also provides an inspectable place to demonstrate
authorization, auditing, transaction integrity, and administrative reporting.

## 3. System architecture

```mermaid
flowchart LR
    UI[Same-origin browser UI] --> API[FastAPI]
    API --> JWT[JWT verification]
    JWT --> DBPolicy[PostgreSQL actor and role lookup]
    API --> Intent[Intent/provider adapter]
    Intent --> Candidate[Untrusted SQL candidate]
    Candidate --> AST[SQLGlot AST and intent validation]
    AST --> Policy[Action, role, scope, limit, risk policy]
    Policy -->|BLOCK / REVIEW| Audit[Decision audit]
    Policy -->|ALLOW| Fixed[Fixed parameterized query repository]
    Fixed --> Runtime[(PostgreSQL runtime login\nread-only transactions + RLS)]
    Audit --> Runtime
    API -->|admin appointment routes| Authz[Database policy check]
    Authz --> ORM[SQLAlchemy admin repository]
    ORM --> AdminDB[(PostgreSQL ORM-admin login\nappointment-only DML + RLS)]
    AdminDB --> Trigger[Appointment audit trigger]
    Trigger --> AuditTable[(security.admin_audit_events)]
```

The browser and API are served from one FastAPI origin. The API authenticates a
bearer token, resolves its subject to an active database actor, and checks
database-backed policy rows. On the generated-query path, validation and hard
policy checks precede the behavior/risk pipeline. The executor chooses a
prewritten repository query by supported action; it does not execute the
model's SQL string.

The ORM path is separate and is limited to authorized administrative
appointment operations. It does not replace Psycopg or SQLGlot in the
security-critical execution pipeline.

See [ARCHITECTURE.md](ARCHITECTURE.md) for module boundaries and
[SECURITY.md](SECURITY.md) for the security model.

## 4. ER diagram, relational schema, and data dictionary

The complete Mermaid ER diagram, column-level data dictionary, key and
constraint inventory, relationship cardinalities, and index list are
maintained in [DATABASE.md](DATABASE.md). That reference is derived from the
SQL migrations, not solely from model or README descriptions.

The application base tables are:

```text
healthcare.users(
  id PK, external_subject UQ, display_name, status, created_at, updated_at
)
healthcare.roles(
  id PK, name UQ, description, created_at
)
healthcare.user_roles(
  user_id PK/FK -> healthcare.users.id,
  role_id PK/FK -> healthcare.roles.id,
  assigned_by FK -> healthcare.users.id, assigned_at
)
healthcare.patients(
  id PK, synthetic_patient_number UQ, given_name, family_name,
  date_of_birth, sex_at_birth, created_at, updated_at
)
healthcare.doctors(
  id PK, user_id nullable/UQ/FK -> healthcare.users.id,
  synthetic_license_number UQ, specialty, active, created_at
)
healthcare.appointments(
  id PK, patient_id FK -> healthcare.patients.id,
  doctor_id FK -> healthcare.doctors.id,
  scheduled_at, status, reason_category, created_at, updated_at
)
healthcare.medical_records(
  id PK, patient_id FK -> healthcare.patients.id,
  author_doctor_id FK -> healthcare.doctors.id,
  record_type, sensitivity, summary, recorded_at, created_at, updated_at
)
healthcare.billing(
  id PK, patient_id FK -> healthcare.patients.id,
  appointment_id nullable/FK -> healthcare.appointments.id,
  amount, currency, status, due_at, created_at, updated_at
)
security.query_logs(
  id PK, request_id UQ, actor_id FK -> healthcare.users.id,
  created_at, query_fingerprint, decision, execution_status, duration_ms, row_count
)
security.behavior_profiles(
  id PK, actor_id FK -> healthcare.users.id, profile_version,
  window_start, window_end, approved_sample_count, confidence,
  feature_summary, update_status, created_at, updated_at,
  UQ(actor_id, profile_version)
)
security.risk_events(
  id PK, request_id UQ/FK -> security.query_logs.request_id,
  actor_id FK -> healthcare.users.id, risk_score, outcome, reason_codes,
  feature_snapshot, policy_version, model_version, profile_version, created_at
)
security.action_role_policies(
  action PK, resource PK, role_id PK/FK -> healthcare.roles.id,
  enabled, created_at
)
security.admin_audit_events(
  id PK, request_id, actor_id FK -> healthcare.users.id,
  appointment_id, action, before_data, after_data, created_at
)
qi_migrations.applied(version PK, applied_at)
```

There are 13 application base tables. `qi_migrations.applied` is a migration
ledger, not application-domain data. `healthcare.general_doctor_directory`,
the two daily security summaries, and the two appointment reports are views.

Relationship highlights:

- Users and roles are many-to-many through `healthcare.user_roles`.
- A user may have zero or one linked doctor row (`doctors.user_id` is nullable
  and unique).
- Patients and doctors have one-to-many relationships with appointments.
- An appointment may have zero or many billing rows; each billing row may have
  a nullable appointment reference.
- A query log may have zero or one risk event; `risk_events.request_id` is a
  unique foreign key.
- The audit event's `appointment_id` intentionally is not a foreign key, so a
  delete event can retain the ID of the removed appointment.

## 5. Normalization

The core transactional tables follow conventional 3NF design: patient,
doctor, user, and role facts are stored separately; appointments reference
patient and doctor keys; user/role associations are represented in a junction
table; and billing and medical-record facts are separate relations. Unique,
primary-key, check, and foreign-key constraints enforce identity and
referential integrity.

Some intentional snapshots/denormalized structures serve different purposes:

- `security.admin_audit_events.before_data` and `after_data` retain row
  snapshots for historical auditability, including after deletion.
- `security.behavior_profiles.feature_summary` and risk snapshots store
  versioned analysis state as JSONB.
- Reporting views derive grouped values from transactional/security tables;
  they are not separately maintained summary tables.

`DATABASE.md` documents nullable references, declared FKs, and potential
referential-integrity improvement opportunities. No claim is made that the
schema is formally certified against every normal form.

## 6. SQL operations, joins, and aggregates

The executable SQL demonstration is
[`backend/sql/advanced_dbms_demo.sql`](backend/sql/advanced_dbms_demo.sql).
It contains `SELECT`, `INSERT`, `UPDATE`, `DELETE`, `WHERE`, `ORDER BY`,
`GROUP BY`, `HAVING`, scalar and correlated subqueries, and `INNER`, `LEFT`,
and `RIGHT JOIN` examples over QueryImmune's actual reporting objects.

Representative read-only examples:

```sql
SELECT activity_day::date, decision, execution_status, request_count,
       returned_row_count, average_duration_ms, minimum_duration_ms,
       maximum_duration_ms
FROM security.query_execution_daily_summary
WHERE activity_day >= now() - interval '30 days'
ORDER BY activity_day DESC, decision, execution_status;
```

```sql
SELECT specialty, count(*) AS doctors,
       sum(appointment_count) AS appointments,
       avg(appointment_count) AS avg_per_doctor,
       min(appointment_count) AS min_per_doctor,
       max(appointment_count) AS max_per_doctor
FROM healthcare.admin_appointment_report
GROUP BY specialty
HAVING sum(appointment_count) > 0
ORDER BY appointments DESC, specialty;
```

```sql
SELECT d.doctor_id, d.specialty, count(a.id) AS appointment_count
FROM healthcare.admin_appointment_detail a
RIGHT JOIN healthcare.admin_appointment_report d
  ON d.doctor_id = a.doctor_id
GROUP BY d.doctor_id, d.specialty
ORDER BY appointment_count DESC, d.specialty;
```

These reporting relations require a configured `qi_orm_admin` transaction
context; they are not exposed to the AI-query runtime login. Returned counts
and dates vary with the database contents. The SQL script's contents have
static test coverage, but it has not been executed in the current verification
environment.

## 7. Views

Migration 012 creates `healthcare.general_doctor_directory`, a
`security_invoker` view joining doctors and users. The underlying doctor RLS
continues to apply to the runtime role.

Migration 014 creates four administrative views:

- `security.query_execution_daily_summary`: request count, returned-row totals,
  and duration statistics by day, decision, and execution state.
- `security.risk_event_daily_summary`: event counts and risk-score statistics
  by day and outcome.
- `healthcare.admin_appointment_report`: appointment counts and time range by
  doctor/specialty; its left joins retain doctors without appointments.
- `healthcare.admin_appointment_detail`: appointment information joined to
  synthetic patient number and clinician name/specialty, without patient
  demographics.

The admin views use a security barrier and an active-administrator predicate;
they are granted to the separate ORM-admin login. View definitions and grants
are verified by source assertions; live query results remain unverified.

## 8. Stored functions and triggers

`security.admin_update_appointment_status(uuid, text)` is an invoker-privilege
PL/pgSQL function. It validates the appointment ID and supported status,
requires actor/request context and admin authorization, updates status and
`updated_at`, and signals invalid, unauthorized, or missing-record errors.
Execution is granted to the ORM-admin role only. It is not called by the
AI-query path.

`security.audit_appointment_admin_change()` is the trigger function for
`appointments_admin_audit`. On appointment insert, update, or delete, it
appends an event to `security.admin_audit_events`. The event and data change
share the same database transaction: rollback of the appointment change also
rolls back the corresponding event.

The migration and unit/source checks verify that these definitions and
privilege declarations exist. PostgreSQL execution of the function/trigger
has not been verified in this environment.

## 9. Transactions and indexes

`backend/sql/advanced_dbms_demo.sql` demonstrates `BEGIN`, `COMMIT`, full
`ROLLBACK`, and `SAVEPOINT` recovery from an invalid foreign key or status.
It also shows a valid update rolled back to a savepoint and a final full
rollback. Intentional SQL errors should be visible in `psql`; do not suppress
them. Do not run this script with `ON_ERROR_STOP=1`, or `psql` will exit at an
intentional constraint/function failure before its savepoint recovery.

The administrative SQLAlchemy `session_scope` commits a successful unit of
work and rolls it back on an exception. Its automated rollback test ran
against SQLite. PostgreSQL trigger/rollback integration tests were skipped.

Indexes support demonstrated lookups and reports, including appointment
patient/doctor timelines, appointment status/time, appointment date, security
audit actor/time, and billing date/status. Primary-key and unique constraints
also create backing indexes. Indexes can reduce scanning and sorting for
matching predicates and orderings, but add storage and write overhead; no
live `EXPLAIN ANALYZE` or performance benchmark was performed.

## 10. SQLAlchemy ORM and administrative CRUD

The SQLAlchemy 2.x mappings are in
[`backend/src/queryimmune_db/orm/models.py`](backend/src/queryimmune_db/orm/models.py);
engine and session scope are in
[`backend/src/queryimmune_db/orm/session.py`](backend/src/queryimmune_db/orm/session.py).
Mappings cover existing tables. They do not create or migrate the schema;
ordered SQL migrations remain authoritative.

Appointment endpoints:

| Operation | Route | Success |
|---|---|---|
| Create | `POST /api/v1/admin/appointments` | `201` |
| List/filter/page | `GET /api/v1/admin/appointments` | `200` |
| Retrieve | `GET /api/v1/admin/appointments/{id}` | `200` |
| Update | `PATCH /api/v1/admin/appointments/{id}` | `200` |
| Delete | `DELETE /api/v1/admin/appointments/{id}` | `204` |
| Audit list | `GET /api/v1/admin/appointments/audit` | `200` |

Requests validate UUIDs, enum status, non-empty reason, timezone-aware
timestamps, and patch fields. List/audit operations support pagination; list
filters include status, patient/doctor, and scheduled-time range. Missing
records return `404`, invalid references `422`, database constraints and
restricted deletes `409`, and authorization denial `403`. Administrative CRUD
uses the separate ORM-admin URL; the runtime role is not given appointment
DML grants.

## 11. Authentication, authorization, and database security

- The API verifies HS256 JWT signature, algorithm, issuer, audience, expiry,
  and optional time claims. Subjects resolve to active database actors.
- There is no public token issuance API. The demo-only command
  `queryimmune_db.api.dev_token` prints short-lived synthetic tokens only when
  the explicit local demo flag and demo provider are enabled.
- Role assignments and action/resource grants come from database tables.
  Unknown operations and missing/failed policy checks fail closed.
- The gateway accepts typed actions, not arbitrary SQL. SQLGlot validates a
  candidate AST and intent match; only the fixed repository template is
  executed after ALLOW.
- The query executor connects as the runtime login and runs in a read-only
  transaction. Runtime grants are narrower than owner/migration access;
  clinical DML, raw billing rows, and medical records are not available
  through that connection.
- Actor-specific row-level policies constrain applicable runtime and admin
  rows. Separate schema roles/credentials are used for bootstrap/migrations,
  runtime query execution, and appointment-administration ORM operations.
- Application responses avoid exposing database/provider exceptions. Database
  statement and lock timeouts are set on request transactions.

These are implemented controls; a successful live privilege/RLS regression
test was not obtained here because the required migrated database was absent.

## 12. Testing and verification results

Verification was run on **2026-10-14** using Python **3.12.5**:

```text
pytest -q -rs backend\tests
115 passed, 23 skipped, 0 failed
```

The 23 skipped cases require a migrated and seeded PostgreSQL database, a
runtime database URL, an ORM-admin URL, or a database-admin URL. The passing
tests include request validation, fake-service CRUD route behavior, ORM
mapping/relationship checks, SQLite session rollback, JWT and authorization
unit tests, SQL AST rejection cases, and static migration/seed content checks.

The following live checks were **not run**: migrations and seed application,
PostgreSQL CRUD, joins/report views, stored function and trigger execution,
transaction/savepoint behavior in PostgreSQL, RLS isolation, persisted
database audit rows, and database role grants.

The local FastAPI process was smoke-tested: the frontend root and static
JavaScript returned HTTP 200, and liveness returned `ok`. Database readiness
reported `unavailable`; this is not a successful database-backed deployment.
PostgreSQL 14 was listening locally, but the app database credentials were not
configured. Migration 012 uses the PostgreSQL `security_invoker` view option,
so use PostgreSQL 15 or newer; the supplied Compose service uses PostgreSQL
16. Docker was unavailable in this environment. One pre-existing
Starlette/httpx TestClient deprecation warning was emitted by the suite.

Evaluation output is described in [EVALUATION.md](EVALUATION.md); it is a
small deterministic development shadow corpus, not a live database/model
benchmark.

## 13. Deployment and local development

### Recommended demonstration path: Compose

Install Docker Desktop with Compose and a working virtualization backend.
Then, from the repository root:

```powershell
python scripts/create_env.py
docker version
docker compose version
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

Compose starts PostgreSQL 16, waits for its health check, applies migrations,
provisions separate logins, loads deterministic synthetic fixtures, then
starts the API. Keep the foreground output visible for the demonstration.
In another PowerShell terminal:

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Proceed only if the second response body says `status = ok`; an HTTP response
alone is not proof that PostgreSQL is ready. Open <http://localhost:8000>.
Compose publishes PostgreSQL on `127.0.0.1:5433` and the UI/API on
`127.0.0.1:8000`.

### Native Windows alternative

The backend can run under Uvicorn on Windows. PostgreSQL **15+** is required
for the checked-in migrations. The existing local PostgreSQL 14 service is not
the supported target; install a compatible server side-by-side (for example
on port 5434) or use the Compose database.

From the repository root, create local random secrets if `.env` does not
already exist, then install the package in the project virtual environment:

```powershell
python scripts/create_env.py
.\.venv\Scripts\python.exe -m pip install -e "backend[test]"
```

Create an empty `queryimmune` database on the PostgreSQL 15+ server using its
administrative account. Set `DATABASE_ADMIN_URL` in the current shell to that
database and allow the project bootstrap identity to create/alter roles and
schemas. Use the generated `DATABASE_RUNTIME_PASSWORD`,
`DATABASE_ORM_ADMIN_PASSWORD`, and `AUTH_JWT_HS256_SECRET` values from the
ignored local `.env`; do not put them in source control or presentation
screenshots. Set:

```text
DATABASE_ADMIN_URL=<trusted PostgreSQL administrator URL>
DATABASE_RUNTIME_PASSWORD=<generated runtime password>
DATABASE_ORM_ADMIN_PASSWORD=<generated ORM-admin password>
DATABASE_URL=<queryimmune_runtime login URL to queryimmune>
DATABASE_ORM_ADMIN_URL=<queryimmune_orm_admin login URL to queryimmune>
AUTH_JWT_HS256_SECRET=<generated local secret>
AUTH_JWT_ISSUER=queryimmune-auth
AUTH_JWT_AUDIENCE=queryimmune-api
AI_PROVIDER=demo
QUERYIMMUNE_DEMO_AUTH=1
```

The URLs must use the host/port of the PostgreSQL 15+ database. Since the
generated passwords are hexadecimal, they are URL-safe; if you use other
passwords, URL-encode special characters. Run migration, role provisioning,
and fixture loading from `backend`:

```powershell
Set-Location backend
..\.venv\Scripts\python.exe -m queryimmune_db.migrate setup
```
Then start the API:

```powershell
..\.venv\Scripts\python.exe -m uvicorn queryimmune_db.main:app --reload --host 127.0.0.1 --port 8000
```

The migration runner reads environment variables and the local dotenv file;
`DATABASE_ADMIN_URL` must identify the prepared database. Never use the
runtime or ORM-admin login as the bootstrap/migration identity.

## 14. Faculty demonstration sequence

Use the Compose path above after the database and API readiness checks pass.
The commands below are intended for a PowerShell window at the repository
root. A native run uses the same HTTP commands after starting its API.

### A. Authenticate as admin and show CRUD plus trigger audit

**Execute:**

```powershell
$token = (docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m queryimmune_db.api.dev_token --subject demo:admin).Trim()
$headers = @{ Authorization = "Bearer $token" }
$body = @{
  patient_id = "30000000-0000-4000-8000-000000000001"
  doctor_id = "20000000-0000-4000-8000-000000000001"
  scheduled_at = "2027-08-20T15:00:00Z"
  status = "scheduled"
  reason_category = "assessment"
} | ConvertTo-Json

$appointment = Invoke-RestMethod -Method Post `
  -Uri "http://localhost:8000/api/v1/admin/appointments" `
  -Headers $headers -ContentType "application/json" -Body $body
$appointment
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/appointments/$($appointment.id)" -Headers $headers
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/appointments?status=scheduled&limit=10&offset=0" -Headers $headers

$patch = @{ status = "completed" } | ConvertTo-Json
Invoke-RestMethod -Method Patch `
  -Uri "http://localhost:8000/api/v1/admin/appointments/$($appointment.id)" `
  -Headers $headers -ContentType "application/json" -Body $patch
Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/appointments/audit?appointment_id=$($appointment.id)" -Headers $headers
Invoke-RestMethod -Method Delete `
  -Uri "http://localhost:8000/api/v1/admin/appointments/$($appointment.id)" -Headers $headers
```

**Expected:** create returns a new appointment ID; get/list return that
appointment; patch shows `completed`; audit contains create/update (and after
delete, delete) actions. The delete endpoint returns no body. Exact IDs and
timestamps vary.

**Concept / explanation:** demonstrate CRUD, request validation, an ORM
repository, database transaction boundaries, and an AFTER trigger recording
before/after data atomically with each write. This is the intended database
behavior; it requires a live migrated database and was not verified in this
environment.

### B. Show role denial

**Execute:**

```powershell
$doctorToken = (docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m queryimmune_db.api.dev_token --subject demo:dr-ada).Trim()
$doctorHeaders = @{ Authorization = "Bearer $doctorToken" }
try {
  Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/appointments" -Headers $doctorHeaders
} catch {
  $_.Exception.Response.StatusCode
}
```

**Expected:** HTTP `403 Forbidden`. A request with no/invalid bearer token
returns `401`.

**Concept / explanation:** authentication resolves the signed subject to a
database actor; a valid doctor identity still lacks the database policy grant
for `appointments.admin_crud`. Do not treat a client-provided role as
authority.

### C. Demonstrate views, joins, grouping, and aggregates

**Execute:** after Compose is ready, construct a `psql`-compatible local URL
from the generated ORM-admin password without printing or sharing it, then
run the SQL file:

```powershell
$ormAdminPassword = ((Get-Content .env | Where-Object { $_ -match '^DATABASE_ORM_ADMIN_PASSWORD=' } | Select-Object -First 1) -replace '^DATABASE_ORM_ADMIN_PASSWORD=', '')
$env:DATABASE_ORM_ADMIN_URL = "postgresql://queryimmune_orm_admin:$ormAdminPassword@127.0.0.1:5433/queryimmune"
psql $env:DATABASE_ORM_ADMIN_URL -f backend/sql/advanced_dbms_demo.sql
```

The host port is `5433` for Compose; use the actual port for a native server.
If `psql` is not on PATH, invoke the executable in your PostgreSQL
installation's `bin` directory. The `postgresql://` URL is for `psql`; the
SQLAlchemy application URL uses its configured `postgresql+psycopg://`
driver form. Never paste either URL or its password into slides or commit it.

**Expected:** report rows depend on data. The script runs daily summaries,
appointment reporting queries, inner/left/right joins, and correlated and
scalar subqueries. It computes `COUNT`, `SUM`, `AVG`, `MIN`, and `MAX`, with
`WHERE`, `GROUP BY`, `HAVING`, and `ORDER BY`.

**Concept / explanation:** the reports are useful QueryImmune administrative
queries, not unrelated sample tables. Explain that `LEFT`/`RIGHT` joins keep
doctors with no appointments, while aggregates summarize activity without
returning raw query/risk rows.

### D. Demonstrate the stored function

The SQL demonstration file invokes
`security.admin_update_appointment_status(uuid, text)` after setting the
synthetic administrator and request context. Its invalid `pending` status is
intentionally rejected and recovered to a savepoint; the supported
`completed` value succeeds.

**Expected:** invalid status reports a database input error; valid status
returns `completed`. The file later deletes its temporary appointment and
commits its transaction.

**Concept / explanation:** this PL/pgSQL function validates input, checks
trusted admin context, updates a real appointment, and reports missing or
unauthorized calls. It is granted only to the ORM-admin role and is not an
AI-query execution primitive.

### E. Demonstrate trigger and transaction behavior

The same SQL file inserts an appointment, deliberately attempts an invalid
foreign-key insert under a savepoint, rolls back to that savepoint, performs
and rolls back a valid update, runs the status function, deletes the temporary
row, and queries its audit events before commit. It then updates a seeded
appointment in a separate transaction and fully rolls back.

**Expected:** `psql` displays the deliberate FK/status errors; later commands
continue because the script rolls back to savepoints. Committed create/update/
delete audit rows match the committed writes. The final rolled-back status
change and its trigger event do not persist.

**Concept / explanation:** PostgreSQL transactions make the row mutation and
trigger-generated audit record atomic. A savepoint permits recovery without
discarding the outer transaction; full rollback reverses all changes since
`BEGIN`.

**Important:** do not pass `ON_ERROR_STOP=1` to `psql` for this demonstration:
the script deliberately produces recoverable SQL errors.

### F. Demonstrate QueryImmune's SQL security boundary

In the browser, authenticate using the synthetic doctor token and submit the
demo scenarios from the UI or
[`DEMO.md`](DEMO.md):

- Normal: `How many patients visited cardiology today?`
- Sensitive: `Show the medical history of every patient.`
- Destructive: `Delete all patient records.`
- Intent mismatch: use the fixture's `Show my appointments.` scenario.

The repeatable test fixture `backend/tests/test_demo_scenarios.py` verifies
that normal intent/candidate SQL passes the validator and that sensitive,
destructive, and mismatch candidates fail it. If the migrated database and
live seeded identities are present, the API integration test expects normal
to ALLOW and the others to BLOCK with `execution_status = not_run`; that
database integration test was skipped in the current environment.

**Expected in the configured demo:** an allowed request returns results from a
fixed query; blocked requests show their reason and `not_run` execution
status. The generated SQL candidate is shown for explanation but is not the
statement passed to Psycopg.

**Concept / explanation:** SQLGlot parses and checks one candidate statement
against intent and allowed structure. Database-backed role/scope policy and
the read-only runtime account add independent controls. The candidate is
untrusted input, not executable authority.

## 15. Limitations and future enhancements

- PostgreSQL integration tests and the full DB-backed demonstration remain to
  be run on a fresh migrated/seeded PostgreSQL 15+ database.
- The current Windows PostgreSQL 14 service is below the migration
  compatibility floor; the Compose definition uses PostgreSQL 16.
- Docker Desktop is not available in the reviewed environment. Native Uvicorn
  starts, but without database credentials database readiness is unavailable.
- Readiness returns HTTP 503 when either runtime or ORM-admin database
  connectivity is unavailable. Compose's health check also validates the JSON
  body; verify readiness reports `ok` before claiming a live deployment.
- Evaluation uses a small development shadow corpus; it is not a held-out
  accuracy study, production benchmark, or full-stack latency measurement.
- There is no public token issuer, full OIDC integration, reviewer workflow,
  rate limiting, production secrets manager integration, or compliance
  certification.
- The admin SQL script's results, execution plans, and runtime performance
  have not been captured against a live database in this environment.

Prioritize a PostgreSQL 16 Compose startup (once Docker Desktop works), run
all database integration tests without skips, capture actual query/function/
trigger outputs, then improve readiness health semantics before presenting
the deployment as complete.

## Related references

- [README](README.md): project overview and entry points.
- [ARCHITECTURE](ARCHITECTURE.md): module design and trust boundaries.
- [DATABASE](DATABASE.md): complete schema, ERD, and data dictionary.
- [SECURITY](SECURITY.md): threat controls and invariants.
- [SETUP](SETUP.md): setup commands and API examples.
- [DEMO](DEMO.md): UI walkthrough and scenario prompts.
- [EVALUATION](EVALUATION.md): development corpus and evaluation limits.
