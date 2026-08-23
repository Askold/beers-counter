import sqlite3
from pathlib import Path

DB_PATH = Path("data/beers.db")


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beers (
                user_id    INTEGER NOT NULL,
                username   TEXT,
                full_name  TEXT,
                count      INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id)
            )
        """)
        conn.commit()


def add_beer(user_id: int, username: str | None, full_name: str) -> int:
    """Increment the beer count for a user and return the new total."""
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO beers (user_id, username, full_name, count)
            VALUES (?, ?, ?, 1)
            ON CONFLICT(user_id) DO UPDATE SET
                username  = excluded.username,
                full_name = excluded.full_name,
                count     = count + 1
        """, (user_id, username, full_name))
        conn.commit()
        row = conn.execute(
            "SELECT count FROM beers WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["count"]


def get_count(user_id: int) -> int:
    """Return the beer count for a single user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT count FROM beers WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["count"] if row else 0


def get_leaderboard(limit: int = 10) -> list[sqlite3.Row]:
    """Return top users ordered by beer count."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT full_name, username, count
            FROM beers
            ORDER BY count DESC
            LIMIT ?
        """, (limit,)).fetchall()


def reset_count(user_id: int) -> None:
    """Reset a user's beer count to zero."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE beers SET count = 0 WHERE user_id = ?", (user_id,)
        )
        conn.commit()
