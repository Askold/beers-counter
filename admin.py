"""Admin-only command handlers: /remove /removelast /clean."""
import datetime
import logging

from telegram import Update
from telegram.ext import ContextTypes

import database
from common import MOSCOW, bulk_delete_messages, escape_md, pinned_message_ids

logger = logging.getLogger(__name__)


async def remove_records(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: /remove <id1> <id2> ... — cascade-delete video_log rows and fix counts."""
    chat = update.effective_chat
    user = update.effective_user

    # Allow in private chat or by group admins only
    if chat.type != "private":
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Только администраторы могут использовать /remove\\.", parse_mode="MarkdownV2")
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
        lines.append(f"  {escape_md(name)}: счёт −{delta}")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def remove_last(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Admin-only: /removelast @username N — delete the N most recent circles for a user."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text("⛔ Только администраторы могут использовать /removelast\\.", parse_mode="MarkdownV2")
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
            f"⛔ Пользователь `{escape_md(username_arg)}` не найден\\.",
            parse_mode="MarkdownV2",
        )
        return

    ids = database.get_last_n_video_ids(target["user_id"], n)
    if not ids:
        await update.message.reply_text("У пользователя нет записей в видеожурнале\\.", parse_mode="MarkdownV2")
        return

    removed, affected = database.remove_video_log_cascade(ids)
    name = escape_md(target["full_name"])
    await update.message.reply_text(
        f"✅ Удалено *{len(removed)}* записей для {name}\\.\n"
        f"IDs: {escape_md(', '.join(map(str, removed)))}",
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

    pinned = await pinned_message_ids(context.bot, chat.id)
    deleted = await bulk_delete_messages(context.bot, chat.id, message_ids, skip=pinned)

    database.clear_text_messages_for_date(chat.id, yesterday)
    await context.bot.send_message(
        chat.id,
        f"🗑 Удалено *{deleted}* сообщений за вчера\\.",
        parse_mode="MarkdownV2",
    )
