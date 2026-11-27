# IBC ProcureFlow — Deployment, Security & Production Readiness Guide

This document provides a complete technical audit, configuration reference, and security review for Industrial Bolt Centre (IBC) ProcureFlow.

---

## 1. What Is Done vs. What Is Not

### ✅ What Is Done (Production Ready)
- **Zero Demo Accounts in the UI**: There are no demo buttons, sample login links, pre-filled credentials, or demo selectors anywhere on the login page or application workspaces.
- **Single Private Demo Account (Local-Only)**: Configured purely through private `.env` variables (`DEMO_MODE=true`, `DEMO_MANAGER_EMAIL`, `DEMO_MANAGER_PASSWORD`). It is never seeded on Supabase/PostgreSQL and is completely invisible in the UI.
- **Render Secret Keys Bootstrap Manager**: When deployed to Render with `BOOTSTRAP_MANAGER_EMAIL` and `BOOTSTRAP_MANAGER_PASSWORD` (or aliases `INITIAL_MANAGER_EMAIL` / `MANAGER_EMAIL`), the initial manager account is automatically created on startup with a PBKDF2-SHA256 hashed password. Subsequent redeployments are idempotent and never overwrite existing credentials.
- **Legacy Fixture Accounts Deactivated**: Old test accounts with default passwords (`user-mgr-1`, `user-mgr-2`, `user-emp-1`, `user-emp-2`) are deactivated at startup, and old unsalted hashes are strictly rejected by the auth engine.
- **Two Distinct Interfaces**: Manager Console (`/app/manager/`) and Employee Workspace (`/app/employee/`) with server-side authorization enforcement on every API route.
- **Segregation of Duties**: Managers are strictly prevented from approving their own Purchase Requisitions or Purchase Orders.
- **Commercial Data Masking**: Unit rates, price histories, vendor contact details, and financial totals are hidden from standard employees.
- **CSRF & Origin Protection**: All state-mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) require a valid `X-CSRF-Token` header and undergo strict origin checking.
- **Reverse-Proxy Client IP Resolution**: The app parses `X-Forwarded-For` to ensure audit logs and rate limiters track real client IPs rather than Render's internal proxy IP.
- **File Upload Hardening**: Transport documents (LRs) are validated by file signatures / magic bytes (`%PDF-`, `PNG`, `JPEG`) and capped at 10 MB.
- **Append-Only Audit Log**: Database triggers prevent any `UPDATE` or `DELETE` on the `audit_log` table.
- **Cleaned File Structure**: Unnecessary root asset files (`ibc logo.jpeg`) removed; `.gitignore` updated to ignore temporary files and local database snapshots.
- **Self-Service Profile & Password Management**: Both manager and employee workspaces include self-service options to update Full Name and Phone Number (`PATCH /api/v1/auth/me`), as well as change account passwords securely with PBKDF2 hashing (`POST /api/v1/auth/change-password`).

### ⚠️ What Requires Your Input / Actions (Before Final Launch)
- **Render Secret Keys**: You must input `BOOTSTRAP_MANAGER_EMAIL` and `BOOTSTRAP_MANAGER_PASSWORD` into Render's Environment Secret Keys.
- **Supabase PostgreSQL URL**: Set `SUPABASE_DB_URL` in Render's Secret Keys pointing to your Supabase connection string.
- **Google OAuth Credentials (Optional)**: If you want IBC staff to sign in with Google Workspace, provide `GOOGLE_OAUTH_CLIENT_ID` and `GOOGLE_OAUTH_CLIENT_SECRET`. If not set, Google sign-in remains disabled and only password login is active.

---

## 2. Demo Account: How It Works & Why Only You Know It

### UI Behavior
- The login interface at `/login` contains **no demo account options, buttons, or hints**.
- No public user list or role preview exists in browser client scripts.

### How Only You Know the Demo Account
1. The demo account is **strictly opt-in** and only works when `DEMO_MODE=true` in your local `.env`.
2. The email and password are set by you in `.env`:
   ```env
   DEMO_MODE=true
   DEMO_MANAGER_EMAIL=your.private.email@ibcbolts.com
   DEMO_MANAGER_PASSWORD=YourChosenSecretPassword123!
   ```
3. The server hashes this password with 600,000 PBKDF2 iterations and inserts it directly into the local database upon startup.
4. **Safety Guard**: `DEMO_MODE=true` is hardcoded to fail on startup if `APP_ENV=production` or if `SUPABASE_DB_URL` is configured. A demo account can **never** be created in your cloud database.
5. In production on Render, `DEMO_MODE` is always `false`.

---

## 3. Render Deployment & Secret Keys Setup

Deploy ProcureFlow to Render as a **Web Service**:

### Service Settings
- **Environment**: Python 3
- **Root Directory**: `.` (leave default)
- **Build Command**: `pip install -r requirements.txt`
- **Start Command**: `uvicorn procureflow.app.main:app --host 0.0.0.0 --port $PORT`
- **Health Check Path**: `/health`

