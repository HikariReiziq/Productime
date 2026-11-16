import os
import sqlite3
from pathlib import Path

from . import migrations

DB_FILENAME = "productime.db"


def open_database(data_dir: Path) -> sqlite3.Connection:
    """Open (creating if needed) the user's database with owner-only permissions."""
    data_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(data_dir, 0o700)
    db_path = data_dir / DB_FILENAME
    if not db_path.exists():
        # Create the file ourselves so it never exists with the default umask permissions.
        os.close(os.open(db_path, os.O_CREAT | os.O_WRONLY, 0o600))
    os.chmod(db_path, 0o600)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA synchronous = NORMAL")
    migrations.apply(conn)
    return conn
