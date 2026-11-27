"""Application tables ordered so rows can be copied with foreign keys enabled."""

APP_TABLES = (
    "departments", "profiles", "sessions", "invitation_tokens", "items", "vendors",
    "document_sequences", "requisitions", "requisition_lines", "enquiries",
    "enquiry_lines", "enquiry_vendors", "quote_lines", "purchase_orders", "lrs",
    "po_lines", "grns", "grn_lines", "custom_field_definitions", "notifications",
    "app_settings", "audit_log",
)
