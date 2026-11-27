"""Manually refresh the SQLite mirror from the current Supabase primary."""

import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from procureflow.app.config import BASE_DIR, settings
from procureflow.app.core.db import sync_local_sqlite_mirror
from procureflow.app.core.db_tables import APP_TABLES


def main() -> None:
    if not settings.postgres_enabled:
        raise SystemExit("Supabase must be configured as the primary database.")
    sync_local_sqlite_mirror()
    mirror = settings.SQLITE_BACKUP_PATH
    if not mirror.is_absolute():
        mirror = (BASE_DIR / mirror).resolve()
    with sqlite3.connect(mirror) as db:
        counts = [db.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] for table in APP_TABLES]
    print(f"SQLite mirror refreshed: {sum(counts)} rows across {len(APP_TABLES)} tables.")


if __name__ == "__main__":
    main()

