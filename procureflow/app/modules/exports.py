"""Downloadable spreadsheet and PDF exports for portal list pages."""
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from ..core.db import get_connection
from ..core.permissions import require_manager, require_procure_access, require_receive_access
from ..core.security import get_current_user
from .reports import export_rows

router = APIRouter(prefix="/api/v1/exports", tags=["exports"])

# Query and column definitions are fixed here so callers cannot inject SQL.
EXPORTS = {
    "requisitions": (
        "SELECT r.number, d.name AS department, p.full_name AS requester, r.required_by, r.purpose, r.priority, r.status, "
        "(SELECT group_concat(rl.item_name_snapshot || ' (' || rl.quantity || ' ' || rl.unit || ')', '; ') FROM requisition_lines rl WHERE rl.requisition_id=r.id) AS items "
        "FROM requisitions r JOIN departments d ON d.id=r.department_id JOIN profiles p ON p.id=r.requested_by WHERE r.deleted_at IS NULL ORDER BY r.created_at DESC",
        [("PR Number", "number"), ("Department", "department"), ("Requester", "requester"), ("Required By", "required_by"), ("Purpose", "purpose"), ("Priority", "priority"), ("Status", "status"), ("Items", "items")], "pr"),
    "enquiries": (
        "SELECT e.number, e.subject, e.status, e.terms, v.name AS awarded_vendor, p.full_name AS created_by, "
        "(SELECT group_concat(el.item_name_snapshot || ' (' || el.quantity || ' ' || el.unit || ')', '; ') FROM enquiry_lines el WHERE el.enquiry_id=e.id) AS items "
        "FROM enquiries e LEFT JOIN vendors v ON v.id=e.awarded_vendor_id JOIN profiles p ON p.id=e.created_by ORDER BY e.created_at DESC",
        [("Enquiry Number", "number"), ("Subject", "subject"), ("Status", "status"), ("Awarded Vendor", "awarded_vendor"), ("Created By", "created_by"), ("Terms", "terms"), ("Items", "items")], "procure"),
    "purchase-orders": (
        "SELECT po.number, po.revision, v.name AS vendor, po.order_date, po.delivery_date, po.status, po.subtotal, po.tax_total, po.grand_total, "
        "(SELECT group_concat(pl.item_name_snapshot || ' (' || pl.quantity || ' ' || pl.unit || ')', '; ') FROM po_lines pl WHERE pl.purchase_order_id=po.id) AS items "
        "FROM purchase_orders po JOIN vendors v ON v.id=po.vendor_id WHERE po.deleted_at IS NULL ORDER BY po.created_at DESC",
        [("PO Number", "number"), ("Revision", "revision"), ("Vendor", "vendor"), ("Order Date", "order_date"), ("Delivery Date", "delivery_date"), ("Status", "status"), ("Subtotal", "subtotal"), ("Tax", "tax_total"), ("Grand Total", "grand_total"), ("Items", "items")], "procure"),
    "grns": (
        "SELECT g.number, po.number AS po_number, v.name AS vendor, g.received_date, p.full_name AS received_by, g.lr_number, g.transporter, g.vehicle_no, g.supplier_invoice_no, g.status, "
        "(SELECT group_concat(pl.item_name_snapshot || ' (received ' || gl.received_qty || ', accepted ' || gl.accepted_qty || ')', '; ') FROM grn_lines gl JOIN po_lines pl ON pl.id=gl.po_line_id WHERE gl.grn_id=g.id) AS items "
        "FROM grns g JOIN purchase_orders po ON po.id=g.purchase_order_id JOIN vendors v ON v.id=po.vendor_id JOIN profiles p ON p.id=g.received_by ORDER BY g.created_at DESC",
        [("GRN Number", "number"), ("PO Number", "po_number"), ("Vendor", "vendor"), ("Received Date", "received_date"), ("Received By", "received_by"), ("LR Number", "lr_number"), ("Transporter", "transporter"), ("Vehicle", "vehicle_no"), ("Supplier Invoice", "supplier_invoice_no"), ("Status", "status"), ("Items", "items")], "receive"),
    "items": ("SELECT sku, name, description, part_number, hsn_code, unit, default_rate, default_tax_percent, currency, active FROM items ORDER BY name", [("SKU", "sku"), ("Name", "name"), ("Description", "description"), ("Part Number", "part_number"), ("HSN", "hsn_code"), ("Unit", "unit"), ("Default Rate", "default_rate"), ("GST %", "default_tax_percent"), ("Currency", "currency"), ("Active", "active")], "items"),
    "vendors": ("SELECT name, contact_person, email, phone, whatsapp, gstin, state_code, address, default_payment_terms, active FROM vendors ORDER BY name", [("Vendor", "name"), ("Contact", "contact_person"), ("Email", "email"), ("Phone", "phone"), ("WhatsApp", "whatsapp"), ("GSTIN", "gstin"), ("State Code", "state_code"), ("Address", "address"), ("Payment Terms", "default_payment_terms"), ("Active", "active")], "vendors"),
    "lrs": ("SELECT lr.number, lr.lr_date, po.number AS po_number, v.name AS vendor, lr.transporter_name, lr.transporter_contact, lr.payment_status, lr.payment_amount, lr.payment_date, lr.payment_reference, lr.notes FROM lrs lr JOIN purchase_orders po ON po.id=lr.purchase_order_id JOIN vendors v ON v.id=po.vendor_id WHERE po.deleted_at IS NULL ORDER BY lr.created_at DESC", [("LR Number", "number"), ("LR Date", "lr_date"), ("PO Number", "po_number"), ("Vendor", "vendor"), ("Transporter", "transporter_name"), ("Transporter Contact", "transporter_contact"), ("Payment Status", "payment_status"), ("Amount Paid", "payment_amount"), ("Payment Date", "payment_date"), ("Payment Reference", "payment_reference"), ("Notes", "notes")], "receive"),
    "users": ("SELECT p.full_name, p.email, p.phone, d.name AS department, p.role, p.status, p.can_procure, p.can_receive FROM profiles p LEFT JOIN departments d ON d.id=p.department_id ORDER BY p.full_name", [("Name", "full_name"), ("Email", "email"), ("Phone", "phone"), ("Department", "department"), ("Role", "role"), ("Status", "status"), ("Can Procure", "can_procure"), ("Can Receive", "can_receive")], "manager"),
    "departments": ("SELECT name, code, active, created_at FROM departments ORDER BY name", [("Department", "name"), ("Code", "code"), ("Active", "active"), ("Created", "created_at")], "manager"),
    "audit": ("SELECT occurred_at, actor_email, action, entity_type, entity_id, before_data, after_data, ip FROM audit_log ORDER BY id DESC", [("Date", "occurred_at"), ("Actor", "actor_email"), ("Action", "action"), ("Entity Type", "entity_type"), ("Entity ID", "entity_id"), ("Before", "before_data"), ("After", "after_data"), ("IP", "ip")], "manager"),
}


