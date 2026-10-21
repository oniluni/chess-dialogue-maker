"""Step 3: draw the chess.com-style UI with Pillow and composite it with the clip.

Python is the compositor: ffmpeg decodes the clip into raw RGB frames, each output
frame is assembled with numpy (reframed video box + UI strip or side panel) and
streamed into a second ffmpeg that encodes x264. That gives frame-exact control
over the pause-on-move freeze frames and the smart reframing (reframe.py).
Identical consecutive frames (static UI over a frozen picture) are reused.

The output timeline is a `Schedule`: for every output frame, the source frame to
show and the UI state. With pause_on_move the video plays until a move has been
said, freezes while its card pops in and the explanation is typed out, then
resumes from the same frame. The audio is assembled sample-exactly in numpy from
the same schedule (original sound, silent during pauses) and mixed with the SFX
track (pause / unpause / typewriter ticks), so picture and sound stay in sync.
"""
from __future__ import annotations

import dataclasses
import logging
import math
import random
import subprocess
import wave
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import numpy as np
from PIL import Image, ImageChops, ImageDraw, ImageFont

import config
import reframe
import style as vstyle
from analyzer import Move
from ingest import fmt_duration, probe
from make_assets import ensure_icons, icon_path, sfx_files, write_wav

log = logging.getLogger(__name__)
L = config.LAYOUT
DEFAULT_STYLE = vstyle.DEFAULT

EVAL_ANIM = DEFAULT_STYLE.eval_anim     # s, eval bar ease-in-out (defaults; a VideoStyle carries its own)
ICON_ANIM = DEFAULT_STYLE.icon_anim     # s, icon pop-in
CARD_FADE = DEFAULT_STYLE.card_fade     # s, card crossfade
BOTTOM_SAFE = 300   # px left free under a bottom analysis strip (TikTok's caption and buttons)
TIKTOK_SAFE_BOTTOM = 420  # px at the bottom of the TikTok screen under its caption / music line
TIKTOK_SAFE_RIGHT = 140   # px at the right edge under its like / comment / share buttons
SAFE_PAD = 8        # px between an overlay and the edge of the free area
PAUSE_TAIL = 0.12   # s, the picture freezes this long after the last word (if there is room)
TICK_GAP = 0.045    # s, minimum distance between two typewriter ticks
AUDIO_SR = 48000
DECODE_MAX = 1920   # px, long side of decoded source frames
SFX_GAIN = {"pause": 0.9, "unpause": 0.75, "tick": 0.55, "move": 0.6}


class RenderError(RuntimeError):
    pass


@dataclass
class RenderOptions:
    pause_on_move: bool = config.PAUSE_ON_MOVE
    sfx_enabled: bool = config.SFX_ENABLED
    sfx_volume: float = config.SFX_VOLUME
    output_format: str = config.OUTPUT_FORMAT
    typing_cps: float = config.TYPING_CPS      # typewriter speed, characters per second
    read_hold: float = config.READ_HOLD        # s of reading time after the text is typed
    sfx_pause: bool = config.SFX_PAUSE
    sfx_unpause: bool = config.SFX_UNPAUSE
    sfx_typing: bool = config.SFX_TYPING
    sfx_typing_set: str = config.SFX_TYPING_SET  # make_assets.TYPING_SETS id (or a folder in assets/sfx/typing)
    sfx_pause_set: str = config.SFX_PAUSE_SET    # make_assets.PAUSE_SETS id: the pause + resume pair

    def sound_on(self, name: str) -> bool:
        if not self.sfx_enabled or self.sfx_volume <= 0:
            return False
        if name.startswith("tick"):
            return self.sfx_typing
        return self.sfx_unpause if name == "unpause" else self.sfx_pause


# ---------------------------------------------------------------- easing
def ease_in_out(t: float) -> float:
    t = max(0.0, min(1.0, t))
    return 4 * t**3 if t < 0.5 else 1 - (-2 * t + 2) ** 3 / 2


def ease_out_back(t: float, k: float = 1.9) -> float:
    t = max(0.0, min(1.0, t)) - 1
    return 1 + (k + 1) * t**3 + k * t**2


# ---------------------------------------------------------------- text helpers
def font(weight: str, size: int, family: str = "Inter") -> ImageFont.FreeTypeFont:
    return vstyle.load_font(family, weight, max(8, int(size)))


def text_w(f: ImageFont.FreeTypeFont, s: str) -> float:
    return f.getlength(s)


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: int, first_w: int | None = None) -> list[str]:
    """Greedy word wrap; `first_w` narrows only the first line (room for the icon)."""
    lines: list[str] = []

    def width() -> int:
        return first_w if first_w is not None and not lines else max_w

    for para in text.split("\n"):
        cur = ""
        for word in para.split():
            cand = f"{cur} {word}".strip()
            if text_w(f, cand) <= width():
                cur = cand
                continue
            if cur:
                lines.append(cur)
            while text_w(f, word) > width():  # hard-break very long tokens
                i = max(1, next((i for i in range(len(word), 0, -1) if text_w(f, word[:i]) <= width()), 1))
                lines.append(word[:i])
                word = word[i:]
            cur = word
        lines.append(cur)
    return lines


def ellipsize(line: str, f: ImageFont.FreeTypeFont, max_w: int) -> str:
    while line and text_w(f, line + "…") > max_w:
        line = line[:-1].rstrip()
    return line + "…"


def line_h(f: ImageFont.FreeTypeFont, spacing: float) -> int:
    asc, desc = f.getmetrics()
    return int((asc + desc) * spacing)


# ---------------------------------------------------------------- static pieces
@dataclass(frozen=True)
class CardSpec:
    title: str
    subtitle: str
    body: str
    color: str           # accent + title
    icon: str | None     # move type for the badge


def card_spec(move: Move, st: vstyle.VideoStyle = DEFAULT_STYLE) -> CardSpec:
    title = move.title.upper() if st.title_upper else move.title
    return CardSpec(title, move.move_number, move.explanation, st.move_color(move.move_type), move.move_type)


def intro_spec(names: dict[str, str], st: vstyle.VideoStyle = DEFAULT_STYLE) -> CardSpec:
    """Card shown before the first move is analysed (pause-on-move mode)."""
    title = f"{names['white']} — {names['black']}"
    return CardSpec(title.upper() if st.title_upper else title, "Партия начинается", "", st.text_muted, None)


@dataclass
class CardText:
    ft: ImageFont.FreeTypeFont
    fs: ImageFont.FreeTypeFont
    fb: ImageFont.FreeTypeFont
    title: list[str]
    body: list[str]
    h_title: int
    h_sub: int
    h_body: int
    gap1: int
    gap2: int

    @property
    def height(self) -> int:
        return len(self.title) * self.h_title + self.gap1 + self.h_sub + self.gap2 + len(self.body) * self.h_body

    @property
    def body_chars(self) -> int:
        """Characters the typewriter reveals (a line break counts as one)."""
        return sum(len(ln) for ln in self.body) + max(0, len(self.body) - 1)


