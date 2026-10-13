"""Postgres-backed board store (Neon-friendly).

``PostgresBoardStore`` implements the same interface and semantics as
:class:`app.store.InMemoryBoardStore`, persisting boards and items in Postgres
via psycopg 3 and a small connection pool.

Design notes
------------
* The connection string is passed in by the caller (``main.py`` reads it from
  the ``DATABASE_URL`` environment variable). It is never logged or stored in
  any message by this module.
* Neon's ``-pooler`` endpoint is PgBouncer in transaction mode, so server-side
  prepared statements are disabled (``prepare_threshold=None``) and no session
  state is relied upon. Every operation runs inside a single transaction that
  is committed when the pooled connection context exits.
* Neon suspends idle computes, which silently kills pooled connections. The
  pool therefore validates connections on checkout
  (``ConnectionPool.check_connection``) and recycles idle/old connections.
* Claim atomicity: :meth:`item_lock` returns a process-local ``RLock`` per
  ``(board_id, item_id)``. The service wraps its get -> mutate -> save claim
  flow in that lock, so claims are atomic as long as the app runs as a SINGLE
  process (which it must anyway, because the WebSocket hub is in-process).
  ``save_item`` is an upsert, so the DB itself does not reject a second
  concurrent claim; do not run multiple workers/instances.
* psycopg is imported lazily in the constructor so the application still runs
  without psycopg installed when Postgres is not configured.
"""

from __future__ import annotations

import threading
import time
from typing import Any

from .models import Board, Item

_SCHEMA_STATEMENTS = (
    """
    CREATE TABLE IF NOT EXISTS potluck_boards (
        id text PRIMARY KEY,
        created_at double precision NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS potluck_items (
        seq bigserial,
        board_id text NOT NULL,
        id text NOT NULL,
        title text NOT NULL,
        image_url text,
        claimed_by text,
        created_at double precision NOT NULL,
        PRIMARY KEY (board_id, id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS potluck_items_board_seq_idx
        ON potluck_items (board_id, seq)
    """,
)

# Additive, idempotent migration for the adder/employee/date enhancement.
# Existing rows get NULL (legacy items); no data is rewritten.
_MIGRATION_STATEMENTS = (
    "ALTER TABLE potluck_items ADD COLUMN IF NOT EXISTS added_by text",
    "ALTER TABLE potluck_items ADD COLUMN IF NOT EXISTS employee_id text",
    "ALTER TABLE potluck_items ADD COLUMN IF NOT EXISTS bring_date text",
)

# Serializes concurrent schema setup across instances (arbitrary constant).
_SCHEMA_LOCK_KEY = 7_262_026_001

_ITEM_COLUMNS = (
    "id, board_id, title, image_url, claimed_by, created_at, "
    "added_by, employee_id, bring_date"
)


def _row_to_item(row: tuple[Any, ...]) -> Item:
    return Item(
        id=row[0],
        board_id=row[1],
        title=row[2],
        image_url=row[3],
        claimed_by=row[4],
        created_at=float(row[5]),
        added_by=row[6],
        employee_id=row[7],
        bring_date=row[8],
    )


