import uuid
from typing import Dict, Any, List
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_manager

router = APIRouter(prefix="/api/v1/settings", tags=["settings"])

class CustomFieldCreate(BaseModel):
    module: str # requisition, enquiry, purchase_order, grn, item, vendor
    field_key: str
    label: str
    field_type: str # text, number, date, boolean, select
    options: List[str] = []
    required: bool = False

@router.get("")
def get_app_settings(user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT key, value FROM app_settings")
    return {row[0]: row[1] for row in cursor.fetchall()}

@router.post("")
def update_app_settings(payload: Dict[str, str], user: dict = Depends(get_current_user)):
    require_manager(user)
    allowed_keys = {"grn_tolerance_percent", "company_name", "company_gstin"}
    if set(payload) - allowed_keys:
        raise HTTPException(status_code=422, detail="One or more settings cannot be changed through this endpoint")
    if "grn_tolerance_percent" in payload:
        try:
            tolerance = float(payload["grn_tolerance_percent"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="GRN tolerance must be a number")
        if not 0 <= tolerance <= 100:
            raise HTTPException(status_code=422, detail="GRN tolerance must be between 0 and 100")
    conn = get_connection()
    with conn:
        for k, v in payload.items():
            conn.execute("""
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = ?
            """, (k, v, v))
    return {"status": "success", "message": "Settings updated"}

@router.get("/custom-fields")
def list_custom_fields(module: str = None, user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM custom_field_definitions WHERE active = 1"
    params = []
    if module:
        query += " AND module = ?"
        params.append(module)
    query += " ORDER BY sort_order ASC, label ASC"
    cursor.execute(query, params)
    return [dict(r) for r in cursor.fetchall()]

@router.post("/custom-fields")
def create_custom_field(payload: CustomFieldCreate, user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cf_id = f"cf-{uuid.uuid4().hex[:6]}"
    import json
    with conn:
        conn.execute("""
        INSERT INTO custom_field_definitions (
            id, module, field_key, label, field_type, options, required, active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            cf_id, payload.module, payload.field_key.strip().lower(), payload.label.strip(),
            payload.field_type, json.dumps(payload.options), 1 if payload.required else 0
        ))
    return {"status": "success", "id": cf_id}
