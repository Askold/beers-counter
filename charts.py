"""Chart rendering in the "beer glass" theme (Pillow).

Three charts share one visual system — a warm radial background, a dark card,
and bars drawn as gradient-filled glasses of beer with a foamy head:

* daily beers, weekly (``/chart``) and monthly (``/chart m``)
* the above-median leaderboard (``/topchart``)

Each chart is drawn on a supersampled canvas and downscaled once for
anti-aliasing. The public async wrappers push the CPU work onto a worker
thread so the bot's event loop keeps serving updates.
"""
import asyncio
import datetime
import io
import logging
import math
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont
from telegram.ext import ContextTypes

import database

logger = logging.getLogger(__name__)

RU_DAYS = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

_FONT_DIR = Path(__file__).parent / "assets" / "fonts"
_BITTER = str(_FONT_DIR / "Bitter.ttf")
_NUNITO = str(_FONT_DIR / "Nunito.ttf")

S = 2  # supersampling — everything is drawn at S× then scaled down once

# ── Palette (lifted from the design reference) ────────────────────────────
_BG_STOPS = [(0.0, (107, 61, 16)), (0.45, (67, 35, 10)), (1.0, (36, 18, 4))]
_CARD_TOP, _CARD_BOT = (54, 28, 8), (26, 13, 4)
_CARD_LINE = (255, 214, 150)
_CREAM = (255, 233, 176)
_CREAM_HI = (255, 243, 205)
_TAN = (227, 198, 155)
_TAN_DIM = (183, 148, 106)
_AXIS = (201, 164, 117)
_GRID = (255, 214, 150)
_BEER_HI, _BEER_LO = (247, 195, 63), (221, 139, 6)
_PEAK_HI, _PEAK_LO = (255, 228, 122), (238, 158, 14)
_FOAM_HI, _FOAM_LO = (255, 253, 245), (255, 231, 189)
_GLOW = (255, 178, 64)


# ── primitives ───────────────────────────────────────────────────────────

@lru_cache(maxsize=128)
def _font(path: str, size: int, weight: str = "Bold") -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size * S)
    try:
        f.set_variation_by_name(weight)
    except Exception:
        pass
    return f


def _lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _lerp_stops(stops, t):
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    for (p0, c0), (p1, c1) in zip(stops, stops[1:]):
        if t <= p1:
            return _lerp(c0, c1, (t - p0) / (p1 - p0) if p1 > p0 else 0.0)
    return stops[-1][1]


def _vgrad(w, h, top, bot):
    """Vertical RGB gradient (top → bottom)."""
    h, w = max(1, int(h)), max(1, int(w))
    strip = Image.new("RGB", (1, h))
    for y in range(h):
        strip.putpixel((0, y), _lerp(top, bot, y / max(1, h - 1)))
    return strip.resize((w, h))


def _radial_bg(w, h):
    """Warm radial background with the light spot near the top centre."""
    sw = 280
    sh = max(1, round(sw * h / w))
    small = Image.new("RGB", (sw, sh))
    px = small.load()
    cx, cy, radius = sw * 0.5, sh * -0.12, sw * 1.35
    for y in range(sh):
        for x in range(sw):
            px[x, y] = _lerp_stops(_BG_STOPS, math.hypot(x - cx, y - cy) / radius)
    return small.resize((w, h), Image.BICUBIC)


def _round_mask(w, h, radius):
    m = Image.new("L", (int(w), int(h)), 0)
    ImageDraw.Draw(m).rounded_rectangle([0, 0, int(w) - 1, int(h) - 1], radius=radius, fill=255)
    return m


def _drop_shadow(w, h, radius, blur, alpha=130):
    pad = blur * 3
    layer = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [pad, pad, pad + w - 1, pad + h - 1], radius=radius, fill=(0, 0, 0, alpha)
    )
    return layer.filter(ImageFilter.GaussianBlur(blur)), pad


