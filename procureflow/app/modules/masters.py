import uuid
from datetime import datetime, timezone
from typing import Optional, List
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_manager, require_procure_access
from ..core.audit import log_audit

router = APIRouter(prefix="/api/v1", tags=["masters"])

# --- Departments ---
class DepartmentCreate(BaseModel):
    name: str
    code: str

@router.get("/departments")
def list_departments(user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM departments WHERE active = 1 ORDER BY name ASC")
    return [dict(r) for r in cursor.fetchall()]

@router.post("/departments")
def create_department(payload: DepartmentCreate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    dept_id = f"dept-{uuid.uuid4().hex[:6]}"
    now = datetime.now(timezone.utc).isoformat()
    try:
        with conn:
            conn.execute("""
            INSERT INTO departments (id, name, code, active, created_at)
            VALUES (?, ?, ?, 1, ?)
            """, (dept_id, payload.name, payload.code.upper(), now))
    except Exception as e:
        raise HTTPException(status_code=409, detail="Department already exists.")
        
    return {"status": "success", "id": dept_id}

# --- Items ---
class ItemCreate(BaseModel):
    sku: str
    name: str
    description: Optional[str] = None
    part_number: Optional[str] = None
    hsn_code: Optional[str] = "7318"
    unit: str
    default_rate: Optional[float] = 0.0
    default_tax_percent: Optional[float] = 18.0

@router.get("/items")
def list_items(
    q: Optional[str] = Query(None, description="Search query"),
    active_only: bool = True,
    user: dict = Depends(get_current_user)
):
    conn = get_connection()
    cursor = conn.cursor()
    
    query = "SELECT * FROM items WHERE 1=1"
    params = []
    if active_only:
        query += " AND active = 1"
    if q:
        query += " AND (name LIKE ? OR sku LIKE ? OR part_number LIKE ?)"
        term = f"%{q.strip()}%"
        params.extend([term, term, term])
        
    query += " ORDER BY name ASC LIMIT 100"
    cursor.execute(query, params)
    rows = cursor.fetchall()
    
    items = []
    is_manager = user["role"] == "manager"
    for r in rows:
        d = dict(r)
        # Mask rates for standard employees per §2.1 & §10.4
        if not is_manager:
            d["default_rate"] = None
            d["default_tax_percent"] = None
        items.append(d)
        
    return items

@router.post("/items")
def create_item(payload: ItemCreate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id FROM items WHERE sku = ?", (payload.sku.strip(),))
    if cursor.fetchone():
        raise HTTPException(status_code=409, detail="Item with this SKU already exists.")

    item_id = f"item-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        INSERT INTO items (
            id, sku, name, description, part_number, hsn_code, unit,
            default_rate, default_tax_percent, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            item_id, payload.sku.strip(), payload.name.strip(), payload.description,
            payload.part_number, payload.hsn_code, payload.unit,
            payload.default_rate, payload.default_tax_percent, now, now
        ))

    log_audit(
        action="item.create",
        entity_type="item",
        entity_id=item_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data=payload.model_dump(),
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "id": item_id, "message": "Item created successfully"}

@router.patch("/items/{item_id}")
def update_item(item_id: str, payload: ItemCreate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE items SET
            name = ?, description = ?, part_number = ?, hsn_code = ?,
            unit = ?, default_rate = ?, default_tax_percent = ?, updated_at = ?
        WHERE id = ?
        """, (
            payload.name, payload.description, payload.part_number, payload.hsn_code,
            payload.unit, payload.default_rate, payload.default_tax_percent, now, item_id
        ))
    return {"status": "success", "message": "Item updated"}

# --- Vendors ---
class VendorCreate(BaseModel):
    name: str
    contact_person: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    whatsapp: Optional[str] = None
    gstin: Optional[str] = None
    state_code: Optional[str] = "27"
    address: Optional[str] = None
    default_payment_terms: Optional[str] = "30 Days Net"

@router.get("/vendors")
def list_vendors(
    q: Optional[str] = None,
    active_only: bool = True,
    user: dict = Depends(get_current_user)
):
    require_procure_access(user)
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM vendors WHERE 1=1"
    params = []
    if active_only:
        query += " AND active = 1"
    if q:
        query += " AND (name LIKE ? OR contact_person LIKE ? OR gstin LIKE ?)"
        term = f"%{q.strip()}%"
        params.extend([term, term, term])
    query += " ORDER BY name ASC"
    cursor.execute(query, params)
    return [dict(r) for r in cursor.fetchall()]

@router.post("/vendors")
def create_vendor(payload: VendorCreate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    v_id = f"ven-{uuid.uuid4().hex[:8]}"
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        INSERT INTO vendors (
            id, name, contact_person, email, phone, whatsapp, gstin,
            state_code, address, default_payment_terms, active, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
        """, (
            v_id, payload.name, payload.contact_person, payload.email, payload.phone,
            payload.whatsapp, payload.gstin, payload.state_code, payload.address,
            payload.default_payment_terms, now, now
        ))

    log_audit(
        action="vendor.create",
        entity_type="vendor",
        entity_id=v_id,
        actor_id=user["id"],
        actor_email=user["email"],
        after_data=payload.model_dump(),
        ip=request.client.host if request.client else None
    )

    return {"status": "success", "id": v_id, "message": "Vendor created successfully"}

@router.patch("/vendors/{vendor_id}")
def update_vendor(vendor_id: str, payload: VendorCreate, request: Request, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("""
        UPDATE vendors SET
            name = ?, contact_person = ?, email = ?, phone = ?, whatsapp = ?,
            gstin = ?, state_code = ?, address = ?, default_payment_terms = ?, updated_at = ?
        WHERE id = ?
        """, (
            payload.name, payload.contact_person, payload.email, payload.phone, payload.whatsapp,
            payload.gstin, payload.state_code, payload.address, payload.default_payment_terms, now, vendor_id
        ))
    return {"status": "success", "message": "Vendor updated successfully"}
