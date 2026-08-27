"""
Backfill current_streak and longest_streak in the beers table
from historical video_log data.

Usage:
    docker cp backfill_streaks.py  beers-counter:/app/backfill_streaks.py
    docker exec -it beers-counter python backfill_streaks.py
"""
import sys
from datetime import date, timedelta

sys.path.insert(0, ".")
import database


def compute_streaks(dates: list[date]) -> tuple[int, int]:
    """Given a sorted list of dates a user sent a circle, return (current, longest)."""
    if not dates:
        return 0, 0

    dates = sorted(set(dates))
    today = database.datetime.now(database.MOSCOW).date()

    longest = 1
    current_run = 1

    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days == 1:
            current_run += 1
            longest = max(longest, current_run)
        else:
            current_run = 1

    # current_streak: length of the streak ending on the most recent date
    # (only counts if it touches today or yesterday — a gap means the streak is dead)
    last_date = dates[-1]
    gap = (today - last_date).days
    if gap <= 1:
        # Walk backwards from the end to find the current run length
        current_streak = 1
        for i in range(len(dates) - 1, 0, -1):
            if (dates[i] - dates[i - 1]).days == 1:
                current_streak += 1
            else:
                break
    else:
        current_streak = 0  # streak is broken

    return current_streak, longest


def main():
    database.init_db()
    from database import get_connection

    with get_connection() as conn:
        # Fetch all video_log dates grouped by user
        rows = conn.execute("""
            SELECT user_id, substr(sent_at, 1, 10) AS date
            FROM video_log
            ORDER BY user_id, date
        """).fetchall()

    from collections import defaultdict
    user_dates: dict[int, list[date]] = defaultdict(list)
    for r in rows:
        try:
            user_dates[r["user_id"]].append(date.fromisoformat(r["date"]))
        except ValueError:
            pass

    if not user_dates:
        print("No video_log data found.")
        return

    print(f"Computing streaks for {len(user_dates)} users…\n")

    with get_connection() as conn:
        names = {
            r["user_id"]: r["full_name"]
            for r in conn.execute("SELECT user_id, full_name FROM beers").fetchall()
        }
        for user_id, dates in sorted(user_dates.items()):
            current, longest = compute_streaks(dates)
            name = names.get(user_id, str(user_id))
            print(f"  {name:<30} current={current}  longest={longest}")
            conn.execute("""
                UPDATE beers SET current_streak = ?, longest_streak = ?
                WHERE user_id = ?
            """, (current, longest, user_id))
        conn.commit()

    print(f"\n✅ Done. Streaks backfilled for {len(user_dates)} users.")


if __name__ == "__main__":
    main()
