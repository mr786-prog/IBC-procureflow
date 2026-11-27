import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_procure_access, require_manager, require_employee_procure_access
from ..core.numbering import generate_document_number
from ..core.audit import log_audit

router = APIRouter(prefix="/api/v1/enquiries", tags=["enquiries"])

class EnquiryCreate(BaseModel):
    subject: str
    requisition_id: Optional[str] = None
    terms: Optional[str] = "Standard warranty, payment 30 days after GRN, delivery door delivered."
    line_ids: Optional[List[str]] = []
    lines: Optional[List[dict]] = []
    vendor_ids: List[str]

class QuoteLineInput(BaseModel):
    enquiry_line_id: str
    quoted_rate: float = Field(ge=0, le=1_000_000_000)
    tax_percent: float = Field(ge=0, le=100, default=18.0)
    lead_time_days: int = Field(ge=0, le=3_650, default=7)
    notes: Optional[str] = None

class VendorQuotesSubmit(BaseModel):
    vendor_id: str
    quote_reference: Optional[str] = None
    payment_terms: Optional[str] = None
    quotes: List[QuoteLineInput]

class AwardVendorRequest(BaseModel):
    vendor_id: str

class ConvertEnquiryRequest(BaseModel):
    approval_required: bool = True
    delivery_date: Optional[str] = None

@router.get("")
def list_enquiries(user: dict = Depends(get_current_user)):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT e.*, v.name as awarded_vendor_name, p.full_name as creator_name,
           (SELECT COUNT(*) FROM enquiry_vendors ev WHERE ev.enquiry_id = e.id) as vendor_count
    FROM enquiries e
    LEFT JOIN vendors v ON e.awarded_vendor_id = v.id
    JOIN profiles p ON e.created_by = p.id
    ORDER BY e.created_at DESC
    """)
    return [dict(r) for r in cursor.fetchall()]

@router.get("/{enq_id}")
def get_enquiry(enq_id: str, user: dict = Depends(get_current_user)):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT e.*, v.name as awarded_vendor_name, p.full_name as creator_name
    FROM enquiries e
    LEFT JOIN vendors v ON e.awarded_vendor_id = v.id
    JOIN profiles p ON e.created_by = p.id
    WHERE e.id = ?
    """, (enq_id,))
    row = cursor.fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Enquiry not found")
        
    enq = dict(row)
    
    # Lines
    cursor.execute("SELECT * FROM enquiry_lines WHERE enquiry_id = ?", (enq_id,))
    enq["lines"] = [dict(lr) for lr in cursor.fetchall()]
    
    # Invited vendors with their quote lines
    cursor.execute("""
    SELECT ev.*, v.name as vendor_name, v.email as vendor_email, v.phone as vendor_phone
    FROM enquiry_vendors ev
    JOIN vendors v ON ev.vendor_id = v.id
    WHERE ev.enquiry_id = ?
    """, (enq_id,))
    vendors = [dict(vr) for vr in cursor.fetchall()]
    
    for v in vendors:
        cursor.execute("SELECT * FROM quote_lines WHERE enquiry_vendor_id = ?", (v["id"],))
        v["quotes"] = [dict(qr) for qr in cursor.fetchall()]
        if user["role"] != "manager":
            for quote in v["quotes"]:
                quote["quoted_rate"] = None
                quote["tax_percent"] = None
        
    enq["vendors"] = vendors
    return enq

