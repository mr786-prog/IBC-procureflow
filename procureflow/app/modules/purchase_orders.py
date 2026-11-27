import uuid
from html import escape
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_procure_access, require_manager, check_po_approval_segregation, require_employee_procure_access
from ..core.numbering import generate_document_number
from ..core.audit import log_audit

router = APIRouter(prefix="/api/v1/purchase-orders", tags=["purchase-orders"])

class POLineItem(BaseModel):
    item_id: Optional[str] = None
    item_name: str
    part_number: Optional[str] = None
    description: Optional[str] = None
    quantity: float = Field(gt=0)
    unit: str
    rate: float = Field(ge=0, le=1_000_000_000)
    tax_percent: float = Field(ge=0, le=100, default=18.0)
    requisition_line_id: Optional[str] = None

class POCreate(BaseModel):
    vendor_id: str
    source_type: str = "manual" # requisition, enquiry, manual
    source_requisition_id: Optional[str] = None
    source_enquiry_id: Optional[str] = None
    approval_required: bool = True
    delivery_date: Optional[str] = None
    payment_terms: Optional[str] = "30 Days Net"
    delivery_terms: Optional[str] = "Door Delivery to IBC Plant"
    ship_to: Optional[str] = "Works: Plot 18, Industrial Estate, Phase 1, Mumbai - 400086"
    bill_to: Optional[str] = "Industrial Bolt Centre, Accounts Dept, Mumbai - 400086"
    lines: List[POLineItem]

class POApprovalAction(BaseModel):
    note: Optional[str] = None

class POCancelAction(BaseModel):
    reason: str

@router.get("")
def list_pos(
    status_filter: Optional[str] = Query(None, alias="status"),
    vendor_id: Optional[str] = None,
    user: dict = Depends(get_current_user)
):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    query = """
    SELECT po.*, v.name as vendor_name, v.gstin as vendor_gstin,
           p.full_name as creator_name, app.full_name as approved_by_name,
           (SELECT COUNT(*) FROM po_lines pl WHERE pl.purchase_order_id = po.id) as line_count
    FROM purchase_orders po
    JOIN vendors v ON po.vendor_id = v.id
    JOIN profiles p ON po.created_by = p.id
    LEFT JOIN profiles app ON po.approved_by = app.id
    WHERE po.deleted_at IS NULL
    """
    params = []
    if status_filter:
        query += " AND po.status = ?"
        params.append(status_filter)
    if vendor_id:
        query += " AND po.vendor_id = ?"
        params.append(vendor_id)
        
    query += " ORDER BY po.created_at DESC"
    cursor.execute(query, params)
    rows = [dict(r) for r in cursor.fetchall()]
    if user["role"] != "manager":
        for row in rows:
            row.update({"subtotal": None, "tax_total": None, "grand_total": None})
    return rows

