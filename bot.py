import logging
import os

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

VIDEO_EMOJI = "🎬"


def _display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


def _escape_md(text: str) -> str:
    """Escape all MarkdownV2 reserved characters."""
    reserved = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in reserved else c for c in text)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        f"{VIDEO_EMOJI} *Video Counter Bot* {VIDEO_EMOJI}\n\n"
        "I automatically count every video sent in this group\\.\n\n"
        "Commands:\n"
        "/count — see your personal video count\n"
        "/leaderboard — see who sent the most videos\n"
        "/reset — reset your own counter to zero",
        parse_mode="MarkdownV2",
    )


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Triggered automatically when any group member sends a video."""
    user = update.effective_user
    if not user:
        return
    new_count = database.add_beer(
        user_id=user.id,
        username=user.username,
        full_name=_display_name(user),
    )
    await update.message.reply_text(
        f"{VIDEO_EMOJI} {_escape_md(_display_name(user))} sent a video\\! "
        f"That's *{new_count}* video{'s' if new_count != 1 else ''} total\\.",
        parse_mode="MarkdownV2",
    )


async def count(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    n = database.get_count(user.id)
    if n == 0:
        await update.message.reply_text(
            "You haven't sent any videos yet\\.",
            parse_mode="MarkdownV2",
        )
    else:
        await update.message.reply_text(
            f"You've sent *{n}* video{'s' if n != 1 else ''} so far\\. {VIDEO_EMOJI * min(n, 10)}",
            parse_mode="MarkdownV2",
        )


async def leaderboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    rows = database.get_leaderboard()
    if not rows:
        await update.message.reply_text("No videos sent yet\\.")
        return

    medals = ["🥇", "🥈", "🥉"]
    lines = [f"*{VIDEO_EMOJI} Video Leaderboard {VIDEO_EMOJI}*\n"]
    for i, row in enumerate(rows):
        medal = medals[i] if i < len(medals) else f"{i + 1}\\."
        name = _escape_md(row["full_name"])
        lines.append(f"{medal} {name} — *{row['count']}* 🎬")

    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    database.reset_count(user.id)
    await update.message.reply_text(
        f"✅ Counter reset to zero for {_escape_md(_display_name(user))}\\.",
        parse_mode="MarkdownV2",
    )


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

    database.init_db()

    app = Application.builder().token(token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("count", count))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("reset", reset))

    # Count every video message sent in the group (requires privacy mode OFF in BotFather)
    app.add_handler(MessageHandler(filters.VIDEO, handle_video))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
