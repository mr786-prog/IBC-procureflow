"""
Authentication module — IBC ProcureFlow.

Endpoints
---------
GET  /api/v1/auth/config              Feature-flag endpoint used by the login page to
                                       discover which OAuth providers are configured.
GET  /api/v1/auth/me                  Return the current authenticated user's profile.
POST /api/v1/auth/login               Email + password login (rate-limited).
POST /api/v1/auth/logout              Invalidate the current session.
POST /api/v1/auth/accept-invitation   Accept an email invitation and set a password.

Google OAuth (requires GOOGLE_OAUTH_CLIENT_ID + GOOGLE_OAUTH_CLIENT_SECRET)
GET  /api/v1/auth/google/login        Redirect to Google consent screen.
GET  /api/v1/auth/google/callback     Handle the OAuth callback and create a session.

Zoho OAuth (requires ZOHO_CLIENT_ID + ZOHO_CLIENT_SECRET)
GET  /api/v1/auth/zoho/login          Redirect to Zoho consent screen.
GET  /api/v1/auth/zoho/callback       Handle the Zoho callback and store the tokens.
"""

import hashlib
import secrets
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field

from ..config import settings
from ..core.audit import log_audit
from ..core.db import get_connection
from ..core.permissions import require_manager
from ..core.security import (
    create_session,
    get_client_ip,
    get_current_user,
    hash_password,
    hash_session_token,
    verify_password,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

# ── Rate limiting (in-memory, process-scoped) ──────────────────────────────────
_login_attempts: dict[str, list[float]] = {}
_login_attempts_lock = threading.Lock()
_LOGIN_WINDOW_SECONDS = 900   # 15 minutes
_LOGIN_MAX_ATTEMPTS   = 8


def _login_rate_limited(key: str, *, record: bool = False, limit: int = _LOGIN_MAX_ATTEMPTS) -> bool:
    """
    Return ``True`` if *key* has exceeded *limit* attempts within the sliding
    15-minute window.  Pass ``record=True`` to register a new failed attempt.
    Automatically prunes stale entries when the in-memory dict grows large.
    """
    now = time.monotonic()
    with _login_attempts_lock:
        attempts = [t for t in _login_attempts.get(key, []) if now - t < _LOGIN_WINDOW_SECONDS]
        if record:
            attempts.append(now)
        _login_attempts[key] = attempts
        # Prevent unbounded memory growth
        if len(_login_attempts) > 10_000:
            for old_key in list(_login_attempts)[:2_000]:
                _login_attempts.pop(old_key, None)
        return len(attempts) >= limit


def _clear_login_attempts(key: str) -> None:
    """Remove all recorded attempts for *key* after a successful login."""
    with _login_attempts_lock:
        _login_attempts.pop(key, None)


# ── Request/response schemas ───────────────────────────────────────────────────
class LoginRequest(BaseModel):
    email:    str
    password: str = Field(min_length=1, max_length=256)


class AcceptInvitationRequest(BaseModel):
    token:    str = Field(min_length=32, max_length=256)
    password: str = Field(min_length=12, max_length=256)


class ProfileUpdateRequest(BaseModel):
    full_name: Optional[str] = Field(None, min_length=1, max_length=120)
    phone:     Optional[str] = Field(None, max_length=40)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=256)
    new_password:     str = Field(min_length=8, max_length=256)


# ── Feature-flag endpoint (used by the login page JS) ─────────────────────────
@router.get("/config")
def auth_config():
    """
    Return which optional auth providers are enabled.

    The login page fetches this once on load to decide whether to show optional
    identity providers.
    """
    return {
        "google_oauth_enabled": settings.google_oauth_enabled,
        "zoho_enabled":         settings.zoho_enabled,
        "supabase_enabled":     settings.supabase_enabled,
        "password_login":       settings.ALLOW_PASSWORD_LOGIN,
    }


# ── Current-user endpoint ──────────────────────────────────────────────────────
@router.get("/me")
def get_me(user: dict = Depends(get_current_user)):
    """Return the authenticated user's profile and CSRF token."""
    return {
        "id":              user["id"],
        "email":           user["email"],
        "full_name":       user["full_name"],
        "phone":           user.get("phone"),
        "role":            user["role"],
        "department_id":   user.get("department_id"),
        "department_name": user.get("department_name"),
        "can_procure":     bool(user.get("can_procure")),
        "can_receive":     bool(user.get("can_receive")),
        "csrf_token":      user.get("csrf_token"),
    }


