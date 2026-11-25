"""Restore a SQLite backup while the dashboard process is stopped."""
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

from .db import DB, ROOT

def restore(source):
    source = Path(source).resolve()
    if source == DB.resolve():
        raise ValueError("Backup source and live database must differ")
    ROOT.mkdir(parents=True, exist_ok=True)
    temporary = ROOT / ".restore.sqlite3"
    try:
        with closing(sqlite3.connect(source.as_uri()+"?mode=ro",uri=True)) as backup:
            if backup.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Backup failed SQLite integrity check")
            if backup.execute("PRAGMA user_version").fetchone()[0] not in (1,2):
                raise ValueError("Unsupported backup schema")
            if not {"bars","levels","versions","snapshots","executions","flows","settings","quotes"} <= {
                    r[0] for r in backup.execute("SELECT name FROM sqlite_master WHERE type='table'")}:
                raise ValueError("Backup is missing dashboard tables")
            with closing(sqlite3.connect(temporary)) as output:
                backup.backup(output)
                output.execute("PRAGMA journal_mode=DELETE")
        if DB.exists():
            with closing(sqlite3.connect(DB)) as live:
                if live.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()[0]:
                    raise RuntimeError("Stop the dashboard before restoring")
                recovery = DB.with_suffix(".before-restore.sqlite3")
                if recovery.exists():
                    raise FileExistsError(f"Move the previous recovery copy first: {recovery}")
                shutil.copy2(DB,recovery)
        os.replace(temporary,DB)
        for sidecar in ("-wal","-shm"):
            Path(str(DB)+sidecar).unlink(missing_ok=True)
    finally:
        temporary.unlink(missing_ok=True)

if __name__=="__main__":
    if len(sys.argv)!=2:
        raise SystemExit("Usage: python -m app.restore /path/to/backup.sqlite3")
    restore(sys.argv[1])
