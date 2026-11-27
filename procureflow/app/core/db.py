"""
Database layer — IBC ProcureFlow.

Provides:
- ``get_connection()``  — returns a new SQLite connection with WAL mode and
  foreign-key enforcement enabled.
- ``init_db()``         — creates all tables (idempotent) and seeds reference data.
- ``seed_initial_data()`` — called once inside ``init_db()``; inserts departments,
  demo users (DEMO_MODE only), items, vendors, and default app settings using
  ``INSERT OR IGNORE`` so reruns are safe.

Supabase PostgreSQL is the primary database when ``SUPABASE_DB_URL`` is set.
After each PostgreSQL commit, the local SQLite mirror is refreshed synchronously.
"""

import hashlib
import secrets
import json
import sqlite3
import re
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlsplit

from ..config import settings, BASE_DIR
from .db_tables import APP_TABLES

try:
    import psycopg2
    from psycopg2.extras import DictCursor
except ImportError:  # SQLite-only local installations need no PostgreSQL driver.
    psycopg2 = None
    DictCursor = None


# ── Path resolution ────────────────────────────────────────────────────────────
def _database_path() -> Path:
    """
    Derive the SQLite file path from ``settings.DATABASE_URL``.

    Handles both POSIX-style absolute paths and SQLAlchemy-style Windows paths
    (``/C:/...``).  Relative paths are resolved against the project root.
    """
    url = settings.DATABASE_URL
    if not url.startswith("sqlite:///"):
        raise RuntimeError(
            "DATABASE_URL must be a SQLite URL when Supabase is not configured."
        )
    raw_path = url[len("sqlite:///"):]
    # SQLAlchemy emits /C:/... on Windows — strip the leading slash.
    if raw_path.startswith("/") and len(raw_path) > 3 and raw_path[2] == ":":
        raw_path = raw_path[1:]
    path = Path(raw_path)
    if not path.is_absolute():
        path = BASE_DIR / path
    return path.resolve()


DB_PATH = _database_path() if (not settings.postgres_enabled or psycopg2 is None) else None
SQLITE_BACKUP_PATH = settings.SQLITE_BACKUP_PATH
if not SQLITE_BACKUP_PATH.is_absolute():
    SQLITE_BACKUP_PATH = (BASE_DIR / SQLITE_BACKUP_PATH).resolve()

_BACKUP_SYNC_LOCK = threading.Lock()
_BACKUP_SYNC_STATE = threading.local()


@contextmanager
def _suppress_backup_sync():
    previous = getattr(_BACKUP_SYNC_STATE, "suppressed", False)
    _BACKUP_SYNC_STATE.suppressed = True
    try:
        yield
    finally:
        _BACKUP_SYNC_STATE.suppressed = previous


def _postgres_sql(sql: str) -> str:
    """Translate the small set of SQLite SQL extensions used by this app."""
    sql = re.sub(r"\bMAX\(\s*0\s*,", "GREATEST(0,", sql, flags=re.I)
    sql = re.sub(r"\bORDER\s+BY\s+el\.rowid\b", "ORDER BY el.ctid", sql, flags=re.I)
    sql = re.sub(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", "BIGSERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\bBLOB\b", "BYTEA", sql, flags=re.I)
    sql = re.sub(r"\bLIKE\b", "ILIKE", sql, flags=re.I)
    # All remaining question marks in application SQL are positional parameters.
    sql = sql.replace("?", "%s")
    return sql


class PostgresCursor(DictCursor if DictCursor is not None else object):
    def execute(self, query, vars=None):
        original = query
        is_ignore_insert = bool(re.match(r"\s*INSERT\s+OR\s+IGNORE\s+INTO\b", original, flags=re.I))
        if is_ignore_insert:
            query = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", query, flags=re.I)
        # SQLite's audit triggers are created with a PostgreSQL equivalent below.
        if re.match(r"\s*CREATE\s+TRIGGER\s+IF\s+NOT\s+EXISTS\s+audit_log_no_(update|delete)", query, flags=re.I):
            event = "UPDATE" if "no_update" in query.lower() else "DELETE"
            trigger = "audit_log_no_update" if event == "UPDATE" else "audit_log_no_delete"
            super().execute("CREATE OR REPLACE FUNCTION audit_log_reject_mutation() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit_log is append-only'; END; $$")
            super().execute(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_trigger "
                f"WHERE tgname = '{trigger}' AND tgrelid = 'audit_log'::regclass) THEN "
                f"CREATE TRIGGER {trigger} BEFORE {event} ON audit_log "
                "FOR EACH ROW EXECUTE FUNCTION audit_log_reject_mutation(); END IF; END $$;"
            )
            return self
        query = _postgres_sql(query)
        if is_ignore_insert:
            query = query.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        super().execute(query, vars)
        return self


class PostgresConnection(psycopg2.extensions.connection if psycopg2 else object):
    def cursor(self, *args, **kwargs):
        kwargs.setdefault("cursor_factory", PostgresCursor)
        return super().cursor(*args, **kwargs)

    def execute(self, query, vars=None):
        cursor = self.cursor()
        cursor.execute(query, vars)
        return cursor

    def commit(self):
        super().commit()
        if not getattr(_BACKUP_SYNC_STATE, "suppressed", False):
            sync_local_sqlite_mirror(after_commit=True)

    def __exit__(self, exc_type, exc_value, traceback):
        if exc_type is None:
            self.commit()
        else:
            self.rollback()
        return False


def _table_columns(cursor, table_name: str) -> set[str]:
    if isinstance(cursor, PostgresCursor):
        rows = cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ?",
            (table_name,),
        ).fetchall()
        return {row[0] for row in rows}
    return {row[1] for row in cursor.execute(f"PRAGMA table_info({table_name})").fetchall()}


