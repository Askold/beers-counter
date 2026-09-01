"""Daily report: the /report command, the midnight job, and its data collector."""
import asyncio
import datetime
import logging
import math

from telegram import Update
from telegram.ext import ContextTypes

import database
from common import (
    GOAL,
    MOSCOW,
    bulk_delete_messages,
    escape_md,
    fmt,
    medal,
    pinned_message_ids,
)

logger = logging.getLogger(__name__)


async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual trigger of the daily report — admin only (or any private chat).
    Report is sent to the chat where the command was called,
    but data always comes from the main group (stored chat_id).
    """
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        main_chat_id = database.get_chat_id()
        if not main_chat_id:
            await update.message.reply_text("Бот ещё не добавлен ни в одну группу\\.", parse_mode="MarkdownV2")
            return
        await daily_report(context, send_to=main_chat_id)
        await update.message.reply_text("✅ Отчёт отправлен в группу\\.", parse_mode="MarkdownV2")
    else:
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Только администраторы могут вызвать отчёт\\.", parse_mode="MarkdownV2")
            return
        # Send to this group, but data from main group
        await daily_report(context, send_to=chat.id)


def collect_report_data(main_chat_id: int, report_date: str, scheduled: bool) -> dict:
    """Run every daily-report query in one batch. Synchronous — invoke via
    asyncio.to_thread so the ~10 sequential queries don't block the event loop
    (matters at midnight when this fires once per registered group)."""
    top3_day = database.get_top_drinkers_for_date(main_chat_id, report_date, limit=3)
    # Record MVP for scheduled (midnight) runs — write stays on the worker thread too.
    if scheduled and top3_day:
        database.record_mvp(report_date, top3_day[0]["user_id"], main_chat_id)
    mvp_streak_uid, mvp_streak = database.get_mvp_streak()
    return {
        "today_count": database.get_date_count(main_chat_id, report_date),
        "total": database.get_total_count(),
        "last5": database.get_videos_last_n_days(5),
        "top3": database.get_leaderboard(limit=3),
        "top3_day": top3_day,
        "on_fire": database.get_users_on_streak(min_days=3),
        "mvp_counts": database.get_mvp_counts(),
        "mvp_streak_uid": mvp_streak_uid,
        "mvp_streak": mvp_streak,
    }


async def daily_report(
    context: ContextTypes.DEFAULT_TYPE,
    send_to: int | None = None,
    scheduled: bool = False,
) -> None:
    """
    send_to   — which chat receives the report message (defaults to main group).
    Data (video counts, leaderboard, MVP) always comes from the main stored chat_id.
    Auto-clean runs only on the main group, regardless of where the report is sent.
    """
    main_chat_id = database.get_chat_id()
    if not main_chat_id:
        logger.warning("daily_report: no chat_id saved yet, skipping")
        return
    if send_to is None:
        send_to = main_chat_id

    now = datetime.datetime.now(MOSCOW)
    if scheduled:
        report_date = (now - datetime.timedelta(days=1)).date().isoformat()
        period_label = "Вчера было выпито"
        heroes_label = "Герои вчерашнего дня"
        no_heroes = "Вчера никто не пил\\."
    else:
        report_date = now.date().isoformat()
        period_label = "Сегодня выпито"
        heroes_label = "Герои сегодняшнего дня"
        no_heroes = "Сегодня ещё никто не пил\\."

    # All data queries use main_chat_id; run them off the event loop as one batch.
    data = await asyncio.to_thread(collect_report_data, main_chat_id, report_date, scheduled)
    today_count = data["today_count"]
    total = data["total"]
    remaining = max(0, GOAL - total)
    top3 = data["top3"]
    top3_day = data["top3_day"]
    on_fire = data["on_fire"]
    mvp_counts = data["mvp_counts"]
    mvp_streak_uid = data["mvp_streak_uid"]
    mvp_streak = data["mvp_streak"]

    avg_per_day = data["last5"] / 5
    if avg_per_day > 0 and remaining > 0:
        days_left = math.ceil(remaining / avg_per_day)
        pace_line = f"При текущем темпе потребуется *{fmt(days_left)}* дн\\. для выполнения плана 📅"
    elif remaining == 0:
        pace_line = "🎊 Цель достигнута\\!"
    else:
        pace_line = "Темп пока не определён"

    top_lines = []
    for i, row in enumerate(top3):
        stars = "⭐" * mvp_counts.get(row["user_id"], 0)
        top_lines.append(f"{medal(i)} {escape_md(row['full_name'])} {stars}— {fmt(row['count'])} 🍺")

    top_day_lines = []
    for i, row in enumerate(top3_day):
        top_day_lines.append(f"{medal(i)} {escape_md(row['full_name'])} — {fmt(row['day_count'])} 🍺")

    streak_line = ""
    if mvp_streak >= 2 and mvp_streak_uid is not None:
        streak_user = next((r for r in top3 if r["user_id"] == mvp_streak_uid), None)
        if streak_user:
            streak_name = escape_md(streak_user["full_name"])
            streak_line = f"🔥 {streak_name} — MVP уже *{mvp_streak}* дня подряд\\!"

    fire_lines = [
        f"🔥 {escape_md(row['full_name'])} — {row['current_streak']} дн\\. подряд"
        for row in on_fire
    ]

    lines = [
        f"🎉 Поздравляю\\! {escape_md(period_label)} *{fmt(today_count)}* пива",
        f"Осталось *{fmt(remaining)}* до цели в 1\\,000\\,000 🍺",
        pace_line,
        "",
        f"*{escape_md(heroes_label)}* 🌟",
    ] + (top_day_lines if top_day_lines else [no_heroes]) + (
        ["", streak_line] if streak_line else []
    ) + [
        "",
        "*Кем гордится наша школа* 🏆",
    ] + (top_lines if top_lines else ["Пока никто не пил\\."]) + (
        ["", "*В огне* 🔥"] + fire_lines if fire_lines else []
    )

    sent = await context.bot.send_message(
        chat_id=send_to,
        text="\n".join(lines),
        parse_mode="MarkdownV2",
    )

    # Pin the daily report in the main group (scheduled runs only)
    if scheduled and send_to == main_chat_id:
        try:
            # Unpin the previous report if we have one stored
            old_pin_id = database.get_setting("pinned_report_message_id")
            if old_pin_id:
                await context.bot.unpin_chat_message(
                    chat_id=main_chat_id,
                    message_id=int(old_pin_id),
                )
            # Pin the new report (silent — no push, service msg cleaned up overnight)
            await context.bot.pin_chat_message(
                chat_id=main_chat_id,
                message_id=sent.message_id,
                disable_notification=True,
            )
            database.save_setting("pinned_report_message_id", str(sent.message_id))
            logger.info("Pinned daily report message_id=%s", sent.message_id)
        except Exception as e:
            logger.warning("Could not pin daily report: %s", e)

    # Auto-clean: only wipe main group's messages from yesterday
    if scheduled:
        message_ids = await asyncio.to_thread(
            database.get_text_message_ids_for_date, main_chat_id, report_date
        )
        pinned = await pinned_message_ids(context.bot, main_chat_id)
        deleted = await bulk_delete_messages(context.bot, main_chat_id, message_ids, skip=pinned)
        if deleted:
            database.clear_text_messages_for_date(main_chat_id, report_date)
            logger.info(f"Auto-clean: deleted {deleted} text messages from {report_date}")
