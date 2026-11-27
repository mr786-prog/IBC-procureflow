import uuid
import hashlib
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, status, Request
from pydantic import BaseModel
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_manager
from ..core.audit import log_audit

router = APIRouter(prefix="/api/v1/users", tags=["users"])

class UserCreate(BaseModel):
    email: str
    full_name: str
    role: str # manager or employee
    department_id: Optional[str] = None
    can_procure: Optional[bool] = False
    can_receive: Optional[bool] = False
    phone: Optional[str] = None

class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    role: Optional[str] = None
    department_id: Optional[str] = None
    can_procure: Optional[bool] = None
    can_receive: Optional[bool] = None
    phone: Optional[str] = None

@router.get("")
def list_users(user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT p.id, p.email, p.full_name, p.role, p.department_id, p.status,
           p.can_procure, p.can_receive, p.phone, p.invited_at, p.first_login_at, p.last_login_at,
           d.name as department_name, d.code as department_code
    FROM profiles p
    LEFT JOIN departments d ON p.department_id = d.id
    ORDER BY p.role DESC, p.full_name ASC
    """)
    rows = cursor.fetchall()
    return [dict(r) for r in rows]

@router.post("")
def create_user(payload: UserCreate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    email = payload.email.strip().lower()
    if payload.role not in ("manager", "employee"):
        raise HTTPException(status_code=400, detail="Role must be 'manager' or 'employee'")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM profiles WHERE lower(email) = ?", (email,))
    if cursor.fetchone():
        raise HTTPException(status_code=409, detail="A user with this email already exists.")

    user_id = f"user-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    invite_token = secrets.token_urlsafe(32)
    invite_hash = hashlib.sha256(invite_token.encode("utf-8")).hexdigest()
    invite_expires = (datetime.now(timezone.utc) + timedelta(hours=24)).isoformat()

    with conn:
        conn.execute("""
        INSERT INTO profiles (
            id, email, full_name, role, department_id, status,
            can_procure, can_receive, phone, password_hash,
            invited_by, invited_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, 'invited', ?, ?, ?, NULL, ?, ?, ?, ?)
        """, (
            user_id, email, payload.full_name, payload.role, payload.department_id,
            1 if payload.can_procure else 0,
            1 if payload.can_receive else 0,
            payload.phone,
            user["id"], now, now, now
        ))
        conn.execute("INSERT INTO invitation_tokens (token_hash, user_id, expires_at, created_at) VALUES (?, ?, ?, ?)",
                     (invite_hash, user_id, invite_expires, now))

    log_audit(
        action="user.create",
        entity_type="profile",
        entity_id=user_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data=payload.model_dump(),
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "id": user_id, "invite_token": invite_token,
            "message": "User invited. Share the one-time setup code securely; it expires in 24 hours."}

@router.patch("/{user_id}")
def update_user(user_id: str, payload: UserUpdate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    
    target_dict = dict(target)

    # Manager cannot change their own role
    if user_id == user["id"] and payload.role and payload.role != user["role"]:
        raise HTTPException(status_code=400, detail="You cannot change your own role.")

    # Guard: Last active manager cannot be demoted
    if payload.role == "employee" and target_dict["role"] == "manager":
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE role = 'manager' AND status = 'active'")
        mgr_count = cursor.fetchone()[0]
        if mgr_count <= 1:
            raise HTTPException(status_code=400, detail="Cannot demote the last active manager.")

    updates = []
    params = []
    for field, val in payload.model_dump(exclude_unset=True).items():
        if val is not None:
            if field in ("can_procure", "can_receive"):
                val = 1 if val else 0
            updates.append(f"{field} = ?")
            params.append(val)

    if not updates:
        return {"status": "unchanged"}

    updates.append("updated_at = ?")
    params.append(datetime.now(timezone.utc).isoformat())
    params.append(user_id)

    with conn:
        conn.execute(f"UPDATE profiles SET {', '.join(updates)} WHERE id = ?", params)

    log_audit(
        action="user.update",
        entity_type="profile",
        entity_id=user_id,
        actor_id=user["id"],
        actor_email=user["email"],
        before_data=target_dict,
        after_data=payload.model_dump(exclude_unset=True),
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "message": "User updated successfully"}

@router.post("/{user_id}/deactivate")
def deactivate_user(user_id: str, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    if user_id == user["id"]:
        raise HTTPException(status_code=400, detail="You cannot deactivate yourself.")

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM profiles WHERE id = ?", (user_id,))
    target = cursor.fetchone()
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    
    target_dict = dict(target)
    if target_dict["role"] == "manager":
        cursor.execute("SELECT COUNT(*) FROM profiles WHERE role = 'manager' AND status = 'active'")
        if cursor.fetchone()[0] <= 1:
            raise HTTPException(status_code=400, detail="Cannot deactivate the last active manager.")

    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE profiles SET status = 'deactivated', updated_at = ? WHERE id = ?", (now, user_id))
        conn.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))

    log_audit(
        action="user.deactivate",
        entity_type="profile",
        entity_id=user_id,
        actor_id=user["id"],
        actor_email=user["email"],
        before_data={"status": target_dict["status"]},
        after_data={"status": "deactivated"},
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "message": "User deactivated and sessions revoked"}

@router.post("/{user_id}/reactivate")
def reactivate_user(user_id: str, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE profiles SET status = 'active', updated_at = ? WHERE id = ?", (now, user_id))

    log_audit(
        action="user.reactivate",
        entity_type="profile",
        entity_id=user_id,
        actor_id=user["id"],
        actor_email=user["email"],
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "message": "User reactivated successfully"}
