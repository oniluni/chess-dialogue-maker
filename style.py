"""Visual style of the generated video: colours, typography, layout, animation timing,
transitions, subtitles and sound tags.

`renderer.py` draws everything from a `VideoStyle`, and the style editor's preview goes
through the same renderer code (`renderer.Composer`), so what the editor shows is what
the render produces. Built-in presets live in `PRESETS`; presets saved by the person go
to `config.PRESETS_FILE`; a job's style is `jobs/<id>/style.json`.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import re
import urllib.request
from dataclasses import asdict, dataclass, field
from functools import lru_cache
from pathlib import Path

import config

log = logging.getLogger(__name__)
FILE = "style.json"

# ---------------------------------------------------------------- fonts (all with Cyrillic)
_GF = "https://github.com/google/fonts/raw/main/ofl"
# family -> weight -> (file in assets/fonts, variable-font weight or None)
FONTS: dict[str, dict[str, tuple[str, int | None]]] = {
    "Inter": {"regular": ("Inter-Regular.ttf", None), "semibold": ("Inter-SemiBold.ttf", None), "bold": ("Inter-Bold.ttf", None)},
    "Montserrat": {w: ("Montserrat[wght].ttf", v) for w, v in (("regular", 450), ("semibold", 600), ("bold", 800))},
    "Rubik": {w: ("Rubik[wght].ttf", v) for w, v in (("regular", 400), ("semibold", 600), ("bold", 750))},
    "Oswald": {w: ("Oswald[wght].ttf", v) for w, v in (("regular", 400), ("semibold", 550), ("bold", 680))},
    "PT Sans": {"regular": ("PT_Sans-Web-Regular.ttf", None), "semibold": ("PT_Sans-Web-Bold.ttf", None),
                "bold": ("PT_Sans-Web-Bold.ttf", None)},
    "PT Serif": {"regular": ("PT_Serif-Web-Regular.ttf", None), "semibold": ("PT_Serif-Web-Bold.ttf", None),
                 "bold": ("PT_Serif-Web-Bold.ttf", None)},
}
FONT_URLS = {
    "Montserrat[wght].ttf": f"{_GF}/montserrat/Montserrat%5Bwght%5D.ttf",
    "Rubik[wght].ttf": f"{_GF}/rubik/Rubik%5Bwght%5D.ttf",
    "Oswald[wght].ttf": f"{_GF}/oswald/Oswald%5Bwght%5D.ttf",
    "PT_Sans-Web-Regular.ttf": f"{_GF}/ptsans/PT_Sans-Web-Regular.ttf",
    "PT_Sans-Web-Bold.ttf": f"{_GF}/ptsans/PT_Sans-Web-Bold.ttf",
    "PT_Serif-Web-Regular.ttf": f"{_GF}/ptserif/PT_Serif-Web-Regular.ttf",
    "PT_Serif-Web-Bold.ttf": f"{_GF}/ptserif/PT_Serif-Web-Bold.ttf",
}


def font_files(family: str) -> list[str]:
    return sorted({f for f, _ in FONTS.get(family, FONTS["Inter"]).values()})


def font_ready(family: str) -> bool:
    return family == "Inter" or all((config.FONTS_DIR / f).exists() for f in font_files(family))


def ensure_font(family: str) -> bool:
    """Download a family's files (SIL OFL, Google Fonts) if missing. False when offline."""
    if font_ready(family):
        return True
    config.FONTS_DIR.mkdir(parents=True, exist_ok=True)
    try:
        for name in font_files(family):
            dst = config.FONTS_DIR / name
            if not dst.exists():
                log.info("Скачиваю шрифт %s…", name)
                with urllib.request.urlopen(FONT_URLS[name], timeout=60) as r:
                    data = r.read()
                dst.with_suffix(".part").write_bytes(data)
                dst.with_suffix(".part").replace(dst)
        load_font.cache_clear()
        return True
    except Exception as e:
        log.warning("Не удалось скачать шрифт %s (%s) — будет Inter", family, e)
        return False


