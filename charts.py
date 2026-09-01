"""Weekly bar-chart rendering (matplotlib, Agg backend) and the Sunday chart job."""
import asyncio
import datetime
import io
import logging

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

from telegram.ext import ContextTypes

import database

logger = logging.getLogger(__name__)

RU_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

# pyplot is not thread-safe; serialize the (rare) off-loop chart renders.
_chart_lock = asyncio.Lock()


async def render_weekly_chart(chat_id: int) -> io.BytesIO:
    """Fetch data and render the weekly chart off the event loop."""
    rows = await asyncio.to_thread(database.get_daily_counts, chat_id, 7)
    async with _chart_lock:
        return await asyncio.to_thread(build_weekly_chart, rows)


def build_weekly_chart(rows: list[tuple[str, int]]) -> io.BytesIO:
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


async def weekly_chart_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: send the weekly chart to the main group every Sunday."""
    chat_id = database.get_chat_id()
    if not chat_id:
        logger.warning("weekly_chart_job: no chat_id saved yet, skipping")
        return
    buf = await render_weekly_chart(chat_id)
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=buf,
        caption="📊 Итоги недели — пиво по дням 🍺",
    )
