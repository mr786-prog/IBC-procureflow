"""
Application configuration for IBC ProcureFlow.

All settings are loaded from environment variables (via a `.env` file at the
project root).  Every integration (Google OAuth, Zoho, Supabase) is optional —
the app starts and runs fully on SQLite alone when those keys are absent.

Environment variables
---------------------
APP_NAME                   Display name shown in the UI title bar.
APP_ENV                    Runtime environment: ``local`` | ``development`` | ``production``.
APP_BASE_URL               Public base URL used for OAuth redirect URIs.
DEMO_MODE                  ``true`` to seed the one locally configured demo manager.
SESSION_COOKIE_SECURE      ``true`` to send the session cookie over HTTPS only.
BACKUP_DIR                 Filesystem path for database backup files.

DATABASE_URL               SQLite URL, e.g. ``sqlite:///procureflow.db``.
SUPABASE_DB_URL             Supabase PostgreSQL connection URI; when present it
                           is the primary database and SQLite becomes a backup.

SESSION_IDLE_MINUTES_MANAGER   Idle timeout (minutes) for manager sessions.
SESSION_IDLE_MINUTES_EMPLOYEE  Idle timeout (minutes) for employee sessions.
ALLOW_PASSWORD_LOGIN       ``true`` to allow email + password login.
BOOTSTRAP_MANAGER_EMAIL    Initial production manager email (Render secret).
BOOTSTRAP_MANAGER_PASSWORD Initial production manager password (Render secret).
DEMO_MANAGER_EMAIL         Optional local demo manager email.
DEMO_MANAGER_PASSWORD     Optional local demo manager password.

GOOGLE_OAUTH_CLIENT_ID     Google Cloud OAuth 2.0 Client ID.
GOOGLE_OAUTH_CLIENT_SECRET Google Cloud OAuth 2.0 Client Secret.

SUPABASE_DB_URL            Supabase PostgreSQL URI; primary application database.
SQLITE_BACKUP_PATH         Local SQLite snapshot/restore path.
SUPABASE_URL               Supabase project URL (e.g. https://xyz.supabase.co).
SUPABASE_PUBLISHABLE_KEY   Supabase publishable key (preferred).
SUPABASE_ANON_KEY          Legacy name for the Supabase publishable/anon key.
SUPABASE_SERVICE_KEY       Supabase service-role key (server-side only).

ZOHO_CLIENT_ID             Zoho OAuth Application Client ID.
ZOHO_CLIENT_SECRET         Zoho OAuth Application Client Secret.
ZOHO_REDIRECT_URI          Zoho OAuth redirect URI (defaults to APP_BASE_URL + /api/v1/auth/zoho/callback).
ZOHO_ORGANIZATION_ID       Zoho Books / Inventory organisation ID.
"""

import os
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel

# ── Paths ──────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent.parent
APP_DIR  = Path(__file__).resolve().parent

load_dotenv(BASE_DIR / ".env")

# Resolve BACKUP_DIR relative to project root when a relative path is supplied.
_backup_raw = os.getenv("BACKUP_DIR", str(BASE_DIR / "backups"))
_backup_dir = Path(_backup_raw)
if not _backup_dir.is_absolute():
    _backup_dir = BASE_DIR / _backup_dir