# ── Connection factory ─────────────────────────────────────────────────────────
def get_connection():
    """
    Open a connection to the configured primary database.

    Supabase mode uses TLS and the private ``procure`` schema. SQLite mode
    enables named/indexed rows, foreign keys, WAL, and a 30-second lock wait.

    Callers are responsible for closing the connection (or using it as a context
    manager).
    """
    if settings.postgres_enabled:
        if psycopg2 is None:
            if settings.APP_ENV.lower() not in {"local", "dev", "development"}:
                raise RuntimeError("PostgreSQL mode requires psycopg2-binary. Install requirements.txt.")
        else:
            url = settings.SUPABASE_DB_URL
            if url.startswith("postgres://"):
                url = "postgresql://" + url[len("postgres://"):]
            if urlsplit(url).port == 6543:
                raise RuntimeError(
                    "Use Supabase direct or session-pooler connection mode. "
                    "Transaction pooling does not preserve this app's private-schema search path."
                )
            connect_options = {"connection_factory": PostgresConnection, "connect_timeout": 10}
            if "sslmode=" not in url.lower():
                connect_options["sslmode"] = "require"
            conn = psycopg2.connect(url, **connect_options)
            with _suppress_backup_sync():
                with conn.cursor() as cursor:
                    cursor.execute("CREATE SCHEMA IF NOT EXISTS procure")
                    cursor.execute("SET search_path TO procure, public")
                conn.commit()
            return conn
    conn = sqlite3.connect(str(DB_PATH), timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


# ── Schema initialisation ──────────────────────────────────────────────────────
def init_db(conn=None, *, seed: Optional[bool] = None):
    """
    Create all application tables (if they do not already exist) and seed
    reference data.

    Safe to call on every application start-up — all ``CREATE TABLE`` statements
    use ``IF NOT EXISTS`` and seed inserts use ``INSERT OR IGNORE``.
    """
    is_primary_connection = conn is None
    if conn is None:
        conn = get_connection()
    is_postgres = isinstance(conn, PostgresConnection)
    with conn:
        cursor = conn.cursor()

        # 1. Departments
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS departments (
            id         TEXT PRIMARY KEY,
            name       TEXT UNIQUE NOT NULL,
            code       TEXT NOT NULL,
            active     INTEGER NOT NULL DEFAULT 1,
            created_at TEXT NOT NULL
        );
        """)
        # 2. User Profiles
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS profiles (
            id              TEXT PRIMARY KEY,
            auth_user_id    TEXT UNIQUE,          -- reserved for future Supabase Auth UID
            email           TEXT UNIQUE NOT NULL,
            full_name       TEXT NOT NULL,
            role            TEXT NOT NULL CHECK(role IN ('manager', 'employee')),
            department_id   TEXT,
            status          TEXT NOT NULL CHECK(status IN ('invited', 'active', 'deactivated')),
            can_procure     INTEGER NOT NULL DEFAULT 0,
            can_receive     INTEGER NOT NULL DEFAULT 0,
            phone           TEXT,
            password_hash   TEXT,
            invited_by      TEXT,
            invited_at      TEXT,
            first_login_at  TEXT,
            last_login_at   TEXT,
            created_at      TEXT NOT NULL,
            updated_at      TEXT NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id)
        );
        """)

        # 3. Sessions
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            token         TEXT PRIMARY KEY,     -- SHA-256 hash of the raw cookie value
            user_id       TEXT NOT NULL,
            csrf_token    TEXT NOT NULL,
            expires_at    TEXT NOT NULL,
            created_at    TEXT NOT NULL,
            last_activity TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
        """)

        # 4. Invitation Tokens
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS invitation_tokens (
            token_hash TEXT PRIMARY KEY,         -- SHA-256 hash of the raw invite token
            user_id    TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
        """)

        # Purge any legacy sessions stored with wrong-length tokens
        cursor.execute("DELETE FROM sessions WHERE length(token) != 64")

        # 5. Items Master
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS items (
            id                  TEXT PRIMARY KEY,
            zoho_item_id        TEXT UNIQUE,     -- populated when synced from Zoho
            sku                 TEXT UNIQUE NOT NULL,
            name                TEXT NOT NULL,
            description         TEXT,
            part_number         TEXT,
            hsn_code            TEXT,
            unit                TEXT NOT NULL,
            default_rate        REAL NOT NULL DEFAULT 0.0,
            default_tax_percent REAL NOT NULL DEFAULT 18.0,
            currency            TEXT NOT NULL DEFAULT 'INR',
            active              INTEGER NOT NULL DEFAULT 1,
            source              TEXT NOT NULL DEFAULT 'manual',  -- 'manual' | 'zoho'
            custom_values       TEXT DEFAULT '{}',
            created_at          TEXT NOT NULL,
            updated_at          TEXT NOT NULL
        );
        """)

        # 6. Vendors Master
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            id                    TEXT PRIMARY KEY,
            zoho_contact_id       TEXT UNIQUE,   -- populated when synced from Zoho
            name                  TEXT NOT NULL,
            contact_person        TEXT,
            email                 TEXT,
            phone                 TEXT,
            whatsapp              TEXT,
            gstin                 TEXT,
            state_code            TEXT,
            address               TEXT,
            default_payment_terms TEXT,
            active                INTEGER NOT NULL DEFAULT 1,
            source                TEXT NOT NULL DEFAULT 'manual',
            custom_values         TEXT DEFAULT '{}',
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL
        );
        """)

        # 7. Document Sequences (auto-numbering per fiscal year)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS document_sequences (
            doc_type    TEXT NOT NULL,
            fy_label    TEXT NOT NULL,
            last_number INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (doc_type, fy_label)
        );
        """)

        # 8. Purchase Requisitions
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS requisitions (
            id            TEXT PRIMARY KEY,
            number        TEXT UNIQUE NOT NULL,
            department_id TEXT NOT NULL,
            requested_by  TEXT NOT NULL,
            required_by   TEXT NOT NULL,
            purpose       TEXT NOT NULL,
            priority      TEXT NOT NULL DEFAULT 'normal',
            status        TEXT NOT NULL CHECK(status IN (
                              'draft', 'submitted', 'approved', 'rejected',
                              'in_progress', 'converted', 'closed', 'cancelled')),
            submitted_at  TEXT,
            decided_by    TEXT,
            decided_at    TEXT,
            decision_note TEXT,
            version       INTEGER NOT NULL DEFAULT 1,
            custom_values TEXT DEFAULT '{}',
            deleted_at    TEXT,
            created_at    TEXT NOT NULL,
            updated_at    TEXT NOT NULL,
            FOREIGN KEY (department_id) REFERENCES departments(id),
            FOREIGN KEY (requested_by)  REFERENCES profiles(id),
            FOREIGN KEY (decided_by)    REFERENCES profiles(id)
        );
        """)
        if "approval_required" not in _table_columns(cursor, "requisitions"):
            cursor.execute("ALTER TABLE requisitions ADD COLUMN approval_required INTEGER NOT NULL DEFAULT 1")

        # 9. Requisition Lines
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS requisition_lines (
            id                   TEXT PRIMARY KEY,
            requisition_id       TEXT NOT NULL,
            item_id              TEXT,
            item_name_snapshot   TEXT NOT NULL,
            part_number          TEXT,
            description          TEXT,
            quantity             REAL NOT NULL CHECK(quantity > 0),
            unit                 TEXT NOT NULL,
            notes                TEXT,
            procure_status       TEXT NOT NULL DEFAULT 'open'
                                      CHECK(procure_status IN ('open', 'enquired', 'ordered')),
            FOREIGN KEY (requisition_id) REFERENCES requisitions(id) ON DELETE CASCADE,
            FOREIGN KEY (item_id)        REFERENCES items(id)
        );
        """)

        # 10. Enquiries (RFQs)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS enquiries (
            id                TEXT PRIMARY KEY,
            number            TEXT UNIQUE NOT NULL,
            requisition_id    TEXT,
            status            TEXT NOT NULL CHECK(status IN (
                                  'draft', 'sent', 'quoted', 'awarded', 'converted', 'cancelled')),
            subject           TEXT NOT NULL,
            terms             TEXT,
            awarded_vendor_id TEXT,
            created_by        TEXT NOT NULL,
            version           INTEGER NOT NULL DEFAULT 1,
            custom_values     TEXT DEFAULT '{}',
            created_at        TEXT NOT NULL,
            updated_at        TEXT NOT NULL,
            FOREIGN KEY (requisition_id)    REFERENCES requisitions(id),
            FOREIGN KEY (awarded_vendor_id) REFERENCES vendors(id),
            FOREIGN KEY (created_by)        REFERENCES profiles(id)
        );
        """)

        # 11. Enquiry Lines
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS enquiry_lines (
            id                   TEXT PRIMARY KEY,
            enquiry_id           TEXT NOT NULL,
            requisition_line_id  TEXT,
            item_id              TEXT,
            item_name_snapshot   TEXT NOT NULL,
            quantity             REAL NOT NULL,
            unit                 TEXT NOT NULL,
            FOREIGN KEY (enquiry_id)          REFERENCES enquiries(id) ON DELETE CASCADE,
            FOREIGN KEY (requisition_line_id) REFERENCES requisition_lines(id),
            FOREIGN KEY (item_id)             REFERENCES items(id)
        );
        """)

        # 12. Enquiry Vendors (vendor legs of an RFQ)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS enquiry_vendors (
            id                 TEXT PRIMARY KEY,
            enquiry_id         TEXT NOT NULL,
            vendor_id          TEXT NOT NULL,
            sent_at            TEXT,
            quote_received_at  TEXT,
            quote_reference    TEXT,
            quote_valid_until  TEXT,
            payment_terms      TEXT,
            delivery_terms     TEXT,
            FOREIGN KEY (enquiry_id) REFERENCES enquiries(id) ON DELETE CASCADE,
            FOREIGN KEY (vendor_id)  REFERENCES vendors(id)
        );
        """)

        # 13. Quote Lines (vendor's quoted prices per line)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS quote_lines (
            id                TEXT PRIMARY KEY,
            enquiry_vendor_id TEXT NOT NULL,
            enquiry_line_id   TEXT NOT NULL,
            quoted_rate       REAL NOT NULL DEFAULT 0.0,
            tax_percent       REAL NOT NULL DEFAULT 18.0,
            lead_time_days    INTEGER DEFAULT 0,
            notes             TEXT,
            FOREIGN KEY (enquiry_vendor_id) REFERENCES enquiry_vendors(id) ON DELETE CASCADE,
            FOREIGN KEY (enquiry_line_id)   REFERENCES enquiry_lines(id)   ON DELETE CASCADE
        );
        """)

        # 14. Purchase Orders
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS purchase_orders (
            id                    TEXT PRIMARY KEY,
            number                TEXT UNIQUE NOT NULL,
            revision              INTEGER NOT NULL DEFAULT 1,
            vendor_id             TEXT NOT NULL,
            source_type           TEXT NOT NULL CHECK(source_type IN ('requisition', 'enquiry', 'manual')),
            source_requisition_id TEXT,
            source_enquiry_id     TEXT,
            status                TEXT NOT NULL CHECK(status IN (
                                      'draft', 'pending_approval', 'approved', 'rejected',
                                      'sent', 'part_received', 'received', 'closed', 'cancelled')),
            order_date            TEXT NOT NULL,
            delivery_date         TEXT,
            payment_terms         TEXT,
            delivery_terms        TEXT,
            ship_to               TEXT,
            bill_to               TEXT,
            currency              TEXT NOT NULL DEFAULT 'INR',
            subtotal              REAL NOT NULL DEFAULT 0.0,
            tax_total             REAL NOT NULL DEFAULT 0.0,
            grand_total           REAL NOT NULL DEFAULT 0.0,
            created_by            TEXT NOT NULL,
            submitted_at          TEXT,
            approved_by           TEXT,
            approved_at           TEXT,
            approval_note         TEXT,
            sent_at               TEXT,
            closed_at             TEXT,
            closed_by             TEXT,
            close_reason          TEXT,
            cancel_reason         TEXT,
            version               INTEGER NOT NULL DEFAULT 1,
            custom_values         TEXT DEFAULT '{}',
            deleted_at            TEXT,
            created_at            TEXT NOT NULL,
            updated_at            TEXT NOT NULL,
            FOREIGN KEY (vendor_id)             REFERENCES vendors(id),
            FOREIGN KEY (source_requisition_id) REFERENCES requisitions(id),
            FOREIGN KEY (source_enquiry_id)     REFERENCES enquiries(id),
            FOREIGN KEY (created_by)            REFERENCES profiles(id),
            FOREIGN KEY (approved_by)           REFERENCES profiles(id),
            FOREIGN KEY (closed_by)             REFERENCES profiles(id)
        );
        """)
        if "approval_required" not in _table_columns(cursor, "purchase_orders"):
            cursor.execute("ALTER TABLE purchase_orders ADD COLUMN approval_required INTEGER NOT NULL DEFAULT 1")

        # 14a. Transport LR records linked to purchase orders
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS lrs (
            id TEXT PRIMARY KEY,
            number TEXT NOT NULL,
            purchase_order_id TEXT NOT NULL,
            lr_date TEXT,
            transporter_name TEXT,
            transporter_contact TEXT,
            payment_status TEXT NOT NULL DEFAULT 'unpaid' CHECK(payment_status IN ('unpaid', 'partial', 'paid')),
            payment_amount REAL NOT NULL DEFAULT 0,
            payment_date TEXT,
            payment_reference TEXT,
            notes TEXT,
            document_filename TEXT,
            document_data BLOB,
            created_by TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(purchase_order_id, number),
            FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (created_by) REFERENCES profiles(id)
        )
        """)
        lr_columns = _table_columns(cursor, "lrs")
        if "document_filename" not in lr_columns:
            cursor.execute("ALTER TABLE lrs ADD COLUMN document_filename TEXT")
        if "document_data" not in lr_columns:
            cursor.execute("ALTER TABLE lrs ADD COLUMN document_data BLOB")

        # 15. PO Lines
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS po_lines (
            id                   TEXT PRIMARY KEY,
            purchase_order_id    TEXT NOT NULL,
            requisition_line_id  TEXT,
            item_id              TEXT,
            item_name_snapshot   TEXT NOT NULL,
            part_number          TEXT,
            description          TEXT,
            quantity             REAL NOT NULL CHECK(quantity > 0),
            unit                 TEXT NOT NULL,
            rate                 REAL NOT NULL DEFAULT 0.0,
            tax_percent          REAL NOT NULL DEFAULT 18.0,
            line_subtotal        REAL NOT NULL DEFAULT 0.0,
            tax_amount           REAL NOT NULL DEFAULT 0.0,
            line_total           REAL NOT NULL DEFAULT 0.0,
            received_qty         REAL NOT NULL DEFAULT 0.0,
            FOREIGN KEY (purchase_order_id)   REFERENCES purchase_orders(id) ON DELETE CASCADE,
            FOREIGN KEY (requisition_line_id) REFERENCES requisition_lines(id),
            FOREIGN KEY (item_id)             REFERENCES items(id)
        );
        """)

        # 16. Goods Receipt Notes (GRN)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS grns (
            id                   TEXT PRIMARY KEY,
            number               TEXT UNIQUE NOT NULL,
            purchase_order_id    TEXT NOT NULL,
            received_date        TEXT NOT NULL,
            received_by          TEXT NOT NULL,
            lr_number            TEXT,
            transporter          TEXT,
            vehicle_no           TEXT,
            supplier_invoice_no  TEXT,
            supplier_invoice_date TEXT,
            remarks              TEXT,
            status               TEXT NOT NULL DEFAULT 'posted'
                                      CHECK(status IN ('posted', 'voided')),
            void_reason          TEXT,
            voided_by            TEXT,
            custom_values        TEXT DEFAULT '{}',
            created_at           TEXT NOT NULL,
            FOREIGN KEY (purchase_order_id) REFERENCES purchase_orders(id),
            FOREIGN KEY (received_by)       REFERENCES profiles(id),
            FOREIGN KEY (voided_by)         REFERENCES profiles(id)
        );
        """)

        # 17. GRN Lines
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS grn_lines (
            id               TEXT PRIMARY KEY,
            grn_id           TEXT NOT NULL,
            po_line_id       TEXT NOT NULL,
            received_qty     REAL NOT NULL,
            accepted_qty     REAL NOT NULL,
            rejected_qty     REAL NOT NULL DEFAULT 0.0,
            rejection_reason TEXT,
            FOREIGN KEY (grn_id)     REFERENCES grns(id)     ON DELETE CASCADE,
            FOREIGN KEY (po_line_id) REFERENCES po_lines(id)
        );
        """)

        # 18. Custom Field Definitions
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS custom_field_definitions (
            id         TEXT PRIMARY KEY,
            module     TEXT NOT NULL CHECK(module IN (
                           'requisition', 'enquiry', 'purchase_order', 'grn', 'item', 'vendor')),
            field_key  TEXT NOT NULL,
            label      TEXT NOT NULL,
            field_type TEXT NOT NULL CHECK(field_type IN ('text', 'number', 'date', 'boolean', 'select')),
            options    TEXT DEFAULT '[]',
            required   INTEGER NOT NULL DEFAULT 0,
            active     INTEGER NOT NULL DEFAULT 1,
            sort_order INTEGER NOT NULL DEFAULT 0,
            UNIQUE (module, field_key)
        );
        """)

        # 19. User Notifications
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS notifications (
            id          TEXT PRIMARY KEY,
            user_id     TEXT NOT NULL,
            type        TEXT NOT NULL,
            title       TEXT NOT NULL,
            body        TEXT NOT NULL,
            entity_type TEXT,
            entity_id   TEXT,
            read_at     TEXT,
            created_at  TEXT NOT NULL,
            FOREIGN KEY (user_id) REFERENCES profiles(id) ON DELETE CASCADE
        );
        """)

        # 20. App Settings (key-value store)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        """)

        # 21. Audit Log (append-only)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            occurred_at TEXT NOT NULL,
            actor_id    TEXT,
            actor_email TEXT,
            action      TEXT NOT NULL,
            entity_type TEXT NOT NULL,
            entity_id   TEXT NOT NULL,
            before_data TEXT,
            after_data  TEXT,
            ip          TEXT,
            user_agent  TEXT,
            request_id  TEXT
        );
        """)

        # Enforce append-only audit log via triggers
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS audit_log_no_update
        BEFORE UPDATE ON audit_log BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;
        """)
        cursor.execute("""
        CREATE TRIGGER IF NOT EXISTS audit_log_no_delete
        BEFORE DELETE ON audit_log BEGIN
            SELECT RAISE(ABORT, 'audit_log is append-only');
        END;
        """)

        # Seed essential reference data (departments, settings, bootstrap manager).
        # Sample items and vendors are seeded only for local development (not is_postgres).
        should_seed = seed if seed is not None else is_primary_connection
        if should_seed:
            seed_initial_data(cursor, is_postgres=is_postgres)


