import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path

DB_PATH = Path("data/beers.db")
GOAL = 1_000_000
MOSCOW = timezone(timedelta(hours=3))


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS beers (
                user_id      INTEGER NOT NULL,
                username     TEXT,
                full_name    TEXT,
                count        INTEGER NOT NULL DEFAULT 0,
                last_video_at TEXT,
                PRIMARY KEY (user_id)
            )
        """)
        # migrate: add last_video_at if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(beers)").fetchall()]
        if "last_video_at" not in cols:
            conn.execute("ALTER TABLE beers ADD COLUMN last_video_at TEXT")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_log (
                id       INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id  INTEGER NOT NULL,
                chat_id  INTEGER NOT NULL,
                sent_at  TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS settings (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS text_messages (
                chat_id    INTEGER NOT NULL,
                message_id INTEGER NOT NULL,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        conn.commit()


# ── Video counting ──────────────────────────────────────────────────────────

def add_beer(user_id: int, username: str | None, full_name: str, chat_id: int) -> tuple[int, int]:
    """Increment count, log the video, return (user_count, total_count)."""
    now = datetime.now(MOSCOW).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO beers (user_id, username, full_name, count, last_video_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username      = excluded.username,
                full_name     = excluded.full_name,
                count         = count + 1,
                last_video_at = excluded.last_video_at
        """, (user_id, username, full_name, now))
        conn.execute(
            "INSERT INTO video_log (user_id, chat_id, sent_at) VALUES (?, ?, ?)",
            (user_id, chat_id, now),
        )
        conn.commit()
        user_count = conn.execute(
            "SELECT count FROM beers WHERE user_id = ?", (user_id,)
        ).fetchone()["count"]
        total = conn.execute("SELECT COALESCE(SUM(count), 0) AS t FROM beers").fetchone()["t"]
        return user_count, total


def get_total_count() -> int:
    with get_connection() as conn:
        return conn.execute(
            "SELECT COALESCE(SUM(count), 0) AS t FROM beers"
        ).fetchone()["t"]


def get_today_count(chat_id: int) -> int:
    """Count circle videos sent today (Moscow date) in this chat."""
    today = datetime.now(MOSCOW).date().isoformat()
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS c FROM video_log
            WHERE chat_id = ? AND substr(sent_at, 1, 10) = ?
        """, (chat_id, today)).fetchone()
        return row["c"]


def get_leaderboard(limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT full_name, username, count
            FROM beers ORDER BY count DESC LIMIT ?
        """, (limit,)).fetchall()


def track_member(user_id: int, username: str | None, full_name: str) -> None:
    """Add a user to the DB if not already known (so they appear in risk zone)."""
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO beers (user_id, username, full_name, count)
            VALUES (?, ?, ?, 0)
        """, (user_id, username, full_name))
        conn.execute("""
            UPDATE beers SET username = ?, full_name = ? WHERE user_id = ?
        """, (username, full_name, user_id))
        conn.commit()


def get_inactive_users(days: int = 20) -> list[sqlite3.Row]:
    """
    Users who:
    - have never sent a circle video (count = 0), OR
    - have a known last_video_at that is older than `days` days.
    Users with count > 0 but NULL last_video_at are legacy records (videos sent
    before timestamp tracking) — we exclude them to avoid false positives.
    """
    cutoff = (datetime.now(MOSCOW) - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        return conn.execute("""
            SELECT full_name, username, count, last_video_at
            FROM beers
            WHERE
                count = 0
                OR (last_video_at IS NOT NULL AND last_video_at < ?)
            ORDER BY last_video_at ASC NULLS FIRST
        """, (cutoff,)).fetchall()


def get_count(user_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT count FROM beers WHERE user_id = ?", (user_id,)
        ).fetchone()
        return row["count"] if row else 0


def reset_count(user_id: int) -> None:
    with get_connection() as conn:
        conn.execute("UPDATE beers SET count = 0 WHERE user_id = ?", (user_id,))
        conn.commit()


# ── Settings (stores group chat_id for daily report) ───────────────────────

def save_chat_id(chat_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES ('chat_id', ?)",
            (str(chat_id),),
        )
        conn.commit()


def get_chat_id() -> int | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = 'chat_id'"
        ).fetchone()
        return int(row["value"]) if row else None


# ── Text message tracking (for /clean) ────────────────────────────────────

def track_text_message(chat_id: int, message_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO text_messages (chat_id, message_id) VALUES (?, ?)",
            (chat_id, message_id),
        )
        conn.commit()


def get_text_message_ids(chat_id: int) -> list[int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT message_id FROM text_messages WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return [r["message_id"] for r in rows]


def clear_text_messages(chat_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM text_messages WHERE chat_id = ?", (chat_id,))
        conn.commit()
