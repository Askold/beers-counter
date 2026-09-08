"""Ad-hoc report: days when the MVP result was a tie.

For every day, the MVP is whoever sent the most circle videos. This script
lists the days where two or more people reached that top count — i.e. the
MVP crown was shared on the drink count and only the tiebreak (all-time MVP
wins) decided who got it recorded in ``mvp_log``.

Run from the project root so ``data/beers.db`` resolves:

    python mvp_ties.py                 # main group, any tie
    python mvp_ties.py --min 2         # only ties where the top count was >= 2
    python mvp_ties.py --chat-id -100  # a specific chat
    python mvp_ties.py --all-chats     # every chat, keyed by (chat, day)
"""
import argparse
from collections import defaultdict

import database


def find_mvp_ties(chat_id: int | None, min_beers: int) -> list[dict]:
    """Return one entry per (chat, day) where >= 2 users share the day's top count.

    Each entry: {chat_id, day, top_count, users: [(full_name, count, is_recorded_mvp)]}.
    """
    params: list = []
    chat_filter = ""
    if chat_id is not None:
        chat_filter = "WHERE v.chat_id = ?"
        params.append(chat_id)

    with database.get_connection() as conn:
        # Per (chat, day, user) drink count.
        daily = conn.execute(f"""
            SELECT v.chat_id                AS chat_id,
                   substr(v.sent_at, 1, 10) AS day,
                   v.user_id                AS user_id,
                   COUNT(*)                 AS cnt
            FROM video_log v
            {chat_filter}
            GROUP BY v.chat_id, day, v.user_id
        """, params).fetchall()

        names = {r["user_id"]: r["full_name"]
                 for r in conn.execute("SELECT user_id, full_name FROM beers").fetchall()}
        recorded_mvp = {(r["chat_id"], r["date"]): r["user_id"]
                        for r in conn.execute("SELECT chat_id, date, user_id FROM mvp_log").fetchall()}

    # Group rows by (chat, day), then keep the users sitting at the day's max.
    by_day: dict[tuple[int, str], list] = defaultdict(list)
    for r in daily:
        by_day[(r["chat_id"], r["day"])].append((r["user_id"], r["cnt"]))

    ties = []
    for (cid, day), rows in by_day.items():
        top = max(cnt for _, cnt in rows)
        if top < min_beers:
            continue
        leaders = [(uid, cnt) for uid, cnt in rows if cnt == top]
        if len(leaders) < 2:
            continue
        mvp_uid = recorded_mvp.get((cid, day))
        users = sorted(
            ((names.get(uid, str(uid)), cnt, uid == mvp_uid) for uid, cnt in leaders),
            key=lambda t: (not t[2], t[0].lower()),
        )
        ties.append({"chat_id": cid, "day": day, "top_count": top, "users": users})

    ties.sort(key=lambda e: (e["day"], e["chat_id"]))
    return ties


def main() -> None:
    parser = argparse.ArgumentParser(description="Days when the MVP result was a tie.")
    parser.add_argument("--min", type=int, default=1, metavar="N",
                        help="only show ties where the top count was at least N (default: 1)")
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--chat-id", type=int, help="restrict to this chat (default: the stored main group)")
    group.add_argument("--all-chats", action="store_true", help="scan every chat")
    args = parser.parse_args()

    if args.all_chats:
        chat_id = None
    elif args.chat_id is not None:
        chat_id = args.chat_id
    else:
        chat_id = database.get_chat_id()
        if chat_id is None:
            parser.error("no main group stored yet — pass --chat-id or --all-chats")

    ties = find_mvp_ties(chat_id, args.min)

    print("Дни с ничьёй за MVP")
    print("===================")
    if not ties:
        print("\nНичьих не найдено.")
        return

    show_chat = chat_id is None
    for e in ties:
        head = f"\n{e['day']}"
        if show_chat:
            head += f"  [chat {e['chat_id']}]"
        head += f"  —  {e['top_count']} 🍺  ({len(e['users'])} чел.)"
        print(head)
        for name, cnt, is_mvp in e["users"]:
            mark = "⭐" if is_mvp else "  "
            print(f"  {mark} {name} — {cnt} 🍺")

    print(f"\nИтого: {len(ties)} дн. с ничьёй за MVP")


if __name__ == "__main__":
    main()