def sync_local_sqlite_mirror(*, after_commit: bool = False) -> None:
    """Refresh the local SQLite mirror after every committed Supabase transaction."""
    if not settings.postgres_enabled or getattr(_BACKUP_SYNC_STATE, "suppressed", False):
        return
    if not _BACKUP_SYNC_LOCK.acquire(blocking=True):
        raise RuntimeError("Could not acquire the local SQLite backup lock.")

    temporary = SQLITE_BACKUP_PATH.with_suffix(SQLITE_BACKUP_PATH.suffix + ".sync.tmp")
    pg = None
    target = None
    try:
        SQLITE_BACKUP_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _suppress_backup_sync():
            pg = get_connection()
            pg.set_session(isolation_level="REPEATABLE READ", readonly=True)
            target = sqlite3.connect(str(temporary), timeout=30.0)
            target.execute("PRAGMA foreign_keys = ON")
            target.execute("PRAGMA journal_mode = DELETE")
            init_db(target, seed=False)
            for table in APP_TABLES:
                rows = pg.execute(f'SELECT * FROM "{table}"').fetchall()
                if not rows:
                    continue
                columns = list(rows[0].keys())
                column_sql = ", ".join(f'"{column}"' for column in columns)
                value_sql = ", ".join("?" for _ in columns)
                target.executemany(
                    f'INSERT INTO "{table}" ({column_sql}) VALUES ({value_sql})',
                    [tuple(bytes(value) if isinstance(value, memoryview) else value
                           for value in (row[column] for column in columns)) for row in rows],
                )
            target.commit()
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"Local SQLite mirror integrity check failed: {check}")
            target.close()
            target = None
            pg.close()
            pg = None
        temporary.replace(SQLITE_BACKUP_PATH)
    except Exception as exc:
        message = (
            "Supabase committed the change, but the synchronous SQLite backup refresh failed. "
            if after_commit else "The local SQLite mirror refresh failed. "
        )
        raise RuntimeError(message + "The next app startup will retry the full mirror refresh.") from exc
    finally:
        if target is not None:
            target.close()
        if pg is not None:
            pg.close()
        temporary.unlink(missing_ok=True)
        _BACKUP_SYNC_LOCK.release()


