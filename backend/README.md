# QueryImmune database foundation

Requires PostgreSQL 15+ and Python 3.11+. Install with `python -m pip install -e ".[test]"` from `backend/`. Copy `.env.example` to `.env`, replace every placeholder with locally managed credentials, and do not commit `.env`. A local PostgreSQL 18 container is defined in `infra/docker-compose.yml` and reads `backend/.env`.

`DATABASE_ADMIN_URL` is for a trusted local bootstrap/migration operator with permission to create roles and schemas. `DATABASE_URL` is the restricted runtime login. The setup command applies ordered migrations, provisions the runtime login with `DATABASE_RUNTIME_PASSWORD`, then loads synthetic fixtures:

```powershell
docker compose -f ../infra/docker-compose.yml up -d postgres
$env:DATABASE_ADMIN_URL = "postgresql://..."
$env:DATABASE_RUNTIME_PASSWORD = "..."
python -m queryimmune_db.migrate setup
$env:DATABASE_URL = "postgresql://queryimmune_runtime:..."
pytest
```

Migrations are in `migrations/`, deterministic fixtures in `seed/`, and representative PostgreSQL queries in `sql/representative_queries.sql`. Runtime transactions use local statement/lock timeouts and a fixed search path. `transaction(read_only=True)` provides an explicit read-only transaction. The runtime role can read healthcare entities and audit history and append query/risk events through narrow database functions; it cannot directly mutate clinical/audit tables, use DDL, or modify profiles. Migration and bootstrap credentials must not be used by the application.

## API

Start the service from this directory with `uvicorn queryimmune_db.main:app --reload`. OpenAPI is served at `/docs`. `POST /api/v1/requests` accepts a typed action (`appointments.mine`, `appointments.by_status`, or `billing.summary`), bounded result limit, and optional date filter; it never accepts SQL. Authentication verifies HS256 bearer tokens against the configured issuer/audience and resolves subject/roles from PostgreSQL. This API verifies tokens only; it does not issue them. Configure the signing secret from the trusted identity provider and never expose the database URL to a model or client.

The Phase 2 policy is deterministic and limited to these read-only query templates. It audits the ALLOW/REVIEW/BLOCK decision before execution. Only ALLOW executes, in a read-only bounded PostgreSQL transaction; completion is recorded through the restricted audit function. Unsupported and role-incompatible actions are audited and blocked.
