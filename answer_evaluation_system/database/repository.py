"""Thin repository helpers. All SQL uses bound parameters; identifiers come only from code."""
import re

from database.connection import connect

_IDENT = re.compile(r"^[a-z_][a-z0-9_]*$")


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return name


def fetch_all(sql: str, params=(), conn=None) -> list:
    own = conn is None
    conn = conn or connect()
    try:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]
    finally:
        if own:
            conn.close()


def fetch_one(sql: str, params=(), conn=None):
    rows = fetch_all(sql, params, conn)
    return rows[0] if rows else None


def execute(sql: str, params, conn) -> int:
    cur = conn.execute(sql, params)
    return cur.lastrowid


def insert(table: str, data: dict, conn) -> int:
    cols = ", ".join(_ident(c) for c in data)
    marks = ", ".join("?" for _ in data)
    return conn.execute(f"INSERT INTO {_ident(table)} ({cols}) VALUES ({marks})", tuple(data.values())).lastrowid


def update(table: str, row_id: int, data: dict, conn) -> None:
    if not data:
        return
    sets = ", ".join(f"{_ident(c)} = ?" for c in data)
    conn.execute(f"UPDATE {_ident(table)} SET {sets} WHERE id = ?", (*data.values(), row_id))


def delete(table: str, row_id: int, conn) -> None:
    conn.execute(f"DELETE FROM {_ident(table)} WHERE id = ?", (row_id,))
