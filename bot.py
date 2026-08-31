import datetime
import io
import logging
import math
import os
from zoneinfo import ZoneInfo

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

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


# ── Chart builder ───────────────────────────────────────────────────────────

RU_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _build_weekly_chart(rows: list[tuple[str, int]]) -> io.BytesIO:
    """
    rows: [(date_str, count), ...] for 7 consecutive days, ascending.
    Returns a PNG as a BytesIO buffer.
    """
    dates = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    labels = [
        f"{RU_DAYS[datetime.date.fromisoformat(d).weekday()]}\n{d[8:]}.{d[5:7]}"
        for d in dates
    ]

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    bar_colors = ["#e94560" if c == max(counts) else "#0f3460" for c in counts]
    bars = ax.bar(labels, counts, color=bar_colors, edgecolor="#e94560", linewidth=0.6, width=0.6)

    # Value labels on top of bars
    for bar, val in zip(bars, counts):
        if val > 0:
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + max(counts) * 0.02,
                str(val),
                ha="center", va="bottom",
                color="white", fontsize=10, fontweight="bold",
            )

    ax.set_title("🍺 Пиво за неделю", color="white", fontsize=14, fontweight="bold", pad=12)
    ax.tick_params(colors="white", labelsize=9)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_ylim(0, max(counts) * 1.25 + 1)
    for spine in ax.spines.values():
        spine.set_edgecolor("#0f3460")
    ax.yaxis.label.set_color("white")
    ax.grid(axis="y", color="#0f3460", linewidth=0.8, linestyle="--", alpha=0.7)

    total = sum(counts)
    ax.set_xlabel(
        f"Итого за 7 дней: {total} 🍺",
        color="#e94560", fontsize=10, labelpad=8,
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


# ── Commands ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "🍺 *Счётчик пива* 🍺\n\n"
        "Цель: выпить *1\\,000\\,000* пива вместе\\!\n\n"
        "Отправь кружочек — и пиво засчитается автоматически\\.\n\n"
        "Команды:\n"
        "/stats — твоя статистика\n"
        "/leaderboard — таблица лидеров",
        parse_mode="MarkdownV2",
    )


async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    n = database.get_count(user.id)
    total = database.get_total_count()
    remaining = max(0, GOAL - total)

    if n == 0:
        await update.message.reply_text(
            "Ты ещё не выпил ни одного пива\\. Отправь кружочек\\! 🍺",
            parse_mode="MarkdownV2",
        )
        return

    mvp_wins = database.get_mvp_wins(user.id)
    current_streak, longest_streak = database.get_streak(user.id)
    rank = database.get_user_rank(user.id)
    user_score = database.get_user_composite_score(user.id)
    pivot = database.get_pivot_score()

    if rank <= 10:
        special = "Ты в числе лучших пивопивов, красавчик\\! 🏆"
    elif user_score >= pivot:
        special = "Выше среднего\\! Так держать\\! 💪"
    else:
        special = "Дружище, надо поднажать\\! 😅"

    streak_val = f"*{current_streak}*" if current_streak >= 1 else "0"
    longest_val = f"*{longest_streak}*" if longest_streak >= 1 else "0"
    mvp_val = f"*{mvp_wins}*" if mvp_wins else "0"

    await update.message.reply_text(
        f"Выпито пив 🍺 : *{_fmt(n)}*\n"
        f"MVP ⭐ : {mvp_val}\n"
        f"Стрик 🔥 : {streak_val} дн\\. \\(рекорд: {longest_val}\\)\n"
        f"Место в таблице: *\\#{rank}*\n"
        f"\n"
        f"{special}\n"
        f"\n"
        f"До общей цели осталось: *{_fmt(remaining)}*",
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
        mvps = mvp_counts.get(row["user_id"], 0)
        if mvps == 0:
            stars = ""
        elif mvps <= 5:
            stars = " " + "⭐" * mvps
        else:
            stars = " " + "⭐" * 5 + f" {mvps}"
        lines.append(f"{medal} {name}{stars} — *{_fmt(row['count'])}* 🍺")
    lines.append(f"\nДо цели осталось: *{_fmt(remaining)}*")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")



async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a bar chart of daily beer counts for the last 7 days. Works in any chat."""
    chat_id = database.get_chat_id()
    if not chat_id:
        await update.message.reply_text("Бот ещё не добавлен ни в одну группу\\.", parse_mode="MarkdownV2")
        return
    rows = database.get_daily_counts(chat_id, days=7)
    buf = _build_weekly_chart(rows)
    await update.message.reply_photo(photo=buf, caption="📊 Пиво за последние 7 дней")


async def weekly_chart_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: send the weekly chart to the main group every Sunday."""
    chat_id = database.get_chat_id()
    if not chat_id:
        logger.warning("weekly_chart_job: no chat_id saved yet, skipping")
        return
    rows = database.get_daily_counts(chat_id, days=7)
    buf = _build_weekly_chart(rows)
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=buf,
        caption="📊 Итоги недели — пиво по дням 🍺",
    )


