from typing import Optional
from fastapi import APIRouter, Depends, Query
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_manager

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])

@router.get("")
def get_audit_trail(
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    limit: int = Query(50, le=200),
    user: dict = Depends(get_current_user)
):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    query = "SELECT * FROM audit_log WHERE 1=1"
    params = []
    if entity_type:
        query += " AND entity_type = ?"
        params.append(entity_type)
    if entity_id:
        query += " AND entity_id = ?"
        params.append(entity_id)
        
    query += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    
    cursor.execute(query, params)
    return [dict(r) for r in cursor.fetchall()]
