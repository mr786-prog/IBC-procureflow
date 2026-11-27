"""One-time, non-destructive migration of the existing SQLite app into Supabase."""

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from procureflow.app.config import BASE_DIR, settings
from procureflow.app.core.db import get_connection, init_db
from procureflow.app.core.db_tables import APP_TABLES


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", nargs="?", default=str(BASE_DIR / "procureflow.db"),
                        help="SQLite database to import (default: project procureflow.db)")
    args = parser.parse_args()
    source = Path(args.source).resolve()
    if not settings.postgres_enabled:
        raise SystemExit("Set SUPABASE_DB_URL in .env before migrating.")
    if settings.DEMO_MODE:
        raise SystemExit("Set DEMO_MODE=false in .env before using Supabase as primary.")
    if not source.is_file():
        raise SystemExit(f"SQLite source database not found: {source}")

    # Creates the remote schema, without seeding or changing the source file.
    init_db()
    src = sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    src.execute("BEGIN")
    remote = get_connection()
    try:
        for table in APP_TABLES:
            count = remote.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            if count:
                raise RuntimeError(
                    f"Supabase table {table} already has {count} rows. "
                    "Migration stopped without writing; inspect the cloud data first."
                )
        with remote:
            for table in APP_TABLES:
                source_columns = [row[1] for row in src.execute(f'PRAGMA table_info("{table}")')]
                source_column_set = set(source_columns)
                remote_columns = {row[0] for row in remote.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = current_schema() AND table_name = ?", (table,)
                ).fetchall()}
                if not source_columns:
                    continue
                missing = remote_columns - source_column_set
                if missing:
                    raise RuntimeError(
                        f"SQLite table {table} is missing required columns: {', '.join(sorted(missing))}. "
                        "Update the local app schema before migrating."
                    )
                columns = [name for name in source_columns if name in remote_columns]
                rows = src.execute(f'SELECT * FROM "{table}"').fetchall()
                if not rows:
                    continue
                column_sql = ", ".join(f'"{column}"' for column in columns)
                values_sql = ", ".join("?" for _ in columns)
                sql = f'INSERT INTO "{table}" ({column_sql}) VALUES ({values_sql})'
                for row in rows:
                    remote.execute(sql, tuple(row[column] for column in columns))
                print(f"Imported {len(rows)} rows from {table}.")
            remote.execute(
                "SELECT setval(pg_get_serial_sequence('procure.audit_log', 'id'), "
                "GREATEST(COALESCE((SELECT MAX(id) FROM audit_log), 1), 1), "
                "EXISTS(SELECT 1 FROM audit_log))"
            )
        print("SQLite data migration completed. The original SQLite file was left untouched.")
    except Exception:
        remote.rollback()
        raise
    finally:
        src.close()
        remote.close()


if __name__ == "__main__":
    main()