async def remove_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: /remove <id1> <id2> ... — cascade-delete video_log rows and fix counts."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        await update.message.reply_text("⛔ Команда доступна только в личном чате с ботом\\.", parse_mode="MarkdownV2")
        return

    if not context.args:
        await update.message.reply_text(
            "Использование: `/remove <id1> <id2> \\.\\.\\.`\n"
            "Пример: `/remove 269 270 271`",
            parse_mode="MarkdownV2",
        )
        return

    # Parse IDs
    try:
        ids = [int(x) for x in context.args]
    except ValueError:
        await update.message.reply_text("⛔ Все аргументы должны быть числами\\.", parse_mode="MarkdownV2")
        return

    removed, affected = database.remove_video_log_cascade(ids)

    if not removed:
        await update.message.reply_text("Записи не найдены\\.", parse_mode="MarkdownV2")
        return

    lines = [f"🗑 Удалено *{len(removed)}* записей из video\\_log\\."]
    for uid, name, delta in affected:
        lines.append(f"  {_escape_md(name)}: счёт −{delta}")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def _pinned_message_ids(bot, chat_id: int) -> set[int]:
    """Return the set of currently-pinned message IDs in the chat (best-effort)."""
    try:
        chat = await bot.get_chat(chat_id)
        if chat.pinned_message:
            return {chat.pinned_message.message_id}
    except Exception:
        pass
    return set()


async def remove_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: /removelast @username N — delete the N most recent circles for a user."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        await update.message.reply_text("⛔ Команда доступна только в личном чате с ботом\\.", parse_mode="MarkdownV2")
        return

    if len(context.args) != 2:
        await update.message.reply_text(
            "Использование: `/removelast @username N`\n"
            "Пример: `/removelast @vasya 3`",
            parse_mode="MarkdownV2",
        )
        return

    username_arg, n_arg = context.args
    try:
        n = int(n_arg)
        if n <= 0:
            raise ValueError
    except ValueError:
        await update.message.reply_text("⛔ N должно быть положительным числом\\.", parse_mode="MarkdownV2")
        return

    target = database.find_user_by_username(username_arg)
    if not target:
        await update.message.reply_text(
            f"⛔ Пользователь `{_escape_md(username_arg)}` не найден\\.",
            parse_mode="MarkdownV2",
        )
        return

    ids = database.get_last_n_video_ids(target["user_id"], n)
    if not ids:
        await update.message.reply_text("У пользователя нет записей в видеожурнале\\.", parse_mode="MarkdownV2")
        return

    removed, affected = database.remove_video_log_cascade(ids)
    name = _escape_md(target["full_name"])
    await update.message.reply_text(
        f"✅ Удалено *{len(removed)}* записей для {name}\\.\n"
        f"IDs: {_escape_md(', '.join(map(str, removed)))}",
        parse_mode="MarkdownV2",
    )


