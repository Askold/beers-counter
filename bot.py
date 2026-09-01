"""Entry point: application wiring and startup."""
import datetime
import logging
import os

from telegram import Update
from telegram.ext import (
    AIORateLimiter,
    Application,
    CommandHandler,
    MessageHandler,
    filters,
)

import database
from admin import clean, remove_last, remove_records
from charts import weekly_chart_job
from commands import (
    chart,
    day,
    help_cmd,
    inactive_cmd,
    leaderboard,
    month,
    start,
    stats,
    week,
)
from common import MOSCOW
from report import daily_report, report_command
from video import handle_video, track_member_handler, track_message

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_BOT_TOKEN environment variable is not set")

    database.init_db()

    app = (
        Application.builder()
        .token(token)
        # Queues outgoing messages under Telegram's flood limits (global 30/s,
        # ~20/min per group) instead of letting bursts raise RetryAfter.
        .rate_limiter(AIORateLimiter())
        # Process updates concurrently so one rate-limited reply doesn't stall
        # the whole queue behind it.
        .concurrent_updates(True)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("leaderboard", leaderboard))
    app.add_handler(CommandHandler("day", day))
    app.add_handler(CommandHandler("week", week))
    app.add_handler(CommandHandler("month", month))
    app.add_handler(CommandHandler("inactive", inactive_cmd))
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
