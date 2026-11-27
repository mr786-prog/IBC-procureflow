from datetime import datetime, timezone
import sqlite3
from .db import get_connection

def get_financial_year(dt: datetime = None) -> str:
    """Financial year runs from 1 April to 31 March. Format: 2026-27"""
    if dt is None:
        dt = datetime.now(timezone.utc)
    year = dt.year
    if dt.month >= 4:
        return f"{year}-{str(year + 1)[-2:]}"
    else:
        return f"{year - 1}-{str(year)[-2:]}"

def generate_document_number(doc_type: str, conn: sqlite3.Connection = None) -> str:
    """
    Format examples:
    - PR:  IBC/PR/2026-27/00001
    - ENQ: IBC/ENQ/2026-27/00001
    - PO:  IBC/PO/2026-27/00001
    - GRN: IBC/GRN/2026-27/00001
    """
    fy_label = get_financial_year()
    close_conn = False
    if conn is None:
        conn = get_connection()
        close_conn = True

    try:
        cursor = conn.cursor()
        cursor.execute("""
        INSERT INTO document_sequences (doc_type, fy_label, last_number)
        VALUES (?, ?, 1)
        ON CONFLICT(doc_type, fy_label) DO UPDATE SET
            last_number = last_number + 1
        RETURNING last_number;
        """, (doc_type.upper(), fy_label))
        
        row = cursor.fetchone()
        next_val = row[0] if row else 1
        conn.commit()
        
        return f"IBC/{doc_type.upper()}/{fy_label}/{next_val:05d}"
    finally:
        if close_conn:
            conn.close()
