"""
One-off: backfill video_log.file_id for rows recorded before the montage
feature existed (see montage.py), using a Telegram chat export that includes
the actual video message files as media.

Bots have no way to fetch file_id for a message they didn't see live — there
is no getMessage(chat_id, message_id) in the Bot API. So instead this script
re-uploads each matched export video to a chat you choose (e.g. your own DM
with the bot), purely to mint a fresh file_id from Telegram, then stores that
file_id on the matching video_log row.

Matching is positional: for each (user_id, date) with rows missing file_id,
the Nth export video from that user on that date is paired with the Nth such
DB row, both sorted by time. This is exact whenever the counts agree (the
usual case, since both ultimately come from the same underlying messages);
mismatches are reported and only the overlapping prefix is backfilled.

Setup:
    1. Telegram Desktop -> Export chat history -> JSON, with "Video messages"
       checked under media, from the group this export covers.
    2. DM the bot once (so it has a private chat with you), then find your
       own numeric user_id (e.g. via @userinfobot) to use as upload_chat_id.

Usage:
    docker cp result.json                    beers-counter:/app/result.json
    docker cp backfill_video_file_ids.py      beers-counter:/app/backfill_video_file_ids.py
    docker exec -it beers-counter python backfill_video_file_ids.py \\
        result.json <source_chat_id> <upload_chat_id> [--dry-run]

    source_chat_id — the video_log.chat_id these export messages belong to
                     (the group the export was taken from).
    upload_chat_id — where re-uploaded videos are sent to mint file_ids.
    --dry-run      — print the matching plan without uploading or writing.
"""
import asyncio
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, ".")
import database
from telegram import Bot


async def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry_run = "--dry-run" in sys.argv
    if len(args) < 3:
        print(__doc__)
        sys.exit(1)

    export_path = Path(args[0])
    source_chat_id = int(args[1])
    upload_chat_id = int(args[2])

    if not export_path.exists():
        print(f"File not found: {export_path}")
        sys.exit(1)

    export_root = export_path.parent
    print(f"Loading {export_path}…")
    with open(export_path, encoding="utf-8") as f:
        data = json.load(f)

    database.init_db()
    from database import get_connection

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT id, user_id, sent_at FROM video_log
            WHERE chat_id = %s AND file_id IS NULL
            ORDER BY user_id, sent_at
        """, (source_chat_id,)).fetchall()

    if not rows:
        print("No rows are missing file_id for this chat_id — nothing to backfill.")
        return

    missing_by_user_date = defaultdict(list)
    for r in rows:
        missing_by_user_date[(r["user_id"], r["sent_at"][:10])].append(r)

    export_by_user_date = defaultdict(list)
    skipped_no_file = 0
    for msg in data.get("messages", []):
        if msg.get("type") != "message" or msg.get("media_type") != "video_message":
            continue
        raw_id = str(msg.get("from_id", "")).replace("user", "")
        if not raw_id:
            continue
        file_rel = msg.get("file")
        if not file_rel:
            skipped_no_file += 1
            continue
        file_path = export_root / file_rel
        if not file_path.exists():
            skipped_no_file += 1
            continue
        date = msg.get("date", "")
        export_by_user_date[(int(raw_id), date[:10])].append((date, file_path))

    if skipped_no_file:
        print(f"Skipped {skipped_no_file} export video message(s) with no local media file "
              f"(export was created without downloading media, or files were pruned)")

    plan = []  # (video_log_row, file_path)
    unmatched_db = unmatched_export = 0
    for key, db_rows in missing_by_user_date.items():
        db_rows_sorted = sorted(db_rows, key=lambda r: r["sent_at"])
        export_entries = sorted(export_by_user_date.get(key, []), key=lambda e: e[0])
        n = min(len(db_rows_sorted), len(export_entries))
        if len(db_rows_sorted) != len(export_entries):
            print(f"⚠️  user_id={key[0]} date={key[1]}: {len(db_rows_sorted)} DB row(s) "
                  f"without file_id vs {len(export_entries)} export video(s) — "
                  f"matching the first {n} in time order")
        for row, (_, file_path) in zip(db_rows_sorted, export_entries):
            plan.append((row, file_path))
        unmatched_db += len(db_rows_sorted) - n
        unmatched_export += len(export_entries) - n

    print(f"\nMatched {len(plan)} row(s) to backfill "
          f"({unmatched_db} DB row(s) with no export match, "
          f"{unmatched_export} export video(s) with no DB match)\n")

    if dry_run:
        for row, file_path in plan:
            print(f"  [dry-run] video_log.id={row['id']} <- {file_path}")
        return

    if not plan:
        return

    bot = Bot(token=os.environ["TELEGRAM_BOT_TOKEN"])
    backfilled = 0
    async with bot:
        for row, file_path in plan:
            try:
                with open(file_path, "rb") as f:
                    sent = await bot.send_video(chat_id=upload_chat_id, video=f)
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE video_log SET file_id = %s WHERE id = %s",
                        (sent.video.file_id, row["id"]),
                    )
                backfilled += 1
                print(f"  ✓ video_log.id={row['id']} ({file_path.name})")
            except Exception as e:
                print(f"  ✗ video_log.id={row['id']} ({file_path.name}): {e}")
            await asyncio.sleep(1)  # stay well under Telegram's flood limits

    print(f"\nDone: {backfilled}/{len(plan)} backfilled.")


if __name__ == "__main__":
    asyncio.run(main())
