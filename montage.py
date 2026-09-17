"""Nightly montage: stitches the day's circle videos into one highlight clip."""
import asyncio
import datetime
import logging
import os
import subprocess
import tempfile
from pathlib import Path

from telegram import Update
from telegram.ext import ContextTypes

import database
from common import MOSCOW

logger = logging.getLogger(__name__)

CLIP_SECONDS = 3        # how much of each circle makes it into the montage
TILE_SIZE = 360         # output is a square, like the circles themselves
FPS = 30
FFMPEG_TIMEOUT = 600    # seconds

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_COUNTER_FONT = str(_FONT_DIR / "Bitter.ttf")
_COUNTER_FONT_SIZE = 42


def _escape_ffmpeg_path(path: str) -> str:
    """Escape a filesystem path for use inside an ffmpeg filtergraph option
    (colons separate filter options, so a path containing one — e.g. a
    Windows drive letter — must be escaped)."""
    return path.replace("\\", "\\\\").replace(":", "\\:")


def _has_audio_stream(path: str) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_type", "-of", "csv=p=0", path],
        capture_output=True, text=True,
    )
    return bool(result.stdout.strip())


def _build_montage(paths: list[str], output_path: str) -> None:
    """Trim each clip to CLIP_SECONDS, normalize to a common square resolution/
    frame rate (source phones vary), burn in a running counter, and
    concatenate. Runs synchronously — call via asyncio.to_thread. Raises
    RuntimeError on ffmpeg failure."""
    font = _escape_ffmpeg_path(_COUNTER_FONT)
    inputs = []
    filter_parts = []
    concat_inputs = []
    for i, path in enumerate(paths):
        inputs += ["-i", path]
        filter_parts.append(
            f"[{i}:v]trim=0:{CLIP_SECONDS},setpts=PTS-STARTPTS,"
            f"scale={TILE_SIZE}:{TILE_SIZE}:force_original_aspect_ratio=increase,"
            f"crop={TILE_SIZE}:{TILE_SIZE},setsar=1,fps={FPS},"
            f"drawtext=fontfile={font}:text='#{i + 1}':fontsize={_COUNTER_FONT_SIZE}:"
            f"fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=10:x=14:y=14[v{i}]"
        )
        if _has_audio_stream(path):
            filter_parts.append(
                f"[{i}:a]atrim=0:{CLIP_SECONDS},asetpts=PTS-STARTPTS,"
                f"aresample=44100,aformat=channel_layouts=stereo[a{i}]"
            )
        else:
            # Silent circle (rare) — feed the concat filter a matching-length
            # silent track so the stream count stays consistent across segments.
            filter_parts.append(
                f"anullsrc=channel_layout=stereo:sample_rate=44100:"
                f"duration={CLIP_SECONDS}[a{i}]"
            )
        concat_inputs += [f"[v{i}]", f"[a{i}]"]

    filter_complex = ";".join(filter_parts) + ";" + \
        "".join(concat_inputs) + f"concat=n={len(paths)}:v=1:a=1[outv][outa]"

    cmd = [
        "ffmpeg", "-y",
        *inputs,
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k",
        output_path,
    ]
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg timed out after {FFMPEG_TIMEOUT}s") from e
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[-2000:]}")


async def _run_montage(context: ContextTypes.DEFAULT_TYPE, chat_id: int, report_date: str) -> bool:
    """Builds a montage from every circle sent in chat_id on report_date and
    sends it to that same chat_id. Returns True if a montage was sent, False
    if there was nothing to send."""
    file_ids = await asyncio.to_thread(
        database.get_video_file_ids_for_date, chat_id, report_date
    )
    if not file_ids:
        logger.info("montage: no circle videos for %s in chat %s, skipping", report_date, chat_id)
        return False

    with tempfile.TemporaryDirectory(prefix="montage_") as tmpdir:
        paths = []
        for i, file_id in enumerate(file_ids):
            try:
                tg_file = await context.bot.get_file(file_id)
                path = os.path.join(tmpdir, f"clip_{i:03d}.mp4")
                await tg_file.download_to_drive(path)
                paths.append(path)
            except Exception:
                logger.warning(
                    "montage: failed to download file_id=%s", file_id, exc_info=True
                )

        if not paths:
            logger.warning("montage: all downloads failed for %s in chat %s", report_date, chat_id)
            return False

        output_path = os.path.join(tmpdir, "montage.mp4")
        await asyncio.to_thread(_build_montage, paths, output_path)

        with open(output_path, "rb") as f:
            await context.bot.send_video(
                chat_id=chat_id,
                video=f,
                caption=f"🎬 Нарезка кружочков за {report_date} — {len(paths)} видео",
                supports_streaming=True,
            )
    logger.info("montage: sent montage for %s in chat %s (%d clips)", report_date, chat_id, len(paths))
    return True


async def montage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual trigger — builds a montage of today's circles so far and sends
    it to the chat the command was called from (a group's own circles, not
    necessarily the main group — handy for testing in a separate group).
    Admin-only in groups. From a private chat there's no group of circles to
    pull from, so it falls back to the stored main group instead."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type == "private":
        chat_id = database.get_chat_id()
        if not chat_id:
            await update.message.reply_text("Бот ещё не добавлен ни в одну группу\\.", parse_mode="MarkdownV2")
            return
    else:
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "⛔ Только администраторы могут вызвать нарезку\\.", parse_mode="MarkdownV2"
            )
            return
        chat_id = chat.id

    report_date = datetime.datetime.now(MOSCOW).date().isoformat()
    await update.message.reply_text("🎬 Собираю нарезку…")
    try:
        sent = await _run_montage(context, chat_id, report_date)
        if not sent:
            await update.message.reply_text("Сегодня ещё нет ни одного кружочка\\.", parse_mode="MarkdownV2")
    except Exception:
        logger.error("montage_command: failed for %s in chat %s", report_date, chat_id, exc_info=True)
        await update.message.reply_text("⚠️ Не получилось собрать нарезку\\.", parse_mode="MarkdownV2")


async def daily_montage_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: build and send yesterday's circle-video montage to the main group."""
    chat_id = database.get_chat_id()
    if not chat_id:
        logger.warning("daily_montage_job: no chat_id saved yet, skipping")
        return
    report_date = (datetime.datetime.now(MOSCOW) - datetime.timedelta(days=1)).date().isoformat()
    try:
        await _run_montage(context, chat_id, report_date)
    except Exception:
        logger.error("daily_montage_job: failed for %s", report_date, exc_info=True)
