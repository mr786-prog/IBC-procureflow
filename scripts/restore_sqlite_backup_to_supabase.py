"""Restore a full SQLite mirror archive into an empty Supabase schema."""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from procureflow.app.config import settings
from procureflow.app.core.db import get_connection, init_db
from procureflow.app.core.db_tables import APP_TABLES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("snapshot", type=Path, help="SQLite archive created by backup_local_db.py")
    args = parser.parse_args()
    if not settings.postgres_enabled:
        raise SystemExit("Set SUPABASE_DB_URL in .env before restoring.")
    if not args.snapshot.is_file():
        raise SystemExit(f"Snapshot not found: {args.snapshot}")

    source_path = args.snapshot.resolve()
    source = sqlite3.connect(source_path.as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    source.execute("BEGIN")
    integrity = source.execute("PRAGMA integrity_check").fetchone()[0]
    if integrity != "ok":
        source.close()
        raise RuntimeError(f"Snapshot integrity check failed: {integrity}")

    init_db()
    remote = get_connection()
    try:
        for table in APP_TABLES:
            count = remote.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count:
                raise RuntimeError(
                    f"Supabase table {table} already contains data. Restore stopped without writing."
                )
        with remote:
            for table in APP_TABLES:
                columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
                if not columns:
                    raise RuntimeError(f"Snapshot is missing required table {table}.")
                rows = source.execute(f'SELECT * FROM "{table}"').fetchall()
                if not rows:
                    continue
                column_sql = ", ".join(f'"{column}"' for column in columns)
                values_sql = ", ".join("?" for _ in columns)
                sql = f'INSERT INTO "{table}" ({column_sql}) VALUES ({values_sql})'
                for row in rows:
                    remote.execute(sql, tuple(row[column] for column in columns))
                print(f"Restored {len(rows)} rows to {table}.")
            remote.execute(
                "SELECT setval(pg_get_serial_sequence('procure.audit_log', 'id'), "
                "GREATEST(COALESCE((SELECT MAX(id) FROM audit_log), 1), 1), "
                "EXISTS(SELECT 1 FROM audit_log))"
            )
        print("SQLite snapshot restored to Supabase.")
    except Exception:
        remote.rollback()
        raise
    finally:
        source.close()
        remote.close()


if __name__ == "__main__":
    main()

