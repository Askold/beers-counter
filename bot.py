import logging
import os

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import database

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BEER_EMOJI = "🍺"


def _display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"{BEER_EMOJI} *Beer Counter Bot* {BEER_EMOJI}\n\n"
        "Track how many beers your group drinks\\!\n\n"
        "Commands:\n"
        "/beer — log one beer 🍺\n"
        "/count — see your personal count\n"
        "/leaderboard — see who's leading\n"
        "/reset — reset your own counter to zero",
        parse_mode="MarkdownV2",
    )


async def beer(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    new_count = database.add_beer(
        user_id=user.id,
        username=user.username,
        full_name=_display_name(user),
    )
    await update.message.reply_text(
        f"{BEER_EMOJI} Cheers, {_display_name(user)}\\! "
        f"That's beer **#{new_count}** for you today\\!",
        parse_mode="MarkdownV2",
    )


async def count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    n = database.get_count(user.id)
    if n == 0:
        await update.message.reply_text(
            "You haven't logged any beers yet\\. Use /beer to add one\\!",
            parse_mode="MarkdownV2",
        )
    else:
        await update.message.reply_text(
            f"You've had *{n}* beer{'s' if n != 1 else ''} so far\\. {BEER_EMOJI * min(n, 10)}",
            parse_mode="MarkdownV2",
        )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = database.get_leaderboard()
    if not rows:
        await update.message.reply_text("No beers logged yet\\. Be the first — use /beer\\!")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"*{BEER_EMOJI} Beer Leaderboard {BEER_EMOJI}*\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < len(medals) else f"{i + 1}\\."
        name = row["full_name"].replace(".", "\\.").replace("!", "\\!").replace("-", "\\-")
        lines.append(f"{medal} {name} — *{row['count']}* 🍺")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    database.reset_count(user.id)
    await update.message.reply_text(
        f"✅ Your beer count has been reset to zero\\, {_display_name(user)}\\.",
        parse_mode="MarkdownV2",
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

    database.init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("beer", beer))
    app.add_handler(CommandHandler("count", count))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("reset", reset))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