@router.patch("/me")
def update_me(payload: ProfileUpdateRequest, request: Request, user: dict = Depends(get_current_user)):
    """Update the current user's profile information (full name, phone)."""
    updates = []
    params = []
    if payload.full_name is not None:
        name = payload.full_name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Full name cannot be empty.")
        updates.append("full_name = ?")
        params.append(name)
    if payload.phone is not None:
        updates.append("phone = ?")
        params.append(payload.phone.strip() if payload.phone else None)

    if not updates:
        return {"status": "unchanged"}

    now_iso = datetime.now(timezone.utc).isoformat()
    updates.append("updated_at = ?")
    params.append(now_iso)
    params.append(user["id"])

    conn = get_connection()
    with conn:
        conn.execute(f"UPDATE profiles SET {', '.join(updates)} WHERE id = ?", params)

    ip = get_client_ip(request)
    log_audit(
        action="user.profile_update",
        entity_type="profile",
        entity_id=user["id"],
        actor_id=user["id"],
        actor_email=user["email"],
        after_data=payload.model_dump(exclude_unset=True),
        ip=ip,
    )
    return {"status": "success", "message": "Profile updated successfully."}


@router.post("/change-password")
def change_password(payload: ChangePasswordRequest, request: Request, user: dict = Depends(get_current_user)):
    """Change the authenticated user's password."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT password_hash FROM profiles WHERE id = ?", (user["id"],))
    row = cursor.fetchone()
    if not row or not row["password_hash"]:
        raise HTTPException(status_code=400, detail="Password login is not enabled for this account.")

    if not verify_password(payload.current_password, row["password_hash"]):
        raise HTTPException(status_code=400, detail="Incorrect current password.")

    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=422, detail="New password must be different from current password.")

    new_hash = hash_password(payload.new_password)
    now_iso = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE profiles SET password_hash = ?, updated_at = ? WHERE id = ?",
                     (new_hash, now_iso, user["id"]))

    ip = get_client_ip(request)
    log_audit(
        action="user.change_password",
        entity_type="profile",
        entity_id=user["id"],
        actor_id=user["id"],
        actor_email=user["email"],
        ip=ip,
    )
    return {"status": "success", "message": "Password changed successfully."}


# ── Accept invitation ──────────────────────────────────────────────────────────
@router.post("/accept-invitation")
def accept_invitation(payload: AcceptInvitationRequest, response: Response):
    """
    Activate a pending user account using the one-time invitation token.

    The token is single-use and expires 24 hours after the invitation was sent.
    On success a session cookie is set and the user's new redirect URL is returned.
    """
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    now = datetime.now(timezone.utc)
    conn = get_connection()

    with conn:
        row = conn.execute(
            """
            SELECT i.user_id, i.expires_at
            FROM   invitation_tokens i
            JOIN   profiles p ON p.id = i.user_id
            WHERE  i.token_hash = ? AND p.status = 'invited'
            """,
            (token_hash,),
        ).fetchone()

        if not row or datetime.fromisoformat(row["expires_at"]) <= now:
            raise HTTPException(status_code=400, detail="Invitation is invalid or has expired.")

        conn.execute(
            "UPDATE profiles SET password_hash = ?, status = 'active', first_login_at = ?, updated_at = ? WHERE id = ?",
            (hash_password(payload.password), now.isoformat(), now.isoformat(), row["user_id"]),
        )
        conn.execute("DELETE FROM invitation_tokens WHERE user_id = ?", (row["user_id"],))

    user_row = conn.execute("SELECT role FROM profiles WHERE id = ?", (row["user_id"],)).fetchone()
    session_token, csrf_token = create_session(row["user_id"], user_row["role"])

    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_token,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )
    return {
        "status":       "success",
        "csrf_token":   csrf_token,
        "redirect_url": "/app/manager/" if user_row["role"] == "manager" else "/app/employee/",
    }


# ── Password login ─────────────────────────────────────────────────────────────
@router.post("/login")
def login(payload: LoginRequest, response: Response, request: Request):
    """
    Authenticate with email and password.

    Rate-limited: 8 attempts per IP+email pair and 40 attempts per IP within
    any 15-minute sliding window.  Returns a session cookie and CSRF token.
    """
    if not settings.ALLOW_PASSWORD_LOGIN:
        raise HTTPException(status_code=403, detail="Password login is disabled.")

    email = payload.email.strip().lower()
    ip    = get_client_ip(request)

    rate_key = hashlib.sha256(f"{ip}:{email}".encode()).hexdigest()
    ip_key   = "ip:" + hashlib.sha256(ip.encode()).hexdigest()

    if _login_rate_limited(rate_key) or _login_rate_limited(ip_key, limit=40):
        raise HTTPException(
            status_code=429,
            detail="Too many login attempts. Please try again in 15 minutes.",
        )

    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE lower(email) = ?", (email,))
    user_row = cursor.fetchone()

    if not user_row:
        _login_rate_limited(rate_key, record=True)
        _login_rate_limited(ip_key, record=True, limit=40)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Access not granted. Ask an IBC manager to add your email.",
        )

    user = dict(user_row)

    if user["status"] == "deactivated":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account deactivated. Please contact your IBC administrator.",
        )

    if not user.get("password_hash") or not verify_password(payload.password, user["password_hash"]):
        _login_rate_limited(rate_key, record=True)
        _login_rate_limited(ip_key, record=True, limit=40)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials.",
        )

    # Activate invited users on first login
    now_iso = datetime.now(timezone.utc).isoformat()
    if user["status"] == "invited":
        with conn:
            conn.execute(
                "UPDATE profiles SET status = 'active', first_login_at = ?, last_login_at = ? WHERE id = ?",
                (now_iso, now_iso, user["id"]),
            )
    else:
        with conn:
            conn.execute(
                "UPDATE profiles SET last_login_at = ? WHERE id = ?",
                (now_iso, user["id"]),
            )

    token, csrf_token = create_session(user["id"], user["role"])
    _clear_login_attempts(rate_key)
    _clear_login_attempts(ip_key)

    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )

    log_audit(
        action="user.login",
        entity_type="profile",
        entity_id=user["id"],
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"role": user["role"], "method": "password"},
        ip=ip,
    )

    return {
        "status":       "success",
        "role":         user["role"],
        "csrf_token":   csrf_token,
        "redirect_url": "/app/manager/" if user["role"] == "manager" else "/app/employee/",
    }


# ── Logout ─────────────────────────────────────────────────────────────────────
@router.post("/logout")
def logout(response: Response, request: Request, user: dict = Depends(get_current_user)):
    """Revoke the current session and clear the session cookie."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if token:
        conn = get_connection()
        with conn:
            conn.execute("DELETE FROM sessions WHERE token = ?", (hash_session_token(token),))

    response.delete_cookie(
        settings.SESSION_COOKIE_NAME,
        path="/",
        secure=settings.SESSION_COOKIE_SECURE,
        httponly=True,
        samesite="lax",
    )
    return {"status": "success", "message": "Logged out successfully."}


