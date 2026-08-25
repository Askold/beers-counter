import datetime
import logging
import math
import os
from zoneinfo import ZoneInfo

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

GOAL = 1_000_000
MOSCOW = ZoneInfo("Europe/Moscow")


def _display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


def _escape_md(text: str) -> str:
    reserved = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in reserved else c for c in text)


def _fmt(n: int) -> str:
    """Format number with spaces as thousands separator."""
    return f"{n:,}".replace(",", " ")


# ── Commands ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🍺 *Счётчик пива* 🍺\n\n"
        "Цель: выпить *1\\,000\\,000* пива вместе\\!\n\n"
        "Отправь кружочек — и пиво засчитается автоматически\\.\n\n"
        "Команды:\n"
        "/count — твой личный счёт\n"
        "/leaderboard — таблица лидеров\n"
        "/reset — сбросить свой счёт",
        parse_mode="MarkdownV2",
    )


async def count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    n = database.get_count(user.id)
    total = database.get_total_count()
    remaining = max(0, GOAL - total)
    mvp_wins = database.get_mvp_wins(user.id)
    stars = "⭐" * mvp_wins
    if n == 0:
        await update.message.reply_text(
            "Ты ещё не выпил ни одного пива\\. Отправь кружочек\\! 🍺",
            parse_mode="MarkdownV2",
        )
    else:
        mvp_line = f"\nПобед MVP: {stars} *{mvp_wins}*" if mvp_wins else ""
        await update.message.reply_text(
            f"Ты выпил *{_fmt(n)}* пива 🍺\n"
            f"До цели осталось: *{_fmt(remaining)}*"
            f"{mvp_line}",
            parse_mode="MarkdownV2",
        )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = database.get_leaderboard(limit=100)
    total = database.get_total_count()
    remaining = max(0, GOAL - total)
    if not rows:
        await update.message.reply_text("Пока никто не выпил пива\\. Начни первым\\! 🍺", parse_mode="MarkdownV2")
        return

    mvp_counts = database.get_mvp_counts()
    medals = ["🥇", "🥈", "🥉"]
    lines = ["*🍺 Таблица лидеров 🍺*\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < 3 else f"{i + 1}\\."
        name = _escape_md(row["full_name"])
        stars = "⭐" * mvp_counts.get(row["user_id"], 0)
        lines.append(f"{medal} {name} {stars}— *{_fmt(row['count'])}* 🍺")
    lines.append(f"\nДо цели осталось: *{_fmt(remaining)}*")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    database.reset_count(user.id)
    await update.message.reply_text(
        f"✅ Счёт сброшен для {_escape_md(_display_name(user))}\\.",
        parse_mode="MarkdownV2",
    )


async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("⛔ Только администраторы могут использовать /clean\\.", parse_mode="MarkdownV2")
        return

    message_ids = database.get_text_message_ids(chat.id)
    if not message_ids:
        await update.message.reply_text("Нет текстовых сообщений для удаления\\.", parse_mode="MarkdownV2")
        return

    deleted = 0
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=msg_id)
            deleted += 1
        except Exception:
            pass

    database.clear_text_messages(chat.id)
    await context.bot.send_message(
        chat.id,
        f"🗑 Удалено *{deleted}* текстовых сообщений\\.",
        parse_mode="MarkdownV2",
    )


# ── Video handler ───────────────────────────────────────────────────────────

async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    if not user:
        return

    # remember this chat for the daily report
    database.save_chat_id(chat_id)

    _user_count, total = database.add_beer(
        user_id=user.id,
        username=user.username,
        full_name=_display_name(user),
        chat_id=chat_id,
    )
    remaining = max(0, GOAL - total)
    name = _escape_md(_display_name(user))

    await update.message.reply_text(
        f"{name} выпил пиво, осталось {_fmt(remaining)} 🍺",
        parse_mode="MarkdownV2",
    )


async def track_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message:
        database.track_text_message(update.effective_chat.id, update.message.message_id)


