"""One-off migration: recompute every recorded daily MVP with the current
tiebreak rule.

The daily MVP is whoever sent the most circle videos that day. When several
people are tied on that count, the crown goes to whoever got there first —
the earliest final circle of the day (see ``database.get_top_drinkers_for_date``).
Older rows in ``mvp_log`` were decided by a different tiebreak (all-time MVP
wins), so this script rewrites them.

Run from the project root so ``data/beers.db`` resolves:

    python reassign_mvp.py            # preview the changes
    python reassign_mvp.py --apply    # write them to mvp_log
"""
import argparse

import database


def compute_changes() -> list[tuple[str, int, int, int]]:
    """Return [(date, chat_id, old_user_id, new_user_id), ...] for MVP rows the
    current rule would decide differently."""
    with database.get_connection() as conn:
        recorded = conn.execute(
            "SELECT date, user_id, chat_id FROM mvp_log ORDER BY date"
        ).fetchall()

    changes = []
    for row in recorded:
        top = database.get_top_drinkers_for_date(row["chat_id"], row["date"], limit=1)
        if not top:
            # No circles remain for that day (records were removed) — leave it.
            continue
        new_uid = top[0]["user_id"]
        if new_uid != row["user_id"]:
            changes.append((row["date"], row["chat_id"], row["user_id"], new_uid))
    return changes


def main() -> None:
    parser = argparse.ArgumentParser(description="Recompute recorded daily MVPs.")
    parser.add_argument("--apply", action="store_true",
                        help="write the changes to mvp_log (default: preview only)")
    args = parser.parse_args()

    with database.get_connection() as conn:
        names = {r["user_id"]: r["full_name"]
                 for r in conn.execute("SELECT user_id, full_name FROM beers").fetchall()}
    name = lambda uid: names.get(uid, str(uid))

    changes = compute_changes()
    if not changes:
        print("Все MVP уже соответствуют текущему правилу — менять нечего.")
        return

    for date, _chat, old, new in changes:
        print(f"{date}: {name(old)} → {name(new)}")
    print(f"\nВсего изменений: {len(changes)}")

    if not args.apply:
        print("\nЭто предпросмотр. Запусти с --apply, чтобы записать.")
        return

    with database.get_connection() as conn:
        conn.executemany(
            "UPDATE mvp_log SET user_id = ? WHERE date = ? AND chat_id = ?",
            [(new, date, chat) for date, chat, _old, new in changes],
        )
        conn.commit()
    print(f"\nГотово: обновлено {len(changes)} записей в mvp_log.")


if __name__ == "__main__":
    main()
