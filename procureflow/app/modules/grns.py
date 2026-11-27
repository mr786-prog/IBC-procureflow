import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_receive_access, require_manager, require_employee_receive_access
from ..core.numbering import generate_document_number
from ..core.audit import log_audit

router = APIRouter(prefix="/api/v1/grns", tags=["grns"])

class GRNLineItem(BaseModel):
    po_line_id: str
    received_qty: float = Field(ge=0)
    accepted_qty: float = Field(ge=0)
    rejected_qty: float = Field(ge=0, default=0.0)
    rejection_reason: Optional[str] = None

class GRNCreate(BaseModel):
    purchase_order_id: str
    received_date: str
    lr_number: Optional[str] = None
    transporter: Optional[str] = None
    vehicle_no: Optional[str] = None
    supplier_invoice_no: Optional[str] = None
    supplier_invoice_date: Optional[str] = None
    remarks: Optional[str] = None
    lines: List[GRNLineItem]

class GRNVoidAction(BaseModel):
    reason: str

@router.get("")
def list_grns(po_id: Optional[str] = None, user: dict = Depends(get_current_user)):
    require_receive_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    query = """
    SELECT g.*, po.number as po_number, v.name as vendor_name,
           p.full_name as receiver_name
    FROM grns g
    JOIN purchase_orders po ON g.purchase_order_id = po.id
    JOIN vendors v ON po.vendor_id = v.id
    JOIN profiles p ON g.received_by = p.id
    WHERE 1=1
    """
    params = []
    if po_id:
        query += " AND g.purchase_order_id = ?"
        params.append(po_id)
    query += " ORDER BY g.created_at DESC"
    cursor.execute(query, params)
    return [dict(r) for r in cursor.fetchall()]