class PostgresBoardStore:
    """Postgres implementation of the ``BoardStore`` interface."""

    def __init__(self, conninfo: str) -> None:
        # Lazy import: only needed when Postgres is actually configured.
        from psycopg_pool import ConnectionPool

        self._pool = ConnectionPool(
            conninfo,
            min_size=1,
            max_size=5,
            open=False,  # opened explicitly below (and on demand)
            timeout=15,
            # Replace connections that Neon suspended/closed instead of failing.
            check=ConnectionPool.check_connection,
            max_idle=240,
            max_lifetime=1800,
            # PgBouncer (transaction mode): no server-side prepared statements.
            kwargs={"prepare_threshold": None},
            name="potluck-pg",
        )
        self._open_guard = threading.Lock()
        self._opened = False

        # Per-item locks keyed by (board_id, item_id). Created lazily.
        self._item_locks: dict[tuple[str, str], threading.RLock] = {}
        self._item_locks_guard = threading.Lock()

        try:
            self.ensure_schema()
        except BaseException:
            # Release pool resources, then propagate the original error as-is
            # (no wrapping, so the connection string is never echoed).
            try:
                self.close()
            except Exception:
                pass
            raise

    # ------------------------------------------------------------------
    # Pool / schema management
    # ------------------------------------------------------------------
    def _connection(self):
        """Return a pooled-connection context manager, opening the pool once."""
        if not self._opened:
            with self._open_guard:
                if not self._opened:
                    self._pool.open(wait=True, timeout=30)
                    self._opened = True
        return self._pool.connection()

    def ensure_schema(self) -> None:
        """Create tables, index and new columns (idempotent, one transaction).

        The pooled-connection context commits on success and rolls back on any
        exception, so a failed migration leaves no partial columns. The lock
        timeout and transaction-level advisory lock keep concurrent starts from
        racing or hanging; both are released at commit/rollback.
        """
        with self._connection() as conn:
            conn.execute("SET LOCAL lock_timeout = '10s'")
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_LOCK_KEY,))
            for statement in _SCHEMA_STATEMENTS:
                conn.execute(statement)
            for statement in _MIGRATION_STATEMENTS:
                conn.execute(statement)

    def close(self) -> None:
        """Shut the connection pool down."""
        with self._open_guard:
            self._pool.close()
            self._opened = False

    # ------------------------------------------------------------------
    # Board operations
    # ------------------------------------------------------------------
    def get_board(self, board_id: str) -> Board | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id, created_at FROM potluck_boards WHERE id = %s",
                (board_id,),
            ).fetchone()
        if row is None:
            return None
        return Board(id=row[0], created_at=float(row[1]))

    def create_board(self, board_id: str) -> Board:
        """Idempotently create the board and return the stored row."""
        with self._connection() as conn:
            conn.execute(
                "INSERT INTO potluck_boards (id, created_at) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (board_id, time.time()),
            )
            row = conn.execute(
                "SELECT id, created_at FROM potluck_boards WHERE id = %s",
                (board_id,),
            ).fetchone()
        return Board(id=row[0], created_at=float(row[1]))

    # ------------------------------------------------------------------
    # Item operations
    # ------------------------------------------------------------------
    def save_item(self, board_id: str, item: Item) -> None:
        """Upsert ``item``; an update keeps its original ``seq``/``created_at``."""
        with self._connection() as conn:
            # Make sure the board exists so lazy creation works.
            conn.execute(
                "INSERT INTO potluck_boards (id, created_at) VALUES (%s, %s) "
                "ON CONFLICT (id) DO NOTHING",
                (board_id, time.time()),
            )
            conn.execute(
                "INSERT INTO potluck_items "
                "(board_id, id, title, image_url, claimed_by, created_at, "
                "added_by, employee_id, bring_date) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (board_id, id) DO UPDATE SET "
                "title = EXCLUDED.title, "
                "image_url = EXCLUDED.image_url, "
                "claimed_by = EXCLUDED.claimed_by",
                (
                    board_id,
                    item.id,
                    item.title,
                    item.image_url,
                    item.claimed_by,
                    item.created_at,
                    item.added_by,
                    item.employee_id,
                    item.bring_date,
                ),
            )

    def get_item(self, board_id: str, item_id: str) -> Item | None:
        with self._connection() as conn:
            row = conn.execute(
                f"SELECT {_ITEM_COLUMNS} FROM potluck_items "
                "WHERE board_id = %s AND id = %s",
                (board_id, item_id),
            ).fetchone()
        return None if row is None else _row_to_item(row)

    def list_items(self, board_id: str) -> list[Item]:
        with self._connection() as conn:
            rows = conn.execute(
                f"SELECT {_ITEM_COLUMNS} FROM potluck_items "
                "WHERE board_id = %s ORDER BY seq",
                (board_id,),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    def delete_item(self, board_id: str, item_id: str) -> bool:
        """Delete one item; True if a row was removed.

        The per-item lock entry is intentionally kept (see InMemoryBoardStore).
        """
        with self._connection() as conn:
            cur = conn.execute(
                "DELETE FROM potluck_items WHERE board_id = %s AND id = %s",
                (board_id, item_id),
            )
            return cur.rowcount > 0

    def delete_expired(self, before_date: str) -> list[Item]:
        """Delete every item dated strictly before ``before_date`` (all boards).

        Items without a ``bring_date`` are never removed. Returns the removed
        items so the caller can broadcast them.
        """
        with self._connection() as conn:
            rows = conn.execute(
                "DELETE FROM potluck_items "
                "WHERE bring_date IS NOT NULL AND bring_date < %s "
                f"RETURNING {_ITEM_COLUMNS}",
                (before_date,),
            ).fetchall()
        return [_row_to_item(row) for row in rows]

    # ------------------------------------------------------------------
    # Concurrency helper
    # ------------------------------------------------------------------
    def item_lock(self, board_id: str, item_id: str) -> threading.RLock:
        """Return the process-local lock for ``(board_id, item_id)``.

        Same lock object for the same key, independent locks for different
        keys. Atomicity of claim/unclaim relies on this lock plus the
        single-process design of the app (see the module docstring).
        """
        key = (board_id, item_id)
        with self._item_locks_guard:
            lock = self._item_locks.get(key)
            if lock is None:
                lock = threading.RLock()
                self._item_locks[key] = lock
            return lock
