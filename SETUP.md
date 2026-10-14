# Setup

## Docker Compose demo

Install Docker Desktop with Compose and Python 3.11 or newer. From the repository root, run:

```powershell
python scripts/create_env.py
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

The environment generator makes independent random local secrets and refuses to overwrite an existing `.env`. Compose starts PostgreSQL, waits for health, applies ordered migrations, provisions separate restricted runtime and appointment-admin logins, seeds synthetic fixtures, then starts the API. The default provider is deterministic `demo`; no remote model key is needed.

If an older `.env` already exists, the generator deliberately leaves it
untouched. Add a fresh random `DATABASE_ORM_ADMIN_PASSWORD` value before
restarting Compose; do not reuse either the bootstrap or runtime password.

Open <http://localhost:8000>. To authenticate the local demo, run in a second terminal and paste the printed short-lived token into the UI:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m queryimmune_db.api.dev_token
```

The local database is persisted in a named Docker volume. To rebuild an empty local database, stop the stack and delete that volume:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml down -v
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

`down -v` deletes all local QueryImmune database contents. The schema and seed are synthetic and can be recreated.

Check both liveness and readiness before demonstrating:

```powershell
Invoke-RestMethod http://localhost:8000/health/live
Invoke-RestMethod http://localhost:8000/health/ready
```

Liveness returns `{"status":"ok"}` while the API process is responsive.
Readiness returns HTTP 200 with `{"status":"ok"}` only when both the restricted
runtime database connection and the administrative ORM database connection
can execute a health query. If either is unavailable, readiness returns HTTP
503 with `{"status":"unavailable"}` and no connection details. Compose checks
both the HTTP response and JSON status.

## Demonstrate appointment CRUD

Get a short-lived synthetic administrator token (available only in the explicit local demo configuration):

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m queryimmune_db.api.dev_token --subject demo:admin
```

Paste the printed token into `$token` in PowerShell. These sample UUIDs are synthetic seed records:

```powershell
$token = "<paste-token>"
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

$update = @{ status = "completed" } | ConvertTo-Json
Invoke-RestMethod -Method Patch `
  -Uri "http://localhost:8000/api/v1/admin/appointments/$($appointment.id)" `
  -Headers $headers -ContentType "application/json" -Body $update

Invoke-RestMethod -Uri "http://localhost:8000/api/v1/admin/appointments/audit?limit=10" -Headers $headers
Invoke-RestMethod -Method Delete -Uri "http://localhost:8000/api/v1/admin/appointments/$($appointment.id)" -Headers $headers
```

The API supports safe filters `status`, `patient_id`, `doctor_id`,
`scheduled_from`, and `scheduled_to`. A non-admin gets HTTP 403. Invalid
references are rejected and deleting an appointment referenced by billing
returns HTTP 409; deletes do not cascade. Appointment writes and their audit
events commit or roll back together.

## Demonstrate SQL and transaction concepts

With PostgreSQL running, set `DATABASE_ORM_ADMIN_URL` in the current PowerShell
environment to the generated local admin URL, then run the advanced SQL script
from the repository root:

```powershell
psql $env:DATABASE_ORM_ADMIN_URL -f backend/sql/advanced_dbms_demo.sql
```

It reports execution/risk/appointment summaries, demonstrates inner/left/right
joins and correlated subqueries, performs an audited appointment
insert/update/delete, recovers from invalid operations with savepoints, and
rolls back an entire transaction. The script intentionally prints database
errors for its failure demonstrations; those statements are followed by
savepoint recovery. See [DATABASE.md](DATABASE.md) for query/result examples
and the view, routine, trigger, transaction, and index explanations.

## Python tests

Install the package and test dependencies:

```powershell
python -m pip install -e ".[test]"
Set-Location backend
python -m pytest -q -rs
python evaluation/run_evaluation.py
```

The PostgreSQL integration/end-to-end tests require a running migrated and seeded database plus `DATABASE_URL` set to the host-published `queryimmune_runtime` connection. For example, set it to `postgresql://queryimmune_runtime:<DATABASE_RUNTIME_PASSWORD>@localhost:5432/queryimmune`, using the generated value from the ignored root `.env`. Without it, database tests skip explicitly.

The full live demo test posts normal, sensitive, destructive, mismatch, and drift cases through FastAPI, SQL validation, risk policy, the PostgreSQL executor, and audit persistence.

## Remote provider (optional)

For a non-demo local run, set `AI_PROVIDER=openai-compatible`, `AI_BASE_URL`, `AI_API_KEY`, and `AI_MODEL` in `.env`, then recreate the API container. The provider must use HTTPS except for loopback development endpoints. Never put patient data or database credentials in prompts. Remote-provider end-to-end behavior needs a configured provider and is not part of the deterministic test suite.

## Native Windows PostgreSQL 16 development

Use PostgreSQL 15 or newer: migration 012 creates a `security_invoker` view.
The Compose database is PostgreSQL 16. If PostgreSQL 14 is already installed
on port 5432, install PostgreSQL 16 side-by-side on port 5433; do not point
these migrations at the PostgreSQL 14 service. The commands below assume the
standard PostgreSQL 16 install directory and an installer-created
`postgres` superuser.

From the repository root, generate the local application secrets if `.env`
does not exist. The generator refuses to overwrite an existing file:

