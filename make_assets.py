"""Generate move-classification icons (SVG + PNG) and sound effects, fetch free
fonts and the face/voice models.

    python make_assets.py              # icons + SFX + fonts (Inter + style families) + face/voice/sound models
    python make_assets.py --no-fonts   # skip the fonts (resolved via fontconfig)
    python make_assets.py --no-models  # skip the face (YuNet/SFace), voice (WeSpeaker) and sound (YAMNet) models
"""
from __future__ import annotations

import argparse
import io
import logging
import math
import urllib.request
import wave
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

import config

log = logging.getLogger(__name__)

INTER_URL = "https://github.com/rsms/inter/releases/download/v4.1/Inter-4.1.zip"
INTER_FILES = ("Inter-Regular.ttf", "Inter-SemiBold.ttf", "Inter-Bold.ttf")
ICON_PX = 256

# OpenCV Zoo (Apache-2.0): YuNet face detector + SFace face embeddings;
# WeSpeaker ResNet34 (Apache-2.0, ONNX export by sherpa-onnx): voice embeddings
_ZOO = "https://github.com/opencv/opencv_zoo/raw/main/models"
_SHERPA = "https://github.com/k2-fsa/sherpa-onnx/releases/download/speaker-recongition-models"
MODELS = {
    "face_detection_yunet_2023mar.onnx": f"{_ZOO}/face_detection_yunet/face_detection_yunet_2023mar.onnx",
    "face_recognition_sface_2021dec.onnx": f"{_ZOO}/face_recognition_sface/face_recognition_sface_2021dec.onnx",
    "wespeaker_en_voxceleb_resnet34.onnx": f"{_SHERPA}/wespeaker_en_voxceleb_resnet34.onnx",
}
# YAMNet (Apache-2.0, ONNX export of Google's AudioSet model): sound events for sounds.py
_YAMNET = "https://huggingface.co/audiomagic/yamnet-onnx/resolve/main"
SOUND_MODELS = {"yamnet.onnx": f"{_YAMNET}/yamnet.onnx", "yamnet_class_map.csv": f"{_YAMNET}/yamnet_class_map.csv"}
SFX_SR = 48000
TYPE_TICKS = 4  # type_tick_1..4.wav, slightly different pitch each


def star_points(cx: float, cy: float, r_out: float, r_in: float) -> list[tuple[float, float]]:
    pts = []
    for i in range(10):
        r = r_out if i % 2 == 0 else r_in
        a = -math.pi / 2 + i * math.pi / 5
        pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
    return pts


def _glyph_font(text: str, box: int) -> ImageFont.FreeTypeFont:
    """Largest bold font whose rendering of `text` fits into `box` px."""
    size = box
    path = config.font_path("bold")
    while size > 8:
        font = ImageFont.truetype(path, size)
        l, t, r, b = font.getbbox(text)
        if r - l <= box and b - t <= box:
            return font
        size -= 4
    return ImageFont.truetype(path, size)


def render_icon_png(style: config.MoveStyle, size: int = ICON_PX) -> Image.Image:
    ss = 4  # supersampling for smooth edges
    s = size * ss
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # soft drop shadow + body + subtle bottom shade, like chess.com badges
    d.ellipse((int(s * 0.04), int(s * 0.07), int(s * 0.96), int(s * 0.99)), fill=(0, 0, 0, 90))
    d.ellipse((int(s * 0.04), int(s * 0.03), int(s * 0.96), int(s * 0.95)), fill=style.color)
    c = s / 2
    cy = s * 0.49
    if style.glyph == "★":
        d.polygon(star_points(c, cy, s * 0.30, s * 0.125), fill="white")
    else:
        font = _glyph_font(style.glyph, int(s * 0.52))
        d.text((c, cy), style.glyph, font=font, fill="white", anchor="mm")
    return img.resize((size, size), Image.LANCZOS)


def render_icon_svg(style: config.MoveStyle) -> str:
    if style.glyph == "★":
        pts = " ".join(f"{x:.2f},{y:.2f}" for x, y in star_points(50, 49, 30, 12.5))
        glyph = f'<polygon points="{pts}" fill="#fff"/>'
    else:
        glyph = (
            f'<text x="50" y="50" dy="0.35em" text-anchor="middle" fill="#fff" '
            f'font-family="Inter, Roboto, Arial, sans-serif" font-weight="800" '
            f'font-size="{46 if len(style.glyph) > 1 else 56}">{style.glyph}</text>'
        )
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" width="100" height="100">'
        '<circle cx="50" cy="53" r="46" fill="#000" fill-opacity="0.35"/>'
        f'<circle cx="50" cy="49" r="46" fill="{style.color}"/>'
        f"{glyph}</svg>\n"
    )