# ── Seed data ──────────────────────────────────────────────────────────────────
def seed_initial_data(cursor, *, is_postgres: bool = False):
    """
    Insert default departments, demo users, items, vendors, and app settings.

    All inserts use ``INSERT OR IGNORE`` so this function is safe to call on
    every startup without duplicating rows.

    The optional local demo manager and configured bootstrap manager are seeded
    only when explicitly configured; passwords are stored as PBKDF2 hashes.
    """
    now = datetime.now(timezone.utc).isoformat()

    # ── Departments ──────────────────────────────────────────────────────────
    departments = [
        ("dept-1", "Maintenance",       "MAINT"),
        ("dept-2", "Production",        "PROD"),
        ("dept-3", "Quality Control",   "QC"),
        ("dept-4", "Tool Room & Die",   "TRD"),
        ("dept-5", "Logistics & Stores","STORES"),
        ("dept-6", "Administration",    "ADMIN"),
    ]
    for d_id, name, code in departments:
        cursor.execute(
            "INSERT OR IGNORE INTO departments (id, name, code, active, created_at) VALUES (?, ?, ?, 1, ?)",
            (d_id, name, code, now),
        )

    # Retire the old public fixture accounts in existing local databases.
    # New demo access uses only the private credentials supplied in the environment.
    legacy_demo_ids = ("user-mgr-1", "user-mgr-2", "user-emp-1", "user-emp-2")
    cursor.execute(
        "UPDATE profiles SET status = 'deactivated', password_hash = NULL, updated_at = ? "
        "WHERE id IN (?, ?, ?, ?)",
        (now, *legacy_demo_ids),
    )
    cursor.execute(
        "DELETE FROM sessions WHERE user_id IN (?, ?, ?, ?)", legacy_demo_ids
    )

    def seed_manager(email: str, password: str, source: str) -> None:
        if not email or not password:
            return
        existing = cursor.execute(
            "SELECT id, role, status FROM profiles WHERE lower(email) = ?", (email.lower(),)
        ).fetchone()
        if existing:
            if existing["role"] != "manager":
                raise RuntimeError(f"Configured {source} email is already assigned to a non-manager account.")
            if existing["id"] in legacy_demo_ids and existing["status"] == "deactivated":
                salt = secrets.token_bytes(16)
                digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000).hex()
                password_hash = f"pbkdf2_sha256$600000${salt.hex()}${digest}"
                cursor.execute(
                    "UPDATE profiles SET status = 'active', password_hash = ?, updated_at = ? WHERE id = ?",
                    (password_hash, now, existing["id"]),
                )
            return
        salt = secrets.token_bytes(16)
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 600_000).hex()
        password_hash = f"pbkdf2_sha256$600000${salt.hex()}${digest}"
        manager_id = f"bootstrap-{hashlib.sha256(email.lower().encode()).hexdigest()[:24]}"
        cursor.execute(
            """INSERT INTO profiles (
                id, email, full_name, role, department_id, status,
                can_procure, can_receive, password_hash, created_at, updated_at
            ) VALUES (?, ?, ?, 'manager', 'dept-6', 'active', 1, 1, ?, ?, ?)""",
            (manager_id, email.lower(), email.split("@")[0], password_hash, now, now),
        )

    # Local demo mode: only when DEMO_MODE=true on local SQLite
    if settings.DEMO_MODE and not is_postgres:
        seed_manager(settings.DEMO_MANAGER_EMAIL, settings.DEMO_MANAGER_PASSWORD, "demo manager")
    # Production / Bootstrap manager: seeded from Render secret keys or .env
    seed_manager(settings.BOOTSTRAP_MANAGER_EMAIL, settings.BOOTSTRAP_MANAGER_PASSWORD, "bootstrap manager")

    # ── App Settings (required on all environments for PDF headers, tolerance, etc.) ──
    default_settings = {
        "grn_tolerance_percent": "0.0",
        "company_name":          "Industrial Bolt Centre",
        "company_address":       "Works: Plot 18, Industrial Estate, Phase 1, Mumbai - 400086",
        "company_gstin":         "27AAACI9999M1Z5",
        "company_phone":         "+91 22 2500 1122",
        "company_email":         "purchase@ibcbolts.com",
    }
    for key, value in default_settings.items():
        cursor.execute(
            "INSERT OR IGNORE INTO app_settings (key, value) VALUES (?, ?)",
            (key, value),
        )

    # ── Sample Items & Vendors (seeded only for local development on SQLite) ──
    if not is_postgres:
        initial_items = [
            ("item-1", "HB-1260", "Hex Bolt M12 x 60 Grade 8.8",          "High tensile hex bolt with zinc coating",          "HB-1260", "7318", "Nos",   28.50, 18.0),
            ("item-2", "HN-1200", "Hex Nut M12 Grade 8",                   "Zinc electroplated hex nut ISO 4032",              "HN-1200", "7318", "Nos",    8.20, 18.0),
            ("item-3", "FW-1200", "Flat Washer M12 Din 125A",              "Plain carbon steel flat washer zinc plated",       "FW-1200", "7318", "Nos",    3.10, 18.0),
            ("item-4", "CS-0840", "Socket Head Cap Screw M8 x 40",         "Alloy steel black oxide finish 12.9",              "CS-0840", "7318", "Nos",   19.40, 18.0),
            ("item-5", "SS-1050", "Stainless Steel Hex Bolt M10 x 50 SS316","Marine grade stainless steel hex head",           "SS-1050", "7318", "Nos",   64.00, 18.0),
            ("item-6", "TR-1610", "Threaded Rod M16 x 1000mm Class 4.6",   "Hot dip galvanised all-thread bar",                "TR-1610", "7318", "Meter", 215.00, 18.0),
            ("item-7", "WD-4000", "Industrial Lubricant Spray 400ml",       "Multi-use rust remover and lubricant",             "WD-4000", "3403", "Can",   390.00, 18.0),
            ("item-8", "CP-0500", "Cutting Oil Coolant Emulsion",           "Water soluble metalworking cutting fluid",         "CP-0500", "3403", "Litre", 185.00, 18.0),
        ]
        for i_id, sku, name, desc, part, hsn, unit, rate, tax in initial_items:
            cursor.execute(
                """
                INSERT OR IGNORE INTO items (
                    id, sku, name, description, part_number, hsn_code, unit,
                    default_rate, default_tax_percent, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (i_id, sku, name, desc, part, hsn, unit, rate, tax, now, now),
            )

        initial_vendors = [
            ("ven-1", "Bharat Fasteners & Alloys Ltd",    "Vikram Shah",     "sales@bharatfasteners.in",  "+91 98201 12345", "+91 98201 12345", "27AAACB1234F1Z8", "27", "Plot 42, MIDC Industrial Area, Turbhe, Navi Mumbai - 400705", "30 Days Net"),
            ("ven-2", "Apex Steel & Wire Industries",     "Mahesh Kulkarni", "apex.steel@apexwire.co.in", "+91 98220 54321", "+91 98220 54321", "27AAZCS9876Q1Z2", "27", "Gala 14, Phase II, Bhosari, Pune - 411026",                  "15 Days Net"),
            ("ven-3", "Precision Tooling & Hardware Mart","Arjun Agarwal",   "orders@precisionmart.com",  "+91 99300 78901", "+91 99300 78901", "24AABCP5544R1Z0", "24", "Ring Road GIDC, Surat, Gujarat - 395002",                   "Against Delivery"),
        ]
        for v_id, name, cp, email, phone, wa, gstin, state, addr, terms in initial_vendors:
            cursor.execute(
                """
                INSERT OR IGNORE INTO vendors (
                    id, name, contact_person, email, phone, whatsapp, gstin,
                    state_code, address, default_payment_terms, active, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                """,
                (v_id, name, cp, email, phone, wa, gstin, state, addr, terms, now, now),
            )