@router.get("/{po_id}")
def get_po(po_id: str, user: dict = Depends(get_current_user)):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT po.*, v.name as vendor_name, v.contact_person as vendor_contact,
           v.email as vendor_email, v.phone as vendor_phone, v.gstin as vendor_gstin,
           v.address as vendor_address, v.state_code as vendor_state_code,
           p.full_name as creator_name, app.full_name as approved_by_name
    FROM purchase_orders po
    JOIN vendors v ON po.vendor_id = v.id
    JOIN profiles p ON po.created_by = p.id
    LEFT JOIN profiles app ON po.approved_by = app.id
    WHERE po.id = ? AND po.deleted_at IS NULL
    """, (po_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Purchase Order not found")
        
    po = dict(row)
    cursor.execute("SELECT * FROM po_lines WHERE purchase_order_id = ?", (po_id,))
    po["lines"] = [dict(lr) for lr in cursor.fetchall()]
    if user["role"] != "manager":
        for key in ("subtotal", "tax_total", "grand_total"):
            po[key] = None
        for line in po["lines"]:
            for key in ("rate", "tax_percent", "line_subtotal", "tax_amount", "line_total"):
                line[key] = None
    
    # Associated GRNs
    cursor.execute("SELECT * FROM grns WHERE purchase_order_id = ? ORDER BY received_date DESC", (po_id,))
    po["grns"] = [dict(gr) for gr in cursor.fetchall()]
    return po

@router.post("")
def create_po(payload: POCreate, request: Request, user: dict = Depends(get_current_user)):
    require_employee_procure_access(user)
    if not payload.lines:
        raise HTTPException(status_code=422, detail="At least one line item is required.")
        
    conn = get_connection()
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    number = generate_document_number("PO", conn)
    now = datetime.now(timezone.utc).isoformat()
    order_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    
    # Recalculate totals strictly on server
    subtotal = 0.0
    tax_total = 0.0
    
    with conn:
        conn.execute("""
        INSERT INTO purchase_orders (
            id, number, revision, vendor_id, source_type, source_requisition_id, source_enquiry_id,
            status, approval_required, order_date, delivery_date, payment_terms, delivery_terms, ship_to, bill_to,
            subtotal, tax_total, grand_total, created_by, created_at, updated_at
        ) VALUES (?, ?, 1, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, 0, 0, 0, ?, ?, ?)
        """, (
            po_id, number, payload.vendor_id, payload.source_type,
            payload.source_requisition_id, payload.source_enquiry_id, int(payload.approval_required),
            order_date, payload.delivery_date, payload.payment_terms, payload.delivery_terms,
            payload.ship_to, payload.bill_to, user["id"], now, now
        ))
        
        for line in payload.lines:
            line_sub = round(line.quantity * line.rate, 2)
            line_tax = round(line_sub * (line.tax_percent / 100.0), 2)
            line_tot = round(line_sub + line_tax, 2)
            
            subtotal += line_sub
            tax_total += line_tax
            
            line_id = f"pol-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO po_lines (
                id, purchase_order_id, requisition_line_id, item_id, item_name_snapshot,
                part_number, description, quantity, unit, rate, tax_percent,
                line_subtotal, tax_amount, line_total, received_qty
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0.0)
            """, (
                line_id, po_id, line.requisition_line_id, line.item_id, line.item_name,
                line.part_number, line.description, line.quantity, line.unit,
                line.rate, line.tax_percent, line_sub, line_tax, line_tot
            ))
            
            # If from requisition, mark PR line as ordered
            if line.requisition_line_id:
                conn.execute("UPDATE requisition_lines SET procure_status = 'ordered' WHERE id = ?", (line.requisition_line_id,))
                
        grand_total = round(subtotal + tax_total, 2)
        conn.execute("""
        UPDATE purchase_orders SET subtotal = ?, tax_total = ?, grand_total = ? WHERE id = ?
        """, (subtotal, tax_total, grand_total, po_id))
        
        # If PR sourced, check if all PR lines are ordered
        if payload.source_requisition_id:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT COUNT(*) FROM requisition_lines
            WHERE requisition_id = ? AND procure_status != 'ordered'
            """, (payload.source_requisition_id,))
            remaining = cursor.fetchone()[0]
            if remaining == 0:
                conn.execute("UPDATE requisitions SET status = 'converted', updated_at = ? WHERE id = ?", (now, payload.source_requisition_id))

    log_audit(
        action="purchase_order.create",
        entity_type="purchase_order",
        entity_id=po_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"number": number, "grand_total": grand_total},
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "id": po_id, "number": number}

@router.post("/{po_id}/submit")
def submit_po(po_id: str, request: Request, user: dict = Depends(get_current_user)):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="PO not found")
    po = dict(row)
    if po["status"] not in ("draft", "rejected"):
        raise HTTPException(status_code=400, detail="Only draft POs can be submitted.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE purchase_orders SET status = ?, submitted_at = ?, updated_at = ?
        WHERE id = ?
        """, ("pending_approval" if po.get("approval_required", 1) else "approved", now, now, po_id))
        
        # Notify managers
        cursor.execute("SELECT id FROM profiles WHERE role = 'manager' AND status = 'active'" if po.get("approval_required", 1) else "SELECT id FROM profiles WHERE 1 = 0")
        for mgr in cursor.fetchall():
            notif_id = f"notif-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO notifications (id, user_id, type, title, body, entity_type, entity_id, created_at)
            VALUES (?, ?, 'po_pending', 'PO Pending Approval', ?, 'purchase_order', ?, ?)
            """, (notif_id, mgr[0], f"PO {po['number']} awaits approval.", po_id, now))
            
    log_audit(
        action="purchase_order.submit",
        entity_type="purchase_order",
        entity_id=po_id,
        actor_id=user["id"],
        actor_email=user["email"],
        ip=request.client.host if request.client else None
    )
    message = "PO submitted for approval" if po.get("approval_required", 1) else "PO submitted and approved without manager approval"
    return {"status": "success", "message": message}

@router.post("/{po_id}/approve")
def approve_po(po_id: str, payload: POApprovalAction, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM purchase_orders WHERE id = ?", (po_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="PO not found")
    po = dict(row)
    
    # Segregation of duties: PO creator cannot approve their own PO
    check_po_approval_segregation(user, po)
    
    if po["status"] != "pending_approval":
        raise HTTPException(status_code=400, detail="Only pending POs can be approved.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE purchase_orders SET
            status = 'approved', approved_by = ?, approved_at = ?, approval_note = ?, updated_at = ?
        WHERE id = ?
        """, (user["id"], now, payload.note, now, po_id))
        
        notif_id = f"notif-{uuid.uuid4().hex[:8]}"
        conn.execute("""
        INSERT INTO notifications (id, user_id, type, title, body, entity_type, entity_id, created_at)
        VALUES (?, ?, 'po_approved', 'PO Approved', ?, 'purchase_order', ?, ?)
        """, (notif_id, po["created_by"], f"PO {po['number']} was approved by {user['full_name']}.", po_id, now))
        
    log_audit(
        action="purchase_order.approve",
        entity_type="purchase_order",
        entity_id=po_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"approved_by": user["id"], "note": payload.note},
        ip=request.client.host if request.client else None
    )
    return {"status": "success", "message": "PO approved successfully"}

