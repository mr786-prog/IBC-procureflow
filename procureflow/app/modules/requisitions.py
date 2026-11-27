import uuid
from datetime import datetime, timezone
from typing import Optional, List, Literal
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_manager, check_pr_approval_segregation
from ..core.numbering import generate_document_number
from ..core.audit import log_audit

router = APIRouter(prefix="/api/v1/requisitions", tags=["requisitions"])

class RequisitionLineItem(BaseModel):
    item_id: Optional[str] = None
    item_name: str
    part_number: Optional[str] = None
    description: Optional[str] = None
    quantity: float = Field(gt=0)
    unit: str
    notes: Optional[str] = None

class RequisitionCreate(BaseModel):
    department_id: str
    required_by: str
    purpose: str
    priority: Literal["low", "normal", "high", "urgent"] = "normal"
    approval_required: bool = True
    lines: List[RequisitionLineItem]

class RequisitionAction(BaseModel):
    note: Optional[str] = None

@router.get("")
def list_requisitions(
    status_filter: Optional[str] = Query(None, alias="status"),
    user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cursor = conn.cursor()
    
    query = """
    SELECT r.*, d.name as department_name, p.full_name as requester_name,
           dec.full_name as decided_by_name,
           (SELECT COUNT(*) FROM requisition_lines rl WHERE rl.requisition_id = r.id) as line_count
    FROM requisitions r
    JOIN departments d ON r.department_id = d.id
    JOIN profiles p ON r.requested_by = p.id
    LEFT JOIN profiles dec ON r.decided_by = dec.id
    WHERE r.deleted_at IS NULL
    """
    params = []
    
    # Employee can only view their own PRs unless they have procurement capability
    if user["role"] == "employee" and not user.get("can_procure"):
        query += " AND r.requested_by = ?"
        params.append(user["id"])
        
    if status_filter:
        query += " AND r.status = ?"
        params.append(status_filter)
        
    query += " ORDER BY r.created_at DESC"
    cursor.execute(query, params)
    return [dict(row) for row in cursor.fetchall()]

@router.get("/{req_id}")
def get_requisition(req_id: str, user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT r.*, d.name as department_name, p.full_name as requester_name,
           dec.full_name as decided_by_name
    FROM requisitions r
    JOIN departments d ON r.department_id = d.id
    JOIN profiles p ON r.requested_by = p.id
    LEFT JOIN profiles dec ON r.decided_by = dec.id
    WHERE r.id = ? AND r.deleted_at IS NULL
    """, (req_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Requisition not found")
        
    req = dict(row)
    if user["role"] == "employee" and not user.get("can_procure") and req["requested_by"] != user["id"]:
        raise HTTPException(status_code=403, detail="Forbidden: You can only view your own requisitions.")
        
    cursor.execute("SELECT * FROM requisition_lines WHERE requisition_id = ?", (req_id,))
    req["lines"] = [dict(lr) for lr in cursor.fetchall()]
    return req

@router.post("")
def create_requisition(payload: RequisitionCreate, request: Request, user: dict = Depends(get_current_user)):
    if user["role"] != "employee":
        raise HTTPException(status_code=403, detail="Purchase requisitions must be created from the employee portal.")
    if payload.department_id != user.get("department_id"):
        raise HTTPException(status_code=403, detail="You can only raise requisitions for your assigned department.")
    if not payload.lines:
        raise HTTPException(status_code=422, detail="At least one line item is required.")
        
    conn = get_connection()
    req_id = f"pr-{uuid.uuid4().hex[:8]}"
    number = generate_document_number("PR", conn)
    now = datetime.now(timezone.utc).isoformat()
    
    with conn:
        conn.execute("""
        INSERT INTO requisitions (
            id, number, department_id, requested_by, required_by,
            purpose, priority, approval_required, status, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'draft', ?, ?)
        """, (
            req_id, number, payload.department_id, user["id"], payload.required_by,
            payload.purpose, payload.priority, int(payload.approval_required), now, now
        ))
        
        for item in payload.lines:
            line_id = f"prl-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO requisition_lines (
                id, requisition_id, item_id, item_name_snapshot, part_number,
                description, quantity, unit, notes, procure_status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open')
            """, (
                line_id, req_id, item.item_id, item.item_name, item.part_number,
                item.description, item.quantity, item.unit, item.notes
            ))
            
    log_audit(
        action="requisition.create",
        entity_type="requisition",
        entity_id=req_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"number": number, "lines_count": len(payload.lines)},
        ip=request.client.host if request.client else None
    )
    
    return {"status": "success", "id": req_id, "number": number}