async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    chat = update.effective_chat
    user = update.effective_user
    main_chat_id = database.get_chat_id()

    # /clean only works in the main group
    if main_chat_id and chat.id != main_chat_id:
        await update.message.reply_text("⛔ /clean работает только в основной группе\\.", parse_mode="MarkdownV2")
        return

    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("⛔ Только администраторы могут использовать /clean\\.", parse_mode="MarkdownV2")
        return

    yesterday = (datetime.datetime.now(MOSCOW) - datetime.timedelta(days=1)).date().isoformat()
    message_ids = database.get_text_message_ids_for_date(chat.id, yesterday)
    if not message_ids:
        await update.message.reply_text("Нет сообщений за вчера для удаления\\.", parse_mode="MarkdownV2")
        return

    pinned = await _pinned_message_ids(context.bot, chat.id)
    deleted = 0
    for msg_id in message_ids:
        if msg_id in pinned:
            continue
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=msg_id)
            deleted += 1
        except Exception:
            pass

    database.clear_text_messages_for_date(chat.id, yesterday)
    await context.bot.send_message(
        chat.id,
        f"🗑 Удалено *{deleted}* сообщений за вчера\\.",
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

    msg = update.effective_message
    message_id = msg.message_id if msg else None

    _user_count, total, added = database.add_beer(
        user_id=user.id,
        username=user.username,
        full_name=_display_name(user),
        chat_id=chat_id,
        message_id=message_id,
    )

    if not added:
        # Duplicate delivery (bot restart replay) — silently skip.
        logger.info("Skipped duplicate message_id=%s from user %s", message_id, user.id)
        return

    remaining = max(0, GOAL - total)
    name = _escape_md(_display_name(user))
    current_streak, _ = database.get_streak(user.id)
    streak_suffix = f" 🔥 {current_streak}" if current_streak >= 2 else ""

    if msg:
        await msg.reply_text(
            f"{name} выпил пиво, осталось {_fmt(remaining)} 🍺{streak_suffix}",
            parse_mode="MarkdownV2",
        )


async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track every non-circle, non-command message so /clean can delete it later."""
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

    # All data queries use main_chat_id
    today_count = database.get_date_count(main_chat_id, report_date)
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
    top3_day = database.get_top_drinkers_for_date(main_chat_id, report_date, limit=3)
    inactive = database.get_inactive_users(days=20)
    on_fire = database.get_users_on_streak(min_days=3)

    # Record MVP for scheduled (midnight) runs
    if scheduled and top3_day:
        database.record_mvp(report_date, top3_day[0]["user_id"], main_chat_id)

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

    streak_line = ""
    if mvp_streak >= 2 and mvp_streak_uid is not None:
        streak_user = next((r for r in top3 if r["user_id"] == mvp_streak_uid), None)
        if streak_user:
            streak_name = _escape_md(streak_user["full_name"])
            streak_line = f"🔥 {streak_name} — MVP уже *{mvp_streak}* дня подряд\\!"

    fire_lines = [
        f"🔥 {_escape_md(row['full_name'])} — {row['current_streak']} дн\\. подряд"
        for row in on_fire
    ]

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
    ] + (top_lines if top_lines else ["Пока никто не пил\\."]) + (
        ["", "*В огне* 🔥"] + fire_lines if fire_lines else []
    ) + [
        "",
        "*В зоне риска* ⚠️",
    ] + (risk_lines if risk_lines else ["Все молодцы, никто не отстаёт\\! 💪"])

    await context.bot.send_message(
        chat_id=send_to,
        text="\n".join(lines),
        parse_mode="MarkdownV2",
    )

    # Auto-clean: only wipe main group's messages from yesterday
    if scheduled:
        message_ids = database.get_text_message_ids_for_date(main_chat_id, report_date)
        pinned = await _pinned_message_ids(context.bot, main_chat_id)
        deleted = 0
        for msg_id in message_ids:
            if msg_id in pinned:
                continue
            try:
                await context.bot.delete_message(chat_id=main_chat_id, message_id=msg_id)
                deleted += 1
            except Exception:
                pass
        if deleted:
            database.clear_text_messages_for_date(main_chat_id, report_date)
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
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("chart", chart))
    app.add_handler(CommandHandler("remove", remove_records))
    app.add_handler(CommandHandler("removelast", remove_last))
    app.add_handler(CommandHandler("clean", clean))
    app.add_handler(CommandHandler("report", report_command))

    # Count only circle (round) video messages
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video))

    # Track all non-circle, non-command, non-pinned-notification messages for /clean
    app.add_handler(MessageHandler(
        ~filters.COMMAND & ~filters.VIDEO_NOTE & ~filters.StatusUpdate.PINNED_MESSAGE,
        track_message,
    ))

    # Track all message senders (group=1 runs after group=0, never blocks)
    app.add_handler(MessageHandler(filters.ALL, track_member_handler), group=1)

    # Daily report at 00:00 Moscow time (scheduled=True → counts yesterday)
    app.job_queue.run_daily(
        lambda ctx: daily_report(ctx, send_to=None, scheduled=True),
        time=datetime.time(0, 0, 0, tzinfo=MOSCOW),
        name="daily_report",
    )

    # Weekly chart every Sunday at 20:00 Moscow time (weekday 6 = Sunday)
    app.job_queue.run_daily(
        weekly_chart_job,
        time=datetime.time(20, 0, 0, tzinfo=MOSCOW),
        days=(6,),
        name="weekly_chart",
    )

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
