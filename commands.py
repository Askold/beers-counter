"""User-facing command handlers: /start /help /stats /leaderboard /none /day /week /month /inactive /chart."""
import datetime
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database
from charts import render_weekly_chart
from common import GOAL, INACTIVE_DAYS, MOSCOW, escape_md, fmt, medal, plural_ru, reply_chunked

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "🍺 *Счётчик пива — 1\\,000\\,000 пив*\n\n"
    "Отправь кружок видеоролик в чат — бот засчитает одно пиво и ответит, сколько осталось до цели\\. "
    "Выпивай хотя бы одно пиво каждый день, чтобы держать стрик 🔥\n\n"
    "*Моя статистика*\n"
    "/stats — пиво, MVP\\-победы, стрик, место в таблице\n\n"
    "*Таблицы лидеров*\n"
    "/leaderboard — все за всё время\n"
    "/none — насколько лидер оторвался от хвоста таблицы\n"
    "/day — лидеры за сегодня\n"
    "/week — лидеры за 7 дней\n"
    "/month — лидеры за 30 дней\n"
    "/inactive — кто в зоне риска 😴\n"
    "/chart — график по дням за неделю\n\n"
    "*⭐ MVP*\n"
    "Каждую ночь бот определяет MVP дня — кто выпил больше всего\\. "
    "Победа фиксируется навсегда: в /leaderboard рядом с именем ⭐ за каждую победу\\.\n\n"
    "*Полезно знать*\n"
    "📊 Закреплённый отчёт обновляется каждую ночь\n"
    "🏆 Топ\\-3 получают особый статус в /stats\n"
    "😴 С самыми пассивными участниками мы будем вынуждены попрощаться"
)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="MarkdownV2")


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(HELP_TEXT, parse_mode="MarkdownV2")


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

    if rank <= 3:
        special = "ТЫ ЛУЧШИЙ ПИВОПИВ 🏆👑"
    elif rank <= 10:
        special = "Ты в числе лучших пивопивов, красавчик\\! 🏆"
    elif user_score >= pivot:
        special = "Выше среднего\\! Так держать\\! 💪"
    else:
        special = "Дружище, надо поднажать\\! 😅"

    streak_val = f"*{current_streak}*" if current_streak >= 1 else "0"
    longest_val = f"*{longest_streak}*" if longest_streak >= 1 else "0"
    mvp_val = f"*{mvp_wins}*" if mvp_wins else "0"

    await update.message.reply_text(
        f"Выпито пив 🍺 : *{fmt(n)}*\n"
        f"MVP ⭐ : {mvp_val}\n"
        f"Стрик 🔥 : {streak_val} дн\\. \\(рекорд: {longest_val}\\)\n"
        f"Место в таблице: *\\#{rank}*\n"
        f"\n"
        f"{special}\n"
        f"\n"
        f"До общей цели осталось: *{fmt(remaining)}*",
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
    lines = ["*🍺 Таблица лидеров 🍺*\n"]
    for i, row in enumerate(rows):
        name = escape_md(row["full_name"])
        mvps = mvp_counts.get(row["user_id"], 0)
        if mvps == 0:
            stars = ""
        elif mvps <= 5:
            stars = " " + "⭐" * mvps
        else:
            stars = " " + "⭐" * 5 + f" {mvps}"
        lines.append(f"{medal(i)} {name}{stars} — *{fmt(row['count'])}* 🍺")
    lines.append(f"\nДо цели осталось: *{fmt(remaining)}*")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def none_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/none — show how many people from the bottom of the all-time leaderboard,
    summed together, it takes for their beers to reach (or first exceed) the #1 drinker."""
    rows = database.get_leaderboard(limit=None)
    if not rows:
        await update.message.reply_text("Пока никто не выпил пива\\. Начни первым\\! 🍺", parse_mode="MarkdownV2")
        return
    if len(rows) < 2:
        await update.message.reply_text(
            "В таблице пока только один пивопив — сравнивать не с кем\\. 🍺",
            parse_mode="MarkdownV2",
        )
        return

    leader = rows[0]
    leader_count = leader["count"]
    name = escape_md(leader["full_name"])

    # Walk up from the last place, adding counts until the tail catches the leader.
    acc = tail = 0
    for row in reversed(rows[1:]):
        acc += row["count"]
        tail += 1
        if acc >= leader_count:
            break

    head = f"🥇 {name} — *{fmt(leader_count)}* 🍺"
    if acc < leader_count:
        body = (
            f"В одиночку это больше, чем все остальные вместе взятые "
            f"\\(*{fmt(acc)}* 🍺\\)\\. 👑"
        )
    else:
        people = plural_ru(tail, "человека", "человек", "человек")
        prefix = "Ровно столько же" if acc == leader_count else "Столько же"
        body = (
            f"{prefix} выпил хвост таблицы из *{fmt(tail)}* {people} "
            f"\\(*{fmt(acc)}* 🍺\\)\\."
        )

    await update.message.reply_text(f"{head}\n\n{body}", parse_mode="MarkdownV2")


async def _period_leaderboard(update, label: str, since: datetime.date) -> None:
    """Shared renderer for /day, /week, /month leaderboards."""
    chat_id = database.get_chat_id()
    if not chat_id:
        await update.message.reply_text("Бот ещё не добавлен в группу\\.", parse_mode="MarkdownV2")
        return
    rows = database.get_leaderboard_for_period(chat_id, since.isoformat(), limit=20)
    if not rows:
        await update.message.reply_text(f"*{label}*\n\nНикто не пил 😴", parse_mode="MarkdownV2")
        return
    lines = [f"*{label}*\n"]
    for i, row in enumerate(rows):
        name = escape_md(row["full_name"])
        lines.append(f"{medal(i)} {name} — *{fmt(row['period_count'])}* 🍺")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def day(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    today = datetime.datetime.now(MOSCOW).date()
    await _period_leaderboard(update, "Пиво сегодня 📅", today)


async def week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    since = (datetime.datetime.now(MOSCOW) - datetime.timedelta(days=6)).date()
    await _period_leaderboard(update, "Пиво за неделю 📅", since)


async def month(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    since = (datetime.datetime.now(MOSCOW) - datetime.timedelta(days=29)).date()
    await _period_leaderboard(update, "Пиво за месяц 📅", since)


async def inactive_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """List every user in the risk zone: never drank, or no circle for INACTIVE_DAYS+ days."""
    now = datetime.datetime.now(MOSCOW)
    rows = database.get_inactive_users(days=INACTIVE_DAYS)
    if not rows:
        await update.message.reply_text(
            "Все молодцы, никто не отстаёт\\! 💪", parse_mode="MarkdownV2"
        )
        return

    lines = []
    for row in rows:
        name = escape_md(row["full_name"])
        if row["count"] == 0:
            lines.append(f"😴 {name} \\(никогда не пил\\)")
        elif row["last_video_at"]:
            last = datetime.datetime.fromisoformat(row["last_video_at"])
            days_ago = (now - last).days
            lines.append(f"😴 {name} \\({days_ago} дн\\. без пива\\)")

    header = f"*В зоне риска* ⚠️ \\({len(lines)}\\)\n"
    await reply_chunked(update, lines, header)


async def chart(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Send a bar chart of daily beer counts for the last 7 days. Works in any chat."""
    chat_id = database.get_chat_id()
    if not chat_id:
        await update.message.reply_text("Бот ещё не добавлен ни в одну группу\\.", parse_mode="MarkdownV2")
        return
    buf = await render_weekly_chart(chat_id)
    await update.message.reply_photo(photo=buf, caption="📊 Пиво за последние 7 дней")
