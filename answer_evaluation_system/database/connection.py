"""SQLite connection management, transactions and versioned SQL migrations."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path

import config

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_db_path = Path(config.DATABASE_PATH)


def configure(path) -> None:
    """Point the application at a different database file (used by tests / deployment)."""
    global _db_path
    _db_path = Path(path)


def get_db_path() -> Path:
    return _db_path


def connect() -> sqlite3.Connection:
    _db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(_db_path), timeout=30, isolation_level=None)  # explicit transactions
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


@contextmanager
def transaction():
    """Atomic unit of work: commits on success, rolls back on any exception."""
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def run_migrations() -> list:
    """Apply any pending migrations/*.sql files in order. Returns the versions applied."""
    conn = connect()
    applied = []
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(version TEXT PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT (datetime('now','localtime')))")
        done = {r["version"] for r in conn.execute("SELECT version FROM schema_migrations")}
        for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if path.stem in done:
                continue
            conn.executescript("BEGIN;\n" + path.read_text(encoding="utf-8") +
                               f"\nINSERT INTO schema_migrations(version) VALUES ('{path.stem}');\nCOMMIT;")
            applied.append(path.stem)
    finally:
        conn.close()
    return applied
