"""
Seed the beers.db database from a Telegram chat export.

Usage:
    python seed_from_export.py /path/to/result.json

Counts all video messages (video_file and video_message / round videos)
per user and inserts them into the database.
"""

import json
import sys
from pathlib import Path

import database


VIDEO_TYPES = {"video_file", "video_message"}  # video_message = round video


def load_export(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_video_counts(data: dict) -> dict[str, dict]:
    """Return {user_id: {full_name, username, count}} for all video messages."""
    users: dict[str, dict] = {}

    for msg in data.get("messages", []):
        if msg.get("type") != "message":
            continue
        if msg.get("media_type") not in VIDEO_TYPES:
            continue

        user_id = str(msg.get("from_id", "")).replace("user", "")
        if not user_id:
            continue

        full_name = msg.get("from", "Unknown")
        if user_id not in users:
            users[user_id] = {"full_name": full_name, "username": None, "count": 0}
        users[user_id]["count"] += 1

    return users


def seed(users: dict[str, dict]) -> None:
    database.init_db()

    from database import get_connection
    with get_connection() as conn:
        for user_id, info in users.items():
            existing = conn.execute(
                "SELECT count FROM beers WHERE user_id = ?", (user_id,)
            ).fetchone()

            if existing:
                conn.execute(
                    "UPDATE beers SET count = count + ?, full_name = ? WHERE user_id = ?",
                    (info["count"], info["full_name"], user_id),
                )
            else:
                conn.execute(
                    "INSERT INTO beers (user_id, username, full_name, count) VALUES (?, ?, ?, ?)",
                    (user_id, info["username"], info["full_name"], info["count"]),
                )
        conn.commit()


def main():
    if len(sys.argv) < 2:
        print("Usage: python seed_from_export.py /path/to/result.json")
        sys.exit(1)

    export_path = sys.argv[1]
    if not Path(export_path).exists():
        print(f"File not found: {export_path}")
        sys.exit(1)

    print(f"Loading export from {export_path}…")
    data = load_export(export_path)
    users = extract_video_counts(data)

    if not users:
        print("No video messages found in the export.")
        sys.exit(0)

    print(f"\nFound videos from {len(users)} user(s):")
    for uid, info in users.items():
        print(f"  {info['full_name']} (id={uid}): {info['count']} video(s)")

    confirm = input("\nSeed these into the database? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        sys.exit(0)

    seed(users)
    print("✅ Done! Run /leaderboard in the bot to verify.")


if __name__ == "__main__":
    main()
