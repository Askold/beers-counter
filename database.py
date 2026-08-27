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
                user_id        INTEGER NOT NULL,
                username       TEXT,
                full_name      TEXT,
                count          INTEGER NOT NULL DEFAULT 0,
                last_video_at  TEXT,
                current_streak INTEGER NOT NULL DEFAULT 0,
                longest_streak INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY (user_id)
            )
        """)
        # migrate: add columns if missing
        cols = [r[1] for r in conn.execute("PRAGMA table_info(beers)").fetchall()]
        if "last_video_at" not in cols:
            conn.execute("ALTER TABLE beers ADD COLUMN last_video_at TEXT")
        if "current_streak" not in cols:
            conn.execute("ALTER TABLE beers ADD COLUMN current_streak INTEGER NOT NULL DEFAULT 0")
        if "longest_streak" not in cols:
            conn.execute("ALTER TABLE beers ADD COLUMN longest_streak INTEGER NOT NULL DEFAULT 0")

        conn.execute("""
            CREATE TABLE IF NOT EXISTS video_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id    INTEGER NOT NULL,
                chat_id    INTEGER NOT NULL,
                sent_at    TEXT NOT NULL,
                message_id INTEGER
            )
        """)
        # migrate: add message_id to video_log if missing
        vl_cols = [r[1] for r in conn.execute("PRAGMA table_info(video_log)").fetchall()]
        if "message_id" not in vl_cols:
            conn.execute("ALTER TABLE video_log ADD COLUMN message_id INTEGER")
        # partial unique index: prevents duplicate message processing on bot restart
        conn.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS uq_video_log_msg
            ON video_log(chat_id, message_id)
            WHERE message_id IS NOT NULL
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
                sent_at    TEXT,
                PRIMARY KEY (chat_id, message_id)
            )
        """)
        # migrate: add sent_at if missing
        tm_cols = [r[1] for r in conn.execute("PRAGMA table_info(text_messages)").fetchall()]
        if "sent_at" not in tm_cols:
            conn.execute("ALTER TABLE text_messages ADD COLUMN sent_at TEXT")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mvp_log (
                date    TEXT PRIMARY KEY,
                user_id INTEGER NOT NULL,
                chat_id INTEGER NOT NULL
            )
        """)
        conn.commit()


# ── Video counting ──────────────────────────────────────────────────────────

def add_beer(
    user_id: int,
    username: str | None,
    full_name: str,
    chat_id: int,
    message_id: int | None = None,
) -> tuple[int, int, bool]:
    """Increment count, log the video, return (user_count, total_count, added).

    added=False when message_id is already in video_log (duplicate delivery on restart).
    In that case beers.count is NOT changed.
    """
    now = datetime.now(MOSCOW).isoformat()
    with get_connection() as conn:
        # Try to record the video first; OR IGNORE silently skips known message_ids.
        conn.execute(
            "INSERT OR IGNORE INTO video_log (user_id, chat_id, sent_at, message_id) VALUES (?, ?, ?, ?)",
            (user_id, chat_id, now, message_id),
        )
        added = conn.execute("SELECT changes()").fetchone()[0] == 1

        if not added:
            # Duplicate — return current counts unchanged.
            row = conn.execute("SELECT count FROM beers WHERE user_id = ?", (user_id,)).fetchone()
            user_count = row["count"] if row else 0
            total = conn.execute("SELECT COALESCE(SUM(count), 0) AS t FROM beers").fetchone()["t"]
            return user_count, total, False

        # Calculate streak before upserting
        today = datetime.now(MOSCOW).date()
        existing = conn.execute(
            "SELECT last_video_at, current_streak, longest_streak FROM beers WHERE user_id = ?",
            (user_id,),
        ).fetchone()

        if existing and existing["last_video_at"]:
            last_date = datetime.fromisoformat(existing["last_video_at"]).date()
            delta = (today - last_date).days
            if delta == 0:
                # Already sent one today — keep streak
                new_streak = existing["current_streak"]
            elif delta == 1:
                # Consecutive day — extend streak
                new_streak = existing["current_streak"] + 1
            else:
                # Gap — reset
                new_streak = 1
        else:
            new_streak = 1

        new_longest = max(new_streak, existing["longest_streak"] if existing else 0)

        conn.execute("""
            INSERT INTO beers (user_id, username, full_name, count, last_video_at,
                               current_streak, longest_streak)
            VALUES (?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username       = excluded.username,
                full_name      = excluded.full_name,
                count          = count + 1,
                last_video_at  = excluded.last_video_at,
                current_streak = excluded.current_streak,
                longest_streak = excluded.longest_streak
        """, (user_id, username, full_name, now, new_streak, new_longest))
        conn.commit()
        user_count = conn.execute(
            "SELECT count FROM beers WHERE user_id = ?", (user_id,)
        ).fetchone()["count"]
        total = conn.execute("SELECT COALESCE(SUM(count), 0) AS t FROM beers").fetchone()["t"]
        return user_count, total, True


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


def get_date_count(chat_id: int, date_str: str) -> int:
    """Count circle videos for a specific date (YYYY-MM-DD) in this chat."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) AS c FROM video_log
            WHERE chat_id = ? AND substr(sent_at, 1, 10) = ?
        """, (chat_id, date_str)).fetchone()
        return row["c"]


def get_videos_last_n_days(n: int = 5) -> int:
    """Total circle videos logged across all chats in the last n days."""
    cutoff = (datetime.now(MOSCOW) - timedelta(days=n)).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM video_log WHERE sent_at >= ?",
            (cutoff,)
        ).fetchone()
        return row["c"]


def get_top_drinkers_for_date(chat_id: int, date_str: str, limit: int = 3) -> list[sqlite3.Row]:
    """Top drinkers for a specific date (YYYY-MM-DD), joined with full_name from beers."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT b.user_id, b.full_name, COUNT(*) AS day_count
            FROM video_log v
            JOIN beers b ON b.user_id = v.user_id
            WHERE v.chat_id = ? AND substr(v.sent_at, 1, 10) = ?
            GROUP BY v.user_id
            ORDER BY day_count DESC
            LIMIT ?
        """, (chat_id, date_str, limit)).fetchall()


def get_leaderboard(limit: int = 10) -> list[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT user_id, full_name, username, count
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


# ── MVP tracking ───────────────────────────────────────────────────────────

def record_mvp(date_str: str, user_id: int, chat_id: int) -> None:
    """Record the day's MVP (top drinker). Safe to call multiple times — overwrites."""
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO mvp_log (date, user_id, chat_id) VALUES (?, ?, ?)",
            (date_str, user_id, chat_id),
        )
        conn.commit()