@router.get("/{entity}")
def download_export(entity: str, format: str = Query("xlsx", pattern="^(xlsx|excel|pdf|csv)$"), user: dict = Depends(get_current_user)):
    spec = EXPORTS.get(entity)
    if not spec:
        raise HTTPException(status_code=404, detail="Export type not found")
    query, columns, access = spec
    if access == "manager":
        require_manager(user)
    elif access == "procure":
        require_procure_access(user)
    elif access == "receive":
        require_receive_access(user)
    elif access == "pr" and user.get("role") == "employee" and not user.get("can_procure"):
        query = query.replace("WHERE r.deleted_at IS NULL ORDER BY", "WHERE r.deleted_at IS NULL AND r.requested_by = ? ORDER BY")
    if entity == "items" and user.get("role") != "manager":
        query = query.replace("default_rate, default_tax_percent", "NULL AS default_rate, NULL AS default_tax_percent")
    conn = get_connection()
    params = (user["id"],) if access == "pr" and user.get("role") == "employee" and not user.get("can_procure") else ()
    rows = [dict(row) for row in conn.execute(query, params).fetchall()]
    fmt = "xlsx" if format == "excel" else format
    filename = entity.replace("-", "_")
    if fmt in {"xlsx", "pdf"}:
        return export_rows(rows, [label for label, _ in columns], [field for _, field in columns], fmt, filename)
    import csv
    import io
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([label for label, _ in columns])
    for row in rows:
        values = []
        for _, field in columns:
            value = row.get(field)
            if isinstance(value, str) and value.lstrip(" \t\r\n")[:1] in {"=", "+", "-", "@"}:
                value = "'" + value
            values.append(value)
        writer.writerow(values)
    return Response(content="\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8", headers={"Content-Disposition": f"attachment; filename={filename}.csv", "X-Content-Type-Options": "nosniff"})
