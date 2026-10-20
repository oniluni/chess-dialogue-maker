"""Step 1: get the source clip (YouTube/any yt-dlp URL or a local file), cut it,
normalize it, and prepare avatars for both players."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageOps

import config

log = logging.getLogger(__name__)
AVATAR_PX = 256


class IngestError(RuntimeError):
    pass


class SourceError(IngestError):
    """The source itself is not usable (wrong file type, missing file, bad link): Russian text."""

    def __init__(self, title: str, hint: str = ""):
        super().__init__(f"{title} {hint}".strip())
        self.title, self.hint = title, hint


class DownloadError(IngestError):
    """A download failure with a ready Russian explanation; `kind` says what can be done
    (see DL_KINDS). errors.explain shows .title/.hint as they are."""

    def __init__(self, kind: str, title: str, hint: str = "", detail: str = ""):
        super().__init__(f"{title} {hint} {detail}".strip())
        self.kind, self.title, self.hint, self.detail = kind, title, hint, detail


class BotCheckError(DownloadError):
    def __init__(self, detail: str = ""):
        t, h = DL_KINDS["auth"]
        super().__init__("auth", t, h, detail)


class PartialUnavailable(DownloadError):
    """Only the selected fragment could not be fetched; downloading the whole video might work,
    but that is the person's decision (job.allow_full_download)."""

    def __init__(self, reason: str, duration: float | None, size: int | None, detail: str = ""):
        size_txt = f" (≈ {size / 1e6:.0f} МБ)" if size else ""
        whole = f"весь ролик{f' {fmt_duration(duration)}' if duration else ''}{size_txt}"
        super().__init__("partial", "Не получилось скачать только выбранный фрагмент.",
                         f"{reason} Можно повторить попытку или скачать {whole} и вырезать фрагмент на компьютере.", detail)
        self.reason, self.duration, self.size = reason, duration, size


class NeedsRange(DownloadError):
    """No fragment is selected and the video is long: ask before downloading all of it."""

    def __init__(self, duration: float, size: int | None):
        size_txt = f", ≈ {size / 1e6:.0f} МБ" if size else ""
        super().__init__("range", f"Не выбран фрагмент, а ролик длинный ({fmt_duration(duration)}{size_txt}).",
                         "Укажите начало и конец диалога — скачается только он. Или подтвердите, что нужен весь ролик.")
        self.duration, self.size = duration, size


# kind -> (title, hint). Nothing here tries to get around a restriction: private, age-restricted,
# members-only or region-locked videos need an account that has access (cookies the person set up).
DL_KINDS: dict[str, tuple[str, str]] = {
    "auth": ("YouTube просит войти в аккаунт («подтвердите, что вы не бот»).",
             "Так бывает после многих загрузок с одного адреса. Укажите браузер, в котором вы вошли в YouTube, "
             "в настройке «Доступ к YouTube» (cookies читает yt-dlp на вашем компьютере) — или подождите и повторите."),
    "age": ("Видео с возрастным ограничением.",
            "Скачать его можно только из аккаунта, которому оно доступно: укажите браузер с этим аккаунтом в «Доступе к YouTube»."),
    "members": ("Видео только для спонсоров канала.", "Без доступа к этому каналу скачать его нельзя."),
    "private": ("Видео приватное.", "Его может скачать только тот, у кого есть доступ. Выберите другой ролик."),
    "unavailable": ("Видео недоступно — удалено или ссылка неверна.", "Проверьте ссылку или выберите другой ролик."),
    "geo": ("Видео недоступно в вашем регионе.", "Выберите другой ролик."),
    "drm": ("Видео защищено от копирования (DRM).", "Такие видео скачать нельзя — выберите другой источник."),
    "live": ("Это трансляция или премьера, которая ещё не закончилась.", "Дождитесь записи трансляции и попробуйте снова."),
    "rate": ("Сайт временно ограничил загрузки (слишком много запросов).", "Подождите 10–30 минут и повторите."),
    "forbidden": ("Сайт отказал в скачивании (HTTP 403).",
                  "Часто помогает обновить yt-dlp (кнопка «Обновить yt-dlp») и повторить."),
    "expired": ("Ссылка на видеопоток устарела.", "Повторите — ссылка будет получена заново."),
    "outdated": ("yt-dlp не смог разобрать страницу — вероятно, сайт изменился.",
                 "Обновите yt-dlp (кнопка «Обновить yt-dlp») и повторите."),
    "network": ("Нет связи с сайтом.", "Проверьте интернет и повторите."),
    "unsupported": ("Эту ссылку yt-dlp не поддерживает.", "Вставьте ссылку на страницу ролика (YouTube, VK, Vimeo…) или файл."),
    "nofile": ("Скачивание закончилось без файла.", "Повторите; если не поможет — обновите yt-dlp."),
    "other": ("Не удалось скачать видео.", "Повторите позже; подробности — в «Подробностях»."),
}
_DL_PATTERNS: list[tuple[str, str]] = [
    ("age", r"confirm your age|age.restricted|inappropriate for some users"),
    ("auth", r"confirm you.?re not a bot|cookies for the authentication|Sign in to confirm|login required|"
             r"This video is only available for registered users|requires authentication"),
    ("members", r"members.only|Join this channel|available to this channel.s members"),
    ("private", r"Private video|This video is private"),
    ("geo", r"not available in your country|geo.?restrict|blocked it in your country"),
    ("drm", r"DRM"),
    ("live", r"live event will begin|Premieres in|is a live stream|This live stream recording is not available"),
    ("unavailable", r"Video unavailable|has been removed|no longer available|does not exist|account .* terminated|"
                    r"HTTP Error 404|404: Not Found|is not available"),
    ("rate", r"HTTP Error 429|Too Many Requests|rate.?limit"),
    ("expired", r"HTTP Error 410|expired"),
    ("forbidden", r"HTTP Error 403|403 Forbidden|Forbidden"),
    ("outdated", r"Signature extraction failed|nsig extraction failed|Unable to extract|Please report this issue|"
                 r"Some formats may be missing|Requested format is not available"),
    ("unsupported", r"Unsupported URL|is not a valid URL"),
    ("network", r"Temporary failure in name resolution|Name or service not known|Network is unreachable|timed out|"
                r"Connection reset|Connection refused|getaddrinfo failed|Unable to download webpage"),
]
RETRY_KINDS = ("expired", "network")          # one automatic retry with a fresh extraction
FINAL_KINDS = ("auth", "age", "members", "private", "unavailable", "geo", "drm", "live", "rate", "unsupported")


