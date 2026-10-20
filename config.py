"""Global settings: paths, LLM provider, colors, layout.

Everything can be overridden through environment variables or a `.env` file
placed next to this module (see `.env.example`).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
ENV_FILE = Path(os.getenv("ENV_FILE", str(ROOT / ".env")))  # keys saved from the web UI go here too
load_dotenv(ENV_FILE)


def _env(name: str, default: str) -> str:
    return os.getenv(name, default).strip()


def write_env(name: str, value: str) -> None:
    """Set NAME=value in the .env file (replacing an existing or commented-out line) and in os.environ."""
    import re

    lines = ENV_FILE.read_text().splitlines() if ENV_FILE.exists() else []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(name)}\s*=")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = f"{name}={value}"
            break
    else:
        lines.append(f"{name}={value}")
    ENV_FILE.write_text("\n".join(lines) + "\n")
    try:
        ENV_FILE.chmod(0o600)  # may hold API keys: readable by the owner only
    except OSError:
        pass
    os.environ[name] = value


def _env_int(name: str, default: int) -> int:
    return int(_env(name, str(default)))


def _env_float(name: str, default: float) -> float:
    return float(_env(name, str(default)))


def _env_bool(name: str, default: bool) -> bool:
    return _env(name, str(default)).lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------- paths
ASSETS_DIR = ROOT / "assets"
ICONS_DIR = ASSETS_DIR / "icons"
FONTS_DIR = ASSETS_DIR / "fonts"
SFX_DIR = ASSETS_DIR / "sfx"
MODELS_DIR = ASSETS_DIR / "models"
JOBS_DIR = Path(_env("JOBS_DIR", str(ROOT / "jobs")))
PREFS_FILE = Path(_env("PREFS_FILE", str(ROOT / ".ui_prefs.json")))  # last choices made in the web UI
SOURCES_DIR = Path(_env("SOURCES_DIR", str(JOBS_DIR / "_sources")))  # downloaded originals, shared by all jobs
SOURCE_CACHE = _env_bool("SOURCE_CACHE", True)
COOKIES_FILE = Path(_env("TIKTOK_COOKIES", str(ROOT / "cookies.txt")))
PRESETS_FILE = Path(_env("PRESETS_FILE", str(ROOT / "style_presets.json")))  # visual presets saved in the style editor
CAST_DIR = Path(_env("CAST_DIR", str(JOBS_DIR / "_cast")))  # confirmed characters (names + faces), shared by all jobs
SOUND_MODEL = MODELS_DIR / "yamnet.onnx"                   # AudioSet sound-event classifier (Apache-2.0)
SOUND_CLASSES = MODELS_DIR / "yamnet_class_map.csv"

# ---------------------------------------------------------------- yt-dlp
# YouTube may answer "Sign in to confirm you're not a bot": pass cookies then
YTDLP_COOKIES = _env("YTDLP_COOKIES", "")                          # path to Netscape cookies.txt
YTDLP_COOKIES_FROM_BROWSER = _env("YTDLP_COOKIES_FROM_BROWSER", "")  # e.g. firefox, chromium
YTDLP_BIN = _env("YTDLP_BIN", "")  # another yt-dlp executable (e.g. a newer standalone build); default: the one in .venv
FULL_DOWNLOAD_ASK = _env_float("FULL_DOWNLOAD_ASK", 900)  # s: with no fragment selected, longer videos need a yes first

# ---------------------------------------------------------------- whisper
WHISPER_MODEL = _env("WHISPER_MODEL", "small")          # tiny/base/small/medium/large-v3
WHISPER_DEVICE = _env("WHISPER_DEVICE", "auto")         # auto/cpu/cuda
WHISPER_COMPUTE_TYPE = _env("WHISPER_COMPUTE_TYPE", "int8")
WHISPER_LANGUAGE = _env("WHISPER_LANGUAGE", "") or None  # None = autodetect

# ---------------------------------------------------------------- LLM
LLM_PROVIDERS = ("ollama", "pollinations", "github", "groq", "openrouter", "mistral", "gemini")  # see llm.SPECS
LLM_PROVIDER = _env("LLM_PROVIDER", "ollama")           # last choice; the web UI switches it at runtime and saves here
LLM_MODEL = _env("LLM_MODEL", "")                       # model for LLM_PROVIDER ("" = the provider's default)
OLLAMA_URL = _env("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = _env("OLLAMA_MODEL", "qwen2.5-coder")
GEMINI_API_KEY = _env("GEMINI_API_KEY", "")
GEMINI_MODEL = _env("GEMINI_MODEL", "gemini-2.5-flash")
LLM_LANGUAGE = _env("LLM_LANGUAGE", "русском")          # language of titles/explanations
LLM_MAX_RETRIES = _env_int("LLM_MAX_RETRIES", 3)
LLM_TEMPERATURE = _env_float("LLM_TEMPERATURE", 0.4)
LLM_TIMEOUT = _env_int("LLM_TIMEOUT", 600)
EVAL_GAIN = _env_float("EVAL_GAIN", 1.5)                # amplifies the LLM's eval swings (1.0 = as is)

# ---------------------------------------------------------------- speakers / faces
DIARIZATION = _env_bool("DIARIZATION", True)           # audio-visual "who speaks" hints for the LLM
VISION_FPS = _env_float("VISION_FPS", 10)               # face detection samples per second
SPEAKER_HINT_MIN_CONF = _env_float("SPEAKER_HINT_MIN_CONF", 0.6)  # weaker hints are shown as "?"

# ---------------------------------------------------------------- video
WIDTH, HEIGHT = 1080, 1920
FPS = _env_int("FPS", 30)
OUTPUT_FORMATS = ("tiktok", "original", "landscape")
OUTPUT_FORMAT = _env("OUTPUT_FORMAT", "tiktok")         # tiktok 1080x1920 | original aspect | landscape 1920x1080
VIDEO_MODE = _env("VIDEO_MODE", "smart")                # smart | fill | crop | fit
VIDEO_CROP_ASPECT = _env_float("VIDEO_CROP_ASPECT", 1.2)  # width/height when VIDEO_MODE=crop
VIDEO_ANCHOR_Y = _env_float("VIDEO_ANCHOR_Y", 0.3)      # vertical crop position: 0 = top, 0.5 = centre
X264_CRF = _env_int("X264_CRF", 18)
X264_PRESET = _env("X264_PRESET", "medium")

# ---------------------------------------------------------------- pause-on-move + SFX
PAUSE_ON_MOVE = _env_bool("PAUSE_ON_MOVE", True)        # freeze the video while each move is explained
TYPING_CPS = _env_float("TYPING_CPS", 28)               # typewriter speed, characters per second
READ_HOLD = _env_float("READ_HOLD", 2.0)                # s the fully typed text stays before the video resumes
SFX_ENABLED = _env_bool("SFX_ENABLED", True)            # master switch
SFX_VOLUME = _env_float("SFX_VOLUME", 0.8)              # 0..1
SFX_PAUSE = _env_bool("SFX_PAUSE", True)                # individual sounds
SFX_UNPAUSE = _env_bool("SFX_UNPAUSE", True)
SFX_TYPING = _env_bool("SFX_TYPING", True)

# ---------------------------------------------------------------- publishing
TIKTOK_HEADLESS = _env_bool("TIKTOK_HEADLESS", False)
TIKTOK_BROWSER = _env("TIKTOK_BROWSER", "chromium")  # chromium (playwright) | chrome | firefox


# ---------------------------------------------------------------- move types
@dataclass(frozen=True)
class MoveStyle:
    key: str
    label_ru: str
    glyph: str
    color: str


MOVE_TYPES: dict[str, MoveStyle] = {
    s.key: s
    for s in (
        MoveStyle("Brilliant", "Блестящий ход", "!!", "#1baca6"),
        MoveStyle("Great", "Отличный ход", "!", "#5c8bb0"),
        MoveStyle("Best", "Лучший ход", "★", "#96bc4b"),
        MoveStyle("Inaccuracy", "Неточность", "?!", "#f0c15c"),
        MoveStyle("Mistake", "Ошибка", "?", "#e6912c"),
        MoveStyle("Blunder", "Зевок", "??", "#ca3431"),
    )
}


# ---------------------------------------------------------------- UI palette / layout
@dataclass(frozen=True)
class Theme:
    page_bg: str = "#000000"
    card_bg: str = "#262421"
    players_bg: str = "#161512"
    text_main: str = "#ffffff"
    text_muted: str = "#9e9b98"
    eval_white: str = "#ffffff"
    eval_black: str = "#403d39"


@dataclass(frozen=True)
class Layout:
    width: int = WIDTH           # width of the UI strip / side panel
    top_margin: int = 120        # room for TikTok's "Following | For You" header
    card_h: int = 260            # actual value is fitted per video between min/max
    card_min_h: int = 170
    card_max_h: int = 320
    card_pad_x: int = 40
    card_pad_y: int = 18
    card_accent_w: int = 7
    card_margin: int = 0         # the card is inset from the strip edges (style.card_margin)
    card_radius: int = 0
    icon_size: int = 80
    title_size: int = 36
    subtitle_size: int = 22
    body_size: int = 31
    title_lh: float = 1.12       # line-height multipliers
    subtitle_lh: float = 1.15
    body_lh: float = 1.22
    title_gap: int = 4           # title -> subtitle
    body_gap: int = 12           # subtitle -> explanation
    players_h: int = 84
    players_pad: int = 18
    avatar_size: int = 62
    name_size: int = 31
    side_size: int = 20
    eval_gap: int = 3
    eval_h: int = 30
    eval_label_size: int = 19
    video_gap: int = 0           # video starts right under the eval bar
    panel_h: int = 0             # landscape side panel: total height (0 = just the strip)

    @property
    def card_y(self) -> int:
        return self.top_margin

    @property
    def players_y(self) -> int:
        return self.card_y + self.card_h

    @property
    def eval_y(self) -> int:
        return self.players_y + self.players_h + self.eval_gap

    @property
    def ui_h(self) -> int:
        """Height of the overlay strip rendered by Pillow (from y=0)."""
        return max(self.panel_h, self.eval_y + self.eval_h)

    @property
    def video_y(self) -> int:
        return self.ui_h + self.video_gap


THEME = Theme()
LAYOUT = Layout()
LANDSCAPE_PANEL_W = 640          # analysis panel to the right of the video in landscape output


# ---------------------------------------------------------------- fonts
FONT_CANDIDATES = {
    # weight -> (files looked up in assets/fonts, fontconfig fallback patterns)
    "regular": (["Inter-Regular.ttf", "Roboto-Regular.ttf"], ["Inter", "Roboto", "DejaVu Sans"]),
    "semibold": (["Inter-SemiBold.ttf", "Roboto-Medium.ttf"], ["Inter:semibold", "Roboto:medium", "DejaVu Sans:bold"]),
    "bold": (["Inter-Bold.ttf", "Roboto-Bold.ttf"], ["Inter:bold", "Roboto:bold", "DejaVu Sans:bold"]),
}


def font_path(weight: str = "regular") -> str:
    files, patterns = FONT_CANDIDATES[weight]
    for name in files:
        p = FONTS_DIR / name
        if p.exists():
            return str(p)
    if shutil.which("fc-match"):
        for pat in patterns:
            out = subprocess.run(["fc-match", "-f", "%{file}", pat], capture_output=True, text=True)
            if out.returncode == 0 and out.stdout:
                return out.stdout.strip()
    raise FileNotFoundError(
        f"No font found for weight '{weight}'. Run `python make_assets.py` to download Inter."
    )