@lru_cache(maxsize=256)
def load_font(family: str, weight: str, size: int):
    """A Pillow font of the family; falls back to Inter (config.font_path) when the file is missing."""
    from PIL import ImageFont

    file, wght = FONTS.get(family, FONTS["Inter"]).get(weight, (None, None))
    path = config.FONTS_DIR / file if file else None
    if path is None or not path.exists():
        return ImageFont.truetype(config.font_path(weight), size)
    f = ImageFont.truetype(str(path), size)
    if wght is not None:
        try:
            f.set_variation_by_axes([wght])
        except Exception as e:  # FreeType without variation support: the default instance
            log.debug("font variation %s: %s", file, e)
    return f


# ---------------------------------------------------------------- the style
TRANSITIONS = [("Плавная смена", "fade"), ("Сдвиг", "slide"), ("Без анимации", "none"),
               ("По смыслу хода", "auto")]
ACCENTS = [("Полоса слева", "bar"), ("Рамка", "border"), ("Без акцента", "none")]
POSITIONS = [("Сверху", "top"), ("Снизу", "bottom")]
PANEL_SIDES = [("Справа", "right"), ("Слева", "left")]
TAG_POSITIONS = [("Слева сверху", "top-left"), ("Справа сверху", "top-right"), ("Над субтитрами", "above-subs")]


@dataclass
class VideoStyle:
    name: str = "Chess.com"
    # colours
    page_bg: str = "#000000"
    card_bg: str = "#262421"
    players_bg: str = "#161512"
    text_main: str = "#ffffff"
    text_muted: str = "#9e9b98"
    eval_white: str = "#ffffff"
    eval_black: str = "#403d39"
    avatar_ring: str = "#3a3835"
    move_colors: dict[str, str] = field(default_factory=dict)   # overrides of config.MOVE_TYPES colours
    accent: str = "bar"             # how the move colour shows on the card: bar | border | none
    title_accent: bool = True       # title in the move colour (else text_main)
    # typography
    font_family: str = "Inter"
    title_scale: float = 1.0
    body_scale: float = 1.0
    title_upper: bool = False
    # layout
    ui_position: str = "top"        # vertical formats: analysis strip above or below the video
    panel_side: str = "right"       # landscape: analysis panel right or left of the video
    card_margin: int = 0            # px the card is inset from the strip edges
    card_radius: int = 0
    show_players: bool = True
    show_eval: bool = True
    show_icon: bool = True
    # animation, s
    card_fade: float = 0.18
    eval_anim: float = 0.5
    icon_anim: float = 0.35
    transition: str = "fade"        # fade | slide | none | auto
    # subtitles (burnt into the video area, word timestamps from whisper)
    subtitles: bool = False
    sub_size: int = 46
    sub_color: str = "#ffffff"
    sub_highlight: str = "#ffd34d"  # the word being said; "" = no karaoke highlight
    sub_box: bool = True            # rounded box behind the text (else an outline)
    sub_bg: str = "#000000a6"       # colour of that box (#rrggbbaa)
    sub_position: float = 0.84      # centre of the text block, share of the video height
    sub_words: int = 6              # words per subtitle chunk at most
    sub_upper: bool = False
    # sound events (sounds.json, only the ones marked «в видео»)
    sound_tags: bool = True
    tag_position: str = "top-left"
    tag_bg: str = "#000000b3"
    tag_text: str = "#ffffff"

    # ------------------------------------------------------------ helpers
    def move_color(self, move_type: str) -> str:
        return self.move_colors.get(move_type) or config.MOVE_TYPES[move_type].color

    def font(self, weight: str, size: int):
        return load_font(self.font_family, weight, max(8, int(size)))

    def key(self) -> str:
        return json.dumps(asdict(self), sort_keys=True, ensure_ascii=False)

    def problems(self) -> list[str]:
        """Invalid values and hard-to-read colour pairs, as Russian messages."""
        out = validate(asdict(self))[1]
        pairs = [("текст карточки", self.text_main, self.card_bg), ("имена игроков", self.text_main, self.players_bg),
                 ("звуковые метки", self.tag_text, self.tag_bg)]
        if self.subtitles and self.sub_box:
            pairs.append(("субтитры", self.sub_color, self.sub_bg))
        for what, fg, bg in pairs:
            try:
                ratio = contrast(fg, bg)
            except ValueError:
                continue
            if ratio < 3.0:
                out.append(f"Плохо читается: {what} — контраст {ratio:.1f}:1 (нужно хотя бы 3:1) — измените цвет текста или фона.")
        return out


