from fastapi import APIRouter, Depends
from ..core.db import get_connection
from ..core.security import get_current_user

router = APIRouter(prefix="/api/v1/notifications", tags=["notifications"])

@router.get("")
def get_my_notifications(user: dict = Depends(get_current_user)):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT * FROM notifications
    WHERE user_id = ?
    ORDER BY created_at DESC LIMIT 50
    """, (user["id"],))
    return [dict(r) for r in cursor.fetchall()]

@router.post("/{notif_id}/read")
def mark_read(notif_id: str, user: dict = Depends(get_current_user)):
    conn = get_connection()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE notifications SET read_at = ? WHERE id = ? AND user_id = ?", (now, notif_id, user["id"]))
    return {"status": "success"}

@router.post("/read-all")
def mark_all_read(user: dict = Depends(get_current_user)):
    conn = get_connection()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute("UPDATE notifications SET read_at = ? WHERE user_id = ? AND read_at IS NULL", (now, user["id"]))
    return {"status": "success"}