def _card_rect(geo: config.Layout, card_h: int) -> tuple[int, int, int, int]:
    m = geo.card_margin
    return m, m // 2, geo.width - 1 - m, card_h - 1 - m // 2


def _card_x(geo: config.Layout) -> tuple[int, int, int]:
    x = geo.card_margin + geo.card_accent_w + geo.card_pad_x - 8
    full_w = geo.width - geo.card_margin - x - geo.card_pad_x
    return x, full_w, full_w - int(geo.icon_size * 0.9)


def layout_card(spec: CardSpec, geo: config.Layout, card_h: int | None = None,
                st: vstyle.VideoStyle = DEFAULT_STYLE) -> CardText:
    """Shrink fonts until the text fits `card_h`; ellipsize the body as a last resort."""
    card_h = card_h or geo.card_h
    _, full_w, title_w = _card_x(geo)
    avail_h = card_h - 2 * geo.card_pad_y - geo.card_margin
    for scale in (1.0, 0.93, 0.86, 0.8, 0.74, 0.68):
        ft = st.font("bold", geo.title_size * scale)
        fs = st.font("regular", geo.subtitle_size * max(scale, 0.85))
        fb = st.font("regular", geo.body_size * scale)
        ct = CardText(
            ft, fs, fb,
            title=wrap(spec.title, ft, full_w, first_w=title_w if spec.icon and geo.icon_size else None)[:3],
            body=wrap(spec.body, fb, full_w) if spec.body else [],
            h_title=line_h(ft, geo.title_lh), h_sub=line_h(fs, geo.subtitle_lh), h_body=line_h(fb, geo.body_lh),
            gap1=int(geo.title_gap * scale), gap2=int(geo.body_gap * scale),
        )
        if ct.height <= avail_h:
            return ct
    max_body = max(1, (avail_h - ct.height + len(ct.body) * ct.h_body) // ct.h_body)
    if len(ct.body) > max_body:
        ct.body = ct.body[:max_body]
        ct.body[-1] = ellipsize(ct.body[-1], ct.fb, full_w)
    return ct


def fit_card_height(specs: list[CardSpec], geo: config.Layout, st: vstyle.VideoStyle = DEFAULT_STYLE) -> int:
    """One card height per video: tall enough for the longest move, so nothing jumps."""
    need = max(layout_card(s, geo, geo.card_max_h, st).height for s in specs) + 2 * geo.card_pad_y + geo.card_margin
    return max(geo.card_min_h, min(geo.card_max_h + geo.card_margin, need))


def render_card(spec: CardSpec, geo: config.Layout, reveal: int | None = None,
                st: vstyle.VideoStyle = DEFAULT_STYLE) -> Image.Image:
    """`reveal` = how many characters of the body are typed so far (None = all)."""
    W, H = geo.width, geo.card_h
    img = Image.new("RGB", (W, H), st.page_bg)
    d = ImageDraw.Draw(img)
    rect, r = _card_rect(geo, H), geo.card_radius
    if st.accent == "border":
        d.rounded_rectangle(rect, r, fill=st.card_bg, outline=spec.color, width=3)
    else:
        d.rounded_rectangle(rect, r, fill=st.card_bg)
        if st.accent == "bar":  # the stripe follows the card's rounded corners
            shape = Image.new("L", (W, H), 0)
            ImageDraw.Draw(shape).rounded_rectangle(rect, r, fill=255)
            strip = Image.new("L", (W, H), 0)
            ImageDraw.Draw(strip).rectangle((rect[0], 0, rect[0] + geo.card_accent_w - 1, H), fill=255)
            img.paste(spec.color, mask=ImageChops.multiply(shape, strip))
    x, full_w, _ = _card_x(geo)
    ct = layout_card(spec, geo, None, st)

    y = geo.card_pad_y + geo.card_margin // 2
    title_fill = spec.color if spec.icon and st.title_accent else st.text_main
    for ln in ct.title:
        d.text((x, y), ln, font=ct.ft, fill=title_fill)
        y += ct.h_title
    y += ct.gap1
    sub = spec.subtitle
    if text_w(ct.fs, sub) > full_w:
        sub = ellipsize(sub, ct.fs, full_w)
    d.text((x, y), sub, font=ct.fs, fill=st.text_muted)
    y += ct.h_sub + ct.gap2
    left = ct.body_chars if reveal is None else reveal
    for ln in ct.body:
        if left <= 0:
            break
        d.text((x, y), ln[:left], font=ct.fb, fill=st.text_main)
        left -= len(ln) + 1
        y += ct.h_body
    return img


def _fit_font(st: vstyle.VideoStyle, weight: str, size: int, text: str, max_w: int) -> ImageFont.FreeTypeFont:
    while size > 18 and text_w(st.font(weight, size), text) > max_w:
        size -= 2
    return st.font(weight, size)


def render_players(names: dict[str, str], avatars: dict[str, Path], geo: config.Layout = L,
                   st: vstyle.VideoStyle = DEFAULT_STYLE) -> Image.Image:
    W = geo.width
    img = Image.new("RGB", (W, max(1, geo.players_h)), st.players_bg)
    d = ImageDraw.Draw(img)
    a = geo.avatar_size
    pad = geo.players_pad + geo.card_margin
    ay = (geo.players_h - a) // 2
    mask = Image.new("L", (a, a), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, a - 1, a - 1), radius=6, fill=255)
    max_name_w = W // 2 - a - pad - 32
    fside = st.font("regular", geo.side_size)

    for side, label in (("white", "Белые"), ("black", "Чёрные")):
        av = Image.open(avatars[side]).convert("RGB").resize((a, a), Image.LANCZOS)
        ax = pad if side == "white" else W - pad - a
        d.rounded_rectangle((ax - 2, ay - 2, ax + a + 1, ay + a + 1), radius=8, fill=st.avatar_ring)
        img.paste(av, (ax, ay), mask)
        fname = _fit_font(st, "bold", geo.name_size, names[side], max_name_w)
        name_h = line_h(fname, 1.0)
        block_h = name_h + line_h(fside, 1.0)
        ty = (geo.players_h - block_h) // 2
        if side == "white":
            tx, anchor = ax + a + 16, "la"
        else:
            tx, anchor = ax - 16, "ra"
        d.text((tx, ty), names[side], font=fname, fill=st.text_main, anchor=anchor)
        d.text((tx, ty + name_h + 2), label, font=fside, fill=st.text_muted, anchor=anchor)
    return img


EVAL_BAR_SOFTNESS = 5.0  # smaller = small evals move the bar more


