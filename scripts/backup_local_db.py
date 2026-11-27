"""Create a timestamped, verified archive of the continuously synced SQLite mirror."""

import hashlib
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from procureflow.app.config import BASE_DIR, settings

RETENTION_DAYS = 30


def main() -> None:
    if not settings.postgres_enabled:
        raise SystemExit("Supabase must be primary before archiving its SQLite mirror.")
    source = settings.SQLITE_BACKUP_PATH
    if not source.is_absolute():
        source = (BASE_DIR / source).resolve()
    if not source.is_file():
        raise SystemExit("The SQLite mirror is missing. Start the app to create it from Supabase first.")

    settings.BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    destination = settings.BACKUP_DIR / f"procureflow-{stamp}.sqlite3"
    temporary = destination.with_suffix(".sqlite3.tmp")
    try:
        with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as source_db, sqlite3.connect(temporary) as target:
            source_db.backup(target)
            check = target.execute("PRAGMA integrity_check").fetchone()[0]
            if check != "ok":
                raise RuntimeError(f"SQLite archive integrity check failed: {check}")
        temporary.replace(destination)
    finally:
        temporary.unlink(missing_ok=True)

    hasher = hashlib.sha256()
    with destination.open("rb") as archive:
        for chunk in iter(lambda: archive.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()
    destination.with_suffix(destination.suffix + ".sha256").write_text(
        f"{digest}  {destination.name}\n", encoding="ascii"
    )

    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    for old_backup in settings.BACKUP_DIR.glob("procureflow-*.sqlite3"):
        if old_backup == destination:
            continue
        modified = datetime.fromtimestamp(old_backup.stat().st_mtime, timezone.utc)
        if modified < cutoff:
            old_backup.unlink(missing_ok=True)
            old_backup.with_suffix(old_backup.suffix + ".sha256").unlink(missing_ok=True)
    print(f"Created SQLite mirror archive: {destination}")


if __name__ == "__main__":
    main()