@router.post("/{req_id}/submit")
def submit_requisition(req_id: str, request: Request, user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM requisitions WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Requisition not found")
    req = dict(row)
    
    if req["requested_by"] != user["id"] and user["role"] != "manager":
        raise HTTPException(status_code=403, detail="Only requester can submit this requisition.")
        
    if req["status"] not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail=f"Cannot submit requisition in '{req['status']}' state.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE requisitions SET status = ?, submitted_at = ?, updated_at = ?
        WHERE id = ?
        """, ("submitted" if req.get("approval_required", 1) else "approved", now, now, req_id))
        
        # Notify managers only when the requester selected approval.
        cursor.execute("SELECT id FROM profiles WHERE role = 'manager' AND status = 'active'" if req.get("approval_required", 1) else "SELECT id FROM profiles WHERE 1 = 0")
        for mgr in cursor.fetchall():
            notif_id = f"notif-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO notifications (id, user_id, type, title, body, entity_type, entity_id, created_at)
            VALUES (?, ?, 'pr_submitted', 'New PR Submitted', ?, 'requisition', ?, ?)
            """, (notif_id, mgr[0], f"PR {req['number']} submitted for approval.", req_id, now))
            
    log_audit(
        action="requisition.submit",
        entity_type="requisition",
        entity_id=req_id,
        actor_id=user["id"],
        actor_email=user["email"],
        ip=request.client.host if request.client else None
    )
    message = "Requisition submitted for approval" if req.get("approval_required", 1) else "Requisition submitted and approved without manager approval"
    return {"status": "success", "message": message}

@router.post("/{req_id}/approve")
def approve_requisition(req_id: str, payload: RequisitionAction, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM requisitions WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Requisition not found")
    req = dict(row)
    
    # Check segregation of duties
    check_pr_approval_segregation(user, req)
    
    if req["status"] != "submitted":
        raise HTTPException(status_code=400, detail="Only submitted requisitions can be approved.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE requisitions SET
            status = 'approved', decided_by = ?, decided_at = ?, decision_note = ?, updated_at = ?
        WHERE id = ?
        """, (user["id"], now, payload.note, now, req_id))
        
        # Notify requester
        notif_id = f"notif-{uuid.uuid4().hex[:8]}"
        conn.execute("""
        INSERT INTO notifications (id, user_id, type, title, body, entity_type, entity_id, created_at)
        VALUES (?, ?, 'pr_approved', 'Requisition Approved', ?, 'requisition', ?, ?)
        """, (notif_id, req["requested_by"], f"Your PR {req['number']} was approved by {user['full_name']}.", req_id, now))
        
    log_audit(
        action="requisition.approve",
        entity_type="requisition",
        entity_id=req_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"decided_by": user["id"], "note": payload.note},
        ip=request.client.host if request.client else None
    )
    return {"status": "success", "message": "Requisition approved"}

@router.post("/{req_id}/reject")
def reject_requisition(req_id: str, payload: RequisitionAction, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    if not payload.note or not payload.note.strip():
        raise HTTPException(status_code=422, detail="Mandatory note is required when rejecting a requisition.")
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM requisitions WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Requisition not found")
    req = dict(row)
    
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE requisitions SET
            status = 'rejected', decided_by = ?, decided_at = ?, decision_note = ?, updated_at = ?
        WHERE id = ?
        """, (user["id"], now, payload.note.strip(), now, req_id))
        
        notif_id = f"notif-{uuid.uuid4().hex[:8]}"
        conn.execute("""
        INSERT INTO notifications (id, user_id, type, title, body, entity_type, entity_id, created_at)
        VALUES (?, ?, 'pr_rejected', 'Requisition Rejected', ?, 'requisition', ?, ?)
        """, (notif_id, req["requested_by"], f"PR {req['number']} was rejected: {payload.note}", req_id, now))
        
    log_audit(
        action="requisition.reject",
        entity_type="requisition",
        entity_id=req_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"note": payload.note},
        ip=request.client.host if request.client else None
    )
    return {"status": "success", "message": "Requisition rejected"}

@router.post("/{req_id}/cancel")
def cancel_requisition(req_id: str, payload: RequisitionAction, request: Request, user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM requisitions WHERE id = ?", (req_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Requisition not found")
    req = dict(row)
    
    is_owner = req["requested_by"] == user["id"]
    is_manager = user["role"] == "manager"
    if not (is_owner or is_manager):
        raise HTTPException(status_code=403, detail="Forbidden")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE requisitions SET status = 'cancelled', decision_note = ?, updated_at = ? WHERE id = ?",
                     (payload.note, now, req_id))
    return {"status": "success", "message": "Requisition cancelled"}