# ── Settings model ─────────────────────────────────────────────────────────────
class Settings(BaseModel):
    """Centralised application settings — one instance created at import time."""

    # Core
    APP_NAME: str = "IBC ProcureFlow"
    APP_ENV:  str = os.getenv("APP_ENV", "local")
    APP_BASE_URL: str = os.getenv("APP_BASE_URL", "http://localhost:8000")

    # Database
    DATABASE_URL: str = os.getenv(
        "DATABASE_URL",
        f"sqlite:///{BASE_DIR / 'procureflow.db'}"
    )
    SUPABASE_DB_URL: str = os.getenv("SUPABASE_DB_URL", "").strip()
    SQLITE_BACKUP_PATH: Path = Path(
        os.getenv("SQLITE_BACKUP_PATH", "backups/procureflow-latest.sqlite3")
    )

    @property
    def postgres_enabled(self) -> bool:
        return bool(self.SUPABASE_DB_URL)

    # Session / Auth
    SESSION_COOKIE_NAME: str = "procureflow_session"
    SESSION_IDLE_MINUTES_MANAGER:  int  = int(os.getenv("SESSION_IDLE_MINUTES_MANAGER",  "30"))
    SESSION_IDLE_MINUTES_EMPLOYEE: int  = int(os.getenv("SESSION_IDLE_MINUTES_EMPLOYEE", "60"))
    ALLOW_PASSWORD_LOGIN: bool = os.getenv("ALLOW_PASSWORD_LOGIN", "true").lower() in ("true", "1")
    SESSION_COOKIE_SECURE: bool = os.getenv("SESSION_COOKIE_SECURE", "false").lower() in ("true", "1")

    # Feature flags
    DEMO_MODE: bool = os.getenv("DEMO_MODE", "false").lower() in ("true", "1")

    # Filesystem
    BACKUP_DIR: Path = _backup_dir.resolve()

    # One-time account bootstrap credentials. Passwords are never exposed to the UI.
    BOOTSTRAP_MANAGER_EMAIL: str = (
        os.getenv("BOOTSTRAP_MANAGER_EMAIL")
        or os.getenv("INITIAL_MANAGER_EMAIL")
        or os.getenv("MANAGER_EMAIL")
        or ""
    ).strip().lower()
    BOOTSTRAP_MANAGER_PASSWORD: str = (
        os.getenv("BOOTSTRAP_MANAGER_PASSWORD")
        or os.getenv("INITIAL_MANAGER_PASSWORD")
        or os.getenv("MANAGER_PASSWORD")
        or ""
    ).strip()
    DEMO_MANAGER_EMAIL: str = os.getenv("DEMO_MANAGER_EMAIL", "").strip().lower()
    DEMO_MANAGER_PASSWORD: str = os.getenv("DEMO_MANAGER_PASSWORD", "").strip()

    # ── Google OAuth (optional) ────────────────────────────────────────────────
    # Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET to enable the
    # "Sign in with Google" button.  When either value is empty the button is
    # hidden and the /api/v1/auth/google/* routes return 503.
    GOOGLE_OAUTH_CLIENT_ID:     str = os.getenv("GOOGLE_OAUTH_CLIENT_ID", "")
    GOOGLE_OAUTH_CLIENT_SECRET: str = os.getenv("GOOGLE_OAUTH_CLIENT_SECRET", "")

    @property
    def google_oauth_enabled(self) -> bool:
        """True when both Google OAuth credentials are configured."""
        return bool(self.GOOGLE_OAUTH_CLIENT_ID and self.GOOGLE_OAUTH_CLIENT_SECRET)

    # ── Supabase (optional) ───────────────────────────────────────────────────
    # These public API credentials are separate from SUPABASE_DB_URL above.
    # The app database connects directly to PostgreSQL and does not use the
    # Supabase Data API or service-role key for ordinary queries.
    SUPABASE_URL:         str = os.getenv("SUPABASE_URL", "")
    SUPABASE_PUBLISHABLE_KEY: str = os.getenv("SUPABASE_PUBLISHABLE_KEY", "")
    SUPABASE_ANON_KEY:    str = os.getenv("SUPABASE_ANON_KEY", "")
    SUPABASE_SERVICE_KEY: str = os.getenv("SUPABASE_SERVICE_KEY", "")

    @property
    def supabase_api_key(self) -> str:
        """Return the configured public key, preferring the current key name."""
        return self.SUPABASE_PUBLISHABLE_KEY or self.SUPABASE_ANON_KEY

    @property
    def supabase_enabled(self) -> bool:
        """True when the project URL and public API key are configured."""
        return bool(self.SUPABASE_URL and self.supabase_api_key)

    # ── Zoho OAuth (optional) ─────────────────────────────────────────────────
    # Set ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET to enable the Zoho integration
    # (item/vendor sync).  The redirect URI defaults to APP_BASE_URL +
    # /api/v1/auth/zoho/callback; override with ZOHO_REDIRECT_URI if needed.
    ZOHO_CLIENT_ID:       str = os.getenv("ZOHO_CLIENT_ID", "").strip()
    ZOHO_CLIENT_SECRET:   str = os.getenv("ZOHO_CLIENT_SECRET", "").strip()
    ZOHO_REDIRECT_URI:    str = os.getenv("ZOHO_REDIRECT_URI", "")
    ZOHO_ORGANIZATION_ID: str = os.getenv("ZOHO_ORGANIZATION_ID", "").strip()

    @property
    def zoho_enabled(self) -> bool:
        """True when both Zoho OAuth credentials are configured."""
        return bool(self.ZOHO_CLIENT_ID and self.ZOHO_CLIENT_SECRET)

    @property
    def zoho_redirect_uri(self) -> str:
        """Resolved Zoho OAuth redirect URI."""
        if self.ZOHO_REDIRECT_URI:
            return self.ZOHO_REDIRECT_URI
        return f"{self.APP_BASE_URL.rstrip('/')}/api/v1/auth/zoho/callback"


# ── Singleton ──────────────────────────────────────────────────────────────────
settings = Settings()

# ── Start-up guards ────────────────────────────────────────────────────────────
_env = settings.APP_ENV.lower()

if _env not in {"local", "development", "dev"} and not settings.SESSION_COOKIE_SECURE:
    raise RuntimeError(
        "SESSION_COOKIE_SECURE=true is required outside local development. "
        "Set it in your .env file."
    )

if settings.DEMO_MODE and _env not in {"local", "development", "dev"}:
    raise RuntimeError(
        "DEMO_MODE=true is only allowed in local/development environments. "
        "Set DEMO_MODE=false for staging or production."
    )

if settings.DEMO_MODE and not (settings.DEMO_MANAGER_EMAIL and settings.DEMO_MANAGER_PASSWORD):
    raise RuntimeError(
        "Local demo mode requires DEMO_MANAGER_EMAIL and DEMO_MANAGER_PASSWORD."
    )

if settings.DEMO_MODE and settings.postgres_enabled:
    raise RuntimeError("DEMO_MODE is not allowed with a remote primary database.")

if _env in {"production", "prod"} and not (
    settings.BOOTSTRAP_MANAGER_EMAIL and settings.BOOTSTRAP_MANAGER_PASSWORD
):
    raise RuntimeError(
        "Production requires BOOTSTRAP_MANAGER_EMAIL and BOOTSTRAP_MANAGER_PASSWORD "
        "to create the initial manager account."
    )

if _env in {"production", "prod"} and not settings.postgres_enabled:
    raise RuntimeError(
        "Production startup requires Supabase PostgreSQL. "
        "Set SUPABASE_DB_URL in your .env file."
    )