def eval_fraction(score: float) -> float:
    """Share of the bar that is white. Non-linear like chess.com, so small evals are visible:
    0 -> 50%, +1 -> 60%, +3 -> 77%, +5 -> 88%, +10 -> 98%."""
    return max(0.02, min(0.98, 0.5 + 0.5 * math.tanh(score / EVAL_BAR_SOFTNESS)))


def format_eval(score: float) -> str:
    score = round(score, 1)
    if score == 0:
        return "0.0"
    return f"{score:+.1f}"


def render_eval(score: float, geo: config.Layout = L, st: vstyle.VideoStyle = DEFAULT_STYLE) -> Image.Image:
    W = geo.width
    img = Image.new("RGB", (W, geo.eval_h), st.eval_black)
    d = ImageDraw.Draw(img)
    split = round(W * eval_fraction(score))
    d.rectangle((0, 0, split - 1, geo.eval_h), fill=st.eval_white)
    f = st.font("bold", geo.eval_label_size)
    label = format_eval(score)
    cy = geo.eval_h // 2
    if score >= 0:
        d.text((12, cy), label, font=f, fill=st.eval_black, anchor="lm")
    else:
        d.text((W - 12, cy), label, font=f, fill=st.eval_white, anchor="rm")
    return img


@lru_cache(maxsize=16)
def icon_image(move_type: str, size: int = L.icon_size) -> Image.Image:
    ensure_icons()
    return Image.open(icon_path(move_type)).convert("RGBA").resize((size, size), Image.LANCZOS)


# ---------------------------------------------------------------- output geometry
@dataclass
class Geometry:
    canvas: tuple[int, int]            # w, h
    layout: config.Layout              # UI strip / panel (its width, fitted card height)
    ui_pos: tuple[int, int]            # top-left of the UI image on the canvas
    box: tuple[int, int, int, int]     # video box x, y, w, h


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def styled_layout(st: vstyle.VideoStyle) -> config.Layout:
    """config.LAYOUT with the style's typography scale, card shape and visible blocks."""
    return dataclasses.replace(
        L, title_size=round(L.title_size * st.title_scale), body_size=round(L.body_size * st.body_scale),
        subtitle_size=round(L.subtitle_size * min(1.2, max(0.9, st.body_scale))),
        card_margin=st.card_margin, card_radius=st.card_radius,
        players_h=L.players_h if st.show_players else 0, eval_h=L.eval_h if st.show_eval else 0,
        eval_gap=L.eval_gap if st.show_eval else 0, icon_size=L.icon_size if st.show_icon else 0,
    )


