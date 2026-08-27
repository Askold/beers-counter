"""
Import all non-circle messages from a Telegram chat export (result.json)
into the text_messages table so the bot can clean them up.

Skips:
  - circle videos (media_type == "video_message")
  - service messages (type != "message")
  - records already in the table (INSERT OR IGNORE)

Usage:
    docker cp result.json                beers-counter:/app/result.json
    docker cp restore_text_messages.py   beers-counter:/app/restore_text_messages.py
    docker exec -it beers-counter python restore_text_messages.py result.json
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

    with get_connection() as conn:
        chat_id_row = conn.execute(
            "SELECT value FROM settings WHERE key='chat_id'"
        ).fetchone()
        chat_id = int(chat_id_row["value"]) if chat_id_row else 0

        existing = conn.execute(
            "SELECT COUNT(*) AS c FROM text_messages WHERE chat_id = ?", (chat_id,)
        ).fetchone()["c"]

    print(f"Using chat_id: {chat_id}")
    print(f"Records already in text_messages for this chat: {existing}\n")

    records = []
    skipped_circles = 0
    skipped_service = 0

    for msg in data.get("messages", []):
        # Only regular user messages
        if msg.get("type") != "message":
            skipped_service += 1
            continue
        # Skip circle videos — those are beers, not clutter
        if msg.get("media_type") == "video_message":
            skipped_circles += 1
            continue

        message_id = msg.get("id")
        sent_at = msg.get("date", "")  # ISO timestamp from export

        if not message_id:
            continue

        records.append((chat_id, message_id, sent_at))

    print(f"Skipped {skipped_circles} circle videos")
    print(f"Skipped {skipped_service} service messages")
    print(f"Inserting up to {len(records)} records into text_messages…\n")

    if not records:
        print("Nothing to insert.")
        return

    with get_connection() as conn:
        conn.executemany(
            "INSERT OR IGNORE INTO text_messages (chat_id, message_id, sent_at) VALUES (?, ?, ?)",
            records,
        )
        conn.commit()
        inserted = conn.execute("SELECT changes()").fetchone()[0]

    print(f"✅ Done. {inserted} new records inserted ({len(records) - inserted} were already present).")
    print("Run /clean or wait for midnight auto-clean to delete them from the chat.")


if __name__ == "__main__":
    main()
