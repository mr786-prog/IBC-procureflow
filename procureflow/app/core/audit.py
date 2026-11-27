"""
Audit logging — IBC ProcureFlow.

Provides a single ``log_audit()`` helper that appends a row to the
``audit_log`` table.  The table is enforced as append-only by database
triggers — no row can ever be updated or deleted.

The function is intentionally fire-and-forget: it commits its own transaction
and silently swallows errors so that an audit failure never prevents the main
business operation from succeeding.
"""

import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from .db import get_connection


def log_audit(
    action: str,
    entity_type: str,
    entity_id: str,
    actor_id: Optional[str] = None,
    actor_email: Optional[str] = None,
    before_data: Optional[dict] = None,
    after_data: Optional[dict] = None,
    ip: Optional[str] = None,
    user_agent: Optional[str] = None,
    request_id: Optional[str] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> None:
    """
    Append one row to the ``audit_log`` table.

    Parameters
    ----------
    action:
        Dot-namespaced action identifier, e.g. ``"user.login"``,
        ``"requisition.approve"``, ``"po.send"``.
    entity_type:
        The database table / domain object being acted on, e.g. ``"profile"``,
        ``"requisition"``, ``"purchase_order"``.
    entity_id:
        Primary key of the affected record.
    actor_id:
        ``profiles.id`` of the user performing the action (``None`` for
        system-generated events).
    actor_email:
        Email of the actor, stored denormalised for readability in log queries.
    before_data:
        Snapshot of the record *before* the change (omit for create events).
    after_data:
        Snapshot of the record *after* the change (omit for delete events).
    ip:
        Client IP address from the HTTP request.
    user_agent:
        ``User-Agent`` header value.
    request_id:
        Optional correlation ID linking multiple audit rows to one HTTP request.
    conn:
        An existing open connection to reuse.  When ``None`` a new connection
        is opened and closed after the insert.
    """
    now        = datetime.now(timezone.utc).isoformat()
    before_str = json.dumps(before_data) if before_data is not None else None
    after_str  = json.dumps(after_data)  if after_data  is not None else None

    _owns_conn = conn is None
    if _owns_conn:
        conn = get_connection()

    try:
        conn.execute(
            """
            INSERT INTO audit_log (
                occurred_at, actor_id, actor_email, action, entity_type, entity_id,
                before_data, after_data, ip, user_agent, request_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now, actor_id, actor_email, action, entity_type, entity_id,
                before_str, after_str, ip, user_agent, request_id,
            ),
        )
        conn.commit()
    except Exception:
        # Audit failures must not bubble up and break the caller
        pass
    finally:
        if _owns_conn:
            conn.close()
