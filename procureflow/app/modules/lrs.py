import uuid
import re
from datetime import datetime, timezone
from typing import Optional, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..core.audit import log_audit
from ..core.db import get_connection
from ..core.permissions import require_receive_access, require_employee_receive_access
from ..core.security import get_current_user

router = APIRouter(prefix="/api/v1/lrs", tags=["lrs"])


class LRCreate(BaseModel):
    number: str = Field(min_length=1, max_length=80)
    purchase_order_id: str
    lr_date: Optional[str] = None
    transporter_name: Optional[str] = None
    transporter_contact: Optional[str] = None
    notes: Optional[str] = None


class LRUpdate(BaseModel):
    transporter_name: Optional[str] = None
    transporter_contact: Optional[str] = None
    payment_status: Optional[Literal["unpaid", "partial", "paid"]] = None
    payment_amount: Optional[float] = Field(default=None, ge=0, le=1_000_000_000)
    payment_date: Optional[str] = None
    payment_reference: Optional[str] = None
    notes: Optional[str] = None


@router.get("")
def list_lrs(user: dict = Depends(get_current_user)):
    require_receive_access(user)
    conn = get_connection()
    rows = conn.execute("""
        SELECT lr.id, lr.number, lr.purchase_order_id, lr.lr_date, lr.transporter_name,
               lr.transporter_contact, lr.payment_status, lr.payment_amount, lr.payment_date,
               lr.payment_reference, lr.notes, lr.document_filename, lr.created_at, lr.updated_at,
               po.number AS po_number, po.status AS po_status,
               v.id AS vendor_id, v.name AS vendor_name
        FROM lrs lr
        JOIN purchase_orders po ON po.id = lr.purchase_order_id
        JOIN vendors v ON v.id = po.vendor_id
        WHERE po.deleted_at IS NULL
        ORDER BY lr.created_at DESC
    """).fetchall()
    return [dict(row) for row in rows]


@router.post("")
def create_lr(payload: LRCreate, request: Request, user: dict = Depends(get_current_user)):
    require_employee_receive_access(user)
    number = payload.number.strip()
    if not number:
        raise HTTPException(status_code=422, detail="LR number is required.")
    conn = get_connection()
    po = conn.execute("SELECT id, number, status FROM purchase_orders WHERE id = ? AND deleted_at IS NULL",
                      (payload.purchase_order_id,)).fetchone()
    if not po:
        raise HTTPException(status_code=404, detail="Purchase Order not found")
    if po["status"] not in {"approved", "sent", "part_received", "received", "closed"}:
        raise HTTPException(status_code=400, detail="An LR can only be attached to an authorized Purchase Order.")
    lr_id = f"lr-{uuid.uuid4().hex[:10]}"
    now = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            conn.execute("""INSERT INTO lrs
                (id, number, purchase_order_id, lr_date, transporter_name, transporter_contact, notes,
                 created_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (lr_id, number, payload.purchase_order_id, payload.lr_date,
                 payload.transporter_name, payload.transporter_contact, payload.notes,
                 user["id"], now, now))
    except Exception as exc:
        if "UNIQUE" in str(exc):
            raise HTTPException(status_code=409, detail="This LR number is already attached to this PO.")
        raise
    log_audit(action="lr.create", entity_type="lr", entity_id=lr_id, actor_id=user["id"],
              actor_email=user["email"], after_data={"number": number, "po": po["number"]},
              ip=request.client.host if request.client else None)
    return {"status": "success", "id": lr_id}


@router.patch("/{lr_id}")
def update_lr(lr_id: str, payload: LRUpdate, request: Request, user: dict = Depends(get_current_user)):
    require_employee_receive_access(user)
    conn = get_connection()
    existing = conn.execute("SELECT * FROM lrs WHERE id = ?", (lr_id,)).fetchone()
    if not existing:
        raise HTTPException(status_code=404, detail="LR not found")
    changes = payload.model_dump(exclude_unset=True)
    if not changes:
        return {"status": "success"}
    if "payment_status" in changes and changes["payment_status"] is None:
        raise HTTPException(status_code=422, detail="Payment status cannot be empty.")
    now = datetime.now(timezone.utc).isoformat()
    columns = ", ".join(f"{column} = ?" for column in changes)
    with conn:
        conn.execute(f"UPDATE lrs SET {columns}, updated_at = ? WHERE id = ?",
                     (*changes.values(), now, lr_id))
    log_audit(action="lr.update", entity_type="lr", entity_id=lr_id, actor_id=user["id"],
              actor_email=user["email"], before_data={k: existing[k] for k in existing.keys() if k != "document_data"}, after_data=changes,
              ip=request.client.host if request.client else None)
    return {"status": "success"}


@router.post("/{lr_id}/document")
async def upload_lr_document(lr_id: str, file: UploadFile = File(...),
                             user: dict = Depends(get_current_user)):
    require_employee_receive_access(user)
    allowed = {"application/pdf", "image/jpeg", "image/png"}
    if file.content_type not in allowed:
        raise HTTPException(status_code=415, detail="Upload a PDF, JPEG, or PNG transport document.")
    content = await file.read(10 * 1024 * 1024 + 1)
    if not content or len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="The LR document must be between 1 byte and 10 MB.")
    if file.content_type == "application/pdf" and not content.startswith(b"%PDF-"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid PDF.")
    if file.content_type == "image/png" and not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid PNG image.")
    if file.content_type == "image/jpeg" and not content.startswith(b"\xff\xd8\xff"):
        raise HTTPException(status_code=415, detail="The uploaded file is not a valid JPEG image.")
    conn = get_connection()
    safe_filename = re.sub(r"[^A-Za-z0-9._ -]", "_", (file.filename or "transport-document").replace("\\", "/").split("/")[-1])[:180]
    with conn:
        result = conn.execute("UPDATE lrs SET document_filename = ?, document_data = ?, updated_at = ? WHERE id = ?",
                              (safe_filename or "transport-document", content,
                               datetime.now(timezone.utc).isoformat(), lr_id))
        if result.rowcount == 0:
            raise HTTPException(status_code=404, detail="LR not found")
    return {"status": "success", "filename": file.filename}


@router.get("/{lr_id}/document")
def download_lr_document(lr_id: str, user: dict = Depends(get_current_user)):
    require_receive_access(user)
    conn = get_connection()
    row = conn.execute("SELECT document_filename, document_data FROM lrs WHERE id = ?", (lr_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="LR not found")
    if not row["document_data"]:
        raise HTTPException(status_code=404, detail="No document is attached to this LR")
    filename = re.sub(r"[^A-Za-z0-9._ -]", "_", (row["document_filename"] or "transport-document"))[:180]
    blob = row["document_data"]
    media_type = "application/pdf" if blob.startswith(b"%PDF-") else "image/jpeg" if blob.startswith(b"\xff\xd8\xff") else "image/png"
    return Response(content=blob, media_type=media_type,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"', "X-Content-Type-Options": "nosniff"})
