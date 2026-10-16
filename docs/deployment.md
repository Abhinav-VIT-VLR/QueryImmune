# Render deployment

QueryImmune can run on Render as a Docker web service with an external PostgreSQL database. This configuration deliberately disables automatic deploys: apply and verify database migrations with a separate trusted bootstrap environment before manually deploying application changes. The long-running web service receives only the runtime database URL, the narrowly scoped ORM login URL, and the JWT verification key. It never receives `DATABASE_ADMIN_URL` or the passwords used to provision database roles.

Use only synthetic data for a public demonstration. The app provides one environment-configured local login for this demo; its subject must map to an active database user with an assigned role. The server issues 15-minute HS256 bearer tokens and the existing API checks authorization against database role policies. Logout clears the token from the current browser tab; a copied token remains valid until it expires. There is no account recovery or multi-user account management. Do not enable `QUERYIMMUNE_DEMO_AUTH` on the public service; that flag is for the local CLI demo only.

## Database choice

Neon Postgres is a compatible managed PostgreSQL option for this role model. Neon documents that project owner roles inherit `neon_superuser`, which includes `CREATEROLE`, and that SQL can create `NOLOGIN` privilege roles. The migration runner also uses `SET ROLE qi_owner`; grant that role to the Neon project owner account before running the setup command below. Keep the owner/bootstrap URL on the trusted machine used for migrations, not in Render's web-service environment.

The Neon Free plan currently includes 1 GB database storage and 100 compute-unit hours per project per month. Compute scales to zero after five minutes idle, so the first request after idle can be slower. The Free plan's instant-restore history is six hours; keep a separate backup before relying on this database. Render Free web services sleep after 15 minutes of inactivity and have no persistent local disk, which is why application state must stay in PostgreSQL. Render Free Postgres databases expire after 30 days, so this guide does not use one. Check both providers' current limits before deploying.

## Prepare the database

1. Create a Neon PostgreSQL project in a region close to the Render service. Use its direct connection URL for migration setup and append `sslmode=require` if it is not already present.
2. In the Neon SQL Editor, connected as the project owner, create the owner role and grant it to the project's owner login. Replace `<neon_owner_role>` with the username from the Neon connection details:

   ```sql
   CREATE ROLE qi_owner NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
   -- Replace neonproject_owner with the actual username from Neon connection details.
   GRANT qi_owner TO neonproject_owner;
   ```

   This membership is needed because the checked-in migrations explicitly `SET ROLE qi_owner`. The migration runner creates the remaining QueryImmune privilege and login roles with their restricted attributes.
3. On a trusted local machine, install the backend and set these environment variables for the migration command: `DATABASE_ADMIN_URL` (Neon owner connection URL), `DATABASE_RUNTIME_PASSWORD`, and `DATABASE_ORM_ADMIN_PASSWORD`. Use fresh strong passwords and do not save or print these values in the repository. From `backend/`, run:

   ```powershell
   python -m pip install -e .
   python -m queryimmune_db.migrate setup
   ```

   `setup` applies only migrations absent from `qi_migrations.applied`, provisions the restricted logins, and inserts the project's synthetic fixtures. Run it only against the new demo database. Keep the bootstrap URL and role passwords out of Render's web-service environment.
4. Construct two Render runtime connection URLs from the Neon host/database and the provisioned logins: `DATABASE_URL` must use `queryimmune_runtime`; `DATABASE_ORM_ADMIN_URL` must use `queryimmune_orm_admin` and the `postgresql+psycopg://` scheme. Preserve `sslmode=require` and URL-encode special characters in passwords. Do not use the Neon owner login for either URL.

For later releases, run `python -m queryimmune_db.migrate setup` with the trusted bootstrap environment before deploying code that depends on new schema. The Render service has automatic deploys turned off to keep that order explicit.

## Create the Render service

1. Push the reviewed deployment files to the repository's GitHub default branch, then in Render choose **New > Blueprint** and connect the repository. The Blueprint builds from the repository root using `backend/Dockerfile`.
2. Supply the Blueprint's prompted values: `DATABASE_URL`, `DATABASE_ORM_ADMIN_URL`, `AUTH_JWT_HS256_SECRET`, `AUTH_LOGIN_USERNAME`, `AUTH_LOGIN_PASSWORD_HASH`, and `AUTH_LOGIN_SUBJECT`. Generate the password hash locally from `backend/` by running `python -m queryimmune_db.api.password_hash`; enter a strong password at the hidden prompts and copy the resulting hash into Render. Never enter the plaintext password into Render or commit the hash. Generate a random signing secret locally with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it as `AUTH_JWT_HS256_SECRET`. Set `AUTH_LOGIN_SUBJECT` to a seeded identity such as `demo:dr-ada` (doctor) or `demo:admin` (administrator); database roles are resolved from PostgreSQL, not from the login form. The configured `PORT=8000` matches the existing Dockerfile command. The health check calls the existing `/health/ready` endpoint, which requires both application database logins to work.
3. Deploy manually after database setup. When Render reports the service healthy, open its `onrender.com` URL and `/health/live` or `/health/ready` to verify the public endpoint. Sign in with the configured username and the password used to generate its hash. The login endpoint issues the token; the browser stores it in tab-scoped `sessionStorage` and sends it only as a bearer token to this API. Sign out clears it from the tab. This single-login setup is intended for a synthetic classroom demonstration, not multi-user production identity management.

Render Free web services sleep when idle, so they are suitable for a short demonstration rather than a reliably warm service. Upgrade the web service if it must stay available without cold starts. Free-tier limits and prices change; review the current [Render Free instance limits](https://render.com/docs/free) and [Neon pricing and limits](https://neon.com/pricing) before use.
