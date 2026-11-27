"""
IBC ProcureFlow — FastAPI application entry point.

Registers all API routers, mounts static files, provides HTML shell routes for
the manager and employee SPA workspaces, and applies a lightweight security
middleware that enforces CORS origin checks and CSRF validation on every
state-mutating request.
"""

from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .config import settings, BASE_DIR
from .core.db import init_db
from .core.security import get_current_user_from_token, verify_csrf

# ── API routers ────────────────────────────────────────────────────────────────
from .modules.auth         import router as auth_router
from .modules.users        import router as users_router
from .modules.masters      import router as masters_router
from .modules.requisitions import router as requisitions_router
from .modules.enquiries    import router as enquiries_router
from .modules.purchase_orders import router as po_router
from .modules.grns         import router as grns_router
from .modules.lrs          import router as lrs_router
from .modules.reports      import router as reports_router
from .modules.settings     import router as settings_router
from .modules.notifications import router as notif_router
from .modules.audit        import router as audit_router
from .modules.exports      import router as exports_router

STATIC_DIR = Path(__file__).resolve().parent / "static"

# ── FastAPI app ────────────────────────────────────────────────────────────────
_is_prod = settings.APP_ENV.lower() in {"production", "prod"}

app = FastAPI(
    title=settings.APP_NAME,
    description="Industrial Bolt Centre (IBC) ProcureFlow — procurement management system.",
    version="1.0.0",
    # Disable interactive docs in production.
    docs_url=None    if _is_prod else "/docs",
    redoc_url=None   if _is_prod else "/redoc",
    openapi_url=None if _is_prod else "/openapi.json",
)


# ── Security middleware ────────────────────────────────────────────────────────
@app.middleware("http")
async def security_middleware(request: Request, call_next):
    """
    Applied to every request:

    - Rejects cross-origin state-mutating requests (POST/PUT/PATCH/DELETE)
      whose ``Origin`` header doesn't match the server ``Host``.
    - Enforces CSRF token validation for authenticated mutating requests.
    - Adds common security response headers on all responses.
    - Adds ``Strict-Transport-Security`` in production.
    """
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        origin = request.headers.get("origin") or request.headers.get("referer")
        if origin:
            from urllib.parse import urlsplit
            origin_netloc = urlsplit(origin).netloc.lower().split(":")[0]
            host_netloc = request.headers.get("host", "").lower().split(":")[0]
            if origin_netloc and host_netloc and origin_netloc != host_netloc:
                return JSONResponse(
                    status_code=403,
                    content={"detail": "Cross-origin request rejected."}
                )

        token = request.cookies.get(settings.SESSION_COOKIE_NAME)
        user  = get_current_user_from_token(token)

        # Paths that are allowed without an active session
        _open_paths = {
            "/api/v1/auth/login",
            "/api/v1/auth/accept-invitation",
            "/api/v1/auth/google/callback",
        }
        _is_open = request.url.path in _open_paths
        if user:
            try:
                verify_csrf(request, user)
            except HTTPException as exc:
                return JSONResponse(
                    status_code=exc.status_code,
                    content={"detail": exc.detail}
                )
        elif not _is_open:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required."}
            )

    response = await call_next(request)

    # Security headers
    response.headers.setdefault("X-Content-Type-Options",  "nosniff")
    response.headers.setdefault("X-Frame-Options",         "DENY")
    response.headers.setdefault("Referrer-Policy",         "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy",      "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'; "
        "form-action 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "script-src 'self' 'unsafe-inline'; connect-src 'self'",
    )
    if _is_prod:
        response.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")

    return response


# ── Lifecycle ──────────────────────────────────────────────────────────────────
@app.on_event("startup")
def on_startup():
    """Initialise the database schema and seed reference data on first run."""
    init_db()


# ── API routers ────────────────────────────────────────────────────────────────
app.include_router(auth_router)
app.include_router(users_router)
app.include_router(masters_router)
app.include_router(requisitions_router)
app.include_router(enquiries_router)
app.include_router(po_router)
app.include_router(grns_router)
app.include_router(lrs_router)
app.include_router(reports_router)
app.include_router(settings_router)
app.include_router(notif_router)
app.include_router(audit_router)
app.include_router(exports_router)


# ── Static files ───────────────────────────────────────────────────────────────
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── HTML shell routes ──────────────────────────────────────────────────────────
@app.get("/", include_in_schema=False)
def index_redirect(request: Request):
    """Redirect authenticated users to their workspace; unauthenticated to login."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user  = get_current_user_from_token(token)
    if not user:
        return RedirectResponse(url="/login")
    return RedirectResponse(
        url="/app/manager/" if user["role"] == "manager" else "/app/employee/"
    )


@app.get("/login", response_class=HTMLResponse, include_in_schema=False)
def login_page(request: Request):
    """Serve the login page; redirect already-authenticated users to their workspace."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user  = get_current_user_from_token(token)
    if user:
        return RedirectResponse(
            url="/app/manager/" if user["role"] == "manager" else "/app/employee/"
        )
    login_file = STATIC_DIR / "login.html"
    if login_file.exists():
        return HTMLResponse(content=login_file.read_text(encoding="utf-8"))
    return HTMLResponse("<h3>Login page loading…</h3>")


@app.get("/accept-invitation", response_class=HTMLResponse, include_in_schema=False)
def accept_invitation_page():
    """Serve the invitation acceptance page."""
    invite_file = STATIC_DIR / "invite.html"
    return HTMLResponse(content=invite_file.read_text(encoding="utf-8"))


@app.get("/app/manager/", response_class=HTMLResponse, include_in_schema=False)
def manager_workspace(request: Request):
    """Serve the Manager SPA shell; redirects if unauthenticated or wrong role."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user  = get_current_user_from_token(token)
    if not user:
        return RedirectResponse(url="/login")
    if user["role"] != "manager":
        return RedirectResponse(url="/app/employee/")
    mgr_file = STATIC_DIR / "manager" / "index.html"
    return HTMLResponse(content=mgr_file.read_text(encoding="utf-8"))


@app.get("/app/employee/", response_class=HTMLResponse, include_in_schema=False)
def employee_workspace(request: Request):
    """Serve the Employee SPA shell; redirects if unauthenticated."""
    token = request.cookies.get(settings.SESSION_COOKIE_NAME)
    user  = get_current_user_from_token(token)
    if not user:
        return RedirectResponse(url="/login")
    emp_file = STATIC_DIR / "employee" / "index.html"
    return HTMLResponse(content=emp_file.read_text(encoding="utf-8"))


# ── Utility endpoints ──────────────────────────────────────────────────────────
@app.get("/health", tags=["system"])
def health_check():
    """
    Liveness probe.

    Returns the application name, environment, and enabled integration flags so
    monitoring tools and the frontend can confirm the server is reachable and
    discover which optional features are active.
    """
    return {
        "status": "ok",
        "app":    settings.APP_NAME,
        "env":    settings.APP_ENV,
        "integrations": {
            "google_oauth": settings.google_oauth_enabled,
            "supabase":     settings.supabase_enabled,
            "supabase_database": settings.postgres_enabled,
            "zoho":         settings.zoho_enabled,
        },
    }
