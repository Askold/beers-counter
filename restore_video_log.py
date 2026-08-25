"""
Restore video_log from a Telegram chat export (result.json).

Only inserts records for dates NOT already covered in video_log,
so running it after the bot has been live won't duplicate today's entries.

Usage:
    docker cp result.json              beers-counter:/app/result.json
    docker cp restore_video_log.py     beers-counter:/app/restore_video_log.py
    docker exec -it beers-counter python restore_video_log.py result.json
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
import database


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"Loading {path}…")
    with open(path, encoding="utf-8") as f:
        data = json.load(f)

    database.init_db()
    from database import get_connection

    # Dates already in video_log — skip them to avoid duplicates
    with get_connection() as conn:
        existing_dates = {
            r["date"]
            for r in conn.execute(
                "SELECT DISTINCT substr(sent_at,1,10) AS date FROM video_log"
            ).fetchall()
        }
        chat_id_row = conn.execute(
            "SELECT value FROM settings WHERE key='chat_id'"
        ).fetchone()
        chat_id = int(chat_id_row["value"]) if chat_id_row else 0

    print(f"Dates already in video_log: {sorted(existing_dates) or 'none'}")
    print(f"Using chat_id: {chat_id}\n")

    # Parse circle videos from export
    records = []
    skipped_dates = set()
    for msg in data.get("messages", []):
        if msg.get("type") != "message":
            continue
        if msg.get("media_type") != "video_message":
            continue
        raw_id = str(msg.get("from_id", "")).replace("user", "")
        if not raw_id:
            continue
        user_id = int(raw_id)
        date_str = msg.get("date", "")[:10]  # YYYY-MM-DD
        sent_at = msg.get("date", "")        # full ISO timestamp from export

        if date_str in existing_dates:
            skipped_dates.add(date_str)
            continue

        records.append((user_id, chat_id, sent_at))

    print(f"Skipped {len(skipped_dates)} dates already tracked: {sorted(skipped_dates)}")
    print(f"Inserting {len(records)} records into video_log…\n")

    if not records:
        print("Nothing to insert.")
        return

    with get_connection() as conn:
        conn.executemany(
            "INSERT INTO video_log (user_id, chat_id, sent_at) VALUES (?, ?, ?)",
            records,
        )
        conn.commit()

        # Show summary by date
        rows = conn.execute("""
            SELECT substr(sent_at,1,10) AS date, COUNT(*) AS cnt
            FROM video_log
            WHERE substr(sent_at,1,10) NOT IN ({})
            GROUP BY date ORDER BY date
        """.format(",".join(f"'{d}'" for d in existing_dates) if existing_dates else "'__none__'")
        ).fetchall()
        for r in rows:
            print(f"  {r['date']}  {r['cnt']} videos")

    print(f"\n✅ Done. {len(records)} records restored.")
    print("Now run the MVP backfill command to populate mvp_log.")


if __name__ == "__main__":
    main()
