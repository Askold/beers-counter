"""One-time data migration: copy data/beers.db (SQLite) into the Postgres
database referenced by DATABASE_URL.

Run once the postgres container is up and its schema has been created
(start the bot against it, or `python -c "import database; database.init_db()"`,
before running this):

    docker-compose up -d postgres
    python scripts/migrate_to_postgres.py

Requires data/beers.db to exist (run from the project root) and DATABASE_URL
to be set — loaded from .env if present and not already in the environment.
"""
import os
import sqlite3
import sys
from pathlib import Path

import psycopg

SQLITE_PATH = Path("data/beers.db")

# Explicit column lists — never SELECT *, so column order can't silently drift
# between the two schemas.
TABLES = {
    "beers": ["user_id", "username", "full_name", "count", "last_video_at",
              "current_streak", "longest_streak"],
    "video_log": ["id", "user_id", "chat_id", "sent_at", "message_id"],
    "settings": ["key", "value"],
    "text_messages": ["chat_id", "message_id", "sent_at"],
    "mvp_log": ["date", "user_id", "chat_id"],
}


def _load_dotenv() -> None:
    if "DATABASE_URL" in os.environ:
        return
    env_path = Path(".env")
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())


def migrate() -> None:
    _load_dotenv()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        sys.exit("DATABASE_URL is not set (checked environment and .env)")
    if not SQLITE_PATH.exists():
        sys.exit(f"{SQLITE_PATH} not found — run this from the project root")

    sqlite_conn = sqlite3.connect(SQLITE_PATH)
    sqlite_conn.row_factory = sqlite3.Row

    with psycopg.connect(database_url) as pg_conn:
        for table, columns in TABLES.items():
            rows = sqlite_conn.execute(f"SELECT {', '.join(columns)} FROM {table}").fetchall()
            if not rows:
                print(f"{table}: 0 rows (nothing to copy)")
                continue
            placeholders = ", ".join(["%s"] * len(columns))
            with pg_conn.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    [tuple(r) for r in rows],
                )
            print(f"{table}: copied {len(rows)} rows")

        # video_log.id is an identity column — after inserting explicit ids,
        # bump the sequence past the max so future auto-generated ids don't collide.
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT setval(pg_get_serial_sequence('video_log', 'id'), "
                "COALESCE((SELECT MAX(id) FROM video_log), 1))"
            )

        print("\nRow count check:")
        for table, _columns in TABLES.items():
            sqlite_count = sqlite_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            pg_count = pg_conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            status = "OK" if sqlite_count == pg_count else "MISMATCH"
            print(f"  {table}: sqlite={sqlite_count} postgres={pg_count} [{status}]")

    sqlite_conn.close()


if __name__ == "__main__":
    migrate()