async def track_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track every user who sends any message so they appear in the risk zone."""
    user = update.effective_user
    if user and not user.is_bot:
        database.track_member(
            user_id=user.id,
            username=user.username,
            full_name=_display_name(user),
        )


# ── Daily report ────────────────────────────────────────────────────────────

async def report_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual trigger of the daily report — admin only (or any private chat)."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        # In private chat: send report to the saved group (no admin check needed)
        target_chat_id = database.get_chat_id()
        if not target_chat_id:
            await update.message.reply_text("Бот ещё не добавлен ни в одну группу\\.", parse_mode="MarkdownV2")
            return
        await daily_report(context, chat_id=target_chat_id)
        await update.message.reply_text("✅ Отчёт отправлен в группу\\.", parse_mode="MarkdownV2")
    else:
        # In a group: admin only, always use the stored chat_id for consistency
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Только администраторы могут вызвать отчёт\\.", parse_mode="MarkdownV2")
            return
        target_chat_id = database.get_chat_id() or chat.id
        await daily_report(context, chat_id=target_chat_id)


async def daily_report(context: ContextTypes.DEFAULT_TYPE, chat_id: int | None = None, scheduled: bool = False) -> None:
    if chat_id is None:
        chat_id = database.get_chat_id()
    if not chat_id:
        logger.warning("daily_report: no chat_id saved yet, skipping")
        return

    now = datetime.datetime.now(MOSCOW)
    # Scheduled (midnight) report → show the day that just ended (yesterday)
    # Manual report → show today so far
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

    today_count = database.get_date_count(chat_id, report_date)
    total = database.get_total_count()
    remaining = max(0, GOAL - total)

    last5 = database.get_videos_last_n_days(5)
    avg_per_day = last5 / 5
    if avg_per_day > 0 and remaining > 0:
        days_left = math.ceil(remaining / avg_per_day)
        pace_line = f"При текущем темпе потребуется *{_fmt(days_left)}* дн\\. для выполнения плана 📅"
    elif remaining == 0:
        pace_line = "🎊 Цель достигнута\\!"
    else:
        pace_line = "Темп пока не определён"

    top3 = database.get_leaderboard(limit=3)
    top3_day = database.get_top_drinkers_for_date(chat_id, report_date, limit=3)
    inactive = database.get_inactive_users(days=20)

    # Record MVP for scheduled (midnight) runs
    if scheduled and top3_day:
        database.record_mvp(report_date, top3_day[0]["user_id"], chat_id)

    mvp_counts = database.get_mvp_counts()
    mvp_streak_uid, mvp_streak = database.get_mvp_streak()

    medals = ["🥇", "🥈", "🥉"]

    top_lines = []
    for i, row in enumerate(top3):
        medal = medals[i] if i < 3 else f"{i+1}\\."
        stars = "⭐" * mvp_counts.get(row["user_id"], 0)
        top_lines.append(f"{medal} {_escape_md(row['full_name'])} {stars}— {_fmt(row['count'])} 🍺")

    top_day_lines = []
    for i, row in enumerate(top3_day):
        medal = medals[i] if i < 3 else f"{i+1}\\."
        top_day_lines.append(f"{medal} {_escape_md(row['full_name'])} — {_fmt(row['day_count'])} 🍺")

    risk_lines = []
    for row in inactive:
        name = _escape_md(row["full_name"])
        if row["count"] == 0:
            risk_lines.append(f"😴 {name} \\(никогда не пил\\)")
        elif row["last_video_at"]:
            last = datetime.datetime.fromisoformat(row["last_video_at"])
            days_ago = (now - last).days
            risk_lines.append(f"😴 {name} \\({days_ago} дн\\. без пива\\)")

    # Build streak line
    streak_line = ""
    if mvp_streak >= 2 and mvp_streak_uid is not None:
        streak_user = next((r for r in top3 if r["user_id"] == mvp_streak_uid), None)
        if streak_user:
            streak_name = _escape_md(streak_user["full_name"])
            streak_line = f"🔥 {streak_name} — MVP уже *{mvp_streak}* дня подряд\\!"

    lines = [
        f"🎉 Поздравляю\\! {_escape_md(period_label)} *{_fmt(today_count)}* пива",
        f"Осталось *{_fmt(remaining)}* до цели в 1\\,000\\,000 🍺",
        pace_line,
        "",
        f"*{_escape_md(heroes_label)}* 🌟",
    ] + (top_day_lines if top_day_lines else [no_heroes]) + (
        ["", streak_line] if streak_line else []
    ) + [
        "",
        "*Кем гордится наша школа* 🏆",
    ] + (top_lines if top_lines else ["Пока никто не пил\\."]) + [
        "",
        "*В зоне риска* ⚠️",
    ] + (risk_lines if risk_lines else ["Все молодцы, никто не отстаёт\\! 💪"])

    await context.bot.send_message(
        chat_id=chat_id,
        text="\n".join(lines),
        parse_mode="MarkdownV2",
    )

    # Auto-clean yesterday's text messages after the scheduled report
    if scheduled:
        message_ids = database.get_text_message_ids_for_date(chat_id, report_date)
        deleted = 0
        for msg_id in message_ids:
            try:
                await context.bot.delete_message(chat_id=chat_id, message_id=msg_id)
                deleted += 1
            except Exception:
                pass
        if deleted:
            database.clear_text_messages_for_date(chat_id, report_date)
            logger.info(f"Auto-clean: deleted {deleted} text messages from {report_date}")


# ── Main ────────────────────────────────────────────────────────────────────

def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

    database.init_db()

    app = Application.builder().token(token).build()

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("count", count))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("clean", clean))
    app.add_handler(CommandHandler("report", report_command))

    # Count only circle (round) video messages
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video))

    # Track text messages for /clean
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_text))

    # Track all message senders (group=1 runs after group=0, never blocks)
    app.add_handler(MessageHandler(filters.ALL, track_member_handler), group=1)

    # Daily report at 00:00 Moscow time (scheduled=True → counts yesterday)
    app.job_queue.run_daily(
        lambda ctx: daily_report(ctx, scheduled=True),
        time=datetime.time(0, 0, 0, tzinfo=MOSCOW),
        name="daily_report",
    )

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