```powershell
python scripts/create_env.py
```

Load its values into this PowerShell process without printing them:

```powershell
foreach ($line in Get-Content .env) {
  if ($line -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') {
    Set-Item -Path "Env:$($matches[1])" -Value $matches[2]
  }
}
```

`create_env.py` generates the Compose bootstrap password, runtime password,
ORM-admin password, and JWT secret. Native setup uses the generated
runtime/admin/JWT values; the generated `POSTGRES_PASSWORD` is for the
Compose-only bootstrap database. If `.env` already exists, confirm it contains
non-placeholder values for `DATABASE_RUNTIME_PASSWORD`,
`DATABASE_ORM_ADMIN_PASSWORD`, and `AUTH_JWT_HS256_SECRET`; the generator will
not update an existing file. Do not display or commit `.env`.

Verify the PostgreSQL 16 server and binaries (adjust the installation path if
you selected a custom directory):

```powershell
$pgBin = "$env:ProgramFiles\PostgreSQL\16\bin"
Get-Service postgresql-x64-16
& "$pgBin\psql.exe" --version
& "$pgBin\pg_isready.exe" -h 127.0.0.1 -p 5433
```

If the service is stopped, start it from an elevated PowerShell:

```powershell
Start-Service postgresql-x64-16
```

Create an empty database owned by the installer-created PostgreSQL
administrator. This command prompts for the PostgreSQL administrator
password; it does not put that password in the command line:

```powershell
& "$pgBin\psql.exe" -h 127.0.0.1 -p 5433 -U postgres -d postgres
```

At the `postgres=#` prompt, check whether the target database already exists:

```sql
SELECT current_setting('server_version_num')::integer / 10000 AS major_version;
SELECT datname FROM pg_database WHERE datname = 'queryimmune';
```

Confirm `major_version` is `16`. Only if the database query returns no rows,
create it:

```sql
CREATE DATABASE queryimmune OWNER postgres;
\q
```

Do not drop or overwrite an existing database as a setup shortcut. If the
database already exists, inspect it and take a backup before any reset.

Install the backend and test dependencies in the selected Python 3.12
environment:

```powershell
Set-Location backend
python -m pip install -e ".[test]"
```

Use the PostgreSQL administrator only for provisioning and migrations. Prompt
for its password into this shell's process environment, construct separate
application URLs from the generated values, then run setup. The URL contains
no password; the prompt and environment assignments do not print it:

```powershell
$pgCredential = Get-Credential -UserName postgres -Message "PostgreSQL 16 local administrator password"
$passwordPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($pgCredential.Password)
try {
  $env:PGPASSWORD = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($passwordPointer)
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($passwordPointer)
}

$env:DATABASE_ADMIN_URL = "postgresql://postgres@127.0.0.1:5433/queryimmune"
$env:DATABASE_URL = "postgresql://queryimmune_runtime:$env:DATABASE_RUNTIME_PASSWORD@127.0.0.1:5433/queryimmune"
$env:DATABASE_ORM_ADMIN_URL = "postgresql+psycopg://queryimmune_orm_admin:$env:DATABASE_ORM_ADMIN_PASSWORD@127.0.0.1:5433/queryimmune"
$env:AI_PROVIDER = "demo"
$env:QUERYIMMUNE_DEMO_AUTH = "1"
python -m queryimmune_db.migrate setup
```

The local bootstrap connection must be a PostgreSQL superuser, or a custom
account with database schema-creation permission, authority to create/alter
and grant the QueryImmune roles, and rights to assume `qi_owner`. Using the
installer `postgres` account is the reliable local-development option.
`migrate setup` applies sorted SQL migrations, provisions distinct runtime
and ORM-admin logins from their respective generated passwords, then loads
the synthetic seed. It does not run the API as the bootstrap identity.

Verify the applied migration ledger and seeded fixtures without printing
credentials:

```powershell
& "$pgBin\psql.exe" $env:DATABASE_ADMIN_URL -X -c "SELECT version FROM qi_migrations.applied ORDER BY version;"
& "$pgBin\psql.exe" $env:DATABASE_ADMIN_URL -X -c "SELECT count(*) AS users FROM healthcare.users; SELECT count(*) AS patients FROM healthcare.patients; SELECT count(*) AS appointments FROM healthcare.appointments;"
```

Remove the bootstrap connection and password from the process before starting
the API, so the long-running application does not inherit them:

```powershell
Remove-Item Env:DATABASE_ADMIN_URL
Remove-Item Env:PGPASSWORD
Remove-Item Env:DATABASE_RUNTIME_PASSWORD, Env:DATABASE_ORM_ADMIN_PASSWORD, Env:POSTGRES_PASSWORD
$pgCredential.Password.Dispose()
$pgCredential = $null
```

Start the API in this shell with the runtime and ORM-admin URLs configured
above:

```powershell
python -m uvicorn queryimmune_db.main:app --reload --host 127.0.0.1 --port 8000
```

In another PowerShell window, verify the process and both database
dependencies:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health/live
Invoke-RestMethod http://127.0.0.1:8000/health/ready
```

Readiness must return `{"status":"ok"}`. A PostgreSQL server accepting TCP
connections alone is not sufficient; migration, runtime-login, and
ORM-admin-login connections must all work. The API validates JWTs; production
token issuance remains the responsibility of a trusted issuer.