def get_mvp_counts() -> dict[int, int]:
    """Return {user_id: total_mvp_wins} for all users who have won at least once."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, COUNT(*) AS wins FROM mvp_log GROUP BY user_id"
        ).fetchall()
    return {r["user_id"]: r["wins"] for r in rows}


def get_mvp_streak() -> tuple[int | None, int]:
    """
    Return (user_id, streak_length) for the user holding the current winning streak.
    streak_length=0 means no MVPs recorded yet.
    """
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT date, user_id FROM mvp_log ORDER BY date DESC"
        ).fetchall()
    if not rows:
        return None, 0
    leader_id = rows[0]["user_id"]
    streak = sum(1 for r in rows if r["user_id"] == leader_id and
                 rows[:rows.index(r) + 1][-1]["user_id"] == leader_id)
    # Simpler: count consecutive from the top
    streak = 0
    for r in rows:
        if r["user_id"] == leader_id:
            streak += 1
        else:
            break
    return leader_id, streak


def get_user_rank(user_id: int) -> int:
    """Return the user's all-time rank by count (1 = best). 0 if not found."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) + 1 AS rank
            FROM beers
            WHERE count > (SELECT count FROM beers WHERE user_id = ?)
        """, (user_id,)).fetchone()
    return row["rank"] if row else 0


def get_pivot_score() -> float:
    """
    Composite score for each user = count + mvp_wins*50 + longest_streak*10.
    Returns the average of that score across all users with count > 0.
    Returns 0 if no users.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT b.user_id,
                   b.count,
                   b.longest_streak,
                   COALESCE(m.wins, 0) AS mvp_wins
            FROM beers b
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS wins FROM mvp_log GROUP BY user_id
            ) m ON m.user_id = b.user_id
            WHERE b.count > 0
        """).fetchall()
    if not rows:
        return 0.0
    scores = [r["count"] + r["mvp_wins"] * 50 + r["longest_streak"] * 10 for r in rows]
    return sum(scores) / len(scores)


def get_user_composite_score(user_id: int) -> float:
    """Composite score for a single user."""
    with get_connection() as conn:
        row = conn.execute("""
            SELECT b.count, b.longest_streak,
                   COALESCE(m.wins, 0) AS mvp_wins
            FROM beers b
            LEFT JOIN (
                SELECT user_id, COUNT(*) AS wins FROM mvp_log GROUP BY user_id
            ) m ON m.user_id = b.user_id
            WHERE b.user_id = ?
        """, (user_id,)).fetchone()
    if not row:
        return 0.0
    return row["count"] + row["mvp_wins"] * 50 + row["longest_streak"] * 10


def get_streak(user_id: int) -> tuple[int, int]:
    """Return (current_streak, longest_streak) for a user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT current_streak, longest_streak FROM beers WHERE user_id = ?", (user_id,)
        ).fetchone()
    return (row["current_streak"], row["longest_streak"]) if row else (0, 0)


def get_users_on_streak(min_days: int = 3) -> list[sqlite3.Row]:
    """Users whose current_streak >= min_days, ordered by streak desc."""
    with get_connection() as conn:
        return conn.execute("""
            SELECT user_id, full_name, current_streak, longest_streak
            FROM beers
            WHERE current_streak >= ?
            ORDER BY current_streak DESC
        """, (min_days,)).fetchall()


def get_mvp_wins(user_id: int) -> int:
    """Return total MVP wins for a single user."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS wins FROM mvp_log WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row["wins"]


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
    now = datetime.now(MOSCOW).isoformat()
    with get_connection() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO text_messages (chat_id, message_id, sent_at) VALUES (?, ?, ?)",
            (chat_id, message_id, now),
        )
        conn.commit()


def get_text_message_ids(chat_id: int) -> list[int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT message_id FROM text_messages WHERE chat_id = ?", (chat_id,)
        ).fetchall()
        return [r["message_id"] for r in rows]


def get_text_message_ids_for_date(chat_id: int, date_str: str) -> list[int]:
    """Return message_ids for a specific date (YYYY-MM-DD)."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT message_id FROM text_messages WHERE chat_id = ? AND substr(sent_at,1,10) = ?",
            (chat_id, date_str),
        ).fetchall()
        return [r["message_id"] for r in rows]


def clear_text_messages(chat_id: int) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM text_messages WHERE chat_id = ?", (chat_id,))
        conn.commit()


def clear_text_messages_for_date(chat_id: int, date_str: str) -> None:
    with get_connection() as conn:
        conn.execute(
            "DELETE FROM text_messages WHERE chat_id = ? AND substr(sent_at,1,10) = ?",
            (chat_id, date_str),
        )
        conn.commit()
