# Backend package

The supported fresh local setup is documented in the repository root [SETUP.md](../SETUP.md); Compose now builds the API, PostgreSQL, and one-shot migration/seed job. Do not follow old manual Compose instructions.

For native Python development, install with `python -m pip install -e ".[test]"` from this directory, configure `DATABASE_ADMIN_URL`, `DATABASE_RUNTIME_PASSWORD`, and `DATABASE_URL` using `backend/.env.example`, run `python -m queryimmune_db.migrate setup`, then start `uvicorn queryimmune_db.main:app --reload`. The API requires a trusted JWT issuer configuration for authenticated requests.

Run tests with `python -m pytest -q -rs`. Database integration tests require a migrated, seeded PostgreSQL instance and a restricted runtime `DATABASE_URL`.