def _nice_axis(vmax, target=5):
    """Return (tick_values, axis_top) with a friendly round step."""
    if vmax <= 0:
        return [0, 1], 1
    raw = vmax / target
    mag = 10 ** math.floor(math.log10(raw))
    step = 10 * mag
    for m in (1, 2, 2.5, 3, 4, 5, 6, 8, 10):
        if m * mag >= raw:
            step = m * mag
            break
    top = math.ceil(vmax / step) * step
    ticks = [round(step * i) for i in range(round(top / step) + 1)]
    return ticks, round(top)


def _bubbles(seed, count, w, h):
    """Deterministic bubble positions/sizes inside a box of (w, h)."""
    s = (seed * 2654435761) & 0x7FFFFFFF
    out = []
    for _ in range(count):
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        r = 2 + s % 3
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        x = w * (0.16 + 0.68 * (s % 997) / 997)
        s = (s * 1103515245 + 12345) & 0x7FFFFFFF
        y = h * (0.10 + 0.82 * (s % 991) / 991)
        out.append((x, y, r))
    return out


# ── the beer glass ───────────────────────────────────────────────────────

def _beer_glass(w, h, foam_h, *, peak, seed, streak=True):
    """RGBA sprite of one glass: gradient body, cylindrical sheen, a highlight
    streak, a few bubbles and a foamy head. Top corners rounded."""
    w, h, foam_h = max(1, int(w)), max(1, int(h)), max(0, int(foam_h))
    rad = 4 * S
    mask = _round_mask(w, h, rad)
    body = Image.new("RGBA", (w, h), (0, 0, 0, 0))

    hi, lo = (_PEAK_HI, _PEAK_LO) if peak else (_BEER_HI, _BEER_LO)
    body.paste(_vgrad(w, h, hi, lo), (0, 0), mask)

    # cylindrical sheen: bright near the left edge, faint shadow near the right
    sheen = Image.new("RGBA", (w, 1))
    for x in range(w):
        t = x / max(1, w - 1)
        if t < 0.42:
            sheen.putpixel((x, 0), (255, 255, 255, int(70 * (1 - t / 0.42))))
        elif t > 0.72:
            sheen.putpixel((x, 0), (110, 55, 0, int(60 * (t - 0.72) / 0.28)))
        else:
            sheen.putpixel((x, 0), (0, 0, 0, 0))
    body.alpha_composite(sheen.resize((w, h)))

    d = ImageDraw.Draw(body)

    # bubbles rising through the beer — small, delicate, translucent
    for bx, by, br in _bubbles(seed, max(0, min(5, h // (46 * S))), w, h):
        br = br * S * 0.7
        if by < foam_h + br * 4:
            continue
        d.ellipse([bx - br, by - br, bx + br, by + br], outline=(255, 255, 255, 80), width=1)
        d.ellipse([bx - br * 0.35, by - br * 0.35, bx + br * 0.3, by + br * 0.3],
                  fill=(255, 255, 255, 150))

    # left highlight streak
    if streak:
        y0 = int(h * 0.30)
        sh = h - y0 - 8 * S
        if sh > 6 * S:
            st = Image.new("RGBA", (7 * S, sh), (0, 0, 0, 0))
            for y in range(sh):
                st.paste((255, 255, 255, int(120 * (1 - y / sh))), (0, y, 7 * S, y + 1))
            body.alpha_composite(st.filter(ImageFilter.GaussianBlur(S)), (9 * S, y0))

    # foamy head
    if foam_h > 3 * S:
        bulge = 6 * S
        foam = _vgrad(w, foam_h + bulge, _FOAM_HI, _FOAM_LO).convert("RGBA")
        fd = ImageDraw.Draw(foam)
        for fx, fy, fr in _bubbles(seed ^ 0x5BD1, 6, w, foam_h):
            fr = fr * S * 0.9 + S
            fd.ellipse([fx - fr, fy - fr, fx + fr, fy + fr], fill=(255, 255, 255, 120))
            fd.ellipse([fx - fr, fy - fr, fx + fr * 0.15, fy + fr * 0.15], fill=(255, 255, 255, 180))
        fa = Image.new("L", (w, foam_h + bulge), 0)
        ad = ImageDraw.Draw(fa)
        ad.rectangle([0, 0, w, foam_h], fill=255)
        for k in range(4):
            cxb = w * (0.1 + 0.27 * k)
            rb = w * 0.19
            ad.ellipse([cxb - rb, foam_h - rb, cxb + rb, foam_h + rb], fill=255)
        foam.putalpha(fa.filter(ImageFilter.GaussianBlur(S)))
        body.alpha_composite(foam, (0, 0))
        # soft contact shadow just beneath the foam
        cs = Image.new("RGBA", (w, 12 * S), (0, 0, 0, 0))
        ImageDraw.Draw(cs).rectangle([0, 0, w, 4 * S], fill=(120, 70, 15, 60))
        body.alpha_composite(cs.filter(ImageFilter.GaussianBlur(3 * S)), (0, foam_h))

    body.putalpha(ImageChops.multiply(body.getchannel("A"), mask))
    if peak:
        ImageDraw.Draw(body).rounded_rectangle(
            [0, 0, w - 1, h - 1], radius=rad, outline=(255, 238, 178, 255), width=3 * S)
    return body


def _paste_glow(base, sprite, xy, radius, tint):
    """Composite a warm halo behind `sprite` — a wide soft bloom plus a tight
    bright core, each laid down twice so it reads against the dark card."""
    x, y = xy
    pad = int(radius * 4)
    a = sprite.getchannel("A")
    stamp = Image.new("RGBA", (sprite.width + pad * 2, sprite.height + pad * 2), (0, 0, 0, 0))
    stamp.paste(tint + (255,), (pad, pad, pad + sprite.width, pad + sprite.height), a)
    wide = stamp.filter(ImageFilter.GaussianBlur(radius))
    core = stamp.filter(ImageFilter.GaussianBlur(radius * 0.38))
    for layer in (wide, wide, core, core):
        base.alpha_composite(layer, (x - pad, y - pad))


def _draw_value(base, xy, text, font, fill, *, anchor="md", glow=False):
    if glow:
        layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
        ImageDraw.Draw(layer).text(xy, text, font=font, fill=(255, 196, 92, 255), anchor=anchor)
        blur = layer.filter(ImageFilter.GaussianBlur(7 * S))
        base.alpha_composite(blur)
        base.alpha_composite(blur)
    ImageDraw.Draw(base).text(xy, text, font=font, fill=fill, anchor=anchor)


# ── header / card frame ──────────────────────────────────────────────────

def _mug_icon(size):
    """A tiny gradient beer mug for the header."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    x0, y0, x1, y1 = int(size * 0.10), int(size * 0.18), int(size * 0.66), int(size * 0.94)
    bw, bh = x1 - x0, y1 - y0
    img.paste(_vgrad(bw, bh, _BEER_HI, _BEER_LO), (x0, y0),
              _round_mask(bw, bh, int(size * 0.08)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([int(size * 0.60), int(size * 0.34), int(size * 0.93), int(size * 0.80)],
                        radius=int(size * 0.16), outline=_CREAM, width=max(2, int(size * 0.07)))
    fh = int(bh * 0.4)
    fa = Image.new("L", (bw, fh), 0)
    fad = ImageDraw.Draw(fa)
    fad.rounded_rectangle([0, 0, bw - 1, fh - 1], radius=int(size * 0.08), fill=255)
    for k in range(3):
        fad.ellipse([bw * (0.12 + 0.3 * k) - bw * 0.22, fh * 0.5,
                     bw * (0.12 + 0.3 * k) + bw * 0.22, fh * 1.3], fill=255)
    img.paste(_vgrad(bw, fh, _FOAM_HI, _FOAM_LO), (x0, y0), fa)
    return img


def _frame(width, height, title, pill_text):
    """Background + card + header (mug & title) + the totals pill.
    Returns (image, draw, card_box)."""
    img = _radial_bg(width, height).convert("RGBA")
    m = 26 * S
    head_h = 60 * S
    card = [m, m + head_h, width - m, height - m]
    cw, ch = card[2] - card[0], card[3] - card[1]

    shadow, pad = _drop_shadow(cw, ch, 10 * S, 20 * S)
    img.alpha_composite(shadow, (card[0] - pad, card[1] - pad + 10 * S))
    img.paste(_vgrad(cw, ch, _CARD_TOP, _CARD_BOT), (card[0], card[1]),
              _round_mask(cw, ch, 10 * S))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(card, radius=10 * S, outline=_CARD_LINE + (46,), width=S)

    img.alpha_composite(_mug_icon(head_h), (m, m - 2 * S))
    d.text((m + head_h + 8 * S, m + head_h // 2), title,
           font=_font(_BITTER, 30, "ExtraBold"), fill=_CREAM_HI, anchor="lm")

    if pill_text:
        pf = _font(_NUNITO, 15, "ExtraBold")
        tb = d.textbbox((0, 0), pill_text, font=pf)
        pw, ph = tb[2] - tb[0] + 34 * S, tb[3] - tb[1] + 22 * S
        px0, py0 = width - m - pw, m + (head_h - ph) // 2
        img.paste(_vgrad(pw, ph, (92, 62, 24), (60, 40, 16)), (px0, py0),
                  _round_mask(pw, ph, 7 * S))
        d.rounded_rectangle([px0, py0, px0 + pw, py0 + ph], radius=7 * S,
                            outline=(255, 220, 160, 90), width=S)
        d.text((px0 + pw // 2, py0 + ph // 2), pill_text, font=pf, fill=_CREAM_HI, anchor="mm")

    return img, d, card


def _finish(img, out_w):
    img = img.convert("RGB").resize(
        (out_w, round(img.height * out_w / img.width)), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return buf


# ── daily chart (weekly / monthly) ───────────────────────────────────────

def build_daily_chart(rows, days):
    """rows: [(date_str, count), ...] ascending. ``days`` picks the layout."""
    dates = [r[0] for r in rows]
    counts = [r[1] for r in rows]
    n = len(rows)
    peak = max(counts) if counts else 0
    weekly = n <= 7

    width = (1160 if weekly else 1780) * S
    height = 850 * S
    total = sum(counts)
    img, d, card = _frame(width, height,
                          "Пиво за неделю" if weekly else "Пиво за месяц",
                          f"Итого за {7 if weekly else n} дней: {total}")

    pad_x, pad_top, pad_bot = 30 * S, 24 * S, 16 * S
    axis_w = 48 * S
    xlab_h = 58 * S if weekly else 38 * S
    val_h = 40 * S
    plot = [card[0] + pad_x + axis_w, card[1] + pad_top + val_h,
            card[2] - pad_x, card[3] - pad_bot - xlab_h]
    pw, ph = plot[2] - plot[0], plot[3] - plot[1]

    ticks, top = _nice_axis(peak)
    af = _font(_NUNITO, 13, "Bold")
    for tv in ticks:
        y = plot[3] - ph * tv / top
        d.line([(plot[0], y), (plot[2], y)],
               fill=_GRID + (72 if tv == 0 else 24,), width=S)
        d.text((plot[0] - 12 * S, y), str(tv), font=af, fill=_AXIS, anchor="rm")

    slot = pw / n
    gw = min(84 * S, slot * (0.62 if weekly else 0.88))
    vf = _font(_BITTER, 24 if weekly else 16, "Bold")
    vf_peak = _font(_BITTER, 26, "ExtraBold")

    for i, cnt in enumerate(counts):
        if cnt <= 0:
            continue
        cx = plot[0] + slot * (i + 0.5)
        gh = max(6 * S, ph * cnt / top)
        is_peak = cnt == peak and peak > 0
        foam = min(64 * S, max(10 * S, gh * (0.20 if weekly else 0.16)))
        glass = _beer_glass(gw, gh, foam, peak=is_peak, seed=i + 1, streak=weekly)
        gx, gy = round(cx - gw / 2), round(plot[3] - gh)
        if is_peak:
            _paste_glow(img, glass, (gx, gy), 20 * S, _GLOW)
        img.alpha_composite(glass, (gx, gy))
        if weekly or is_peak:
            _draw_value(img, (cx, gy - 6 * S), str(cnt),
                        vf_peak if is_peak else vf,
                        _CREAM_HI if is_peak else _CREAM, glow=is_peak)

    lf = _font(_NUNITO, 15, "Bold")
    lf_dim = _font(_NUNITO, 13, "SemiBold")
    step = 1 if weekly else max(1, round(n / 12))
    for i, dstr in enumerate(dates):
        if not weekly and i % step:
            continue
        cx = plot[0] + slot * (i + 0.5)
        dt = datetime.date.fromisoformat(dstr)
        hot = counts[i] == peak and peak > 0
        if weekly:
            d.text((cx, plot[3] + 13 * S), RU_DAYS[dt.weekday()], font=lf,
                   fill=_CREAM if hot else _TAN, anchor="ma")
            d.text((cx, plot[3] + 35 * S), f"{dstr[8:]}.{dstr[5:7]}", font=lf_dim,
                   fill=_TAN if hot else _TAN_DIM, anchor="ma")
        else:
            d.text((cx, plot[3] + 12 * S), f"{dstr[8:]}.{dstr[5:7]}", font=lf_dim,
                   fill=_TAN_DIM, anchor="ma")

    return _finish(img, width // S)


# ── leaderboard chart (above the median) ─────────────────────────────────

def _shorten(name, limit=22):
    return name if len(name) <= limit else name[: limit - 1] + "…"


def build_leaderboard_chart(entries):
    """entries: [(full_name, count), ...] filtered/sorted, highest first.
    Each glass lies on its side, foam pointing right toward the count."""
    names = [_shorten(nm) for nm, _ in entries]
    counts = [c for _, c in entries]
    peak = max(counts) if counts else 0
    n = len(entries)

    width = 1160 * S
    height = round((150 + n * 64) * S)
    img, d, card = _frame(width, height, "Лидеры выше среднего",
                          f"Всего у них: {sum(counts)}")

    pad = 26 * S
    name_w, val_w = 292 * S, 96 * S
    x0 = card[0] + pad + name_w
    x1 = card[2] - pad - val_w
    span = x1 - x0
    top = peak * 1.04 or 1
    y0 = card[1] + pad
    band = (card[3] - pad - y0) / n

    nf = _font(_NUNITO, 17, "Bold")
    vf = _font(_BITTER, 20, "Bold")
    vf_peak = _font(_BITTER, 23, "ExtraBold")

    for i, (nm, cnt) in enumerate(zip(names, counts)):
        cy = y0 + band * (i + 0.5)
        bl = max(16 * S, span * cnt / top)
        thick = round(min(44 * S, band * 0.60))
        is_peak = cnt == peak and peak > 0
        foam = min(40 * S, max(12 * S, bl * 0.14))
        glass = _beer_glass(thick, round(bl), round(foam), peak=is_peak,
                            seed=i + 7, streak=False)
        glass = glass.rotate(-90, expand=True, resample=Image.BICUBIC)
        gx, gy = round(x0), round(cy - glass.height / 2)
        if is_peak:
            _paste_glow(img, glass, (gx, gy), 18 * S, _GLOW)
        img.alpha_composite(glass, (gx, gy))
        d.text((x0 - 18 * S, cy), nm, font=nf,
               fill=_CREAM_HI if is_peak else _CREAM, anchor="rm")
        _draw_value(img, (card[2] - pad, cy), str(cnt),
                    vf_peak if is_peak else vf,
                    _CREAM_HI if is_peak else _CREAM, anchor="rm", glow=is_peak)

    return _finish(img, width // S)


# ── async wrappers ───────────────────────────────────────────────────────

# Pillow's font objects aren't safe to render from several threads at once;
# chart requests are rare, so just serialise the (off-loop) drawing.
_render_lock = asyncio.Lock()


async def render_chart(chat_id: int, days: int = 7) -> io.BytesIO:
    """Daily-beers chart off the event loop. days=7 → weekly, days=30 → monthly."""
    rows = await asyncio.to_thread(database.get_daily_counts, chat_id, days)
    async with _render_lock:
        return await asyncio.to_thread(build_daily_chart, rows, days)


async def render_leaderboard_chart(entries: list[tuple[str, int]]) -> io.BytesIO:
    """Above-median leaderboard chart from pre-filtered entries (highest first)."""
    async with _render_lock:
        return await asyncio.to_thread(build_leaderboard_chart, entries)


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
