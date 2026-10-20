"""The trimming editor of the «Источник» section: a preview player, a timeline with
draggable start / end handles, precise time buttons and «play the selection».

Before anything is downloaded a YouTube source is previewed in the embedded YouTube
player; any other link is previewed once it is in the source cache («Скачать для
точной обрезки» downloads it whole, after which every cut works offline and frame by
frame). A local file is previewed directly. The handles write into the «Начало /
Конец фрагмента» fields, so the cut the person sees is exactly the cut the pipeline
makes; all later timestamps (moves, sounds, subtitles) are relative to it.

The player logic lives in design.HEAD (`window.cdmTrim`); this module builds the HTML.
"""
from __future__ import annotations

import hashlib
import logging
import os
import re
import shutil
import tempfile
from pathlib import Path

import config
import ingest
import ui_parts as up

log = logging.getLogger(__name__)
YT_ID = re.compile(r"(?:[?&]v=|youtu\.be/|/shorts/|/embed/|/live/)([A-Za-z0-9_-]{11})")
WEB_VIDEO = (".mp4", ".webm", ".m4v", ".mov", ".ogv")


def cached_full(url: str) -> Path | None:
    cache = config.SOURCES_DIR / ingest.video_key(url)
    return next((f for f in cache.glob("full.*") if f.stat().st_size > 0), None) if cache.exists() else None


def _servable(path: Path) -> Path | None:
    """A path the web UI may serve: inside the jobs folder or Gradio's temp files; any other
    local file gets a hard link (or a copy) in the source cache."""
    path = path.expanduser().resolve()
    roots = [config.JOBS_DIR.resolve(), Path(tempfile.gettempdir()).resolve()]
    if any(path.is_relative_to(r) for r in roots):
        return path
    st = path.stat()
    key = hashlib.sha1(f"{path}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]
    dst_dir = config.SOURCES_DIR / f"local-{key}"
    dst = dst_dir / f"full{path.suffix.lower()}"
    if not dst.exists():
        dst_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.link(path, dst)
        except OSError:
            shutil.copy2(path, dst)
    return dst


def _bar(start: str, end: str, duration: float | None) -> str:
    play = '<svg width="14" height="14" viewBox="0 0 24 24"><path d="M7 4v16l13-8z" fill="currentColor"/></svg>'
    nudges = "".join(f'<button type="button" class="cdm-tp" data-act="nudge" data-which="{w}" data-d="{d}">{lbl}</button>'
                     for w in ("start",) for d, lbl in (("-1", "−1"), ("-0.1", "−0,1"), ("0.1", "+0,1"), ("1", "+1")))
    nudges_end = "".join(f'<button type="button" class="cdm-tp" data-act="nudge" data-which="end" data-d="{d}">{lbl}</button>'
                         for d, lbl in (("-1", "−1"), ("-0.1", "−0,1"), ("0.1", "+0,1"), ("1", "+1")))
    return (
        f'<div class="cdm-trim-bar"><div class="cdm-trim-track"><div class="cdm-trim-sel"></div>'
        f'<div class="cdm-trim-h is-start" title="Начало фрагмента — тяните"></div>'
        f'<div class="cdm-trim-h is-end" title="Конец фрагмента — тяните"></div><div class="cdm-trim-ph"></div></div>'
        f'<div class="cdm-trim-ticks"></div></div>'
        f'<div class="cdm-trim-ctrl">'
        f'<div class="cdm-trim-grp"><button type="button" class="cdm-tp cdm-trim-set" data-act="set" data-which="start">⟦ Начало здесь</button>{nudges}</div>'
        f'<div class="cdm-trim-grp">{nudges_end}<button type="button" class="cdm-tp cdm-trim-set" data-act="set" data-which="end">Конец здесь ⟧</button></div>'
        f'</div><div class="cdm-trim-mid">'
        f'<button type="button" class="cdm-tp cdm-tp-play" data-act="play-sel" title="Проиграть фрагмент">{play}</button>'
        f'<span class="cdm-trim-info">Весь ролик</span><span class="cdm-trim-time">0:00.0</span>'
        f'<button type="button" class="cdm-tp" data-act="clear" title="Без обрезки — весь ролик">Весь ролик</button></div>'
    )


def trim_html(source: str, start: str = "", end: str = "", duration: float | None = None) -> str:
    """The editor for a source (URL or local path); "" when there is nothing to show."""
    source = (source or "").strip()
    if not source:
        return ""
    attrs = f'data-start="{up.esc(start or "")}" data-end="{up.esc(end or "")}" data-duration="{duration or 0:.2f}"'
    local: Path | None = None
    is_url = ingest._is_url(source)
    try:
        if is_url:
            local = cached_full(source)
        elif Path(source).expanduser().exists():
            local = _servable(Path(source))
    except OSError as e:
        log.debug("trim preview file: %s", e)
    if local is not None and local.suffix.lower() in WEB_VIDEO:
        if not duration:
            try:
                attrs = attrs.replace('data-duration="0.00"', f'data-duration="{ingest.probe(local).duration:.2f}"')
            except Exception:
                pass
        src = f"/gradio_api/file={up.esc(str(local))}"
        note = "Скачано — обрезка точная до кадра." if is_url else "Локальный файл."
        return (f'<div class="cdm-trim" data-kind="video" {attrs}><div class="cdm-trim-player">'
                f'<video src="{src}" preload="metadata" playsinline></video></div>{_bar(start, end, duration)}'
                f'<div class="cdm-trim-note">{note} Тяните ручки или ставьте начало и конец по плееру.</div></div>')
    m = YT_ID.search(source) if is_url else None
    if m:
        vid = m.group(1)
        return (f'<div class="cdm-trim" data-kind="youtube" data-vid="{vid}" {attrs}><div class="cdm-trim-player">'
                f'<div class="cdm-trim-yt" id="cdm-yt-{vid}"></div></div>{_bar(start, end, duration)}'
                f'<div class="cdm-trim-note">Предпросмотр YouTube — без скачивания. Для обрезки точно по кадрам '
                f'нажмите «Скачать для точной обрезки».</div></div>')
    if is_url:
        return ('<div class="cdm-trim cdm-trim-empty"><div class="cdm-trim-note">Предпросмотр этой ссылки появится после '
                'скачивания — нажмите «Скачать для точной обрезки». Видео сохранится в кэше: новая обрезка не скачивает '
                'его снова.</div></div>')
    return ""


def needs_download(source: str) -> bool:
    source = (source or "").strip()
    return ingest._is_url(source) and cached_full(source) is None


def download(url: str, progress=None) -> Path:
    """The whole source into the cache (for an exact preview and offline cuts)."""
    if progress:
        progress(0.1, "Скачивание ролика для обрезки")
    full = cached_full(url)
    if full is not None:
        return full
    path, _ = ingest.fetch_source(url, None, None)
    if not path.name.startswith("full"):
        raise ValueError("Ролик скачался частями — предпросмотр всего ролика недоступен.")
    return path
