"""Bar-chart rendering (matplotlib, Agg backend): daily beers (weekly / monthly),
the above-median leaderboard chart, and the Sunday chart job."""
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


async def render_chart(chat_id: int, days: int = 7) -> io.BytesIO:
    """Fetch data and render the daily-beers chart off the event loop.
    days=7 → weekly view, days=30 → monthly view."""
    rows = await asyncio.to_thread(database.get_daily_counts, chat_id, days)
    async with _chart_lock:
        return await asyncio.to_thread(build_chart, rows)


async def render_leaderboard_chart(entries: list[tuple[str, int]]) -> io.BytesIO:
    """Render a horizontal bar chart from pre-filtered leaderboard entries
    ([(full_name, count), ...], highest first)."""
    async with _chart_lock:
        return await asyncio.to_thread(build_leaderboard_chart, entries)


def _shorten(name: str, limit: int = 20) -> str:
    return name if len(name) <= limit else name[: limit - 1] + "…"


def build_chart(rows: list[tuple[str, int]]) -> io.BytesIO:
    """
    rows: [(date_str, count), ...] for N consecutive days, ascending.
    N <= 7 renders the weekday view; longer spans render the month view.
    Returns a PNG as a BytesIO buffer.
    """
    dates = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    n = len(rows)
    peak = max(counts) if counts else 0
    weekly = n <= 7

    x = list(range(n))
    width = 9 if weekly else min(16, 6 + n * 0.34)
    fig, ax = plt.subplots(figsize=(width, 4.5), dpi=120)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    bar_colors = ["#e94560" if peak and c == peak else "#0f3460" for c in counts]
    bars = ax.bar(x, counts, color=bar_colors, edgecolor="#e94560",
                  linewidth=0.6, width=0.6 if weekly else 0.8)

    # Value labels: every bar for the week view, only the peak for the month view.
    for bar, val in zip(bars, counts):
        if val > 0 and (weekly or val == peak):
            ax.text(
                bar.get_x() + bar.get_width() / 2,
                bar.get_height() + peak * 0.02,
                str(val),
                ha="center", va="bottom",
                color="white", fontsize=10 if weekly else 8, fontweight="bold",
            )

    if weekly:
        tick_pos = x
        tick_labels = [
            f"{RU_DAYS[datetime.date.fromisoformat(d).weekday()]}\n{d[8:]}.{d[5:7]}"
            for d in dates
        ]
    else:
        step = max(1, round(n / 10))
        tick_pos = [i for i in x if i % step == 0]
        tick_labels = [f"{dates[i][8:]}.{dates[i][5:7]}" for i in tick_pos]
    ax.set_xticks(tick_pos)
    ax.set_xticklabels(tick_labels)
    ax.set_xlim(-0.6, n - 0.4)

    title = "🍺 Пиво за неделю" if weekly else "🍺 Пиво за месяц"
    period = "7 дней" if weekly else f"{n} дней"
    ax.set_title(title, color="white", fontsize=14, fontweight="bold", pad=12)
    ax.tick_params(colors="white", labelsize=9 if weekly else 8)
    ax.yaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_ylim(0, peak * 1.25 + 1)
    for spine in ax.spines.values():
        spine.set_edgecolor("#0f3460")
    ax.yaxis.label.set_color("white")
    ax.grid(axis="y", color="#0f3460", linewidth=0.8, linestyle="--", alpha=0.7)

    ax.set_xlabel(
        f"Итого за {period}: {sum(counts)} 🍺",
        color="#e94560", fontsize=10, labelpad=8,
    )

    plt.tight_layout()
    buf = io.BytesIO()
    plt.savefig(buf, format="png", facecolor=fig.get_facecolor())
    plt.close(fig)
    buf.seek(0)
    return buf


def build_leaderboard_chart(entries: list[tuple[str, int]]) -> io.BytesIO:
    """
    entries: [(full_name, count), ...] already filtered and sorted, highest first.
    Returns a horizontal bar chart PNG as a BytesIO buffer.
    """
    names = [_shorten(name) for name, _ in entries]
    counts = [c for _, c in entries]
    peak = max(counts) if counts else 0
    n = len(entries)

    fig, ax = plt.subplots(figsize=(9, max(3.0, n * 0.5 + 1.2)), dpi=120)
    fig.patch.set_facecolor("#1a1a2e")
    ax.set_facecolor("#16213e")

    y = list(range(n))
    bar_colors = ["#e94560" if peak and c == peak else "#0f3460" for c in counts]
    bars = ax.barh(y, counts, color=bar_colors, edgecolor="#e94560", linewidth=0.6, height=0.65)
    ax.set_yticks(y)
    ax.set_yticklabels(names)
    ax.invert_yaxis()  # highest first, at the top

    for bar, val in zip(bars, counts):
        ax.text(
            bar.get_width() + peak * 0.01,
            bar.get_y() + bar.get_height() / 2,
            str(val),
            va="center", ha="left",
            color="white", fontsize=9, fontweight="bold",
        )

    ax.set_title("🍺 Лидеры выше среднего", color="white", fontsize=14, fontweight="bold", pad=12)
    ax.tick_params(colors="white", labelsize=10)
    ax.xaxis.set_major_locator(mticker.MaxNLocator(integer=True))
    ax.set_xlim(0, peak * 1.15 + 1)
    for spine in ax.spines.values():
        spine.set_edgecolor("#0f3460")
    ax.grid(axis="x", color="#0f3460", linewidth=0.8, linestyle="--", alpha=0.7)

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
    buf = await render_chart(chat_id, 7)
    await context.bot.send_photo(
        chat_id=chat_id,
        photo=buf,
        caption="📊 Итоги недели — пиво по дням 🍺",
    )
