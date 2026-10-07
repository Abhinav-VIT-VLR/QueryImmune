# Architecture

## Request path

```text
Browser → FastAPI/authentication → intent extraction → SQL candidate generation
       → PostgreSQL AST validation and intent comparison → deterministic policy
       → behavior/risk assessment → durable decision audit → fixed SQL executor → PostgreSQL
```

The API is a modular monolith. PostgreSQL stores synthetic healthcare entities, identities and role assignments, audit events, risk events, and versioned behavior profiles. `healthcare` and `security` schemas separate domain data from security history.

## Modules

- `api`: typed requests/responses, authentication adapter, request IDs, generic error handling, health and OpenAPI routes.
- `ai`: provider contract, intent models, schema metadata retrieval, identifier redaction, deterministic fixtures, and an optional OpenAI-compatible provider.
- `security/sql_validation.py`: SQLGlot PostgreSQL AST parser and allowlist/template/intent checks. The LLM never connects to PostgreSQL.
- `application/nl2sql.py`: links typed intent, AST validation, deterministic action policy, and behavior pipeline.
- `services/policy.py` and `behavior.py`: hard role/action/scope/limit gates, learned workload analysis, drift, explainable risk, and restricted profile update eligibility.
- `services/execution.py` and `repositories/healthcare.py`: persist decision first, then run a fixed parameterized query only for ALLOW in a read-only transaction, then finalize audit state.
- `migrations/` and `seed/`: ordered schema/security changes and repeatable synthetic fixtures.
- `evaluation/`: fixed development corpus, four shadow comparators, raw outcomes and generated plots.

Generated SQL is evidence for the decision and is not passed to the DB driver. The executor chooses a prewritten query by an allowlisted action and supplies server-derived actor identity and validated parameters. REVIEW and BLOCK candidates are never sent to the executor.

## Trust and persistence boundaries

Authentication roles are resolved from database mappings after verifying a signed bearer token. Request bodies cannot supply roles or SQL. Runtime database credentials are separate from bootstrap credentials. The runtime group can read only the granted healthcare relations and security history, and append audit records only through narrow functions; medical records are not granted to the runtime role.

The API’s decision audit is committed before execution. Execution and audit finalization use separate transactions so query failure does not erase the initial decision. Transactions set a fixed search path, five-second statement timeout, two-second lock timeout, and read-only mode for execution. Migration 008 limits runtime column access and exposes billing only through a bounded aggregate function.

## Implemented scope and limitations

Four fixed actions are supported: doctor-scoped appointments, aggregate appointment counts, billing aggregates, and the Cardiology-today count. The schema has medical-record entities for modeling and synthetic fixtures, while current runtime grants and query templates do not expose that content. The app does not implement a reviewer workflow, OIDC discovery, rate limiting, or a separate evaluation-results database. The behavioral risk thresholds are prototype policy values, not clinically calibrated thresholds.