# ══════════════════════════════════════════════════════════════════════════════
# Google OAuth  (enabled only when GOOGLE_OAUTH_CLIENT_ID is configured)
# ══════════════════════════════════════════════════════════════════════════════
_GOOGLE_AUTH_URL  = "https://accounts.google.com/o/oauth2/v2/auth"
_GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
_GOOGLE_USERINFO  = "https://www.googleapis.com/oauth2/v3/userinfo"


def _require_google_oauth():
    """Raise HTTP 503 when Google OAuth credentials are not configured."""
    if not settings.google_oauth_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Google OAuth is not configured. "
                "Set GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET in your .env file."
            ),
        )


@router.get("/google/login", include_in_schema=True)
def google_login(request: Request):
    """
    Redirect the browser to the Google OAuth 2.0 consent screen.

    Requires ``GOOGLE_OAUTH_CLIENT_ID`` and ``GOOGLE_OAUTH_CLIENT_SECRET`` to
    be set in ``.env``.  Returns HTTP 503 when they are absent.
    """
    _require_google_oauth()

    state = secrets.token_urlsafe(16)
    # Store the CSRF state in a short-lived cookie
    redirect_uri = f"{settings.APP_BASE_URL.rstrip('/')}/api/v1/auth/google/callback"

    params = urlencode({
        "client_id":     settings.GOOGLE_OAUTH_CLIENT_ID,
        "redirect_uri":  redirect_uri,
        "response_type": "code",
        "scope":         "openid email profile",
        "state":         state,
        "access_type":   "online",
        "prompt":        "select_account",
    })

    resp = RedirectResponse(url=f"{_GOOGLE_AUTH_URL}?{params}")
    resp.set_cookie("google_oauth_state", state, httponly=True, samesite="lax",
                    secure=settings.SESSION_COOKIE_SECURE, max_age=600)
    return resp


