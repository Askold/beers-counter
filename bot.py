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


async def track_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Track every text message ID so /clean can delete them later."""
    if update.message:
        database.track_text_message(update.effective_chat.id, update.message.message_id)


async def clean(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Delete all tracked text messages in this chat. Admin only."""
    chat = update.effective_chat
    user = update.effective_user

    # Check the caller is an admin
    member = await chat.get_member(user.id)
    if member.status not in ("administrator", "creator"):
        await update.message.reply_text("⛔ Only group admins can use /clean\\.", parse_mode="MarkdownV2")
        return

    message_ids = database.get_text_message_ids(chat.id)
    if not message_ids:
        await update.message.reply_text("No text messages to delete\\.", parse_mode="MarkdownV2")
        return

    deleted = 0
    failed = 0
    for msg_id in message_ids:
        try:
            await context.bot.delete_message(chat_id=chat.id, message_id=msg_id)
            deleted += 1
        except Exception:
            failed += 1  # message already deleted or too old

    database.clear_text_messages(chat.id)

    summary = f"🗑 Deleted *{deleted}* text message{'s' if deleted != 1 else ''}\\."
    if failed:
        summary += f" \\({failed} already gone or too old\\)"
    await context.bot.send_message(chat.id, summary, parse_mode="MarkdownV2")


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
    app.add_handler(CommandHandler("clean", clean))

    # Count only round (circle) video messages
    app.add_handler(MessageHandler(filters.VIDEO_NOTE, handle_video))

    # Track all text messages for /clean
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, track_text))

    logger.info("Bot is running…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