# ---------------------------------------------------------------- helpers
def parse_time(value: str | float | int | None) -> float | None:
    """'83', '1:23', '00:01:23.5' -> seconds."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    value = value.strip().replace(",", ".")
    if not re.fullmatch(r"\d+(:\d{1,2}){0,2}(\.\d+)?", value):
        raise ValueError(f"Bad timecode: {value!r} (use SS, MM:SS or HH:MM:SS)")
    secs = 0.0
    for part in value.split(":"):
        secs = secs * 60 + float(part)
    return secs


def fmt_duration(sec: float | None) -> str:
    """83.4 -> '1:23', 3725 -> '1:02:05'."""
    if sec is None:
        return "?"
    sec = int(round(sec))
    h, m, s = sec // 3600, sec // 60 % 60, sec % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def run(cmd: list[str], what: str) -> subprocess.CompletedProcess:
    log.debug("$ %s", " ".join(map(str, cmd)))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-15:])
        raise IngestError(f"{what} failed:\n{tail}")
    return proc


@dataclass
class MediaInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


def probe(path: Path) -> MediaInfo:
    out = run(
        ["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        "ffprobe",
    ).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise IngestError(f"{path} has no video stream")
    w, h = int(video["width"]), int(video["height"])
    rot = int(video.get("tags", {}).get("rotate", 0) or 0)
    for sd in video.get("side_data_list", []):
        rot = int(sd.get("rotation", rot) or rot)
    if abs(rot) in (90, 270):
        w, h = h, w
    return MediaInfo(
        duration=float(data["format"]["duration"]),
        width=w,
        height=h,
        has_audio=any(s["codec_type"] == "audio" for s in data["streams"]),
    )


def _is_url(source: str) -> bool:
    return bool(re.match(r"^https?://", source.strip()))


# ---------------------------------------------------------------- what the source is
VIDEO_EXT = {".mp4", ".mkv", ".webm", ".mov", ".m4v", ".avi", ".wmv", ".flv", ".mpg", ".mpeg", ".ts", ".mts", ".m2ts",
             ".3gp", ".ogv"}
AUDIO_EXT = {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".opus", ".wma"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".heic", ".tif", ".tiff", ".svg"}
TEXT_EXT = {".txt", ".url", ".webloc", ".md", ".csv", ".json", ".html", ".htm"}
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_BARE_URL = re.compile(r"^(?:www\.|m\.)?(?:youtube\.com|youtu\.be|vk\.com|vkvideo\.ru|vimeo\.com|rutube\.ru|tiktok\.com)/\S+", re.I)


@dataclass
class Source:
    kind: str           # url | file
    value: str          # the URL or the absolute file path
    note: str = ""      # e.g. "ссылка взята из файла links.txt"


def inspect_source(text: str) -> Source:
    """What the person gave: a link, a local video, or something we can explain away.
    Raises SourceError with a Russian message for anything that is not a video source."""
    text = (text or "").strip().strip('"\'')
    if not text:
        raise SourceError("Не указан источник.", "Вставьте ссылку на ролик, выберите видеофайл или найдите ролик по названию.")
    if _BARE_URL.match(text):
        text = "https://" + text
    if _is_url(text):
        if not re.match(r"^https?://[^/\s]+\.[^/\s]+", text) or " " in text:
            raise SourceError("Ссылка выглядит неполной.", "Скопируйте адрес ролика из браузера целиком.")
        return Source("url", text)
    path = Path(text).expanduser()
    if not path.exists():
        if path.suffix.lower() in VIDEO_EXT | AUDIO_EXT | TEXT_EXT or text.startswith(("/", "~", "./")):
            raise SourceError(f"Файл не найден: {path}.", "Проверьте путь или загрузите файл кнопкой 📎.")
        raise SourceError("Это не ссылка и не файл.", "Вставьте ссылку на ролик или введите запрос и нажмите «Найти».")
    if path.is_dir():
        raise SourceError(f"{path.name} — это папка, а не видео.", "Выберите сам видеофайл.")
    ext = path.suffix.lower()
    if ext in TEXT_EXT or (ext not in VIDEO_EXT | AUDIO_EXT | IMAGE_EXT and path.stat().st_size < 64_000 and _is_text(path)):
        url = _url_in_text(path)
        if url is None:
            raise SourceError(f"{path.name} — текстовый файл без ссылки на видео.",
                              "Нужен видеофайл или ссылка на ролик (в файле .txt должна быть ссылка).")
        log.info("Ссылка взята из файла %s: %s", path.name, url)
        return Source("url", url, f"ссылка взята из файла {path.name}")
    if ext in IMAGE_EXT:
        raise SourceError(f"{path.name} — это картинка, а нужно видео.",
                          "Картинку можно поставить аватаркой игрока, а источником выберите видеофайл или ссылку.")
    kinds = _stream_kinds(path)
    if kinds is None:
        raise SourceError(f"{path.name} не похож на видео — ffmpeg не смог его прочитать.", "Выберите видеофайл (mp4, mkv, webm, mov…).")
    if "video" not in kinds:
        if "audio" in kinds:
            raise SourceError(f"{path.name} — только звук, без картинки.", "Для разбора нужно видео: выберите видеофайл.")
        raise SourceError(f"В файле {path.name} нет видео.", "Выберите видеофайл (mp4, mkv, webm, mov…).")
    return Source("file", str(path.resolve()))


def _is_text(path: Path) -> bool:
    try:
        path.read_bytes()[:4096].decode("utf-8")
        return True
    except (UnicodeDecodeError, OSError):
        return False


def _url_in_text(path: Path) -> str | None:
    try:
        text = path.read_bytes()[:256_000].decode("utf-8", errors="replace")
    except OSError:
        return None
    m = _URL_RE.search(text)
    return m.group(0).rstrip(".,;)") if m else None


def _stream_kinds(path: Path) -> set[str] | None:
    proc = subprocess.run(["ffprobe", "-v", "error", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    try:
        streams = json.loads(proc.stdout).get("streams", [])
    except ValueError:
        return None
    kinds = set()
    for st in streams:
        if st.get("codec_type") == "video" and not (st.get("disposition") or {}).get("attached_pic"):
            kinds.add("video")
        elif st.get("codec_type") == "audio":
            kinds.add("audio")
    return kinds


# ---------------------------------------------------------------- download / cut
SECTION_MARGIN = 3.0  # s of extra video around a partial download; the exact cut happens locally



def _ytdlp_base() -> list[str]:
    if config.YTDLP_BIN:
        return [sys.executable, config.YTDLP_BIN] if config.YTDLP_BIN.endswith(".py") else [config.YTDLP_BIN]
    return [sys.executable, "-m", "yt_dlp"]


def _ytdlp_cmd() -> list[str]:
    cmd = _ytdlp_base() + ["--no-playlist", "--newline", "--progress", "--retries", "3", "--fragment-retries", "5",
                           "--socket-timeout", "30"]
    if config.YTDLP_COOKIES:
        cmd += ["--cookies", config.YTDLP_COOKIES]
    elif config.YTDLP_COOKIES_FROM_BROWSER:
        cmd += ["--cookies-from-browser", config.YTDLP_COOKIES_FROM_BROWSER]
    # yt-dlp needs a JS runtime for YouTube; it only enables deno by default
    if not shutil.which("deno") and shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    return cmd


def _redact(text: str) -> str:
    """Never show cookie values or tokens that yt-dlp might echo."""
    text = re.sub(r"(?i)(cookie|authorization|token|sid|session)([=:]\s*)\S+", r"\1\2***", text)
    return re.sub(r"(?i)(--cookies(?:-from-browser)?\s+)\S+", r"\1***", text)


def classify(text: str) -> DownloadError:
    """A DownloadError for yt-dlp's (or ffmpeg's) error output."""
    lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]
    errs = [ln for ln in lines if ln.startswith("ERROR") or "error" in ln.lower()] or lines
    detail = _redact("\n".join(errs[-6:]))[-1200:]
    for kind, pattern in _DL_PATTERNS:
        if re.search(pattern, text or "", re.I):
            title, hint = DL_KINDS[kind]
            return BotCheckError(detail) if kind == "auth" else DownloadError(kind, title, hint, detail)
    title, hint = DL_KINDS["other"]
    return DownloadError("other", title, hint, detail)


_PCT = re.compile(r"\[download\]\s+([\d.]+)%(?:\s+of\s+~?\s*([\d.]+\s*\w+))?(?:.*?at\s+([\d.]+\s*\w+/s))?")
_FFTIME = re.compile(r"time=(\d+):(\d+):([\d.]+)")


def _ytdlp_stream(cmd: list[str], what: str, length: float | None, progress=None) -> str:
    """Run yt-dlp, report download progress (its own % or ffmpeg's time= for sections), return
    the output; raise a classified DownloadError on failure."""
    log.debug("$ %s", _redact(" ".join(map(str, cmd))))
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                errors="replace")
    except FileNotFoundError as e:
        raise DownloadError("other", "Не найден yt-dlp.", "Установите зависимости: `uv pip install -r requirements.txt`.") from e
    out: list[str] = []
    last = 0.0
    for raw in proc.stdout:
        for line in raw.replace("\r", "\n").splitlines():
            out.append(line)
            if len(out) > 400:
                del out[:200]
            frac, extra = None, ""
            m = _PCT.search(line)
            if m:
                frac = float(m.group(1)) / 100
                extra = " · ".join(x for x in (m.group(2), m.group(3)) if x)
            elif length:
                m2 = _FFTIME.search(line)
                if m2:
                    sec = int(m2.group(1)) * 3600 + int(m2.group(2)) * 60 + float(m2.group(3))
                    frac = min(1.0, sec / length)
                    extra = f"{fmt_duration(sec)} из {fmt_duration(length)}"
            if frac is not None and progress and (frac - last >= 0.02 or frac >= 1.0):
                last = frac
                progress(frac, f"{what}: {frac:.0%}" + (f" ({extra})" if extra else ""))
    code = proc.wait()
    text = "\n".join(out)
    if code != 0:
        raise classify(text)
    return text


def download_section(url: str, start: float | None, end: float | None, out_dir: Path, progress=None,
                     what: str = "") -> Path:
    """Download only [start, end] via yt-dlp --download-sections (start=end=None: the whole video)."""
    shutil.rmtree(out_dir, ignore_errors=True)
    out_dir.mkdir(parents=True)
    cmd = _ytdlp_cmd() + [
        "-f", "bv*[height<=1080]+ba/b[height<=1080]/bv*+ba/b",
        "--merge-output-format", "mp4",
        "-o", str(out_dir / "source.%(ext)s"),
    ]
    whole = start is None and end is None
    if not whole:
        sec = f"*{start or 0:.3f}-{end:.3f}" if end is not None else f"*{start:.3f}-inf"
        cmd += ["--download-sections", sec, "--force-keyframes-at-cuts"]
    cmd.append(url)
    part = "весь ролик" if whole else \
        f"фрагмент {fmt_duration(start or 0)}–{fmt_duration(end) if end is not None else 'конец'}"
    what = what or f"Скачивание: {part}"
    log.info("📥 Скачиваю %s…", part)
    length = None if whole or end is None else end - (start or 0.0)
    _ytdlp_stream(cmd, what, length, progress)
    files = sorted((f for f in out_dir.glob("source.*") if not f.name.endswith((".part", ".ytdl"))),
                   key=lambda p: p.stat().st_size, reverse=True)
    if not files:
        title, hint = DL_KINDS["nofile"]
        raise DownloadError("nofile", title, hint)
    return files[0]


# ---------------------------------------------------------------- source cache
def video_key(url: str) -> str:
    """Stable cache key: the YouTube id when there is one, otherwise a hash of the URL."""
    m = re.search(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})", url)
    return f"yt-{m.group(1)}" if m else "url-" + hashlib.sha1(url.strip().encode()).hexdigest()[:16]


INFO_VERSION = 3  # info.json fields: see remote_info


def remote_info(url: str, cache: Path) -> dict:
    """Metadata of a remote video, cached next to its downloads: title, duration, size,
    channel, tags, categories, the start of the description and whether it is a YouTube
    Short. A definite refusal (sign-in, private, removed, region…) is raised as a
    DownloadError before anything is downloaded; anything else just leaves fields empty."""
    info_file = cache / "info.json"
    if info_file.exists():
        try:
            info = json.loads(info_file.read_text())
            if info.get("v") == INFO_VERSION:
                return info
        except ValueError:
            pass
    try:
        out = _ytdlp_stream(_ytdlp_cmd() + ["--skip-download", "-J", url], "Сведения о ролике", None)
        data = json.loads(out[out.index("{"):]) if "{" in out else {}
    except DownloadError as e:
        if e.kind in FINAL_KINDS or e.kind in ("forbidden", "outdated"):
            raise
        log.debug("video info unavailable: %s", e)
        return {"duration": None, "title": "", "url": url}
    except ValueError as e:
        log.debug("video info is not JSON: %s", e)
        return {"duration": None, "title": "", "url": url}
    if data.get("is_live") or data.get("live_status") in ("is_live", "is_upcoming"):
        title, hint = DL_KINDS["live"]
        raise DownloadError("live", title, hint)
    w, h = data.get("width") or 0, data.get("height") or 0
    duration = data.get("duration") or _remote_duration(data.get("url") or url)
    size = data.get("filesize") or data.get("filesize_approx")
    if not size and data.get("requested_formats"):
        size = sum((f.get("filesize") or f.get("filesize_approx") or 0) for f in data["requested_formats"]) or None
    info = {
        "v": INFO_VERSION,
        "url": url,
        "id": data.get("id") or "",
        "title": data.get("title") or "",
        "duration": float(duration) if duration else None,
        "size": int(size) if size else None,
        "channel": data.get("channel") or data.get("uploader") or "",
        "tags": [str(t) for t in (data.get("tags") or [])][:40],
        "categories": [str(c) for c in (data.get("categories") or [])],
        "description": (data.get("description") or "")[:800],
        "upload_date": data.get("upload_date") or "",
        "is_short": "/shorts/" in url or "/shorts/" in (data.get("webpage_url") or "")
                    or bool(h > w and duration and duration <= 180),
    }
    info_file.write_text(json.dumps(info, ensure_ascii=False))
    return info


def _remote_duration(url: str) -> float | None:
    """Length of a direct media link (the site gave none): ffprobe reads only the header."""
    if not _is_url(url):
        return None
    try:
        proc = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", url],
                              capture_output=True, text=True, timeout=20)
        return float(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else None
    except (subprocess.TimeoutExpired, ValueError):
        return None


def source_meta(source: str, fetch: bool = False) -> dict:
    """What is known about a source without downloading it: cached yt-dlp metadata
    for URLs (fetched now only if `fetch`), the file name for local files."""
    if _is_url(source):
        cache = config.SOURCES_DIR / video_key(source)
        info_file = cache / "info.json"
        info: dict = {}
        if fetch:
            cache.mkdir(parents=True, exist_ok=True)
            info = remote_info(source, cache)
        elif info_file.exists():  # whatever is cached, even an older format: no network here
            try:
                info = json.loads(info_file.read_text())
            except ValueError:
                info = {}
        info = {"url": source, "title": "", **info}
        info["is_short"] = bool(info.get("is_short")) or "/shorts/" in source
        return info
    path = Path(source).expanduser()
    return {"url": "", "title": path.stem.replace("_", " ").strip(), "file": str(path), "is_short": False}


def _cached_part(cache: Path, t0: float, t1: float | None) -> tuple[Path, float] | None:
    for f in cache.glob("part_*.*"):
        m = re.match(r"part_([\d.]+)_([\d.]+|end)\.", f.name)
        if not m or f.stat().st_size == 0:
            continue
        a, b = float(m.group(1)), (None if m.group(2) == "end" else float(m.group(2)))
        if a <= t0 + 1e-3 and (b is None or (t1 is not None and b >= t1 - 1e-3)):
            return f, t0 - a
    return None


def cached_source(url: str, t0: float | None, t1: float | None) -> tuple[Path, float] | None:
    """A cached download that covers [t0, t1] (no network)."""
    cache = config.SOURCES_DIR / video_key(url)
    if not cache.exists():
        return None
    full = next((f for f in cache.glob("full.*") if f.stat().st_size > 0), None)
    if full is not None:
        return full, t0 or 0.0
    if t0 is None and t1 is None:
        return None
    return _cached_part(cache, t0 or 0.0, t1)


def fetch_source(url: str, t0: float | None, t1: float | None, progress=None, allow_full: bool = False,
                 info: dict | None = None) -> tuple[Path, float]:
    """A local copy covering [t0, t1] and the position of `t0` inside it. Only the selected fragment
    is downloaded (plus SECTION_MARGIN); downloads go to the shared cache (config.SOURCES_DIR), so a
    later job inside an already downloaded range needs no network. The whole video is downloaded
    only when nothing is selected or `allow_full` is set (the person's explicit choice)."""
    cache = config.SOURCES_DIR / video_key(url)
    cache.mkdir(parents=True, exist_ok=True)
    hit = cached_source(url, t0, t1)
    if hit is not None:
        log.info("Этот фрагмент уже скачан — беру из кэша, без повторной загрузки")
        return hit
    info = info if info is not None else remote_info(url, cache)
    tmp = cache / "tmp"

    def keep(src: Path, name: str) -> Path:
        dst = cache / f"{name}{src.suffix}"
        src.replace(dst)
        shutil.rmtree(tmp, ignore_errors=True)
        return dst

    def full(reason: str) -> tuple[Path, float]:
        log.info("📥 %s", reason)
        return keep(download_section(url, None, None, tmp, progress, "Скачивание всего ролика"), "full"), t0 or 0.0

    if t0 is None and t1 is None:
        return full("Фрагмент не выбран — скачиваю весь ролик")
    start = t0 or 0.0
    a = max(0.0, start - SECTION_MARGIN)
    b = None if t1 is None else t1 + SECTION_MARGIN
    if info.get("duration") and b is not None and b >= info["duration"]:
        b = None  # the margin runs past the end: up to the end
    label = f"Скачивание фрагмента {fmt_duration(start)}–{fmt_duration(t1) if t1 is not None else 'конец'}" + \
            (f" ({t1 - start:.0f} с)" if t1 is not None else "")
    last: DownloadError | None = None
    for attempt in (1, 2):
        try:
            part = download_section(url, a, b, tmp, progress, label)
            return keep(part, f"part_{a:.2f}_{'end' if b is None else f'{b:.2f}'}"), start - a
        except DownloadError as e:
            last = e
            if e.kind in FINAL_KINDS:
                raise
            if attempt == 1 and e.kind in RETRY_KINDS + ("forbidden",):
                log.warning("Фрагмент не скачался (%s) — пробую ещё раз с новой ссылкой", e.title)
                continue
            break
    shutil.rmtree(tmp, ignore_errors=True)
    if allow_full:
        return full("Скачиваю весь ролик — вы выбрали этот вариант, фрагмент вырежу на компьютере")
    raise PartialUnavailable(last.title if last else "", info.get("duration"), info.get("size"),
                             getattr(last, "detail", "")) from last


def _ffmpeg_progress(cmd: list[str], what: str, total: float | None, progress=None) -> None:
    """ffmpeg with `-progress pipe:1`: report the share of `total` seconds done."""
    log.debug("$ %s", " ".join(map(str, cmd)))
    proc = subprocess.Popen(cmd[:1] + ["-progress", "pipe:1", "-nostats"] + cmd[1:], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, errors="replace")
    last = 0.0
    for line in proc.stdout:
        if progress and total and line.startswith("out_time_us="):
            try:
                frac = min(1.0, int(line.split("=", 1)[1]) / 1e6 / total)
            except ValueError:
                continue
            if frac - last >= 0.05:
                last = frac
                progress(frac, f"{what}: {frac:.0%}")
    err = proc.stderr.read()
    if proc.wait() != 0:
        tail = "\n".join(err.strip().splitlines()[-15:])
        raise IngestError(f"{what} failed:\n{tail}")


def normalize(src: Path, dst: Path, start: float | None = None, duration: float | None = None, progress=None) -> Path:
    """Cut [start, start + duration) frame-accurately and re-encode to constant-fps
    H.264/AAC so all later timings are exact. (-ss before -i with re-encoding seeks
    precisely; -t after -i is measured from the cut, like an editor's in/out points.)"""
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(src)]
    if duration is not None:
        cmd += ["-t", f"{duration:.3f}"]
    cmd += [
        "-vf", f"fps={config.FPS},setsar=1",
        "-c:v", "libx264", "-preset", "fast", "-crf", "17", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-ac", "2",
        "-movflags", "+faststart",
        str(dst),
    ]
    total = duration if duration is not None else max(0.1, probe(src).duration - (start or 0))
    _ffmpeg_progress(cmd, "Обрезка фрагмента", total, progress)
    return dst


def ingest(source: str, start: str | float | None, end: str | float | None, job_dir: Path, progress=None,
           allow_full: bool = False) -> Path:
    """Returns path to `<job>/clip.mp4`: exactly [start, end] of the source. Stages (progress):
    the source is checked, its metadata fetched (and the timecodes checked against its
    length), only the selected fragment downloaded, then cut frame-accurately."""
    say = progress or (lambda f, d: None)
    t0, t1 = parse_time(start), parse_time(end)
    if t0 is not None and t1 is not None and t1 <= t0:
        raise ValueError("end_time must be greater than start_time")
    say(0.02, "Проверка источника")
    src_info = inspect_source(source)
    clip = job_dir / "clip.mp4"
    cleanup = None
    if src_info.kind == "url":
        url = src_info.value
        say(0.06, "Сведения о ролике")
        cache = config.SOURCES_DIR / video_key(url)
        cache.mkdir(parents=True, exist_ok=True)
        hit = cached_source(url, t0, t1) if config.SOURCE_CACHE else None
        info = remote_info(url, cache) if hit is None else source_meta(url)
        total_src = info.get("duration")
        if total_src:
            if t0 is not None and t0 >= total_src - 0.05:
                raise ValueError(f"Начало фрагмента ({fmt_duration(t0)}) дальше конца видео ({fmt_duration(total_src)}).")
            if t1 is not None and t1 > total_src + 0.05:
                log.warning("Конец фрагмента %s дальше конца видео (%s) — беру до конца", fmt_duration(t1), fmt_duration(total_src))
                t1 = None
            if t0 is None and t1 is None and total_src > config.FULL_DOWNLOAD_ASK and not allow_full and hit is None:
                raise NeedsRange(total_src, info.get("size"))
        sel = (t1 if t1 is not None else (total_src or 0)) - (t0 or 0)
        if sel > 0:
            log.info("Выбранный фрагмент: %s–%s (%s)", fmt_duration(t0 or 0), fmt_duration(t1) if t1 is not None else "конец",
                     fmt_duration(sel))
        dl = lambda f, d: say(0.1 + 0.6 * f, d)  # noqa: E731
        if config.SOURCE_CACHE:
            src, offset = fetch_source(url, t0, t1, dl, allow_full, info) if hit is None else hit
            if hit is not None:
                log.info("Этот фрагмент уже скачан — беру из кэша, без повторной загрузки")
        else:  # no cache: the fragment goes to the job folder and is removed after the cut
            cleanup = job_dir / "download"
            a = max(0.0, (t0 or 0.0) - SECTION_MARGIN) if (t0 is not None or t1 is not None) else None
            b = None if t1 is None else t1 + SECTION_MARGIN
            if a is None and total_src and total_src > config.FULL_DOWNLOAD_ASK and not allow_full:
                raise NeedsRange(total_src, info.get("size"))
            src = download_section(url, a, b, cleanup, dl)
            offset = (t0 or 0.0) - (a or 0.0)
    else:
        src = Path(src_info.value)
        offset = t0 or 0.0
    length = None if t1 is None else t1 - (t0 or 0.0)
    total = probe(src).duration
    if offset >= total - 0.05:
        raise ValueError(f"Начало фрагмента ({fmt_duration(t0)}) дальше конца видео ({fmt_duration(total)}).")
    say(0.72, "Обрезка фрагмента")
    log.info("✂ Обрезаю %s–%s", fmt_duration(t0 or 0), fmt_duration(t1) if t1 is not None else "конец")
    normalize(src, clip, offset, length, lambda f, d: say(0.72 + 0.26 * f, d))
    if cleanup is not None:
        shutil.rmtree(cleanup, ignore_errors=True)
    info_clip = probe(clip)
    if length is not None and info_clip.duration < length - 0.5:
        log.warning("Видео кончилось раньше: фрагмент вышел %s вместо %s", fmt_duration(info_clip.duration), fmt_duration(length))
    log.info("Клип готов: %s, %d×%d", fmt_duration(info_clip.duration), info_clip.width, info_clip.height)
    say(1.0, f"Клип готов: {fmt_duration(info_clip.duration)}")
    if not info_clip.has_audio:
        raise IngestError("The clip has no audio track — nothing to transcribe")
    return clip


# ---------------------------------------------------------------- avatars
def square_avatar(img: Image.Image) -> Image.Image:
    return ImageOps.fit(img.convert("RGB"), (AVATAR_PX, AVATAR_PX), Image.LANCZOS, centering=(0.5, 0.4))


def placeholder_avatar(name: str, side: str) -> Image.Image:
    bg, fg = ("#e8e6e3", "#262421") if side == "white" else ("#3c3a37", "#ffffff")
    img = Image.new("RGB", (AVATAR_PX, AVATAR_PX), bg)
    initials = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    font = ImageFont.truetype(config.font_path("bold"), 110 if len(initials) == 1 else 92)
    ImageDraw.Draw(img).text((AVATAR_PX / 2, AVATAR_PX / 2), initials, font=font, fill=fg, anchor="mm")
    return img


def auto_avatars(
    va, job_dir: Path, times_by_side: dict[str, list[float]], person_for_side: dict[str, int] | None = None
) -> dict[str, Image.Image | None]:
    """Head crops of the two main persons found by `vision` (YuNet + SFace clusters).
    The person of each side comes from the speaker analysis when known, otherwise
    it is whoever is on screen most while that side is speaking."""
    import vision

    person_for_side = dict(person_for_side or {})
    if len(person_for_side) < len(times_by_side):
        samples = np.array(va.samples)
        on_screen: dict[str, dict[int, int]] = {}
        for side, times in times_by_side.items():
            frames = {int(samples[np.abs(samples - t * va.fps).argmin()]) for t in times} if len(samples) else set()
            counts: dict[int, int] = {}
            for f in va.faces:
                p = va.person_of(f)
                if f.frame in frames and p in va.persons:
                    counts[p] = counts.get(p, 0) + 1
            on_screen[side] = counts
        order = sorted(on_screen, key=lambda sd: -max(on_screen[sd].values(), default=0))  # clearest first
        for side in order:
            if side in person_for_side:
                continue
            taken = set(person_for_side.values())
            ranked = sorted(on_screen[side], key=lambda p: -on_screen[side][p])
            free = [p for p in ranked if p not in taken] or [p for p in va.persons if p not in taken]
            if free:
                person_for_side[side] = free[0]
    result: dict[str, Image.Image | None] = {}
    for side in times_by_side:
        path = vision.person_crop(job_dir, person_for_side[side], va) if side in person_for_side else None
        result[side] = square_avatar(Image.open(path)) if path else None
    return result


def prepare_avatars(
    job_dir: Path,
    names: dict[str, str],
    given: dict[str, str | None],
    va=None,
    times_by_side: dict[str, list[float]] | None = None,
    person_for_side: dict[str, int] | None = None,
) -> dict[str, Path]:
    """Writes `<job>/avatars/{white,black}.png`: given image > detected face > initials.
    `va` is the clip's vision.VideoAnalysis (None = no face detection)."""
    out_dir = job_dir / "avatars"
    out_dir.mkdir(parents=True, exist_ok=True)
    faces: dict[str, Image.Image | None] = {}
    missing = [s for s in ("white", "black") if not given.get(s)]
    if missing and va is not None and va.persons:
        try:
            taken = {s: p for s, p in (person_for_side or {}).items()}
            faces = auto_avatars(va, job_dir, {s: (times_by_side or {}).get(s, []) for s in missing}, taken)
        except Exception as e:  # never fail the pipeline because of avatars
            log.warning("Не удалось взять лица для аватарок (%s) — будут инициалы", e)
    paths = {}
    for side in ("white", "black"):
        if given.get(side):
            img = square_avatar(Image.open(Path(given[side]).expanduser()))
        elif faces.get(side) is not None:
            img = faces[side]
            log.info("Аватарка %s — лицо из видео", "белых" if side == "white" else "чёрных")
        else:
            img = placeholder_avatar(names[side], side)
        paths[side] = out_dir / f"{side}.png"
        img.save(paths[side])
    return paths


# ---------------------------------------------------------------- yt-dlp upkeep
def ytdlp_version() -> str:
    if config.YTDLP_BIN:
        proc = subprocess.run(_ytdlp_base() + ["--version"], capture_output=True, text=True)
        return proc.stdout.strip().splitlines()[0] if proc.returncode == 0 and proc.stdout.strip() else ""
    try:
        from yt_dlp.version import __version__

        return __version__
    except Exception:
        return ""


def update_ytdlp() -> tuple[str, str]:
    """Upgrade yt-dlp inside this environment (uv when available, else pip). Returns (old, new)."""
    old = ytdlp_version()
    uv = shutil.which("uv")
    if config.YTDLP_BIN:  # the person's own build updates itself
        cmd = _ytdlp_base() + ["-U"]
    else:
        cmd = ([uv, "pip", "install", "-U", "yt-dlp", "--python", sys.executable] if uv
               else [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"])
    log.info("Обновляю yt-dlp (сейчас %s)…", old or "?")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-6:])
        raise DownloadError("other", "Не удалось обновить yt-dlp.", "Выполните в терминале: `uv pip install -U yt-dlp`.", tail)
    new = (ytdlp_version() if config.YTDLP_BIN else subprocess.run(
        [sys.executable, "-c", "from yt_dlp.version import __version__; print(__version__)"],
        capture_output=True, text=True).stdout.strip()) or old
    log.info("✓ yt-dlp: %s → %s", old or "?", new)
    return old, new


BROWSERS = ["firefox", "chrome", "chromium", "brave", "edge", "opera", "vivaldi", "safari"]


def set_cookies(browser: str = "", cookies_file: str = "") -> str:
    """Remember how yt-dlp gets the person's own YouTube session: a browser to read cookies
    from, or an exported cookies.txt. Only the choice is stored (.env), never cookie values."""
    browser = (browser or "").strip().lower()
    cookies_file = (cookies_file or "").strip()
    if browser and browser.split(":")[0] not in BROWSERS:
        raise SourceError(f"Неизвестный браузер «{browser}».", f"Доступны: {', '.join(BROWSERS)}.")
    if cookies_file:
        p = Path(cookies_file).expanduser()
        if not p.is_file():
            raise SourceError(f"Файл cookies не найден: {p}.", "Укажите путь к cookies.txt (формат Netscape).")
        head = p.read_text(errors="replace")[:400]
        if "# Netscape HTTP Cookie File" not in head and "\t" not in head:
            raise SourceError("Это не похоже на cookies.txt в формате Netscape.",
                              "Экспортируйте cookies расширением «Get cookies.txt LOCALLY».")
        cookies_file = str(p)
    config.write_env("YTDLP_COOKIES_FROM_BROWSER", browser if not cookies_file else "")
    config.write_env("YTDLP_COOKIES", cookies_file)
    config.YTDLP_COOKIES_FROM_BROWSER, config.YTDLP_COOKIES = (browser if not cookies_file else ""), cookies_file
    return cookies_status()


def cookies_status() -> str:
    if config.YTDLP_COOKIES:
        return f"yt-dlp берёт cookies из файла {Path(config.YTDLP_COOKIES).name}"
    if config.YTDLP_COOKIES_FROM_BROWSER:
        return f"yt-dlp берёт cookies из браузера {config.YTDLP_COOKIES_FROM_BROWSER} (только на этом компьютере)"
    return "Без cookies: скачиваются только общедоступные ролики"