DEFAULT = VideoStyle()

PRESETS: dict[str, VideoStyle] = {s.name: s for s in (
    DEFAULT,
    VideoStyle(name="Неон", page_bg="#05060d", card_bg="#11142a", players_bg="#0b0d1d", text_main="#f2f4ff",
               text_muted="#8f96c8", eval_white="#e9ecff", eval_black="#262b55", avatar_ring="#3b4ad1",
               accent="border", card_margin=28, card_radius=26, font_family="Montserrat", transition="auto",
               card_fade=0.26, eval_anim=0.6, subtitles=True, sub_highlight="#6ef3ff", tag_bg="#1a1f45cc",
               move_colors={"Brilliant": "#19f2d6", "Great": "#5aa9ff", "Best": "#b6ff5c", "Inaccuracy": "#ffe45c",
                            "Mistake": "#ffa63d", "Blunder": "#ff4f6d"}),
    VideoStyle(name="Минимал", page_bg="#0e0e10", card_bg="#18181b", players_bg="#0e0e10", text_main="#fafafa",
               text_muted="#a1a1aa", eval_white="#fafafa", eval_black="#3f3f46", avatar_ring="#27272a",
               accent="none", title_accent=False, card_margin=36, card_radius=18, font_family="Rubik",
               transition="slide", show_icon=True, subtitles=True, sub_highlight="", sub_box=False),
    VideoStyle(name="Светлая", page_bg="#f4f1ea", card_bg="#ffffff", players_bg="#ebe6dc", text_main="#1f1d1a",
               text_muted="#6f6a62", eval_white="#ffffff", eval_black="#3a3733", avatar_ring="#d6d0c4",
               accent="bar", card_margin=24, card_radius=16, font_family="PT Sans", tag_bg="#ffffffe0",
               tag_text="#1f1d1a", subtitles=True, sub_color="#1f1d1a", sub_highlight="#c2410c", sub_bg="#ffffffd9"),
    VideoStyle(name="Кино", page_bg="#000000", card_bg="#0d0c0b", players_bg="#000000", text_main="#f5efe6",
               text_muted="#a59d91", eval_white="#f5efe6", eval_black="#2b2723", avatar_ring="#2b2723",
               accent="border", card_margin=40, card_radius=4, font_family="PT Serif", title_scale=1.08,
               transition="auto", card_fade=0.35, eval_anim=0.8, subtitles=True, sub_color="#ffe9a8",
               sub_highlight="", sub_box=False, sub_size=44),
    VideoStyle(name="Крупно", font_family="Oswald", title_scale=1.18, body_scale=1.1, title_upper=True,
               card_margin=0, accent="bar", transition="auto", subtitles=True, sub_size=58, sub_words=4,
               sub_upper=True, sub_highlight="#96bc4b"),
)}


# ---------------------------------------------------------------- validation and (de)serialisation
_HEX = re.compile(r"^#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_RANGES = {"title_scale": (0.7, 1.5), "body_scale": (0.7, 1.5), "card_margin": (0, 80), "card_radius": (0, 48),
           "card_fade": (0.0, 1.0), "eval_anim": (0.0, 2.0), "icon_anim": (0.0, 1.0), "sub_size": (24, 96),
           "sub_position": (0.1, 0.95), "sub_words": (1, 14)}
_CHOICES = {"accent": [v for _, v in ACCENTS], "transition": [v for _, v in TRANSITIONS],
            "ui_position": [v for _, v in POSITIONS], "panel_side": [v for _, v in PANEL_SIDES],
            "tag_position": [v for _, v in TAG_POSITIONS], "font_family": list(FONTS)}