### Environment Secret Keys (Render Dashboard → Environment)
Input the following secrets in Render:

| Key | Description | Example Value |
|---|---|---|
| `APP_ENV` | Must be production | `production` |
| `APP_BASE_URL` | Public HTTPS URL on Render | `https://ibc-procureflow.onrender.com` |
| `SESSION_COOKIE_SECURE` | Enforces HTTPS-only cookies | `true` |
| `DEMO_MODE` | Must remain false in cloud | `false` |
| `SUPABASE_DB_URL` | Primary Supabase PostgreSQL connection string | `postgresql://postgres:[PASSWORD]@db.[REF].supabase.co:5432/postgres` |
| `BOOTSTRAP_MANAGER_EMAIL` | **Your manager email** | `manager@ibcbolts.com` |
| `BOOTSTRAP_MANAGER_PASSWORD` | **Your secure initial password** | `AStrongSecretPassword123#` |

*(Note: The system also recognizes `INITIAL_MANAGER_EMAIL` / `MANAGER_EMAIL` as aliases).*

### What happens on first boot on Render:
1. ProcureFlow connects to Supabase PostgreSQL using TLS.
2. It creates the private `procure` schema and all tables idempotently.
3. It seeds essential departments (`dept-1` to `dept-6`) and system settings.
4. It inspects the `profiles` table:
   - If `BOOTSTRAP_MANAGER_EMAIL` is not already present, it creates the manager account with status `active`, role `manager`, procurement and receiving capabilities enabled, and the PBKDF2-hashed password.
   - If the account already exists, it leaves existing passwords and staff unchanged.

---

## 4. Security Audit & Hardening Summary

| Security Layer | Implementation Details | Status |
|---|---|:---:|
| **Authentication** | PBKDF2-SHA256 (600,000 iterations, 128-bit salt). Legacy unsalted SHA-256 hashes rejected. | ✅ Verified |
| **Session Security** | 32-byte cryptographically random tokens (`secrets.token_urlsafe(32)`). Raw tokens are never stored; only SHA-256 digests exist in the DB. HttpOnly + SameSite=Lax + Secure cookies. Idle timeout (30m manager, 60m employee) and 12-hour hard limit. | ✅ Verified |
| **CSRF Defense** | State-mutating requests (`POST`, `PUT`, `PATCH`, `DELETE`) require `X-CSRF-Token` validated via `hmac.compare_digest`. | ✅ Verified |
| **Cross-Origin Defense** | Middleware verifies `Origin` and `Referer` headers match host name; rejects foreign origins with HTTP 403. | ✅ Verified |
| **Brute Force Defense** | Per-process sliding-window rate limiting on login (8 failed attempts per user+IP, 40 per IP per 15 minutes). | ✅ Verified |
| **Proxy IP Resolution** | `get_client_ip()` checks `X-Forwarded-For` so rate limits and audit logs identify the real user, not the Render load balancer. | ✅ Hardened |
| **Access Control (RBAC)** | Role validation (`require_manager`, `require_procure_access`, `require_receive_access`) enforced on every endpoint. | ✅ Verified |
| **Segregation of Duties** | `check_pr_approval_segregation` and `check_po_approval_segregation` block self-approval by creators. | ✅ Verified |
| **Audit Trail** | Append-only audit trail logging all lifecycle actions. Database triggers block any `UPDATE` or `DELETE` on `audit_log`. | ✅ Verified |
| **File Upload Security** | Magic bytes verification for `%PDF-`, PNG, JPEG; filename sanitization; 10 MB upload ceiling. | ✅ Verified |
| **Browser Security Headers** | CSP (`default-src 'self'`), `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`, HSTS (`Strict-Transport-Security`). | ✅ Verified |
| **SQL Injection** | Parameterized queries throughout all modules. | ✅ Verified |

---

## 5. First-Time Manager Onboarding & Profile Management

When the bootstrap manager signs in for the first time:

1. **Initial Login**:
   - Navigate to `/login`.
   - Enter `BOOTSTRAP_MANAGER_EMAIL` and `BOOTSTRAP_MANAGER_PASSWORD` configured in Render secret keys (or `.env`).
   - The system automatically authenticates, grants manager access, and redirects to `/app/manager/`.

2. **Editing Profile Details (Name & Phone)**:
   - Navigate to **⚙️ Settings** from the sidebar or click your user name in the top right header.
   - Under **👤 My Profile Details**, you can update:
     - **Full Name**: e.g., your real name instead of the default prefix.
     - **Phone Number**: e.g., `+91 98200 11223`.
   - Click **Save Profile Details**. The header displays your updated name immediately without page reload.

3. **Changing Account Password**:
   - In **⚙️ Settings**, locate the **🔒 Change Account Password** card.
   - Enter your **Current Password** (the initial bootstrap password).
   - Enter and confirm your **New Password** (minimum 8 characters).
   - Click **Update Password**.
   - The password is authenticated and re-hashed using salted PBKDF2-SHA256 (600,000 iterations), and an audit record (`user.change_password`) is logged.

