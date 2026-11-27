# IBC ProcureFlow

ProcureFlow is a procurement and material management application for Industrial Bolt Centre. It provides separate manager and employee workspaces, approval workflows, purchase orders, goods receipts, reports, and an append-only audit trail.

## Stack

- Python, FastAPI, and Uvicorn
- Vanilla HTML, CSS, and JavaScript
- Supabase PostgreSQL for hosted deployments; SQLite for local development and backup snapshots
- Password sessions, role and capability checks, CSRF protection, and audit logging

## Local development

1. Use Python 3.11 or newer.
2. Install dependencies: `pip install -r requirements.txt`.
3. Copy `.env.example` to `.env` and keep `.env` private.
4. Keep `APP_ENV=local`, `DEMO_MODE=false`, and `SESSION_COOKIE_SECURE=false` for local HTTP development.
5. Start the server with `python run.py` and open `http://127.0.0.1:8000`.

To create a private local demo manager, set `DEMO_MODE=true`, `DEMO_MANAGER_EMAIL`, and `DEMO_MANAGER_PASSWORD` in `.env`. Demo mode is local-only and is rejected with a remote database. The account and password are never displayed by the application and there are zero demo account options in the UI. Set demo mode back to false before using any non-demo data.

See [DEPLOYMENT_AND_SECURITY.md](DEPLOYMENT_AND_SECURITY.md) for the detailed security audit, verification checks, and production readiness breakdown.

## Render deployment

Create a Render Web Service from the repository using the included [`render.yaml`](render.yaml) Blueprint. Render will prompt for the three `sync: false` values when creating the service:

- `SUPABASE_DB_URL`: Supabase PostgreSQL connection URI (server-side secret)
- `BOOTSTRAP_MANAGER_EMAIL`: Email for the initial manager
- `BOOTSTRAP_MANAGER_PASSWORD`: A unique, strong initial password

The Blueprint configures production mode, secure cookies, demo mode off, the install/start commands, and `/health` checks. Render supplies the service's public URL; the application uses it as `APP_BASE_URL` unless you explicitly set `APP_BASE_URL` in the Render dashboard.

Treat manager credentials and database URLs as secrets; do not put their values in the repository or a committed `.env` file. Configure Google or Zoho OAuth credentials in Render only if those integrations are needed, and register the service's HTTPS callback URLs with those providers.

The manager account is created during database initialization only when that email is not already present. Existing accounts and passwords are not overwritten on redeploy. After first login, manage staff through the manager interface and rotate or replace the bootstrap password through the normal account administration process. Production startup fails closed if the database or bootstrap credentials are missing. Use Supabase Direct or Session Pooler connection details and keep database network access restricted where practical.

Render's local filesystem is ephemeral. The app's SQLite backup mirror is useful for recovery, but it is not durable storage on a standard Render service. Keep Supabase as the system of record and configure a separate durable backup destination and restore procedure before relying on production data.

## Environment reference

See [`.env.example`](.env.example) for optional OAuth and Supabase API settings. `SUPABASE_DB_URL` is the database connection; it is distinct from Supabase publishable and service-role API keys. Never place the service-role key in browser code.

Important settings:

- `ALLOW_PASSWORD_LOGIN`: enable or disable email and password login.
- `SESSION_IDLE_MINUTES_MANAGER` / `SESSION_IDLE_MINUTES_EMPLOYEE`: idle session limits.
- `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`: optionally enable Google sign-in for existing users.
- `ZOHO_CLIENT_ID` and `ZOHO_CLIENT_SECRET`: optionally enable the Zoho connection.
- `SQLITE_BACKUP_PATH`: local mirror path for a Supabase database.

## Data migration and backups

For a new Supabase database, initialize and migrate an existing SQLite database with `python scripts/migrate_sqlite_to_supabase.py` before serving users. The migration script refuses to write into application tables that already contain data. Refresh the local SQLite mirror with `python scripts/sync_local_sqlite_mirror.py`; create timestamped recovery archives with `python scripts/backup_local_db.py`. Restore an archive into an empty Supabase project with `python scripts/restore_sqlite_backup_to_supabase.py <snapshot-file>`.

Backups contain user, vendor, purchasing, and audit records. Restrict access and retention accordingly. Test restore procedures against a non-production database.

## Security controls

- Manager and employee access is enforced by server-side role checks; hiding a UI element is not an authorization control.
- Passwords use salted PBKDF2-SHA256 hashes. The legacy public sample accounts are deactivated at startup and their old unsalted hashes are rejected.
- Session tokens are random, stored as hashes in the database, and expire by idle and absolute age. Cookies are HttpOnly and SameSite=Lax; HTTPS is required outside local development.
- Mutating requests use CSRF validation and same-origin checks. Common browser security headers and a Content Security Policy are applied.
- Login attempts are rate-limited per process. For multi-instance deployments, add a shared rate-limit store or an edge/WAF limit.
- Keep dependencies current, rotate any credentials that have ever been committed or shared, and review database/network access before launch.

## Project layout

```text
procureflow/app/
  config.py                 Environment configuration and production guards
  core/                     Database, authentication, permissions, numbering, audit
  modules/                  API routes grouped by business workflow
  static/                   Login page and manager/employee browser applications
scripts/                    Database migration, backup, sync, and restore tools
run.py                      Local development entry point
requirements.txt            Python dependencies
```
