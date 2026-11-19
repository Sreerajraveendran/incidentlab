"""Database access for the sample app's items.

The pool is small on purpose: replicas x max_size must stay under Postgres'
max_connections once the autoscaler adds pods.
"""

import os

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

DATABASE_URL = os.getenv("DATABASE_URL")
POOL_MAX = int(os.getenv("DB_POOL_MAX", "4"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id          BIGSERIAL PRIMARY KEY,
    name        TEXT        NOT NULL,
    created_by  TEXT        NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

_pool: AsyncConnectionPool | None = None
_schema_ready = False


class DatabaseUnavailable(Exception):
    pass


async def open_pool() -> None:
    global _pool
    if not DATABASE_URL:
        return
    _pool = AsyncConnectionPool(DATABASE_URL, min_size=1, max_size=POOL_MAX, open=False, timeout=2)
    # wait=False: the app starts even if Postgres is down; requests that need
    # the database fail with 503 until it is back.
    await _pool.open(wait=False)


async def close_pool() -> None:
    if _pool is not None:
        await _pool.close()


def _connection():
    if _pool is None:
        raise DatabaseUnavailable("DATABASE_URL is not set")
    return _pool.connection()


async def _ensure_schema(conn) -> None:
    global _schema_ready
    if not _schema_ready:
        await conn.execute(SCHEMA)
        _schema_ready = True


async def ping() -> bool:
    try:
        async with _connection() as conn:
            await conn.execute("SELECT 1")
        return True
    except Exception:
        return False


async def list_items(limit: int) -> list[dict]:
    try:
        async with _connection() as conn:
            await _ensure_schema(conn)
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(
                "SELECT id, name, created_by, created_at FROM items ORDER BY id DESC LIMIT %s",
                (limit,),
            )
            return await cur.fetchall()
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        raise DatabaseUnavailable(str(exc)) from exc


# The SQL is kept as constants so the request trace can show exactly what ran.
SQL_INSERT = (
    "INSERT INTO items (name, created_by) VALUES (%s, %s) "
    "RETURNING id, name, created_by, created_at"
)
SQL_SELECT = "SELECT id, name, created_by, created_at FROM items WHERE id = %s"
SQL_UPDATE = "UPDATE items SET name = %s WHERE id = %s RETURNING id, name, created_by, created_at"
SQL_DELETE = "DELETE FROM items WHERE id = %s RETURNING id"


async def _one(sql: str, params: tuple) -> dict | None:
    try:
        async with _connection() as conn:
            await _ensure_schema(conn)
            cur = conn.cursor(row_factory=dict_row)
            await cur.execute(sql, params)
            return await cur.fetchone()
    except DatabaseUnavailable:
        raise
    except Exception as exc:
        raise DatabaseUnavailable(str(exc)) from exc


async def create_item(name: str, created_by: str) -> dict:
    return await _one(SQL_INSERT, (name, created_by))


async def get_item(item_id: int) -> dict | None:
    return await _one(SQL_SELECT, (item_id,))


async def update_item(item_id: int, name: str) -> dict | None:
    return await _one(SQL_UPDATE, (name, item_id))


async def delete_item(item_id: int) -> bool:
    return await _one(SQL_DELETE, (item_id,)) is not None
