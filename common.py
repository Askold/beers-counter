"""Shared constants and helpers used across the bot's modules."""
import logging
from zoneinfo import ZoneInfo

from telegram.error import TelegramError

logger = logging.getLogger(__name__)

GOAL = 1_000_000
MOSCOW = ZoneInfo("Europe/Moscow")
INACTIVE_DAYS = 10
MEDALS = ["🥇", "🥈", "🥉"]


def display_name(user) -> str:
    return user.full_name or user.username or str(user.id)


def escape_md(text: str) -> str:
    reserved = r"\_*[]()~`>#+-=|{}.!"
    return "".join(f"\\{c}" if c in reserved else c for c in text)


def fmt(n: int) -> str:
    """Format number with spaces as thousands separator."""
    return f"{n:,}".replace(",", " ")


def plural_ru(n: int, one: str, few: str, many: str) -> str:
    """Pick the Russian plural form for `n` — e.g.
    plural_ru(n, "человека", "человек", "человек") → "1 человека" / "3 человек"."""
    tens = abs(n) % 100
    if 11 <= tens <= 14:
        return many
    ones = tens % 10
    if ones == 1:
        return one
    if 2 <= ones <= 4:
        return few
    return many


def medal(i: int) -> str:
    """Rank marker for leaderboard row `i` (0-based): 🥇🥈🥉 for the top 3,
    else a MarkdownV2-escaped '<n>.'."""
    return MEDALS[i] if i < 3 else f"{i + 1}\\."


async def reply_chunked(update, lines: list[str], header: str) -> None:
    """Send `lines` as one or more MarkdownV2 messages, each under Telegram's 4096-char limit."""
    chunk, size = [header], len(header)
    for line in lines:
        if size + len(line) + 1 > 3800 and len(chunk) > 1:
            await update.message.reply_text("\n".join(chunk), parse_mode="MarkdownV2")
            chunk, size = [], 0
        chunk.append(line)
        size += len(line) + 1
    if chunk:
        await update.message.reply_text("\n".join(chunk), parse_mode="MarkdownV2")


async def pinned_message_ids(bot, chat_id: int) -> set[int]:
    """Return the set of currently-pinned message IDs in the chat (best-effort)."""
    try:
        chat = await bot.get_chat(chat_id)
        if chat.pinned_message:
            return {chat.pinned_message.message_id}
    except Exception:
        pass
    return set()


async def bulk_delete_messages(bot, chat_id: int, message_ids, skip: set[int] | None = None) -> int:
    """Delete many messages using the bulk endpoint (100 per call, one API round-trip).
    Falls back to per-message deletion for a chunk that the bulk call rejects
    (e.g. it contains a message older than 48h). Returns the count deleted."""
    skip = skip or set()
    ids = [m for m in dict.fromkeys(message_ids) if m not in skip]
    deleted = 0
    for i in range(0, len(ids), 100):
        chunk = ids[i:i + 100]
        try:
            await bot.delete_messages(chat_id=chat_id, message_ids=chunk)
            deleted += len(chunk)
        except TelegramError:
            for msg_id in chunk:
                try:
                    await bot.delete_message(chat_id=chat_id, message_id=msg_id)
                    deleted += 1
                except TelegramError:
                    pass
    return deleted
