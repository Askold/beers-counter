"""
Resync beers.db from a Telegram chat export (result.json).

Steps:
  1. Parse result.json — count circle (video_message) messages per real Telegram user_id.
  2. Wipe beers + video_log tables.
  3. Re-insert every user with counts from the export (real positive user IDs).

After this, the bot will count new circle videos on top of these restored counts.

Usage (run on VPS):
    docker cp result.json       beers-counter:/app/result.json
    docker cp resync_from_export.py beers-counter:/app/resync_from_export.py
    docker exec -it beers-counter python resync_from_export.py result.json
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, ".")
import database


def load_export(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_circle_counts(data: dict) -> list[tuple[int, str, str]]:
    """Return list of (user_id, username, full_name, count) sorted by count desc."""
    users: dict[int, dict] = {}
    for msg in data.get("messages", []):
        if msg.get("type") != "message":
            continue
        if msg.get("media_type") != "video_message":
            continue
        raw_id = str(msg.get("from_id", "")).replace("user", "")
        if not raw_id:
            continue
        user_id = int(raw_id)
        full_name = msg.get("from") or "Unknown"
        if user_id not in users:
            users[user_id] = {"full_name": full_name, "count": 0}
        users[user_id]["count"] += 1

    return sorted(
        [(uid, info["full_name"], info["count"]) for uid, info in users.items()],
        key=lambda x: -x[2],
    )


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "result.json"
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"Loading {path}…")
    data = load_export(path)
    users = extract_circle_counts(data)

    total = sum(u[2] for u in users)
    print(f"Found {len(users)} users with circle videos, {total} total.\n")

    for uid, name, count in users:
        print(f"  {name:<30} (id={uid}): {count}")

    print(f"\n⚠️  This will WIPE all existing beers and video_log data and re-seed from the export.")
    confirm = input("Type 'yes' to continue: ").strip().lower()
    if confirm != "yes":
        print("Aborted.")
        sys.exit(0)

    database.init_db()
    from database import get_connection
    with get_connection() as conn:
        conn.execute("DELETE FROM beers")
        conn.execute("DELETE FROM video_log")
        for uid, full_name, count in users:
            conn.execute(
                "INSERT INTO beers (user_id, username, full_name, count) VALUES (?, ?, ?, ?)",
                (uid, None, full_name, count),
            )
        conn.commit()

    print(f"\n✅ Done. Seeded {len(users)} users, {total} beers total.")
    print("Usernames will be filled in automatically when each user next sends a circle video.")


if __name__ == "__main__":
    main()
