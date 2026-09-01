"""Circle-video counting and message/member tracking handlers."""
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database
from common import GOAL, display_name, escape_md, fmt

logger = logging.getLogger(__name__)


async def handle_video(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    chat_id = update.effective_chat.id
    if not user:
        return

    msg = update.effective_message

    # Skip forwarded circle videos — they were already counted when originally sent.
    if msg and msg.forward_origin is not None:
        logger.info("Skipped forwarded circle from user %s (forward_origin set)", user.id)
        return

    # Skip if the message sender doesn't match the update user (extra safety net).
    if msg and msg.from_user and msg.from_user.id != user.id:
        logger.info("Skipped circle: from_user mismatch (%s vs %s)", msg.from_user.id, user.id)
        return

    # remember this chat for the daily report
    database.save_chat_id(chat_id)

    message_id = msg.message_id if msg else None

    _user_count, total, added, current_streak = database.add_beer(
        user_id=user.id,
        username=user.username,
        full_name=display_name(user),
        chat_id=chat_id,
        message_id=message_id,
    )

    if not added:
        # Duplicate delivery (bot restart replay) — silently skip.
        logger.info("Skipped duplicate message_id=%s from user %s", message_id, user.id)
        return

    remaining = max(0, GOAL - total)
    name = escape_md(display_name(user))
    streak_suffix = f" 🔥 {current_streak}" if current_streak >= 2 else ""

    if msg:
        await msg.reply_text(
            f"{name} выпил пиво, осталось {fmt(remaining)} 🍺{streak_suffix}",
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
            full_name=display_name(user),
        )
