import csv
import io
from fastapi import APIRouter, Depends, Query, Response
from ..core.db import get_connection
from ..core.security import get_current_user
from ..core.permissions import require_manager

router = APIRouter(prefix="/api/v1/reports", tags=["reports"])

def export_rows(rows, headers, fields, fmt, filename):
    if fmt == "xlsx":
        from openpyxl import Workbook
        output = io.BytesIO()
        book = Workbook()
        sheet = book.active
        sheet.title = "Report"
        sheet.append(headers)
        for row in rows:
            values = []
            for field in fields:
                value = row.get(field)
                if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
                    value = "'" + value
                values.append(value)
            sheet.append(values)
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        for column in sheet.columns:
            letter = column[0].column_letter
            sheet.column_dimensions[letter].width = min(32, max(12, max(len(str(cell.value or "")) for cell in column) + 2))
        book.save(output)
        return Response(content=output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f"attachment; filename={filename}.xlsx", "X-Content-Type-Options": "nosniff"})
    if fmt == "pdf":
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import landscape, A4
        from reportlab.platypus import SimpleDocTemplate, Table, TableStyle
        output = io.BytesIO()
        doc = SimpleDocTemplate(output, pagesize=landscape(A4), leftMargin=24, rightMargin=24, topMargin=24, bottomMargin=24)
        data = [headers] + [[str(row.get(field) if row.get(field) is not None else "") for field in fields] for row in rows]
        table = Table(data, repeatRows=1)
        table.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1e3a5f")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#cbd5e1")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f1f5f9")]),
            ("FONTSIZE", (0, 0), (-1, -1), 8), ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        doc.build([table])
        return Response(content=output.getvalue(), media_type="application/pdf",
                        headers={"Content-Disposition": f"attachment; filename={filename}.pdf", "X-Content-Type-Options": "nosniff"})
    return None

def csv_safe(value):
    if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
        return "'" + value
    return value

@router.get("/summary")
def get_dashboard_summary(user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    
    # Metrics
    cursor.execute("SELECT COUNT(*) FROM requisitions WHERE status = 'submitted'")
    prs_to_approve = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM purchase_orders WHERE status = 'pending_approval'")
    pos_to_approve = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM purchase_orders WHERE status IN ('approved', 'sent', 'part_received')")
    open_pos = cursor.fetchone()[0]
    
    cursor.execute("SELECT COALESCE(SUM(grand_total), 0.0) FROM purchase_orders WHERE status IN ('approved', 'sent', 'part_received', 'received')")
    total_spend = cursor.fetchone()[0]
    
    cursor.execute("""
    SELECT occurred_at, actor_email, action, entity_type, entity_id
    FROM audit_log ORDER BY id DESC LIMIT 8
    """)
    recent_activity = [dict(r) for r in cursor.fetchall()]
    
    return {
        "prs_to_approve": prs_to_approve,
        "pos_to_approve": pos_to_approve,
        "open_pos": open_pos,
        "total_spend": total_spend,
        "recent_activity": recent_activity
    }

@router.get("/item-price-history")
def item_price_history(item_id: str = Query(...), format: str = "json", user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT po.number as po_number, po.order_date, v.name as vendor_name,
           pl.item_name_snapshot, pl.rate, pl.quantity, pl.unit, pl.line_total
    FROM po_lines pl
    JOIN purchase_orders po ON pl.purchase_order_id = po.id
    JOIN vendors v ON po.vendor_id = v.id
    WHERE pl.item_id = ? AND po.status NOT IN ('draft', 'cancelled')
    ORDER BY po.order_date DESC
    """, (item_id,))
    rows = [dict(r) for r in cursor.fetchall()]
    
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["PO Number", "Date", "Vendor", "Item", "Rate", "Quantity", "Unit", "Total"])
        for r in rows:
            writer.writerow([csv_safe(r["po_number"]), csv_safe(r["order_date"]), csv_safe(r["vendor_name"]), csv_safe(r["item_name_snapshot"]), r["rate"], r["quantity"], csv_safe(r["unit"]), r["line_total"]])
        return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=item_price_history.csv", "X-Content-Type-Options": "nosniff"})
        
    return rows

@router.get("/pending-deliveries")
def pending_deliveries(format: str = "json", user: dict = Depends(get_current_user)):
    require_manager(user)
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT po.number as po_number, po.order_date, po.delivery_date, v.name as vendor_name,
           pl.item_name_snapshot, pl.quantity as ordered_qty, pl.received_qty,
           (pl.quantity - pl.received_qty) as pending_qty, pl.unit
    FROM po_lines pl
    JOIN purchase_orders po ON pl.purchase_order_id = po.id
    JOIN vendors v ON po.vendor_id = v.id
    WHERE po.status IN ('approved', 'sent', 'part_received')
      AND (pl.quantity - pl.received_qty) > 0
    ORDER BY po.delivery_date ASC
    """)
    rows = [dict(r) for r in cursor.fetchall()]

    headers = ["PO Number", "Order Date", "Delivery Date", "Vendor", "Item", "Ordered Qty", "Received Qty", "Pending Qty", "Unit"]
    fields = ["po_number", "order_date", "delivery_date", "vendor_name", "item_name_snapshot", "ordered_qty", "received_qty", "pending_qty", "unit"]
    exported = export_rows(rows, headers, fields, format, "pending_deliveries")
    if exported:
        return exported
    
    if format == "csv":
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["PO Number", "Order Date", "Delivery Date", "Vendor", "Item", "Ordered Qty", "Received Qty", "Pending Qty", "Unit"])
        for r in rows:
            writer.writerow([csv_safe(r["po_number"]), csv_safe(r["order_date"]), csv_safe(r["delivery_date"]), csv_safe(r["vendor_name"]), csv_safe(r["item_name_snapshot"]), r["ordered_qty"], r["received_qty"], r["pending_qty"], csv_safe(r["unit"])])
        return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": "attachment; filename=pending_deliveries.csv", "X-Content-Type-Options": "nosniff"})
        
    return rows
