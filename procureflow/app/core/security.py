"""
Security helpers — IBC ProcureFlow.

Provides password hashing, session creation, session validation, CSRF checking,
and the FastAPI dependency used by all protected endpoints.

Password storage
----------------
New passwords are stored as PBKDF2-SHA256 with 600 000 iterations and a 128-bit
random salt, formatted as::

    pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>

Legacy demo accounts were seeded with a plain SHA-256 hash.  The first
successful password login transparently upgrades the hash to PBKDF2.

Session lifecycle
-----------------
A session is a ``secrets.token_urlsafe(32)`` value stored in an HttpOnly
cookie.  Only its SHA-256 hash is persisted in the database — the raw token
is never stored.  Sessions expire by both idle time (role-dependent) and an
absolute maximum age of 12 hours.
"""

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import HTTPException, Request, status

from ..config import settings
from .db import get_connection

# ── Constants ──────────────────────────────────────────────────────────────────
PASSWORD_ITERATIONS = 600_000
MAX_SESSION_AGE     = timedelta(hours=12)


# ── Password utilities ─────────────────────────────────────────────────────────
def hash_session_token(token: str) -> str:
    """Return the SHA-256 hex digest of *token* for database storage."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """
    Hash *password* with PBKDF2-SHA256 (600 000 iterations, 128-bit salt).

    Returns a string in the format::

        pbkdf2_sha256$<iterations>$<salt_hex>$<digest_hex>
    """
    salt   = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS)
    return f"pbkdf2_sha256${PASSWORD_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """
    Verify *plain_password* against *hashed_password*.

    Supports PBKDF2-SHA256 hashes only. Legacy unsalted hashes are rejected.

    Returns ``True`` on a match, ``False`` otherwise.  Timing-safe.
    """
    try:
        algorithm, iterations_str, salt_hex, stored_digest = hashed_password.split("$", 3)
        if algorithm == "pbkdf2_sha256":
            iterations = int(iterations_str)
            if not 100_000 <= iterations <= 1_000_000 or len(stored_digest) != 64:
                return False
            candidate = hashlib.pbkdf2_hmac(
                "sha256",
                plain_password.encode("utf-8"),
                bytes.fromhex(salt_hex),
                iterations,
            ).hex()
            return hmac.compare_digest(candidate, stored_digest)
    except (ValueError, TypeError):
        pass

    return False


# ── Session management ─────────────────────────────────────────────────────────
def create_session(user_id: str, role: str) -> tuple[str, str]:
    """
    Create a new session for *user_id* with *role*.

    Generates a cryptographically random session token and CSRF token, writes
    the token hash to the database, and returns ``(raw_token, csrf_token)``.

    The raw token is set as the cookie value; only its hash is stored.
    """
    token      = secrets.token_urlsafe(32)
    csrf_token = secrets.token_hex(16)

    idle_minutes = (
        settings.SESSION_IDLE_MINUTES_MANAGER
        if role == "manager"
        else settings.SESSION_IDLE_MINUTES_EMPLOYEE
    )
    now        = datetime.now(timezone.utc)
    expires_at = (now + timedelta(minutes=idle_minutes)).isoformat()
    now_iso    = now.isoformat()

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (token, user_id, csrf_token, expires_at, created_at, last_activity)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (hash_session_token(token), user_id, csrf_token, expires_at, now_iso, now_iso),
        )

    return token, csrf_token


def get_current_user_from_token(token: Optional[str]) -> Optional[dict]:
    """
    Validate *token* and return the authenticated user dict, or ``None``.

    Validation checks:
    1. The token hash exists in the ``sessions`` table.
    2. The session has not exceeded the role-dependent idle timeout.
    3. The session has not exceeded the absolute 12-hour maximum age.
    4. The user account is in ``active`` status.

    On success, the session's ``expires_at`` and ``last_activity`` are
    refreshed (sliding window).  On failure the session row is deleted.
    """
    if not token:
        return None

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.token, s.csrf_token, s.expires_at, s.last_activity, s.created_at,
                   p.id, p.email, p.full_name, p.phone, p.role, p.department_id,
                   p.status, p.can_procure, p.can_receive,
                   d.name  AS department_name,
                   d.code  AS department_code
            FROM   sessions s
            JOIN   profiles p ON s.user_id = p.id
            LEFT JOIN departments d ON p.department_id = d.id
            WHERE  s.token = ?
            """,
            (hash_session_token(token),),
        )
        row = cursor.fetchone()
        if not row:
            return None

        user_dict = dict(row)
        now       = datetime.now(timezone.utc)

        idle_minutes = (
            settings.SESSION_IDLE_MINUTES_MANAGER
            if user_dict["role"] == "manager"
            else settings.SESSION_IDLE_MINUTES_EMPLOYEE
        )
        expires_at    = datetime.fromisoformat(user_dict["expires_at"])
        last_activity = datetime.fromisoformat(user_dict["last_activity"])
        created_at    = datetime.fromisoformat(user_dict["created_at"])

        # Check all expiry conditions
        is_expired = (
            now > expires_at
            or now - last_activity > timedelta(minutes=idle_minutes)
            or now - created_at   > MAX_SESSION_AGE
        )
        if is_expired:
            with conn:
                conn.execute("DELETE FROM sessions WHERE token = ?", (hash_session_token(token),))
            return None

        if user_dict["status"] != "active":
            return None

        # Slide the expiry window
        new_expires = (now + timedelta(minutes=idle_minutes)).isoformat()
        with conn:
            conn.execute(
                "UPDATE sessions SET expires_at = ?, last_activity = ? WHERE token = ?",
                (new_expires, now.isoformat(), hash_session_token(token)),
            )

        return user_dict

    finally:
        conn.close()


def get_current_user(request: Request) -> dict:
    """
    FastAPI dependency — resolve the authenticated user from the session cookie.

    Raises ``HTTP 401`` when the session is absent, expired, or invalid.
    Use as ``user: dict = Depends(get_current_user)`` on any protected endpoint.
    """
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user  = get_current_user_from_token(token)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expired or not authenticated. Please log in.",
        )
    return user


# ── CSRF protection ────────────────────────────────────────────────────────────
def verify_csrf(request: Request, user: dict):
    """
    Validate the ``X-CSRF-Token`` request header for state-mutating methods.

    Called from the security middleware for POST / PUT / PATCH / DELETE requests
    made by an authenticated user.  Raises ``HTTP 403`` on mismatch.
    """
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        csrf_header = request.headers.get("X-CSRF-Token", "")
        expected    = user.get("csrf_token", "")
        if not csrf_header or not hmac.compare_digest(csrf_header, expected):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="CSRF validation failed.",
            )


# ── Client IP extraction ───────────────────────────────────────────────────────
def get_client_ip(request: Request) -> str:
    """
    Return the client IP address.

    Inspects ``X-Forwarded-For`` (taking the leftmost client IP) when behind
    reverse proxies (such as Render or cloud load balancers), falling back to
    ``request.client.host``.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
        if client_ip:
            return client_ip
    return request.client.host if request.client else "unknown"