@router.get("/{grn_id}")
def get_grn(grn_id: str, user: dict = Depends(get_current_user)):
    require_receive_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT g.*, po.number as po_number, v.name as vendor_name,
           p.full_name as receiver_name
    FROM grns g
    JOIN purchase_orders po ON g.purchase_order_id = po.id
    JOIN vendors v ON po.vendor_id = v.id
    JOIN profiles p ON g.received_by = p.id
    WHERE g.id = ?
    """, (grn_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="GRN not found")
        
    grn = dict(row)
    cursor.execute("""
    SELECT gl.*, pl.item_name_snapshot, pl.part_number, pl.unit, pl.quantity as ordered_qty
    FROM grn_lines gl
    JOIN po_lines pl ON gl.po_line_id = pl.id
    WHERE gl.grn_id = ?
    """, (grn_id,))
    grn["lines"] = [dict(lr) for lr in cursor.fetchall()]
    return grn

@router.post("")
def post_grn(payload: GRNCreate, request: Request, user: dict = Depends(get_current_user)):
    require_employee_receive_access(user)
    if not payload.lines:
        raise HTTPException(status_code=422, detail="At least one receipt line is required.")
        
    conn = get_connection()
    cursor = conn.cursor()
    
    # Verify PO status
    cursor.execute("SELECT status, number FROM purchase_orders WHERE id = ?", (payload.purchase_order_id,))
    po_row = cursor.fetchone()
    if not po_row:
        raise HTTPException(status_code=404, detail="Purchase order not found")
        
    po_status, po_number = po_row[0], po_row[1]
    if po_status not in ("approved", "sent", "part_received"):
        raise HTTPException(status_code=400, detail=f"Cannot post GRN against PO with status '{po_status}'.")
        
    # Get GRN tolerance setting
    cursor.execute("SELECT value FROM app_settings WHERE key = 'grn_tolerance_percent'")
    tol_row = cursor.fetchone()
    tolerance_pct = float(tol_row[0]) if tol_row else 0.0
    
    grn_id = f"grn-{uuid.uuid4().hex[:8]}"
    number = generate_document_number("GRN", conn)
    now = datetime.now(timezone.utc).isoformat()
    
    with conn:
        conn.execute("""
        INSERT INTO grns (
            id, number, purchase_order_id, received_date, received_by,
            lr_number, transporter, vehicle_no, supplier_invoice_no, supplier_invoice_date,
            remarks, status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'posted', ?)
        """, (
            grn_id, number, payload.purchase_order_id, payload.received_date, user["id"],
            payload.lr_number, payload.transporter, payload.vehicle_no,
            payload.supplier_invoice_no, payload.supplier_invoice_date,
            payload.remarks, now
        ))
        
        for line in payload.lines:
            # Rule: accepted + rejected = received
            if round(line.accepted_qty + line.rejected_qty, 3) != round(line.received_qty, 3):
                raise HTTPException(
                    status_code=422,
                    detail=f"Line validation error: Accepted ({line.accepted_qty}) + Rejected ({line.rejected_qty}) must equal Received ({line.received_qty})."
                )
                
            cursor.execute("SELECT quantity, received_qty, purchase_order_id FROM po_lines WHERE id = ?", (line.po_line_id,))
            pol_row = cursor.fetchone()
            if not pol_row:
                raise HTTPException(status_code=404, detail=f"PO line {line.po_line_id} not found.")
                
            ordered_qty, current_rec, line_po_id = pol_row[0], pol_row[1], pol_row[2]
            if line_po_id != payload.purchase_order_id:
                raise HTTPException(status_code=422, detail="Each receipt line must belong to the selected purchase order.")
            max_allowed = ordered_qty * (1.0 + (tolerance_pct / 100.0))
            if current_rec + line.accepted_qty > max_allowed:
                raise HTTPException(
                    status_code=400,
                    detail=f"Over-receipt rejected: Cumulative accepted quantity ({current_rec + line.accepted_qty}) exceeds allowed order maximum ({max_allowed})."
                )
                
            gl_id = f"grnl-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO grn_lines (
                id, grn_id, po_line_id, received_qty, accepted_qty, rejected_qty, rejection_reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                gl_id, grn_id, line.po_line_id, line.received_qty,
                line.accepted_qty, line.rejected_qty, line.rejection_reason
            ))
            
            # Update PO line accepted/received quantity
            conn.execute("""
            UPDATE po_lines SET received_qty = received_qty + ? WHERE id = ?
            """, (line.accepted_qty, line.po_line_id))
            
        # Re-evaluate PO status
        cursor.execute("""
        SELECT SUM(quantity), SUM(received_qty) FROM po_lines WHERE purchase_order_id = ?
        """, (payload.purchase_order_id,))
        totals = cursor.fetchone()
        tot_ordered, tot_rec = totals[0], totals[1]
        
        new_po_status = "received" if tot_rec >= tot_ordered else "part_received"
        conn.execute("UPDATE purchase_orders SET status = ?, updated_at = ? WHERE id = ?", (new_po_status, now, payload.purchase_order_id))

    log_audit(
        action="grn.post",
        entity_type="grn",
        entity_id=grn_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"number": number, "po_number": po_number, "new_po_status": new_po_status},
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "id": grn_id, "number": number, "po_status": new_po_status}

@router.post("/{grn_id}/void")
def void_grn(grn_id: str, payload: GRNVoidAction, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    if not payload.reason or not payload.reason.strip():
        raise HTTPException(status_code=422, detail="Mandatory reason required to void GRN.")
        
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, purchase_order_id FROM grns WHERE id = ?", (grn_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="GRN not found")
        
    grn_status, po_id = row[0], row[1]
    if grn_status == "voided":
        raise HTTPException(status_code=400, detail="GRN is already voided.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        # Revert accepted quantities from po_lines
        cursor.execute("SELECT po_line_id, accepted_qty FROM grn_lines WHERE grn_id = ?", (grn_id,))
        for po_line_id, accepted_qty in cursor.fetchall():
            conn.execute("UPDATE po_lines SET received_qty = MAX(0, received_qty - ?) WHERE id = ?", (accepted_qty, po_line_id))
            
        conn.execute("""
        UPDATE grns SET status = 'voided', void_reason = ?, voided_by = ? WHERE id = ?
        """, (payload.reason.strip(), user["id"], grn_id))
        
        # Recompute PO status
        cursor.execute("SELECT SUM(quantity), SUM(received_qty) FROM po_lines WHERE purchase_order_id = ?", (po_id,))
        tot_ordered, tot_rec = cursor.fetchone()
        po_status = "sent" if tot_rec == 0 else ("received" if tot_rec >= tot_ordered else "part_received")
        conn.execute("UPDATE purchase_orders SET status = ?, updated_at = ? WHERE id = ?", (po_status, now, po_id))
        
    log_audit(
        action="grn.void",
        entity_type="grn",
        entity_id=grn_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"void_reason": payload.reason},
        ip=request.client.host if request.client else None
    )
    return {"status": "success", "message": "GRN voided and PO quantities reversed"}