def icon_path(move_type: str):
    return config.ICONS_DIR / f"{move_type.lower()}.png"


def make_icons() -> None:
    config.ICONS_DIR.mkdir(parents=True, exist_ok=True)
    for style in config.MOVE_TYPES.values():
        render_icon_png(style).save(icon_path(style.key))
        (config.ICONS_DIR / f"{style.key.lower()}.svg").write_text(render_icon_svg(style))
        print(f"  icon  {style.key:<10} {style.glyph:<2} {style.color}")


def ensure_icons() -> None:
    if not all(icon_path(k).exists() for k in config.MOVE_TYPES):
        make_icons()


# ---------------------------------------------------------------- sound effects
def _t(dur: float) -> np.ndarray:
    return np.arange(int(dur * SFX_SR)) / SFX_SR


def _bandpass(x: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """FFT band-pass with soft (half-octave) edges."""
    spec = np.fft.rfft(x)
    f = np.fft.rfftfreq(len(x), 1 / SFX_SR) + 1e-9
    lo_gain = np.clip(np.log2(f / lo) * 2 + 1, 0, 1)
    hi_gain = np.clip(np.log2(hi / f) * 2 + 1, 0, 1)
    return np.fft.irfft(spec * lo_gain * hi_gain, len(x))


def _modes(t: np.ndarray, modes: list[tuple[float, float, float]], t0: float = 0.0) -> np.ndarray:
    """Sum of exponentially decaying sines (freq, amplitude, decay) starting at t0."""
    tt = np.clip(t - t0, 0, None)
    on = t >= t0
    return sum(a * np.sin(2 * np.pi * f * tt) * np.exp(-tt / tau) * on for f, a, tau in modes)


def _sweep(t: np.ndarray, f0: float, f1: float, dur: float, tau: float) -> np.ndarray:
    """Exponential glide f0 -> f1 over `dur`, with a soft attack and decay."""
    f = f0 * (f1 / f0) ** np.clip(t / dur, 0, 1)
    phase = 2 * np.pi * np.cumsum(f) / SFX_SR
    return np.sin(phase) * (1 - np.exp(-t / 0.01)) * np.exp(-t / tau)


def _finish(x: np.ndarray, peak: float) -> np.ndarray:
    n_in, n_out = int(0.001 * SFX_SR), int(0.006 * SFX_SR)
    x = x.copy()
    x[:n_in] *= np.linspace(0, 1, n_in)
    x[-n_out:] *= np.linspace(1, 0, n_out)
    return x / (np.abs(x).max() + 1e-9) * peak


def _click(t: np.ndarray, t0: float, rng: np.random.Generator, bright: float = 1.0) -> np.ndarray:
    """Wooden chess-piece "tock": short noise transient + a few resonant modes + low thump."""
    burst = _bandpass(rng.standard_normal(len(t)), 1800, 8000) * np.exp(-np.clip(t - t0, 0, None) / 0.0025) * (t >= t0)
    body = _modes(t, [(1150 * bright, 1.0, 0.018), (2370 * bright, 0.45, 0.011), (3950 * bright, 0.2, 0.007)], t0)
    thump = _modes(t, [(170, 0.8, 0.03)], t0)
    return 0.35 * burst / (np.abs(burst).max() + 1e-9) + 0.5 * body + thump * 0.6


def sfx_pause() -> np.ndarray:
    """Freeze: a firm wooden click with a soft "tape-stop" glide down."""
    rng = np.random.default_rng(1)
    t = _t(0.42)
    glide = _sweep(t, 760, 330, 0.3, 0.12) * 0.22
    air = _bandpass(rng.standard_normal(len(t)), 400, 2500) * np.exp(-t / 0.07) * 0.05
    return _finish(_click(t, 0.0, rng) + glide + air, 0.7)


def sfx_unpause() -> np.ndarray:
    """Resume: a short rising glide that lands on a lighter click."""
    rng = np.random.default_rng(2)
    t = _t(0.36)
    glide = _sweep(t, 330, 820, 0.11, 0.07) * 0.25
    air = _bandpass(rng.standard_normal(len(t)), 900, 5000) * (t / 0.1) * np.exp(-t / 0.05) * 0.06
    return _finish(glide + air + 0.8 * _click(t, 0.1, rng, bright=1.25), 0.6)


def sfx_type_tick(i: int) -> np.ndarray:
    """Soft key tick for the typewriter; `i` varies pitch and grain."""
    rng = np.random.default_rng(10 + i)
    t = _t(0.05)
    pitch = 1 + 0.06 * (i - (TYPE_TICKS - 1) / 2)
    noise = _bandpass(rng.standard_normal(len(t)), 1600 * pitch, 7000) * np.exp(-t / 0.004)
    blip = _modes(t, [(2050 * pitch, 0.35, 0.006), (430 * pitch, 0.25, 0.005)])
    return _finish(noise / (np.abs(noise).max() + 1e-9) + blip, 0.45)


def write_wav(path: Path, x: np.ndarray, sr: int = SFX_SR) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1 if pcm.ndim == 1 else pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


def sfx_files() -> dict[str, Path]:
    names = ["pause", "unpause"] + [f"type_tick_{i}" for i in range(1, TYPE_TICKS + 1)]
    return {n: config.SFX_DIR / f"{n}.wav" for n in names}


def make_sfx() -> None:
    config.SFX_DIR.mkdir(parents=True, exist_ok=True)
    sounds = {"pause": sfx_pause(), "unpause": sfx_unpause()}
    sounds.update({f"type_tick_{i}": sfx_type_tick(i - 1) for i in range(1, TYPE_TICKS + 1)})
    for name, x in sounds.items():
        write_wav(config.SFX_DIR / f"{name}.wav", x)
        print(f"  sfx   {name:<12} {len(x) / SFX_SR * 1000:4.0f} ms")


def ensure_sfx() -> None:
    if not all(p.exists() for p in sfx_files().values()):
        make_sfx()


# ---------------------------------------------------------------- face / voice models
def download_models(models: dict[str, str] | None = None) -> None:
    config.MODELS_DIR.mkdir(parents=True, exist_ok=True)
    for name, url in (models or {**MODELS, **SOUND_MODELS}).items():
        dst = config.MODELS_DIR / name
        if dst.exists() and dst.stat().st_size > 0:
            continue
        print(f"  model {name} <- {url}")
        tmp = dst.with_suffix(".part")
        with urllib.request.urlopen(url, timeout=120) as resp:
            tmp.write_bytes(resp.read())
        tmp.replace(dst)
    print("  face/voice/sound models ready")


def ensure_models() -> dict[str, Path]:
    """Paths of the face/voice models by file name, downloading them on first use."""
    paths = {name: config.MODELS_DIR / name for name in MODELS}
    if not all(p.exists() for p in paths.values()):
        download_models()
    return paths


def ensure_sound_model() -> bool:
    """The sound-event model, downloaded on first use (16 MB). False when offline."""
    if all((config.MODELS_DIR / n).exists() for n in SOUND_MODELS):
        return True
    try:
        download_models(SOUND_MODELS)
        return True
    except Exception as e:
        log.warning("Не удалось скачать модель звуков (%s)", e)
        return False


def download_fonts() -> None:
    config.FONTS_DIR.mkdir(parents=True, exist_ok=True)
    if all((config.FONTS_DIR / f).exists() for f in INTER_FILES):
        print("  fonts already present")
        return
    print(f"  downloading Inter (SIL OFL) from {INTER_URL}")
    with urllib.request.urlopen(INTER_URL, timeout=60) as resp:
        data = resp.read()
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in INTER_FILES:
            (config.FONTS_DIR / name).write_bytes(zf.read(f"extras/ttf/{name}"))
            print(f"  font  {name}")


def download_style_fonts() -> None:
    """The extra families of the style editor (SIL OFL, Google Fonts)."""
    import style

    for family in style.FONTS:
        if not style.font_ready(family):
            print(f"  font  {family}")
            style.ensure_font(family)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-fonts", action="store_true", help="skip downloading Inter")
    ap.add_argument("--no-models", action="store_true", help="skip downloading the face/voice models")
    args = ap.parse_args()
    build(fonts=not args.no_fonts, models=not args.no_models)


def build(fonts: bool = True, models: bool = True) -> None:
    if fonts:
        try:
            download_fonts()
            download_style_fonts()
        except Exception as e:  # offline is fine: fontconfig fallback kicks in
            print(f"  font download failed ({e}); falling back to system fonts")
    make_icons()
    make_sfx()
    if models:
        try:
            download_models()
        except Exception as e:  # faces are optional: the pipeline falls back to placeholders
            print(f"  model download failed ({e}); the pipeline falls back to simpler heuristics")


if __name__ == "__main__":
    main()
