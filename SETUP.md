# Setup

## Docker Compose demo

Install Docker Desktop with Compose and Python 3.11 or newer. From the repository root, run:

```powershell
python scripts/create_env.py
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

The environment generator makes independent random local secrets and refuses to overwrite an existing `.env`. Compose starts PostgreSQL, waits for health, applies ordered migrations, provisions the restricted runtime login, seeds synthetic fixtures, then starts the API. The default provider is deterministic `demo`; no remote model key is needed.

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

## Native API development

With PostgreSQL migrated and seeded, install from `backend/`, set `DATABASE_URL`, `AUTH_JWT_HS256_SECRET`, issuer, and audience, then run `uvicorn queryimmune_db.main:app --reload`. The API validates JWTs; a trusted issuer is responsible for issuing production tokens.
