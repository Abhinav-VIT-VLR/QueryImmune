# Security

## Invariants

- The model provider receives curated schema metadata and redacted request text only; it has no DB connection or credential.
- Model SQL is untrusted. SQLGlot parses one PostgreSQL statement and the validator checks operation, relations, columns, joins, filters, limits, scope, and selected action template.
- The model output is not executed. Only supported actions map to fixed parameterized SQL in the repository.
- Static action/role/scope/limit rules and SQL mismatch blocks run before behavior scoring. Learned behavior cannot relax a hard denial.
- REVIEW and BLOCK are audited with `not_run` status. Only ALLOW reaches the read-only executor.
- Runtime, migration, and bootstrap authority are separate. Runtime access to clinical tables is SELECT-only and excludes `medical_records`; audit writes use restricted functions.
- Patient rows and credentials are not logged to application logs. Client errors do not include provider/database exception details.

## Local credentials and authentication

`python scripts/create_env.py` generates a local ignored `.env`; do not commit it. The Compose bootstrap account is for migrations and fixture loading. The API uses `queryimmune_runtime`. Production deployments must replace local settings with a managed secret store, TLS, a trusted identity provider, and an appropriately isolated migration job.

The Compose-only `QUERYIMMUNE_DEMO_AUTH=1` flag permits the explicit CLI command `python -m queryimmune_db.api.dev_token` to print a 15-minute token for the synthetic seeded doctor. This is not an HTTP endpoint and must not be enabled in a production deployment. The token uses the same verifier and database role lookup as other tokens.

AI URLs require HTTPS except loopback. Provider redirects are disabled, calls have a timeout and response-size cap, and no automatic retry is configured. Not retrying avoids duplicate requests and unpredictable costs; transient provider errors fail closed.

## Database and query controls

Each runtime DB connection uses a five-second statement timeout and two-second lock timeout. The query executor sets a read-only transaction and a fixed search path. All executed templates are parameterized and bounded. Migration 008 removes broad table-level runtime reads, denies patient demographics/raw billing access, and returns billing totals through a bounded aggregate function. Migration 007 removes unsupported historical result-size statistics and constrains future profile statistics to audited successful samples and the gateway result cap.

## Verification status

Automated checks include role denial, role-field spoofing rejection, prompt injection, multi-statement/destructive SQL, SQL injection predicates, wildcard and sensitive-column access, malformed SQL, profile poisoning gates, behavior drift, API health/auth, and database integration tests. Database-backed tests require PostgreSQL; Docker was unavailable during the final review, so fresh-container migration and end-to-end checks could not be run here. This prototype is not a compliance certification or a substitute for production security review.
