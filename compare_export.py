"""
Compare Telegram chat export with the bot database.

Usage:
    python compare_export.py /path/to/result.json

Shows per-user differences between the export and the current DB counts.
Only counts video_message (circle/round videos), not regular video_file.
"""

import json
import sys
from pathlib import Path

import database


def load_export(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def extract_circle_counts(data: dict) -> dict[str, dict]:
    """Return {user_id: {full_name, count}} for circle video messages only."""
    users: dict[str, dict] = {}
    for msg in data.get("messages", []):
        if msg.get("type") != "message":
            continue
        if msg.get("media_type") != "video_message":  # only round videos
            continue
        user_id = str(msg.get("from_id", "")).replace("user", "")
        if not user_id:
            continue
        full_name = msg.get("from", "Unknown")
        if user_id not in users:
            users[user_id] = {"full_name": full_name, "count": 0}
        users[user_id]["count"] += 1
    return users


def get_db_counts() -> dict[str, dict]:
    database.init_db()
    from database import get_connection
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT user_id, full_name, username, count FROM beers"
        ).fetchall()
    result = {}
    for r in rows:
        result[str(r["user_id"])] = {
            "full_name": r["full_name"],
            "username": r["username"],
            "count": r["count"],
        }
    return result


def main():
    if len(sys.argv) < 2:
        print("Usage: python compare_export.py /path/to/result.json")
        sys.exit(1)

    path = sys.argv[1]
    if not Path(path).exists():
        print(f"File not found: {path}")
        sys.exit(1)

    print(f"Loading {path}…")
    data = load_export(path)
    export = extract_circle_counts(data)
    db = get_db_counts()

    print(f"\nExport: {len(export)} users with circle videos")
    print(f"DB:     {len(db)} users total\n")

    all_ids = set(export.keys()) | set(db.keys())
    issues = []

    for uid in sorted(all_ids):
        e = export.get(uid, {})
        d = db.get(uid, {})
        e_count = e.get("count", 0)
        d_count = d.get("count", 0)
        name = e.get("full_name") or d.get("full_name") or uid

        diff = d_count - e_count
        sign = f"+{diff}" if diff > 0 else str(diff) if diff != 0 else "="
        issues.append((name, uid, e_count, d_count, sign, diff != 0))

    print(f"{'Name':<30} {'ID':<15} {'Export':>8} {'DB':>6} {'Diff':>6}")
    print("-" * 70)
    for name, uid, e_count, d_count, sign, has_diff in sorted(issues, key=lambda x: -abs(x[3])):
        marker = " ⚠️" if has_diff else ""
        print(f"{name:<30} {uid:<15} {e_count:>8} {d_count:>6} {sign:>6}{marker}")

    mismatches = sum(1 for x in issues if x[5])
    print(f"\nTotal: {len(issues)} users, {mismatches} with mismatched counts.")


if __name__ == "__main__":
    main()