@router.post("/{po_id}/send")
def mark_po_sent(po_id: str, request: Request, user: dict = Depends(get_current_user)):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status FROM purchase_orders WHERE id = ?", (po_id,))
    row = cursor.fetchone()
    if not row or row[0] != "approved":
        raise HTTPException(status_code=400, detail="Only approved POs can be marked as sent to vendor.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE purchase_orders SET status = 'sent', sent_at = ?, updated_at = ? WHERE id = ?", (now, now, po_id))
    return {"status": "success", "message": "PO marked as sent"}

@router.post("/{po_id}/cancel")
def cancel_po(po_id: str, payload: POCancelAction, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT status, (SELECT SUM(received_qty) FROM po_lines WHERE purchase_order_id = ?) as total_rec FROM purchase_orders WHERE id = ?", (po_id, po_id))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="PO not found")
        
    status_val, total_rec = row[0], row[1] or 0.0
    if total_rec > 0:
        raise HTTPException(status_code=400, detail="Cannot cancel a PO with posted receipts. Use Short-Close instead.")
        
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE purchase_orders SET status = 'cancelled', cancel_reason = ?, updated_at = ? WHERE id = ?", (payload.reason, now, po_id))
    return {"status": "success", "message": "PO cancelled"}

@router.get("/{po_id}/print", response_class=HTMLResponse)
def print_po_view(po_id: str, user: dict = Depends(get_current_user)):
    """Render beautiful printable PO on IBC letterhead."""
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT po.*, v.name as vendor_name, v.contact_person as vendor_contact,
           v.email as vendor_email, v.phone as vendor_phone, v.gstin as vendor_gstin,
           v.address as vendor_address, v.state_code as vendor_state_code,
           p.full_name as creator_name, app.full_name as approved_by_name
    FROM purchase_orders po
    JOIN vendors v ON po.vendor_id = v.id
    JOIN profiles p ON po.created_by = p.id
    LEFT JOIN profiles app ON po.approved_by = app.id
    WHERE po.id = ?
    """, (po_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="PO not found")
    po = dict(row)
    
    cursor.execute("SELECT * FROM po_lines WHERE purchase_order_id = ?", (po_id,))
    lines = [
        {key: escape(value) if isinstance(value, str) else value for key, value in dict(lr).items()}
        for lr in cursor.fetchall()
    ]
    
    # Escape all business data before inserting it into the HTML document.
    po = {key: escape(value) if isinstance(value, str) else value for key, value in po.items()}
    # Render HTML template with high-contrast print styling
    lines_html = "".join([f"""
    <tr>
        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: center;">{idx+1}</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px;">
            <strong>{l['item_name_snapshot']}</strong>
            <div style="font-size: 11px; color: #64748b;">SKU/Part: {l['part_number'] or 'N/A'}</div>
        </td>
        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: right;">{l['quantity']} {l['unit']}</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: right;">₹{l['rate']:.2f}</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: right;">₹{l['line_subtotal']:.2f}</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: right;">{l['tax_percent']}%</td>
        <td style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; font-weight: 600;">₹{l['line_total']:.2f}</td>
    </tr>
    """ for idx, l in enumerate(lines)])
    
    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>Purchase Order {po['number']}</title>
        <style>
            @media print {{
                .no-print {{ display: none !important; }}
                body {{ padding: 0; }}
            }}
            body {{
                font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
                margin: 0; padding: 24px; color: #1e293b; font-size: 13px; line-height: 1.5;
            }}
            .header-bar {{
                border-bottom: 2px solid #0f172a; padding-bottom: 16px; margin-bottom: 20px;
                display: flex; justify-content: space-between; align-items: flex-start;
            }}
            .company-name {{ font-size: 22px; font-weight: 800; color: #0f172a; text-transform: uppercase; letter-spacing: 0.5px; }}
            .btn-print {{
                background: #0f172a; color: #fff; border: none; padding: 8px 16px; border-radius: 6px;
                cursor: pointer; font-weight: 600; font-size: 13px;
            }}
        </style>
    </head>
    <body>
        <div class="no-print" style="margin-bottom: 20px; display: flex; justify-content: flex-end; gap: 10px;">
            <button class="btn-print" onclick="window.print()">Print / Save as PDF</button>
            <button class="btn-print" style="background:#64748b;" onclick="window.close()">Close Window</button>
        </div>
        
        <div class="header-bar">
            <div>
                <div class="company-name">Industrial Bolt Centre</div>
                <div style="color: #475569; font-size: 12px; margin-top: 4px;">Works: Plot 18, Industrial Estate, Phase 1, Mumbai - 400086</div>
                <div style="color: #475569; font-size: 12px;">GSTIN: 27AAACI9999M1Z5 · Email: purchase@ibcbolts.com</div>
            </div>
            <div style="text-align: right;">
                <div style="font-size: 18px; font-weight: 800; color: #0284c7;">PURCHASE ORDER</div>
                <div style="font-size: 14px; font-weight: 700; margin-top: 4px;">{po['number']} (Rev {po['revision']})</div>
                <div style="color: #64748b; font-size: 12px;">Date: {po['order_date']}</div>
                <div style="color: #64748b; font-size: 12px;">Status: <span style="text-transform:uppercase; font-weight:700;">{po['status']}</span></div>
            </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 24px;">
            <div style="border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; background: #f8fafc;">
                <div style="font-weight: 700; color: #0f172a; margin-bottom: 6px; text-transform: uppercase; font-size: 11px;">Vendor Details:</div>
                <div style="font-size: 14px; font-weight: 700;">{po['vendor_name']}</div>
                <div>{po['vendor_address'] or ''}</div>
                <div>Contact: {po['vendor_contact'] or 'N/A'} · Phone: {po['vendor_phone'] or 'N/A'}</div>
                <div>GSTIN: <strong>{po['vendor_gstin'] or 'N/A'}</strong> (State: {po['vendor_state_code'] or '27'})</div>
            </div>
            <div style="border: 1px solid #e2e8f0; border-radius: 6px; padding: 12px; background: #f8fafc;">
                <div style="font-weight: 700; color: #0f172a; margin-bottom: 6px; text-transform: uppercase; font-size: 11px;">Delivery & Billing:</div>
                <div><strong>Ship To:</strong> {po['ship_to']}</div>
                <div><strong>Delivery Date:</strong> {po['delivery_date'] or 'Immediate / As agreed'}</div>
                <div><strong>Payment Terms:</strong> {po['payment_terms'] or '30 Days Net'}</div>
                <div><strong>Delivery Terms:</strong> {po['delivery_terms']}</div>
            </div>
        </div>

        <table style="width: 100%; border-collapse: collapse; margin-bottom: 20px;">
            <thead>
                <tr style="background: #f1f5f9;">
                    <th style="border: 1px solid #cbd5e1; padding: 8px; width: 40px;">#</th>
                    <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: left;">Item Description</th>
                    <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; width: 90px;">Qty</th>
                    <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; width: 90px;">Rate</th>
                    <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; width: 100px;">Amount</th>
                    <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; width: 70px;">GST %</th>
                    <th style="border: 1px solid #cbd5e1; padding: 8px; text-align: right; width: 110px;">Total (INR)</th>
                </tr>
            </thead>
            <tbody>
                {lines_html}
            </tbody>
        </table>

        <div style="display: flex; justify-content: flex-end; margin-bottom: 30px;">
            <div style="width: 280px; border: 1px solid #cbd5e1; border-radius: 6px; padding: 12px; background: #f8fafc;">
                <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                    <span>Subtotal:</span>
                    <strong>₹{po['subtotal']:.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; margin-bottom: 6px;">
                    <span>Total GST:</span>
                    <strong>₹{po['tax_total']:.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; border-top: 2px solid #0f172a; padding-top: 8px; font-size: 15px; font-weight: 800; color: #0284c7;">
                    <span>Grand Total:</span>
                    <span>₹{po['grand_total']:.2f}</span>
                </div>
            </div>
        </div>

        <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 40px; margin-top: 60px; padding-top: 20px;">
            <div>
                <div style="border-top: 1px dashed #94a3b8; padding-top: 8px; font-size: 12px;">
                    <strong>Prepared By:</strong> {po['creator_name']}
                </div>
            </div>
            <div style="text-align: right;">
                <div style="border-top: 1px dashed #94a3b8; padding-top: 8px; font-size: 12px;">
                    <strong>Authorised Signatory:</strong> {po['approved_by_name'] or 'Pending Approval'}
                </div>
            </div>
        </div>
    </body>
    </html>
    """
