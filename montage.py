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
FFMPEG_TIMEOUT = 120    # seconds, per clip

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


def _run_ffmpeg(cmd: list[str]) -> None:
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=FFMPEG_TIMEOUT
        )
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"ffmpeg timed out after {FFMPEG_TIMEOUT}s") from e
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg failed (exit {result.returncode}): {result.stderr[-2000:]}")


def _normalize_clip(path: str, index: int, output_path: str) -> None:
    """Trim one clip to CLIP_SECONDS, normalize it to a common square
    resolution/frame rate (source phones vary), and burn in its counter.
    One ffmpeg process per clip — keeps peak memory low regardless of how
    many circles are in the day, unlike a single filter graph with every
    clip open as an input at once (which OOMed on the host)."""
    font = _escape_ffmpeg_path(_COUNTER_FONT)
    video_filter = (
        f"trim=0:{CLIP_SECONDS},setpts=PTS-STARTPTS,"
        f"scale={TILE_SIZE}:{TILE_SIZE}:force_original_aspect_ratio=increase,"
        f"crop={TILE_SIZE}:{TILE_SIZE},setsar=1,fps={FPS},"
        f"drawtext=fontfile={font}:text='#{index + 1}':fontsize={_COUNTER_FONT_SIZE}:"
        f"fontcolor=white:box=1:boxcolor=black@0.5:boxborderw=10:x=14:y=14"
    )
    if _has_audio_stream(path):
        cmd = [
            "ffmpeg", "-y", "-threads", "1", "-i", path,
            "-vf", video_filter,
            "-af", f"atrim=0:{CLIP_SECONDS},asetpts=PTS-STARTPTS,aresample=44100,aformat=channel_layouts=stereo",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
    else:
        # Silent circle (rare) — pad with silence, trimmed to match the
        # video via -shortest so the two streams end together.
        cmd = [
            "ffmpeg", "-y", "-threads", "1", "-i", path,
            "-f", "lavfi", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-vf", video_filter, "-shortest",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-c:a", "aac", "-b:a", "128k",
            output_path,
        ]
    _run_ffmpeg(cmd)


def _concat_clips(normalized_paths: list[str], list_file: str, output_path: str) -> None:
    """Stitch already-normalized (identical codec/resolution/fps) clips with
    the concat demuxer — a stream copy, not a re-encode, so it's cheap
    regardless of how many clips there are."""
    with open(list_file, "w") as f:
        for p in normalized_paths:
            f.write(f"file '{p}'\n")
    _run_ffmpeg(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_file, "-c", "copy", output_path])


def _build_montage(paths: list[str], tmpdir: str, output_path: str) -> int:
    """Normalizes each clip one at a time, then concatenates the survivors.
    Runs synchronously — call via asyncio.to_thread. Returns how many clips
    made it into the montage; raises RuntimeError if none did."""
    normalized = []
    for i, path in enumerate(paths):
        norm_path = os.path.join(tmpdir, f"norm_{i:03d}.mp4")
        try:
            _normalize_clip(path, i, norm_path)
            normalized.append(norm_path)
        except Exception:
            logger.warning("montage: failed to normalize clip %d (%s)", i, path, exc_info=True)

    if not normalized:
        raise RuntimeError("no clips survived normalization")

    list_file = os.path.join(tmpdir, "concat_list.txt")
    _concat_clips(normalized, list_file, output_path)
    return len(normalized)


async def _run_montage(
    context: ContextTypes.DEFAULT_TYPE,
    source_chat_id: int,
    dest_chat_id: int,
    report_date: str,
) -> bool:
    """Builds a montage from every circle sent in source_chat_id on
    report_date and sends it to dest_chat_id (the two differ when /montage is
    tested from a separate chat — the circles always come from the main
    group). Returns True if a montage was sent, False if there was nothing
    to send."""
    file_ids = await asyncio.to_thread(
        database.get_video_file_ids_for_date, source_chat_id, report_date
    )
    if not file_ids:
        logger.info(
            "montage: no circle videos for %s in chat %s, skipping", report_date, source_chat_id
        )
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
            logger.warning(
                "montage: all downloads failed for %s in chat %s", report_date, source_chat_id
            )
            return False

        output_path = os.path.join(tmpdir, "montage.mp4")
        clip_count = await asyncio.to_thread(_build_montage, paths, tmpdir, output_path)

        with open(output_path, "rb") as f:
            await context.bot.send_video(
                chat_id=dest_chat_id,
                video=f,
                caption=f"🎬 Нарезка кружочков за {report_date} — {clip_count} видео",
                supports_streaming=True,
            )
    logger.info(
        "montage: sent montage for %s (source chat %s) to chat %s (%d clips)",
        report_date, source_chat_id, dest_chat_id, clip_count,
    )
    return True


async def montage_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual trigger — builds a montage of the main group's circles for today
    so far, and sends it to whichever chat the command was called from (handy
    for previewing in a separate test group without posting to the main one).
    Admin-only in groups; unrestricted from a private chat."""
    chat = update.effective_chat
    user = update.effective_user

    if chat.type != "private":
        member = await chat.get_member(user.id)
        if member.status not in ("administrator", "creator"):
            await update.message.reply_text(
                "⛔ Только администраторы могут вызвать нарезку\\.", parse_mode="MarkdownV2"
            )
            return

    main_chat_id = database.get_chat_id()
    if not main_chat_id:
        await update.message.reply_text("Бот ещё не добавлен ни в одну группу\\.", parse_mode="MarkdownV2")
        return

    report_date = datetime.datetime.now(MOSCOW).date().isoformat()
    await update.message.reply_text("🎬 Собираю нарезку…")
    try:
        sent = await _run_montage(context, main_chat_id, chat.id, report_date)
        if not sent:
            await update.message.reply_text("Сегодня ещё нет ни одного кружочка\\.", parse_mode="MarkdownV2")
    except Exception:
        logger.error("montage_command: failed for %s", report_date, exc_info=True)
        await update.message.reply_text("⚠️ Не получилось собрать нарезку\\.", parse_mode="MarkdownV2")


async def daily_montage_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Scheduled job: build and send yesterday's circle-video montage to the main group."""
    chat_id = database.get_chat_id()
    if not chat_id:
        logger.warning("daily_montage_job: no chat_id saved yet, skipping")
        return
    report_date = (datetime.datetime.now(MOSCOW) - datetime.timedelta(days=1)).date().isoformat()
    try:
        await _run_montage(context, chat_id, chat_id, report_date)
    except Exception:
        logger.error("daily_montage_job: failed for %s", report_date, exc_info=True)
