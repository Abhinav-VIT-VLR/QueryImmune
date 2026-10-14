# Demo walkthrough

## Start and authenticate

Follow [SETUP.md](SETUP.md), then open <http://localhost:8000>. Generate the short-lived local synthetic-doctor token from another terminal:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m queryimmune_db.api.dev_token
```

Paste it into the token field. This login is for local demo only.

For the admin CRUD demonstration, issue a token for the synthetic admin:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m queryimmune_db.api.dev_token --subject demo:admin
```

Use the printed token as `Authorization: Bearer <token>` for the authenticated
appointment endpoints documented in [SETUP.md](SETUP.md). The admin token is
only issued by this CLI in the explicitly enabled local demo container.

Demonstrate the database-backed authorization boundary with these requests:

```powershell
$adminToken = docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m queryimmune_db.api.dev_token --subject demo:admin
curl.exe -H "Authorization: Bearer $adminToken" http://localhost:8000/api/v1/admin/appointments

$doctorToken = docker compose --env-file .env -f infra/docker-compose.yml exec -T api python -m queryimmune_db.api.dev_token --subject demo:dr-ada
curl.exe -i -H "Authorization: Bearer $doctorToken" http://localhost:8000/api/v1/admin/appointments
```

The administrator request is authorized by the `appointments.admin_crud`
database policy. The doctor token is validly authenticated but receives `403`
because the doctor role is not granted that administrative action. Requests
without a bearer token or with an invalid/expired token receive `401`. Token
generation remains a local opt-in CLI operation; the API exposes no token
issuance endpoint.

## Scenarios

Select a preset or enter the matching request:

1. **Normal:** “How many patients visited cardiology today?” Uses the fixed aggregate query and should ALLOW under current policy.
2. **Sensitive:** “Show the medical history of every patient.” Restricted table/scope checks should BLOCK before execution.
3. **Destructive:** “Delete all patient records.” The read-only action and SQL checks should BLOCK; no mutation template exists.
4. **Intent mismatch:** safe appointment request with an invoice query candidate should BLOCK on semantic/entity mismatch.
5. **Workload drift:** “Show my first 10 appointments.” Repeat the legitimate fixture after warm-up and inspect decision, behavior profile, workload history, and persisted audit state. Drift may require REVIEW until the guarded baseline update criteria are met.

The results, decision explanation, history, profile, and workload chart come from the API and PostgreSQL. The normal demo is not a mocked database result path. Synthetic fixture generation is explicit, and all candidate SQL goes through the production validator/policy gateway before the fixed executor.

## API and health

- API docs: <http://localhost:8000/docs>
- Liveness: <http://localhost:8000/health/live>
- Database readiness: <http://localhost:8000/health/ready>
- Dashboard API: `/api/v1/dashboard` (authenticated, actor-scoped)
- Appointment CRUD: `/api/v1/admin/appointments` (database-authorized admin role, ORM-only, audited)
- Appointment audit: `/api/v1/admin/appointments/audit` (paginated, admin-only)
