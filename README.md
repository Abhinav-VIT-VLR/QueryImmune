# QueryImmune

QueryImmune is a PostgreSQL-centered healthcare query defense prototype. It validates natural-language intent and untrusted generated SQL, applies deterministic authorization and workload risk checks, records decisions in PostgreSQL, and executes only fixed parameterized query templates after an ALLOW decision.

The project uses synthetic data only. It is a hackathon prototype, not a certified healthcare product.

## Run the local demo

Requirements: Docker Desktop with Compose and Python 3.11+.

```powershell
python scripts/create_env.py
docker compose --env-file .env -f infra/docker-compose.yml up --build
```

After the API becomes healthy, open <http://localhost:8000>. In another terminal, print a 15-minute synthetic doctor token and paste it into the UI:

```powershell
docker compose --env-file .env -f infra/docker-compose.yml exec api python -m queryimmune_db.api.dev_token
```

This local-only token command is enabled only by the Compose demo configuration. It does not expose a token-issuance HTTP endpoint. See [SETUP.md](SETUP.md), [DEMO.md](DEMO.md), and [SECURITY.md](SECURITY.md).

## Project docs

- [Setup and tests](SETUP.md)
- [Architecture and implemented boundaries](ARCHITECTURE.md)
- [Security model](SECURITY.md)
- [PostgreSQL schema and operations](DATABASE.md)
- [Evaluation method and generated results](EVALUATION.md)
- [Demo walkthrough](DEMO.md)
- [Engineering blueprint](docs/engineering-blueprint.md)

The current evaluation is a small development shadow corpus. It does not contact a model or database and is not a held-out accuracy study. Full-stack latency and live executor instrumentation remain unmeasured.