def validate(data: dict) -> tuple[VideoStyle, list[str]]:
    """A VideoStyle from a dict (unknown keys ignored); invalid values fall back to the
    default and are listed as Russian messages."""
    names = {f.name: f for f in dataclasses.fields(VideoStyle)}
    out, problems = {}, []
    for k, v in (data or {}).items():
        if k not in names:
            continue
        default = getattr(DEFAULT, k)
        if isinstance(default, bool):
            out[k] = bool(v)
        elif isinstance(default, (int, float)):
            try:
                num = float(v)
            except (TypeError, ValueError):
                problems.append(f"«{k}»: нужно число, а не «{v}»")
                continue
            lo, hi = _RANGES.get(k, (-1e9, 1e9))
            if not lo <= num <= hi:
                problems.append(f"«{k}»: {num:g} вне диапазона {lo:g}…{hi:g} — ограничено")
                num = min(hi, max(lo, num))
            out[k] = int(round(num)) if isinstance(default, int) else num
        elif k == "move_colors":
            out[k] = {t: c for t, c in (v or {}).items() if t in config.MOVE_TYPES and isinstance(c, str) and _HEX.match(c)}
        elif isinstance(default, str):
            v = str(v or "")
            if k in _CHOICES and v not in _CHOICES[k]:
                problems.append(f"«{k}»: неизвестное значение «{v}»")
                continue
            is_colour = default.startswith("#") or k == "sub_highlight"
            if is_colour and v and not _HEX.match(v):
                problems.append(f"«{k}»: «{v}» — не цвет (нужно #rrggbb)")
                continue
            out[k] = v
    return dataclasses.replace(DEFAULT, **out), problems


def from_dict(data: dict) -> VideoStyle:
    return validate(data)[0]


def contrast(fg: str, bg: str) -> float:
    """WCAG contrast ratio of two colours (the background's alpha is ignored)."""
    def lum(c: str) -> float:
        if not _HEX.match(c or ""):
            raise ValueError(c)
        ch = [v / 255 for v in color_rgba(c)[:3]]
        ch = [v / 12.92 if v <= 0.03928 else ((v + 0.055) / 1.055) ** 2.4 for v in ch]
        return 0.2126 * ch[0] + 0.7152 * ch[1] + 0.0722 * ch[2]
    a, b = sorted((lum(fg), lum(bg)), reverse=True)
    return (a + 0.05) / (b + 0.05)


def color_rgba(value: str, alpha: int = 255) -> tuple[int, int, int, int]:
    v = value.lstrip("#")
    if len(v) == 3:
        v = "".join(c * 2 for c in v)
    r, g, b = (int(v[i:i + 2], 16) for i in (0, 2, 4))
    a = int(v[6:8], 16) if len(v) == 8 else alpha
    return r, g, b, a


# ---------------------------------------------------------------- persistence
def load_job_style(job_dir: Path) -> VideoStyle:
    p = job_dir / FILE
    if p.exists():
        try:
            return from_dict(json.loads(p.read_text()))
        except Exception as e:
            log.warning("Стиль задания повреждён (%s) — беру стандартный", e)
    return default_style()


def save_job_style(job_dir: Path, st: VideoStyle) -> None:
    tmp = job_dir / (FILE + ".tmp")
    tmp.write_text(json.dumps(asdict(st), ensure_ascii=False, indent=1))
    tmp.replace(job_dir / FILE)


def user_presets() -> dict[str, VideoStyle]:
    p = config.PRESETS_FILE
    if not p.exists():
        return {}
    try:
        return {name: from_dict(d) for name, d in json.loads(p.read_text()).items()}
    except Exception as e:
        log.warning("Файл пресетов стиля повреждён (%s)", e)
        return {}


def all_presets() -> dict[str, VideoStyle]:
    return {**PRESETS, **user_presets()}


def save_preset(st: VideoStyle, name: str) -> VideoStyle:
    name = " ".join((name or "").split())[:40]
    if not name:
        raise ValueError("Введите название пресета.")
    if name in PRESETS:
        raise ValueError(f"«{name}» — встроенный пресет, выберите другое название.")
    st = dataclasses.replace(st, name=name)
    data = {k: asdict(v) for k, v in user_presets().items()}
    data[name] = asdict(st)
    config.PRESETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return st


def delete_preset(name: str) -> bool:
    data = {k: asdict(v) for k, v in user_presets().items()}
    if name not in data:
        return False
    del data[name]
    config.PRESETS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=1))
    return True


def default_style() -> VideoStyle:
    """The preset chosen last in the editor (prefs), else Chess.com."""
    import prefs

    name = prefs.load().get("style_preset") or DEFAULT.name
    return all_presets().get(name, DEFAULT)