def geometry(fmt: str, src_w: int, src_h: int, specs: list[CardSpec], st: vstyle.VideoStyle = DEFAULT_STYLE) -> Geometry:
    if fmt not in config.OUTPUT_FORMATS:
        raise RenderError(f"Unknown output format '{fmt}' (use {', '.join(config.OUTPUT_FORMATS)})")
    base = styled_layout(st)
    if fmt == "landscape":
        cw, ch, pw = 1920, 1080, config.LANDSCAPE_PANEL_W
        lay = dataclasses.replace(base, width=pw, top_margin=0, card_min_h=240, card_max_h=560)
        lay = dataclasses.replace(lay, card_h=fit_card_height(specs, lay, st))
        stack = lay.card_h + lay.players_h + lay.eval_gap + lay.eval_h
        lay = dataclasses.replace(lay, top_margin=(ch - stack) // 2, panel_h=ch)
        if st.panel_side == "left":
            return Geometry((cw, ch), lay, (0, 0), (pw, 0, cw - pw, ch))
        return Geometry((cw, ch), lay, (cw - pw, 0), (0, 0, cw - pw, ch))
    bottom = st.ui_position == "bottom"
    top = L.top_margin if fmt == "tiktok" and not bottom else 0  # room for TikTok's header only on TikTok
    lay = dataclasses.replace(base, width=config.WIDTH, top_margin=top)
    lay = dataclasses.replace(lay, card_h=fit_card_height(specs, lay, st))
    if fmt == "original":
        vh = _even(config.WIDTH * src_h / src_w)
        h = lay.ui_h + vh
        if bottom:
            return Geometry((config.WIDTH, h + h % 2), lay, (0, vh), (0, 0, config.WIDTH, vh))
        vy = lay.video_y
        return Geometry((config.WIDTH, vy + vh + (vy + vh) % 2), lay, (0, 0), (0, vy, config.WIDTH, vh))
    if bottom:
        uy = config.HEIGHT - BOTTOM_SAFE - lay.ui_h
        return Geometry((config.WIDTH, config.HEIGHT), lay, (0, uy), (0, 0, config.WIDTH, uy // 2 * 2))
    vy = lay.video_y
    avail = (config.HEIGHT - vy) // 2 * 2
    vh = min(avail, _even(config.WIDTH / config.VIDEO_CROP_ASPECT)) if config.VIDEO_MODE == "crop" else avail
    return Geometry((config.WIDTH, config.HEIGHT), lay, (0, 0), (0, vy, config.WIDTH, vh))


# ---------------------------------------------------------------- UI frames
@dataclass
class FrameState:
    idx: int             # move index; -1 = intro card (before the first pause)
    eval_score: float
    icon_scale: float
    card_mix: float      # 0 = previous card, 1 = current card
    reveal: int | None = None  # typed characters of the explanation (None = all)

    def key(self):
        return (self.idx, round(self.eval_score, 3), round(self.icon_scale, 3), round(self.card_mix, 2), self.reveal)


def transition_kind(moves: list[Move], i: int, st: vstyle.VideoStyle) -> str:
    """How card `i` replaces the previous one. "auto" reads the move: a brilliant move or a big
    swing flashes, a blunder shakes, a change of speaker slides in from that player's side."""
    if st.transition != "auto":
        return st.transition
    m, prev = moves[i], moves[i - 1] if i > 0 else None
    if m.move_type == "Brilliant" or (prev is not None and abs(m.eval_score - prev.eval_score) >= 3):
        return "pop"
    if m.move_type in ("Blunder", "Mistake"):
        return "shake"
    if prev is not None and prev.speaker != m.speaker:
        return "slide"
    return "fade"


class UIRenderer:
    def __init__(self, moves: list[Move], names: dict[str, str], avatars: dict[str, Path], geo: config.Layout,
                 st: vstyle.VideoStyle = DEFAULT_STYLE):
        self.moves = moves
        self.geo = geo
        self.st = st
        self.specs = [card_spec(m, st) for m in moves]
        self.intro = intro_spec(names, st)
        self.full = [render_card(s, geo, None, st) for s in self.specs]
        self.intro_img = render_card(self.intro, geo, None, st)
        self.chars = [layout_card(s, geo, None, st).body_chars for s in self.specs]
        self.kinds = [transition_kind(moves, i, st) for i in range(len(moves))]
        self.base = Image.new("RGB", (geo.width, geo.ui_h), st.page_bg)
        if geo.players_h:
            self.base.paste(render_players(names, avatars, geo, st), (0, geo.players_y))
        self._typed: dict[tuple[int, int], Image.Image] = {}

    def card(self, idx: int, reveal: int | None) -> Image.Image:
        if idx < 0:
            return self.intro_img
        if reveal is None or reveal >= self.chars[idx]:
            return self.full[idx]
        key = (idx, reveal)
        if key not in self._typed:
            if len(self._typed) > 64:
                self._typed.clear()
            self._typed[key] = render_card(self.specs[idx], self.geo, reveal, self.st)
        return self._typed[key]

    def _transition(self, prev: Image.Image, card: Image.Image, idx: int, mix: float) -> Image.Image:
        kind = self.kinds[idx] if idx >= 0 else "fade"
        e = ease_in_out(mix)
        if kind == "none":
            return card
        if kind == "slide":
            out = Image.new("RGB", card.size, self.st.page_bg)
            direction = -1 if self.moves[idx].speaker == "white" else 1  # white comes in from the left
            w = card.width
            out.paste(prev, (round(-direction * e * w), 0))
            out.paste(card, (round(direction * (1 - e) * w), 0))
            return out
        out = Image.blend(prev, card, mix)
        if kind == "pop":  # a flash of the move colour that fades out
            flash = Image.new("RGB", card.size, self.specs[idx].color)
            out = Image.blend(out, flash, 0.32 * (1 - e))
        elif kind == "shake":  # a short decaying shake
            dx = round(math.sin(mix * math.pi * 5) * 16 * (1 - mix))
            moved = Image.new("RGB", card.size, self.st.page_bg)
            moved.paste(out, (dx, 0))
            out = moved
        return out

    def frame(self, st: FrameState, intro_first: bool = False) -> Image.Image:
        geo = self.geo
        img = self.base.copy()
        card = self.card(st.idx, st.reveal)
        if st.card_mix < 1.0 and (st.idx > 0 or (st.idx == 0 and intro_first)):
            prev = self.full[st.idx - 1] if st.idx > 0 else self.intro_img
            card = self._transition(prev, card, st.idx, st.card_mix)
        img.paste(card, (0, geo.card_y))
        if geo.eval_h:
            img.paste(render_eval(st.eval_score, geo, self.st), (0, geo.eval_y))
        if st.idx >= 0 and geo.icon_size and st.icon_scale > 0.01:
            ic = icon_image(self.moves[st.idx].move_type, geo.icon_size)
            size = max(2, round(geo.icon_size * st.icon_scale))
            ic = ic.resize((size, size), Image.BILINEAR) if size != geo.icon_size else ic
            if st.icon_scale < 1.0:  # fade in while popping
                alpha = ic.getchannel("A").point(lambda v: int(v * min(1.0, st.icon_scale * 1.4)))
                ic.putalpha(alpha)
            r = geo.icon_size / 2
            cx, cy = geo.width - geo.card_margin - r - 12, geo.card_y + geo.card_margin // 2 + r * 0.7
            img.paste(ic, (round(cx - size / 2), round(cy - size / 2)), ic)
        return img


# ---------------------------------------------------------------- overlays on the video: subtitles, sound tags
@dataclass
class SubWord:
    start: float
    end: float
    text: str


@dataclass
class SubChunk:
    start: float
    end: float
    words: list[SubWord]
    until: float = 0.0   # shown until (next chunk's start or a short hold)


@dataclass
class SoundTag:
    start: float
    end: float
    text: str


def subtitle_chunks(segments, max_words: int) -> list[SubChunk]:
    """Word timestamps (analyzer.Segment.words) grouped into short subtitle chunks: a chunk ends
    at a segment end, a sentence end, a pause over 0.6 s or after `max_words` words."""
    chunks: list[SubChunk] = []
    for seg in segments:
        words = [SubWord(w.start, w.end, w.word.strip()) for w in (getattr(seg, "words", None) or []) if w.word.strip()]
        if not words and seg.text.strip():  # no word timestamps: one chunk per segment
            words = [SubWord(seg.start, seg.end, seg.text.strip())]
        cur: list[SubWord] = []
        for w in words:
            if cur and (len(cur) >= max_words or w.start - cur[-1].end > 0.6 or cur[-1].text[-1:] in ".!?…"):
                chunks.append(SubChunk(cur[0].start, cur[-1].end, cur))
                cur = []
            cur.append(w)
        if cur:
            chunks.append(SubChunk(cur[0].start, cur[-1].end, cur))
    chunks.sort(key=lambda c: c.start)
    for a, b in zip(chunks, chunks[1:] + [None]):
        a.until = min(b.start, a.end + 0.7) if b is not None else a.end + 0.7
    return chunks


TAG_FADE = 0.2


class VideoOverlay:
    """Subtitles and sound tags drawn over the video box. `key(t)` changes only when what is
    visible changes, so the render loop re-composites the box only then."""

    def __init__(self, box_w: int, box_h: int, st: vstyle.VideoStyle, segments=None, tags: list[SoundTag] | None = None,
                 safe: tuple[int, int] | None = None):
        """`safe` = (right, bottom) of the free area in box pixels: subtitles and tags stay left of
        `right` and above `bottom` (TikTok's own buttons and caption cover the rest)."""
        self.bw, self.bh, self.st = box_w, box_h, st
        self.right = self.bw if safe is None else max(1, min(self.bw, safe[0]))
        self.bottom = self.bh if safe is None else max(1, min(self.bh, safe[1]))
        self.safe = safe
        self.chunks = subtitle_chunks(segments or [], st.sub_words) if st.subtitles else []
        self.starts = [c.start for c in self.chunks]
        self.tags = sorted(tags or [], key=lambda g: g.start) if st.sound_tags else []
        self._cache: dict = {}

    def key(self, t: float):
        sub = None
        if self.chunks:
            import bisect

            k = bisect.bisect_right(self.starts, t + 0.05) - 1
            if k >= 0 and t < self.chunks[k].until:
                c = self.chunks[k]
                wi = max((i for i, w in enumerate(c.words) if w.start <= t + 0.02), default=-1)
                sub = (k, wi if self.st.sub_highlight else -1)
        tags = []
        for i, g in enumerate(self.tags):
            if g.start - 0.01 <= t < g.end + 0.4:
                a = min(1.0, (t - g.start) / TAG_FADE + 0.2, (g.end + 0.4 - t) / TAG_FADE)
                tags.append((i, round(max(0.0, a) * 8) / 8))
        if sub is None and not tags:
            return None
        return sub, tuple(tags)

    # ------------------------------------------------------------ drawing
    def _subtitle(self, k: int, wi: int) -> tuple[Image.Image, int, int]:
        st = self.st
        words = [w.text.upper() if st.sub_upper else w.text for w in self.chunks[k].words]
        cx = self.bw / 2
        size = st.sub_size
        while True:  # at most two lines: shrink the font if needed
            f = st.font("bold", size)
            max_w = int(self.bw * 0.88)
            if self.safe is not None:  # centred, and the whole block left of the right-hand buttons
                max_w = max(80, min(max_w, int(2 * (self.right - SAFE_PAD - cx)) - 2 * int(size * 0.45) - 8))
            lines: list[list[int]] = [[]]
            for i, w in enumerate(words):
                cand = " ".join(words[j] for j in lines[-1] + [i])
                if lines[-1] and text_w(f, cand) > max_w:
                    lines.append([])
                lines[-1].append(i)
            if len(lines) <= 2 or size <= 24:
                break
            size -= 4
        lh = line_h(f, 1.18)
        space = text_w(f, " ")
        widths = [text_w(f, " ".join(words[j] for j in ln)) for ln in lines]
        pad_x, pad_y = int(size * 0.45), int(size * 0.22)
        w_img = int(max(widths)) + 2 * pad_x + 8
        h_img = lh * len(lines) + 2 * pad_y
        img = Image.new("RGBA", (w_img, h_img), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        if st.sub_box:
            d.rounded_rectangle((0, 0, w_img - 1, h_img - 1), radius=int(size * 0.35), fill=vstyle.color_rgba(st.sub_bg, 165))
        stroke = 0 if st.sub_box else max(2, size // 13)
        y = pad_y
        for ln, lw in zip(lines, widths):
            x = (w_img - lw) / 2
            for j in ln:
                fill = st.sub_highlight if (st.sub_highlight and j == wi) else st.sub_color
                d.text((x, y), words[j], font=f, fill=fill, stroke_width=stroke, stroke_fill=(0, 0, 0, 230))
                x += text_w(f, words[j]) + space
            y += lh
        cy = self.bh * st.sub_position
        return img, round(cx - w_img / 2), round(min(self.bottom - h_img - SAFE_PAD, max(8, cy - h_img / 2)))

    def _tag(self, i: int) -> Image.Image:
        st = self.st
        f = st.font("semibold", 30)
        text = self.tags[i].text
        pad_x, pad_y = 20, 10
        w, h = int(text_w(f, text)) + 2 * pad_x + 22, line_h(f, 1.15) + 2 * pad_y
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle((0, 0, w - 1, h - 1), radius=h // 2, fill=vstyle.color_rgba(st.tag_bg, 190))
        d.ellipse((pad_x - 4, h / 2 - 6, pad_x + 8, h / 2 + 6), fill=st.sub_highlight or st.tag_text)
        d.text((pad_x + 22, h / 2), text, font=f, fill=st.tag_text, anchor="lm")
        return img

    def pieces(self, key) -> list[tuple[Image.Image, int, int]]:
        if key in self._cache:
            return self._cache[key]
        if len(self._cache) > 256:
            self._cache.clear()
        out = []
        sub, tags = key
        sub_top = self.bottom
        if sub is not None:
            img, x, y = self._subtitle(*sub)
            out.append((img, x, y))
            sub_top = y
        y = 28
        for n, (i, alpha) in enumerate(tags):
            img = self._tag(i)
            if alpha < 1.0:
                img.putalpha(img.getchannel("A").point(lambda v, a=alpha: int(v * a)))
            pos = self.st.tag_position
            if pos == "top-right":
                x = self.right - img.width - 28
            elif pos == "above-subs":
                x = (self.bw - img.width) // 2
            else:
                x = 28
            yy = (sub_top - 16 - (n + 1) * (img.height + 10)) if pos == "above-subs" else y
            out.append((img, x, max(8, yy)))
            y += img.height + 10
        self._cache[key] = out
        return out

    def apply(self, tile: np.ndarray, key) -> np.ndarray:
        if key is None:
            return tile
        tile = tile.copy()
        for img, x, y in self.pieces(key):
            a = np.asarray(img)
            x0, y0 = max(0, x), max(0, y)
            x1, y1 = min(self.bw, x + a.shape[1]), min(self.bh, y + a.shape[0])
            if x1 <= x0 or y1 <= y0:
                continue
            part = a[y0 - y : y1 - y, x0 - x : x1 - x]
            alpha = part[..., 3:4].astype(np.float32) / 255
            region = tile[y0:y1, x0:x1].astype(np.float32)
            tile[y0:y1, x0:x1] = (region * (1 - alpha) + part[..., :3] * alpha).astype(np.uint8)
        return tile


# ---------------------------------------------------------------- timeline
class Timeline:
    """Continuous mode: cards change over the running video at each move's start."""

    def __init__(self, moves: list[Move], st: vstyle.VideoStyle = DEFAULT_STYLE):
        if not moves:
            raise RenderError("No moves to render")
        self.moves = moves
        self.st = st
        # first card is visible from t=0 so the screen is never empty
        self.starts = [0.0] + [m.start for m in moves[1:]]

    def state(self, t: float) -> FrameState:
        idx = 0
        for i, s in enumerate(self.starts):
            if t >= s:
                idx = i
        dt = t - self.starts[idx]
        prev_eval = self.moves[idx - 1].eval_score if idx > 0 else 0.0
        cur_eval = self.moves[idx].eval_score
        ev = prev_eval + (cur_eval - prev_eval) * ease_in_out(dt / max(1e-3, self.st.eval_anim))
        icon = ease_out_back(dt / self.st.icon_anim) if dt < self.st.icon_anim else 1.0
        mix = min(1.0, dt / self.st.card_fade) if idx > 0 and self.st.card_fade > 0 else 1.0
        return FrameState(idx, ev, icon, mix)


MIN_PAUSE = 0.8  # s, even an empty explanation gets a moment for the card to pop in


def typing_time(chars: int, cps: float) -> float:
    return chars / max(1.0, cps)


def pause_duration(chars: int, opts: "RenderOptions", st: vstyle.VideoStyle = DEFAULT_STYLE) -> float:
    """Card pop-in, then typing at `typing_cps`, then `read_hold` seconds to read."""
    return max(MIN_PAUSE, st.card_fade + typing_time(chars, opts.typing_cps) + max(0.0, opts.read_hold))


@dataclass
class Schedule:
    src: np.ndarray                                  # (n_out,) source frame per output frame
    states: list[FrameState]
    runs: list[tuple[int, int, int]]                 # (out frame, src frame, length) of normal playback
    sfx: list[tuple[float, str]] = field(default_factory=list)  # (output second, sound)
    intro_first: bool = False                        # card 0 crossfades from the intro card

    @property
    def n_out(self) -> int:
        return len(self.src)


def build_schedule(moves: list[Move], n_src: int, fps: int, opts: RenderOptions, chars: list[int],
                   st: vstyle.VideoStyle = DEFAULT_STYLE) -> Schedule:
    if not moves:
        raise RenderError("No moves to render")
    fade = max(1e-3, st.card_fade)
    if not opts.pause_on_move:
        tl = Timeline(moves, st)
        sfx = [(tl.starts[i], "move") for i in range(len(moves))]
        return Schedule(np.arange(n_src), [tl.state(k / fps) for k in range(n_src)], [(0, 0, n_src)], sfx)

    duration = n_src / fps
    src: list[int] = []
    states: list[FrameState] = []
    runs: list[tuple[int, int, int]] = []
    sfx: list[tuple[float, str]] = []
    rng = random.Random(7)
    cur_src, held = 0, FrameState(-1, 0.0, 0.0, 1.0)
    for i, m in enumerate(moves):
        nxt = moves[i + 1].start if i + 1 < len(moves) else duration
        freeze_t = m.end + min(PAUSE_TAIL, max(0.0, (nxt - m.end) / 2))
        stop = min(n_src, max(cur_src, 1, math.ceil(freeze_t * fps - 1e-6)))
        if stop > cur_src:  # play up to the end of the move's line
            runs.append((len(src), cur_src, stop - cur_src))
            src += range(cur_src, stop)
            states += [held] * (stop - cur_src)
        cur_src = stop
        # freeze on the last shown frame while the move is explained
        n = max(1, round(pause_duration(chars[i], opts, st) * fps))
        out0 = len(src)
        prev_eval = moves[i - 1].eval_score if i else 0.0
        t_type0 = st.card_fade
        t_type1 = t_type0 + max(1 / fps, typing_time(chars[i], opts.typing_cps))
        sfx.append((out0 / fps, "pause"))
        typed, last_tick = 0, -1.0
        body = card_body_text(moves, i, chars)
        for q in range(n):
            tau = q / fps
            frac = min(1.0, max(0.0, (tau - t_type0) / (t_type1 - t_type0)))
            reveal = round(chars[i] * frac)
            if reveal > typed and any(not c.isspace() for c in body[typed:reveal]) and tau - last_tick >= TICK_GAP:
                sfx.append(((out0 + q) / fps, f"tick{rng.randint(1, 4)}"))
                last_tick = tau
            typed = reveal
            states.append(FrameState(
                i,
                prev_eval + (m.eval_score - prev_eval) * ease_in_out(tau / max(1e-3, st.eval_anim)),
                ease_out_back(tau / st.icon_anim) if tau < st.icon_anim else 1.0,
                min(1.0, tau / fade),
                None if reveal >= chars[i] else reveal,
            ))
        src += [max(0, cur_src - 1)] * n
        sfx.append((max(out0 / fps, (out0 + n) / fps - 0.08), "unpause"))
        held = FrameState(i, m.eval_score, 1.0, 1.0)
    if cur_src < n_src:
        runs.append((len(src), cur_src, n_src - cur_src))
        src += range(cur_src, n_src)
        states += [held] * (n_src - cur_src)
    return Schedule(np.array(src), states, runs, sfx, intro_first=True)


def card_body_text(moves: list[Move], i: int, chars: list[int]) -> str:
    """The body as the typewriter sees it (wrapped lines joined by single spaces)."""
    return " ".join(moves[i].explanation.split())[: chars[i]].ljust(chars[i])


# ---------------------------------------------------------------- audio
def _read_wav(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as w:
        x = np.frombuffer(w.readframes(w.getnframes()), "<i2").astype(np.float32) / 32768
        ch, sr = w.getnchannels(), w.getframerate()
    x = x.reshape(-1, ch).mean(axis=1)
    if sr != AUDIO_SR:
        x = np.interp(np.arange(int(len(x) * AUDIO_SR / sr)) * sr / AUDIO_SR, np.arange(len(x)), x)
    return x


_SFX_CACHE: dict[tuple, dict[str, np.ndarray]] = {}


def load_sfx(typing: str = config.SFX_TYPING_SET, pause: str = config.SFX_PAUSE_SET) -> dict[str, np.ndarray]:
    """The chosen sets: pause, unpause, move (= pause) and tick1..4 (a set with fewer ticks repeats them)."""
    files = sfx_files(typing, pause)
    key = (typing, pause, *(f.stat().st_mtime for f in files.values()))
    if key not in _SFX_CACHE:
        out = {"pause": _read_wav(files["pause"]), "unpause": _read_wav(files["unpause"])}
        out["move"] = out["pause"]
        ticks = [_read_wav(f) for k, f in files.items() if k.startswith("type_tick_")]
        for i in range(1, 5):
            out[f"tick{i}"] = ticks[(i - 1) % len(ticks)]
        _SFX_CACHE.clear()
        _SFX_CACHE[key] = out
    return _SFX_CACHE[key]


def decode_audio(clip: Path) -> np.ndarray:
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-vn", "-ac", "2", "-ar", str(AUDIO_SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0:
        return np.zeros((0, 2), np.float32)
    return np.frombuffer(proc.stdout, np.float32).reshape(-1, 2)


def soft_limit(x: np.ndarray, t: float = 0.89) -> np.ndarray:
    a = np.abs(x)
    over = a > t
    x[over] = np.sign(x[over]) * (t + (1 - t) * np.tanh((a[over] - t) / (1 - t)))
    return x


def build_audio(clip: Path, sched: Schedule, fps: int, opts: RenderOptions) -> np.ndarray:
    """Original sound laid out along the schedule (silence while frozen) + SFX."""
    return lay_audio(decode_audio(clip), sched, fps, opts)


def lay_audio(src: np.ndarray, sched: Schedule, fps: int, opts: RenderOptions) -> np.ndarray:
    """`src` (decoded stereo at AUDIO_SR) laid out along the schedule + the SFX, soft-limited."""
    sidx = lambda k: int(round(k * AUDIO_SR / fps))  # noqa: E731
    out = np.zeros((sidx(sched.n_out), 2), np.float32)
    fade = int(0.015 * AUDIO_SR)
    for out0, src0, n in sched.runs:
        a0, a1 = sidx(out0), sidx(out0 + n)
        seg = src[sidx(src0) : sidx(src0) + (a1 - a0)].copy()
        if not len(seg):
            continue
        if len(sched.runs) > 1:  # declick the cuts around pauses
            k = min(fade, len(seg) // 2)
            if out0 > 0:
                seg[:k] *= np.linspace(0, 1, k)[:, None]
            if out0 + n < sched.n_out:
                seg[-k:] *= np.linspace(1, 0, k)[:, None]
        out[a0 : a0 + len(seg)] = seg
    events = [(t, name) for t, name in sched.sfx if opts.sound_on(name)]
    if events:
        sounds = load_sfx(opts.sfx_typing_set, opts.sfx_pause_set)
        for t, name in events:
            snd = sounds[name] * SFX_GAIN["tick" if name.startswith("tick") else name] * opts.sfx_volume
            p = int(t * AUDIO_SR)
            n = min(len(snd), len(out) - p)
            if n > 0:
                out[p : p + n] += snd[:n, None]
    return soft_limit(out)


PREVIEW_TYPING = 1.5   # s of typing in the sound preview
PREVIEW_AFTER = 1.5    # s of the clip after the pause
_PREVIEW_TEXT = "Сильный ход: соперник теряет опору и отступает, а ответа у него нет. "


def decode_audio_range(clip: Path, start: float, seconds: float) -> np.ndarray:
    """`seconds` of the clip's sound from `start` (stereo, AUDIO_SR), zero-padded at the end."""
    n = int(round(seconds * AUDIO_SR))
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-ss", f"{max(0.0, start):.4f}", "-t", f"{seconds:.4f}",
         "-vn", "-ac", "2", "-ar", str(AUDIO_SR), "-f", "f32le", "-"],
        capture_output=True,
    )
    x = np.frombuffer(proc.stdout, np.float32) if proc.returncode == 0 else np.zeros(0, np.float32)
    x = x[: len(x) // 2 * 2].reshape(-1, 2)[:n]
    return np.vstack([x, np.zeros((n - len(x), 2), np.float32)]) if len(x) < n else x.copy()


def sound_preview(clip: Path | None, opts: RenderOptions, out: Path, at: float = 0.0, seconds: float = 2.5,
                  st: vstyle.VideoStyle = DEFAULT_STYLE) -> Path:
    """A short WAV that sounds exactly like the render around one move: `seconds` of the clip
    from `at`, then the pause with its sounds (pop-in, typing for PREVIEW_TYPING s at
    `opts.typing_cps`, the reading time, the resume), then PREVIEW_AFTER s more of the clip.
    Without pause-on-move: the clip plays on and the move sound comes at `seconds`. Built with
    the render's own schedule and mixer (gains, volume, switches, declick, limiter)."""
    fps = config.FPS
    seconds = max(0.2, float(seconds))
    n_src = int(round((seconds + PREVIEW_AFTER) * fps))
    chars = max(1, round(PREVIEW_TYPING * max(1.0, opts.typing_cps)))
    text = (_PREVIEW_TEXT * (chars // len(_PREVIEW_TEXT) + 1))[:chars].strip() or "Ход"
    move = Move(start=0.0, end=max(0.0, seconds - PAUSE_TAIL), move_number="1", speaker="white", move_type="Best",
                title="Ход", explanation=text, eval_score=0.0)
    if opts.pause_on_move:
        sched = build_schedule([move], n_src, fps, opts, [chars], st)
    else:
        sched = Schedule(np.arange(n_src), [], [(0, 0, n_src)], [(seconds, "move")])
    total = sched.n_out / fps
    src = decode_audio_range(clip, at, n_src / fps) if clip is not None and Path(clip).exists() else \
        np.zeros((int(round(n_src / fps * AUDIO_SR)), 2), np.float32)
    out.parent.mkdir(parents=True, exist_ok=True)
    write_wav(out, lay_audio(src, sched, fps, opts), AUDIO_SR)
    log.debug("sound preview: %.2f s (clip from %.2f s, pause %.2f s) -> %s", total, at, total - n_src / fps, out)
    return out


# ---------------------------------------------------------------- composition (render loop and preview share it)
class Composer:
    """Geometry, UI, schedule, reframing plan and overlays of one render. `frame(k, src)`
    returns output frame `k` as the render writes it; the style editor's preview calls the
    same method with a single decoded source frame."""

    def __init__(self, src_w: int, src_h: int, duration: float, moves: list[Move], names: dict[str, str],
                 avatars: dict[str, Path], opts: RenderOptions, st: vstyle.VideoStyle = DEFAULT_STYLE,
                 vision=None, speaker_at=None, segments=None, tags: list[SoundTag] | None = None,
                 fps: int = config.FPS, plan=None):
        if not moves:
            raise RenderError("No moves to render")
        self.fps, self.st, self.opts = fps, st, opts
        self.n_src = max(1, int(duration * fps))
        s = min(1.0, DECODE_MAX / max(src_w, src_h))
        self.dw, self.dh = _even(src_w * s), _even(src_h * s)
        self.geo = geometry(opts.output_format, src_w, src_h, [card_spec(m, st) for m in moves] + [intro_spec(names, st)], st)
        self.ui = UIRenderer(moves, names, avatars, self.geo.layout, st)
        self.sched = build_schedule(moves, self.n_src, fps, opts, self.ui.chars, st)
        bx, by, bw, bh = self.geo.box
        self.plan = plan if plan is not None else reframe.plan(vision, src_w, src_h, bw, bh, self.n_src, fps, speaker_at)
        self.framer = reframe.Framer(bw, bh)
        cw, ch = self.geo.canvas
        self.overlay = VideoOverlay(bw, bh, st, segments, tags, safe_area(opts.output_format, self.geo))
        self.canvas = np.zeros((ch, cw, 3), np.uint8)
        self.canvas[:] = np.array(Image.new("RGB", (1, 1), st.page_bg))[0, 0]
        self._tile_key = self._ui_key = None

    @property
    def n_out(self) -> int:
        return self.sched.n_out

    def frame(self, k: int, src_frame: np.ndarray, src_idx: int | None = None) -> np.ndarray:
        want = int(self.sched.src[k])
        src_idx = want if src_idx is None else src_idx
        bx, by, bw, bh = self.geo.box
        row = self.plan.at(want)
        ov = self.overlay.key(want / self.fps)
        tile_key = (src_idx, row, ov)
        if tile_key != self._tile_key:
            self.canvas[by : by + bh, bx : bx + bw] = self.overlay.apply(self.framer.render(src_frame, row), ov)
            self._tile_key = tile_key
        state = self.sched.states[k]
        if state.key() != self._ui_key:
            ux, uy = self.geo.ui_pos
            uw, uh = self.geo.layout.width, self.geo.layout.ui_h
            self.canvas[uy : uy + uh, ux : ux + uw] = np.asarray(self.ui.frame(state, self.sched.intro_first))
            self._ui_key = state.key()
        return self.canvas

    def out_frame(self, seconds: float) -> int:
        return max(0, min(self.n_out - 1, int(round(seconds * self.fps))))


def safe_area(fmt: str, geo: Geometry) -> tuple[int, int] | None:
    """(right, bottom) of the part of the video box that TikTok's own interface leaves free,
    in box pixels; None for the other formats (nothing covers them)."""
    if fmt != "tiktok":
        return None
    bx, by, bw, bh = geo.box
    cw, ch = geo.canvas
    return min(bw, cw - TIKTOK_SAFE_RIGHT - bx), min(bh, ch - TIKTOK_SAFE_BOTTOM - by)


def decode_frame(clip: Path, src_idx: int, size: tuple[int, int], fps: int = config.FPS) -> np.ndarray:
    """One source frame, decoded and scaled exactly like the render loop does it."""
    w, h = size
    proc = subprocess.run(
        # seek half a frame early: a time rounded up past the frame's own timestamp would give the next frame
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, (src_idx - 0.5) / fps):.4f}", "-i", str(clip), "-an", "-frames:v", "1",
         "-vf", f"scale={w}:{h}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or len(proc.stdout) < w * h * 3:
        return np.zeros((h, w, 3), np.uint8)
    return np.frombuffer(proc.stdout[: w * h * 3], np.uint8).reshape(h, w, 3)


def placeholder_frame(size: tuple[int, int]) -> np.ndarray:
    """A neutral picture for the style preview when there is no clip yet."""
    w, h = size
    y = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    x = np.linspace(0, 1, w, dtype=np.float32)[None, :, None]
    img = (np.array([38, 46, 64], np.float32) * (1 - y) + np.array([92, 70, 58], np.float32) * y) * (0.8 + 0.2 * x)
    return np.clip(img, 0, 255).astype(np.uint8)


# ---------------------------------------------------------------- main entry
def render_video(
    clip: Path,
    moves: list[Move],
    names: dict[str, str],
    avatars: dict[str, Path],
    out: Path,
    progress: Callable[[float], None] | None = None,
    options: RenderOptions | None = None,
    vision=None,
    speaker_at: Callable[[float], int | None] | None = None,
    style: vstyle.VideoStyle | None = None,
    segments=None,
    tags: list[SoundTag] | None = None,
) -> Path:
    """`vision` (vision.VideoAnalysis) and `speaker_at` (time -> person) drive the
    smart reframing; without them the video is centre-cropped. `segments` (transcript)
    give the subtitles, `tags` the sound events shown over the video."""
    opts = options or RenderOptions()
    st = style or DEFAULT_STYLE
    if not moves:
        raise RenderError("No moves to render")
    info = probe(clip)
    fps = config.FPS
    comp = Composer(info.width, info.height, info.duration, moves, names, avatars, opts, st, vision, speaker_at,
                    segments, tags, fps)
    sched, n_src, dw, dh = comp.sched, comp.n_src, comp.dw, comp.dh
    cw, ch = comp.geo.canvas

    wav = out.with_suffix(".audio.wav")
    write_wav(wav, build_audio(clip, sched, fps, opts), AUDIO_SR)
    pauses = (sched.n_out - n_src) / fps
    log.info("Рендер %s %d×%d, стиль «%s»: %s видео%s", opts.output_format, cw, ch, st.name, fmt_duration(n_src / fps),
             f" + {pauses:.0f} с пауз" if pauses > 0.5 else "")
    log.debug("rendering %d frames @ %dfps -> %s", sched.n_out, fps, out)
    dec = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", str(clip), "-an", "-vf", f"fps={fps},scale={dw}:{dh}",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    enc = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{cw}x{ch}", "-r", str(fps), "-i", "-",
         "-i", str(wav),
         "-map", "0:v", "-map", "1:a",
         "-vf", "setsar=1", "-aspect", f"{cw}:{ch}",   # square pixels: no player may stretch the picture
         "-c:v", "libx264", "-preset", config.X264_PRESET, "-crf", str(config.X264_CRF),
         "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", str(fps), "-fps_mode", "cfr",
         "-c:a", "aac", "-b:a", "192k", "-ar", str(AUDIO_SR), "-ac", "2",
         "-movflags", "+faststart", "-shortest",
         str(out)],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    frame_bytes = dw * dh * 3
    cur_idx, cur_frame = -1, np.zeros((dh, dw, 3), np.uint8)
    try:
        for k in range(sched.n_out):
            want = int(sched.src[k])
            while cur_idx < want:  # sequential decode; frozen frames just reuse the last one
                buf = dec.stdout.read(frame_bytes)
                if len(buf) < frame_bytes:
                    cur_idx = want  # decoder ended early: hold the last frame
                    break
                cur_frame = np.frombuffer(buf, np.uint8).reshape(dh, dw, 3)
                cur_idx += 1
            enc.stdin.write(comp.frame(k, cur_frame, cur_idx).data)
            if progress and k % fps == 0:
                progress(k / sched.n_out)
        enc.stdin.close()
    except BrokenPipeError:
        pass
    finally:
        dec.stdout.close()
        dec.kill()
        dec.wait()
    err = enc.stderr.read().decode(errors="replace")
    code = enc.wait()
    wav.unlink(missing_ok=True)
    if code != 0:
        raise RenderError(f"ffmpeg failed:\n{err[-2000:]}")
    if progress:
        progress(1.0)
    return out


def preview_frame(
    moves: list[Move], names: dict[str, str], avatars: dict[str, Path], t: float,
    output_format: str = "tiktok", src_size: tuple[int, int] = (1920, 1080), style: vstyle.VideoStyle | None = None,
) -> Image.Image:
    """Single UI frame on the full canvas (video area left empty) — for quick layout checks."""
    st = style or DEFAULT_STYLE
    geo = geometry(output_format, *src_size, [card_spec(m, st) for m in moves] + [intro_spec(names, st)], st)
    canvas = Image.new("RGB", geo.canvas, st.page_bg)
    ui = UIRenderer(moves, names, avatars, geo.layout, st)
    canvas.paste(ui.frame(Timeline(moves, st).state(t)), geo.ui_pos)
    return canvas


def preview_card(
    moves: list[Move], idx: int, names: dict[str, str], avatars: dict[str, Path],
    output_format: str = "tiktok", src_size: tuple[int, int] = (1080, 1920), style: vstyle.VideoStyle | None = None,
) -> Image.Image:
    """The UI strip exactly as it looks once move `idx` is fully shown (card, players,
    eval bar) — the same code path as the render, for the editors' previews."""
    if not moves:
        raise RenderError("No moves to render")
    st = style or DEFAULT_STYLE
    idx = max(0, min(idx, len(moves) - 1))
    geo = geometry(output_format, *src_size, [card_spec(m, st) for m in moves] + [intro_spec(names, st)], st)
    ui = UIRenderer(moves, names, avatars, geo.layout, st)
    img = ui.frame(FrameState(idx, moves[idx].eval_score, 1.0, 1.0))
    top = geo.layout.top_margin if output_format != "landscape" else 0  # the empty room for TikTok's header
    return img.crop((0, top, img.width, img.height))