@router.get("/google/callback", include_in_schema=True)
def google_callback(code: str, state: str, request: Request, response: Response):
    """
    Handle the Google OAuth 2.0 authorization callback.

    Exchanges the authorisation code for an access token, retrieves the user's
    Google profile, and either logs in an existing account or returns a 403 if
    the email is not registered in ProcureFlow.
    """
    _require_google_oauth()

    import requests as _requests  # optional dependency

    # Validate CSRF state
    stored_state = request.cookies.get("google_oauth_state", "")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        raise HTTPException(status_code=400, detail="OAuth state mismatch. Please try again.")

    redirect_uri = f"{settings.APP_BASE_URL.rstrip('/')}/api/v1/auth/google/callback"

    # Exchange code for tokens
    token_resp = _requests.post(
        _GOOGLE_TOKEN_URL,
        data={
            "code":          code,
            "client_id":     settings.GOOGLE_OAUTH_CLIENT_ID,
            "client_secret": settings.GOOGLE_OAUTH_CLIENT_SECRET,
            "redirect_uri":  redirect_uri,
            "grant_type":    "authorization_code",
        },
        timeout=10,
    )
    if not token_resp.ok:
        raise HTTPException(status_code=502, detail="Failed to exchange Google authorisation code.")

    access_token = token_resp.json().get("access_token")
    if not access_token:
        raise HTTPException(status_code=502, detail="No access token in Google response.")

    # Fetch user profile
    profile_resp = _requests.get(
        _GOOGLE_USERINFO,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not profile_resp.ok:
        raise HTTPException(status_code=502, detail="Failed to fetch Google user profile.")

    google_profile = profile_resp.json()
    email = (google_profile.get("email") or "").strip().lower()
    if not email or not google_profile.get("email_verified"):
        raise HTTPException(status_code=400, detail="Google account does not have a verified email.")

    # Look up the user in ProcureFlow
    conn   = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE lower(email) = ?", (email,))
    user_row = cursor.fetchone()

    # If not registered yet but matches the bootstrap manager email, auto-provision as Manager
    if not user_row and settings.BOOTSTRAP_MANAGER_EMAIL and email == settings.BOOTSTRAP_MANAGER_EMAIL:
        manager_id = f"bootstrap-{hashlib.sha256(email.encode()).hexdigest()[:24]}"
        full_name = (google_profile.get("name") or email.split("@")[0]).strip()
        now_iso = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute(
                """INSERT INTO profiles (
                    id, email, full_name, role, department_id, status,
                    can_procure, can_receive, password_hash, first_login_at, last_login_at,
                    created_at, updated_at
                ) VALUES (?, ?, ?, 'manager', 'dept-6', 'active', 1, 1, NULL, ?, ?, ?, ?)""",
                (manager_id, email, full_name, now_iso, now_iso, now_iso, now_iso),
            )
        cursor.execute("SELECT * FROM profiles WHERE lower(email) = ?", (email,))
        user_row = cursor.fetchone()

    if not user_row:
        raise HTTPException(
            status_code=403,
            detail=(
                f"The Google account ({email}) is not registered in ProcureFlow. "
                "Ask an IBC manager to invite you first."
            ),
        )

    user = dict(user_row)
    if user["status"] == "deactivated":
        raise HTTPException(status_code=403, detail="Account deactivated. Contact your IBC administrator.")

    now_iso = datetime.now(timezone.utc).isoformat()
    google_name = (google_profile.get("name") or "").strip()
    update_fields = ["last_login_at = ?"]
    update_params = [now_iso]

    if user["status"] == "invited":
        update_fields.extend(["status = 'active'", "first_login_at = ?"])
        update_params.append(now_iso)

    if google_name and user["full_name"] == email.split("@")[0]:
        update_fields.append("full_name = ?")
        update_params.append(google_name)
        user["full_name"] = google_name

    update_params.append(user["id"])
    with conn:
        conn.execute(f"UPDATE profiles SET {', '.join(update_fields)} WHERE id = ?", update_params)

    token, csrf_token = create_session(user["id"], user["role"])

    log_audit(
        action="user.login",
        entity_type="profile",
        entity_id=user["id"],
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"role": user["role"], "method": "google_oauth"},
    )

    redirect_resp = RedirectResponse(
        url="/app/manager/" if user["role"] == "manager" else "/app/employee/"
    )
    redirect_resp.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        secure=settings.SESSION_COOKIE_SECURE,
        path="/",
    )
    # Clear OAuth state cookie
    redirect_resp.delete_cookie("google_oauth_state")
    return redirect_resp