@router.post("")
def create_enquiry(payload: EnquiryCreate, request: Request, user: dict = Depends(get_current_user)):
    require_employee_procure_access(user)
    if not payload.vendor_ids:
        raise HTTPException(status_code=422, detail="Select at least one vendor to invite.")
        
    conn = get_connection()
    enq_id = f"enq-{uuid.uuid4().hex[:8]}"
    number = generate_document_number("ENQ", conn)
    now = datetime.now(timezone.utc).isoformat()
    
    with conn:
        if payload.requisition_id:
            cursor = conn.cursor()
            cursor.execute("SELECT status FROM requisitions WHERE id = ? AND deleted_at IS NULL", (payload.requisition_id,))
            source_req = cursor.fetchone()
            if not source_req or source_req["status"] not in {"approved", "in_progress"}:
                raise HTTPException(status_code=400, detail="Enquiries can only use approved, open requisitions.")
            cursor.execute("SELECT COUNT(*) FROM requisition_lines WHERE requisition_id = ? AND procure_status = 'open'",
                           (payload.requisition_id,))
            if cursor.fetchone()[0] == 0:
                raise HTTPException(status_code=400, detail="This requisition has no open lines to add to an enquiry.")
        conn.execute("""
        INSERT INTO enquiries (
            id, number, requisition_id, status, subject, terms, created_by, created_at, updated_at
        ) VALUES (?, ?, ?, 'sent', ?, ?, ?, ?, ?)
        """, (enq_id, number, payload.requisition_id, payload.subject, payload.terms, user["id"], now, now))
        
        # Populate lines from a requisition, explicit requisition line IDs, or manual lines.
        if payload.requisition_id:
            cursor = conn.cursor()
            cursor.execute("""
            SELECT id, item_id, item_name_snapshot, quantity, unit
            FROM requisition_lines
            WHERE requisition_id = ? AND procure_status = 'open'
              AND (? = 0 OR id IN ({line_placeholders}))
            """.format(line_placeholders=",".join("?" for _ in payload.line_ids) or "NULL"),
                (payload.requisition_id, int(bool(payload.line_ids)), *payload.line_ids))
            for pr_line in cursor.fetchall():
                line_id = f"enl-{uuid.uuid4().hex[:8]}"
                conn.execute("""
                INSERT INTO enquiry_lines (id, enquiry_id, requisition_line_id, item_id, item_name_snapshot, quantity, unit)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (line_id, enq_id, pr_line[0], pr_line[1], pr_line[2], pr_line[3], pr_line[4]))
                
                # Mark PR line as enquired
                conn.execute("UPDATE requisition_lines SET procure_status = 'enquired' WHERE id = ?", (pr_line[0],))
                
            conn.execute("UPDATE requisitions SET status = 'in_progress', updated_at = ? WHERE id = ?", (now, payload.requisition_id))
        elif payload.lines:
            for line in payload.lines:
                if not line.get("item_name") or not line.get("quantity") or not line.get("unit"):
                    raise HTTPException(status_code=422, detail="Each enquiry line needs an item name, quantity, and unit.")
                conn.execute("""
                INSERT INTO enquiry_lines (id, enquiry_id, item_id, item_name_snapshot, quantity, unit)
                VALUES (?, ?, ?, ?, ?, ?)
                """, (f"enl-{uuid.uuid4().hex[:8]}", enq_id, line.get("item_id"), line["item_name"], line["quantity"], line["unit"]))
            
        # Invite vendors
        for v_id in payload.vendor_ids:
            ev_id = f"ev-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO enquiry_vendors (id, enquiry_id, vendor_id, sent_at)
            VALUES (?, ?, ?, ?)
            """, (ev_id, enq_id, v_id, now))
            
    log_audit(
        action="enquiry.create",
        entity_type="enquiry",
        entity_id=enq_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"number": number, "vendors_invited": len(payload.vendor_ids)},
        ip=request.client.host if request.client else None
    )
    
    return {"status": "success", "id": enq_id, "number": number}

@router.put("/{enq_id}/quotes")
def record_quotes(enq_id: str, payload: VendorQuotesSubmit, request: Request, user: dict = Depends(get_current_user)):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM enquiry_vendors WHERE enquiry_id = ? AND vendor_id = ?", (enq_id, payload.vendor_id))
    ev_row = cursor.fetchone()
    if not ev_row:
        raise HTTPException(status_code=404, detail="Vendor is not invited to this enquiry.")
        
    ev_id = ev_row[0]
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE enquiry_vendors SET
            quote_received_at = ?, quote_reference = ?, payment_terms = ?
        WHERE id = ?
        """, (now, payload.quote_reference, payload.payment_terms, ev_id))
        
        # Clear old quote lines for this vendor
        conn.execute("DELETE FROM quote_lines WHERE enquiry_vendor_id = ?", (ev_id,))
        
        for q in payload.quotes:
            cursor.execute("SELECT 1 FROM enquiry_lines WHERE id = ? AND enquiry_id = ?", (q.enquiry_line_id, enq_id))
            if not cursor.fetchone():
                raise HTTPException(status_code=422, detail="Quote line must belong to this enquiry.")
            ql_id = f"ql-{uuid.uuid4().hex[:8]}"
            conn.execute("""
            INSERT INTO quote_lines (id, enquiry_vendor_id, enquiry_line_id, quoted_rate, tax_percent, lead_time_days, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (ql_id, ev_id, q.enquiry_line_id, q.quoted_rate, q.tax_percent, q.lead_time_days, q.notes))
            
        conn.execute("UPDATE enquiries SET status = 'quoted', updated_at = ? WHERE id = ?", (now, enq_id))
        
    return {"status": "success", "message": "Quotes recorded successfully"}

