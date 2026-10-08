# General natural-language query path

QueryImmune accepts natural-language requests over a deliberately limited safe read model. This is open-ended language interpretation within the listed schema, not arbitrary database access.

## Provider modes

- `AI_PROVIDER=demo` uses a local deterministic interpreter for common appointment questions, date windows, status and specialty filters, limits, and simple counts. Its supported language is intentionally finite; unrecognized requests return `UNSUPPORTED_INTENT` or `AMBIGUOUS_REQUEST`.
- `AI_PROVIDER=openai-compatible` uses the configured chat-completions provider for broader paraphrases and SQL-candidate generation.
- `AI_PROVIDER=disabled` (the settings default) does not process NL requests. The Compose demo defaults to `demo`.

The external provider receives the redacted request text, curated schema and policy metadata, and then the typed intent in the SQL-generation stage. It receives no database rows, database URL, PostgreSQL credentials, bearer token, or signing secret. Direct-identifier redaction is best-effort and is not a guarantee of de-identification.

## Security boundary

The provider returns a strict `QueryIntent` followed by a candidate statement. Both are untrusted. The candidate must be one PostgreSQL `SELECT`, parsed by SQLGlot and matched against the structured intent before the policy pipeline can consider it. General SELECT is separate from the fixed execution broker.

The general path exposes selected columns from `healthcare.appointments`, `healthcare.doctors`, `healthcare.patients`, and the invoker-security `healthcare.general_doctor_directory` view, recorded in `queryimmune_db.security.schema.GENERAL_QUERY_RELATIONS`. The directory view provides clinician display names while inheriting the doctors table's actor RLS. It excludes medical records, patient demographics, raw billing, raw user identity, and security tables. The allowlist is also sent as model context; it is not authorization. PostgreSQL grants and RLS remain an independent enforcement boundary.

Actor-scoped queries require the authenticated server Actor's ID in the exact `doctors.user_id = :actor_id` predicate. Execution installs that ID transaction-locally, uses a read-only transaction, the existing 5-second statement timeout, PostgreSQL RLS, a maximum of two joins, a bounded AST, at most 100 rows, and a one-megabyte serialized response limit. The client and provider cannot choose the actor.

Cross-actor access is limited to aggregate-only `COUNT(*)` and `COUNT(DISTINCT patient_id)` queries over appointments/doctor directory and is authorized by the `appointments.general_aggregate` action/resource rows in PostgreSQL for analyst/admin roles. PostgreSQL shared-read RLS is enabled only after that policy authorization. Every aggregate must include the matching `HAVING` threshold of at least three. General cross-actor row queries, patient-level result rows, sensitive columns, unsupported relationships, and write/admin statements fail closed. No new grants to medical or billing tables are made.

After deterministic validation and database authorization, the existing behavior, workload-drift, risk, audit, and ALLOW/REVIEW/BLOCK pipeline applies. REVIEW and BLOCK never execute. The model cannot set the risk score or override authorization, RLS, sensitivity, or scope.

## Metadata maintenance

The general-query relation and relationship allowlist is shared between schema context and AST validation. PostgreSQL grants/RLS and the action-role policy migration remain the final database-owned controls; changes to the exposed schema require explicit review and regression tests across both metadata and database privileges. Fixed broker SQL and its action-specific validator remain in place.