# ══════════════════════════════════════════════════════════════════════════════
# Zoho OAuth  (enabled only when ZOHO_CLIENT_ID is configured)
# ══════════════════════════════════════════════════════════════════════════════
_ZOHO_AUTH_URL  = "https://accounts.zoho.in/oauth/v2/auth"
_ZOHO_TOKEN_URL = "https://accounts.zoho.in/oauth/v2/token"

# Scopes required for Zoho Books / Inventory item and vendor sync
_ZOHO_SCOPES = "ZohoBooks.items.READ,ZohoBooks.contacts.READ,ZohoInventory.items.READ"


def _require_zoho():
    """Raise HTTP 503 when Zoho OAuth credentials are not configured."""
    if not settings.zoho_enabled:
        raise HTTPException(
            status_code=503,
            detail=(
                "Zoho integration is not configured. "
                "Set ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET in your .env file."
            ),
        )


@router.get("/zoho/login", include_in_schema=True)
def zoho_login(request: Request, _user: dict = Depends(get_current_user)):
    """
    Redirect the browser to the Zoho OAuth consent screen (managers only).

    Requires ``ZOHO_CLIENT_ID`` and ``ZOHO_CLIENT_SECRET`` to be set in
    ``.env``.  Returns HTTP 503 when they are absent.
    """
    _require_zoho()
    require_manager(_user)

    state = secrets.token_urlsafe(16)
    params = urlencode({
        "client_id":     settings.ZOHO_CLIENT_ID,
        "redirect_uri":  settings.zoho_redirect_uri,
        "response_type": "code",
        "scope":         _ZOHO_SCOPES,
        "state":         state,
        "access_type":   "offline",
        "prompt":        "consent",
    })

    resp = RedirectResponse(url=f"{_ZOHO_AUTH_URL}?{params}")
    resp.set_cookie("zoho_oauth_state", state, httponly=True, samesite="lax",
                    secure=settings.SESSION_COOKIE_SECURE, max_age=600)
    return resp


@router.get("/zoho/callback", include_in_schema=True)
def zoho_callback(code: str, state: str, request: Request, response: Response,
                  _user: dict = Depends(get_current_user)):
    """
    Handle the Zoho OAuth authorisation callback.

    Exchanges the code for access + refresh tokens and stores them in
    ``app_settings`` so that background sync jobs can use them.
    """
    _require_zoho()
    require_manager(_user)

    import requests as _requests

    # Validate CSRF state
    stored_state = request.cookies.get("zoho_oauth_state", "")
    if not stored_state or not secrets.compare_digest(stored_state, state):
        raise HTTPException(status_code=400, detail="Zoho OAuth state mismatch. Please try again.")

    token_resp = _requests.post(
        _ZOHO_TOKEN_URL,
        data={
            "code":          code,
            "client_id":     settings.ZOHO_CLIENT_ID,
            "client_secret": settings.ZOHO_CLIENT_SECRET,
            "redirect_uri":  settings.zoho_redirect_uri,
            "grant_type":    "authorization_code",
        },
        timeout=10,
    )

    if not token_resp.ok:
        raise HTTPException(status_code=502, detail="Failed to exchange Zoho authorisation code.")

    tokens = token_resp.json()
    access_token  = tokens.get("access_token")
    refresh_token = tokens.get("refresh_token")

    if not access_token:
        raise HTTPException(status_code=502, detail="No access token in Zoho response.")

    # Persist tokens in app_settings for use by sync jobs
    conn = get_connection()
    with conn:
        for key, value in [
            ("zoho_access_token",  access_token),
            ("zoho_refresh_token", refresh_token or ""),
        ]:
            conn.execute(
                "INSERT INTO app_settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )

    log_audit(
        action="zoho.oauth.connect",
        entity_type="app_settings",
        entity_id="zoho_tokens",
        actor_id=_user["id"],
        actor_email=_user["email"],
        after_data={"zoho_connected": True},
    )

    resp = RedirectResponse(url="/app/manager/?page=settings&tab=integrations")
    resp.delete_cookie("zoho_oauth_state")
    return resp