@router.post("/{enq_id}/award")
def award_enquiry_vendor(enq_id: str, payload: AwardVendorRequest, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM enquiry_vendors WHERE enquiry_id = ? AND vendor_id = ?", (enq_id, payload.vendor_id))
    if not cursor.fetchone():
        raise HTTPException(status_code=422, detail="Only a vendor invited to this enquiry can be awarded.")
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE enquiries SET
            awarded_vendor_id = ?, status = 'awarded', updated_at = ?
        WHERE id = ?
        """, (payload.vendor_id, now, enq_id))
        
    log_audit(
        action="enquiry.award",
        entity_type="enquiry",
        entity_id=enq_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data={"awarded_vendor_id": payload.vendor_id},
        ip=request.client.host if request.client else None
    )
    return {"status": "success", "message": "Enquiry awarded to vendor"}


@router.post("/{enq_id}/convert-to-po")
def convert_enquiry_to_po(enq_id: str, payload: ConvertEnquiryRequest, request: Request,
                          user: dict = Depends(get_current_user)):
    require_employee_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM enquiries WHERE id = ?", (enq_id,))
    enquiry_row = cursor.fetchone()
    if not enquiry_row:
        raise HTTPException(status_code=404, detail="Enquiry not found")
    enquiry = dict(enquiry_row)
    if enquiry["status"] != "awarded" or not enquiry["awarded_vendor_id"]:
        raise HTTPException(status_code=400, detail="Award the enquiry to a vendor before converting it to a PO.")
    cursor.execute("""SELECT el.*, q.quoted_rate, q.tax_percent FROM enquiry_lines el
        JOIN enquiry_vendors ev ON ev.enquiry_id = el.enquiry_id AND ev.vendor_id = ?
        JOIN quote_lines q ON q.enquiry_vendor_id = ev.id AND q.enquiry_line_id = el.id
        WHERE el.enquiry_id = ? ORDER BY el.rowid""", (enquiry["awarded_vendor_id"], enq_id))
    lines = cursor.fetchall()
    if not lines:
        raise HTTPException(status_code=400, detail="The awarded vendor has no quoted lines to convert.")
    po_id = f"po-{uuid.uuid4().hex[:8]}"
    number = generate_document_number("PO", conn)
    now = datetime.now(timezone.utc).isoformat()
    order_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    subtotal = tax_total = 0.0
    with conn:
        conn.execute("""INSERT INTO purchase_orders
            (id, number, revision, vendor_id, source_type, source_enquiry_id, status,
             approval_required, order_date, delivery_date, payment_terms, delivery_terms, ship_to, bill_to,
             subtotal, tax_total, grand_total, created_by, created_at, updated_at)
            VALUES (?, ?, 1, ?, 'enquiry', ?, 'draft', ?, ?, ?, '30 Days Net',
             'Door Delivery to IBC Plant', 'Works: Plot 18, Industrial Estate, Phase 1, Mumbai - 400086',
             'Industrial Bolt Centre, Accounts Dept, Mumbai - 400086', 0, 0, 0, ?, ?, ?)""",
            (po_id, number, enquiry["awarded_vendor_id"], enq_id, int(payload.approval_required),
             order_date, payload.delivery_date, user["id"], now, now))
        for line in lines:
            sub = round(line["quantity"] * line["quoted_rate"], 2)
            tax = round(sub * line["tax_percent"] / 100, 2)
            subtotal += sub
            tax_total += tax
            conn.execute("""INSERT INTO po_lines
                (id, purchase_order_id, requisition_line_id, item_id, item_name_snapshot, quantity,
                 unit, rate, tax_percent, line_subtotal, tax_amount, line_total, received_qty)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
                (f"pol-{uuid.uuid4().hex[:8]}", po_id, line["requisition_line_id"], line["item_id"],
                 line["item_name_snapshot"], line["quantity"], line["unit"], line["quoted_rate"],
                 line["tax_percent"], sub, tax, round(sub + tax, 2)))
            if line["requisition_line_id"]:
                conn.execute("UPDATE requisition_lines SET procure_status = 'ordered' WHERE id = ?",
                             (line["requisition_line_id"],))
        conn.execute("UPDATE purchase_orders SET subtotal = ?, tax_total = ?, grand_total = ? WHERE id = ?",
                     (subtotal, tax_total, round(subtotal + tax_total, 2), po_id))
        conn.execute("UPDATE enquiries SET status = 'converted', updated_at = ? WHERE id = ?", (now, enq_id))
        if enquiry["requisition_id"]:
            cursor.execute("SELECT COUNT(*) FROM requisition_lines WHERE requisition_id = ? AND procure_status != 'ordered'",
                           (enquiry["requisition_id"],))
            if cursor.fetchone()[0] == 0:
                conn.execute("UPDATE requisitions SET status = 'converted', updated_at = ? WHERE id = ?",
                             (now, enquiry["requisition_id"]))
    log_audit(action="enquiry.convert_to_po", entity_type="purchase_order", entity_id=po_id,
              actor_id=user["id"], actor_email=user["email"],
              after_data={"number": number, "source_enquiry_id": enq_id},
              ip=request.client.host if request.client else None)
    return {"status": "success", "id": po_id, "number": number}
