"""Find videos and YouTube Shorts by a text query (no API key needed).

Regular videos come from yt-dlp's `ytsearch`. Shorts need their own path: yt-dlp's
search skips the Shorts shelf, so `search_shorts` reads the YouTube results page with
the "Shorts" type filter (the `ytInitialData` JSON embedded in it), or a channel's
Shorts tab for `@channel` queries, and falls back to `ytsearch` + a per-video check.
Duration, channel, views and upload date of Shorts are filled in from their
`/shorts/<id>` pages in parallel. Every result says what it is (`Video.kind`).
A query is fetched once per CACHE_TTL: switching filters, sorting or the videos/Shorts
mode reuses it instead of asking YouTube again.
"""
from __future__ import annotations

import concurrent.futures
import dataclasses
import json
import logging
import re
import threading
import time
import urllib.parse
from dataclasses import asdict, dataclass

import config
from ingest import fmt_duration

log = logging.getLogger(__name__)

SHORTS_FILTER = "EgIQCQ%253D%253D"   # YouTube search "Type: Shorts"
SHORT_MAX = 180                      # s: YouTube Shorts are at most 3 minutes
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
    "Cookie": "SOCS=CAI",            # skip the EU consent interstitial
}
SORTS = ("relevance", "views", "date")
CACHE_TTL = 900.0                    # s
_cache: dict[tuple, tuple[float, list]] = {}
_cache_lock = threading.Lock()


class SearchError(RuntimeError):
    pass


@dataclass
class Video:
    id: str
    url: str
    title: str
    duration: float | None
    channel: str
    views: int | None
    kind: str = "video"          # "video" | "short"
    uploaded: str = ""           # ISO date, when known

    @property
    def is_short(self) -> bool:
        return self.kind == "short"

    @property
    def thumb(self) -> str:
        if self.is_short:  # original (vertical) aspect ratio thumbnail
            return f"https://i.ytimg.com/vi/{self.id}/oar2.jpg"
        return f"https://i.ytimg.com/vi/{self.id}/mqdefault.jpg"  # 320x180, same size for every result

    @property
    def caption(self) -> str:
        parts = [fmt_duration(self.duration) if self.duration else ("Shorts" if self.is_short else "LIVE"), self.title]
        meta = " · ".join(p for p in (self.channel, fmt_views(self.views), fmt_date(self.uploaded)) if p)
        return " · ".join(parts) + (f"\n{meta}" if meta else "")

    def to_dict(self) -> dict:
        return {**asdict(self), "thumb": self.thumb}

    @classmethod
    def from_dict(cls, d: dict) -> "Video":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


def fmt_views(n: int | None) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f} млн просмотров".replace(".0 ", " ")
    if n >= 1_000:
        return f"{n // 1000} тыс. просмотров"
    return f"{n} просмотров"


def fmt_date(iso: str) -> str:
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", iso or "")
    return f"{m.group(3)}.{m.group(2)}.{m.group(1)}" if m else ""


def parse_views(text: str) -> int | None:
    """'4,2 млн просмотров' / '468 тыс. просмотров' / '1.2M views' / '9 200' -> int."""
    if not text:
        return None
    t = re.sub(r"(?<=\d)[\s\u00a0\u202f](?=\d)", "", text.lower())  # "9 200" -> "9200"
    m = re.search(r"(\d+(?:[.,]\d+)?)\s*(млрд|млн|тыс|b|m|k)?", t)
    if not m:
        return None
    num = float(m.group(1).replace(",", "."))
    mult = {"млрд": 1e9, "b": 1e9, "млн": 1e6, "m": 1e6, "тыс": 1e3, "k": 1e3}.get(m.group(2) or "", 1)
    return int(num * mult)


def is_url(text: str) -> bool:
    return bool(re.match(r"^\s*https?://", text or ""))


def is_short_url(url: str) -> bool:
    return "/shorts/" in (url or "")


def _ydl_opts(limit: int | None = None) -> dict:
    opts = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist", "skip_download": True, "noprogress": True}
    if limit:
        opts["playlistend"] = limit
    if config.YTDLP_COOKIES:
        opts["cookiefile"] = config.YTDLP_COOKIES
    return opts


def _remembered(key: tuple, fetch, what: str) -> list[Video]:
    """`fetch()` once per key for CACHE_TTL (empty results are not kept); returns copies."""
    with _cache_lock:
        hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        log.info("%s: беру результаты недавнего поиска (%d)", what, len(hit[1]))
    else:
        items = fetch()
        if not items:
            return []
        hit = (time.monotonic(), items)
        with _cache_lock:
            _cache[key] = hit
            for old in sorted(_cache, key=lambda k: _cache[k][0])[:-64]:
                del _cache[old]
    return [dataclasses.replace(v) for v in hit[1]]


def search_videos(query: str, limit: int = 12) -> list[Video]:
    query = (query or "").strip()
    if not query:
        raise SearchError("Введите, что искать: название фильма, сцены или фразу из диалога.")
    return _remembered(("videos", query.lower(), limit), lambda: _fetch_videos(query, limit), f"Поиск «{query}»")


def _fetch_videos(query: str, limit: int) -> list[Video]:
    import yt_dlp

    log.info("Ищу на YouTube: «%s»…", query)
    try:
        with yt_dlp.YoutubeDL(_ydl_opts()) as ydl:
            info = ydl.extract_info(f"ytsearch{limit}:{query}", download=False)
    except Exception as e:
        raise SearchError(f"Поиск не удался: {e}") from e
    videos = []
    for e in info.get("entries") or []:
        if not e or not e.get("id") or e.get("live_status") == "is_upcoming":
            continue
        url = e.get("url") or f"https://www.youtube.com/watch?v={e['id']}"
        videos.append(Video(
            id=e["id"],
            url=url,
            title=e.get("title") or e["id"],
            duration=e.get("duration"),
            channel=e.get("channel") or e.get("uploader") or "",
            views=e.get("view_count"),
            kind="short" if is_short_url(url) else "video",
        ))
    log.info("Найдено роликов: %d", len(videos))
    return videos


# ================================================================ Shorts
def _get(url: str, timeout: float = 15):
    import requests

    return requests.get(url, headers=_HEADERS, timeout=timeout)


def _walk(node, key: str, out: list) -> None:
    if isinstance(node, dict):
        if key in node:
            out.append(node[key])
        for v in node.values():
            _walk(v, key, out)
    elif isinstance(node, list):
        for v in node:
            _walk(v, key, out)


def _initial_data(html: str) -> dict:
    m = re.search(r"var ytInitialData = (\{.*?\});</script>", html, re.S)
    if not m:
        raise SearchError("YouTube вернул страницу в непривычном виде — поиск Shorts временно недоступен.")
    return json.loads(m.group(1))


def _shorts_from_results(query: str) -> list[Video]:
    """Shorts from the search results page with the Shorts type filter."""
    url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(query)}&sp={SHORTS_FILTER}"
    data = _initial_data(_get(url).text)
    found: list[Video] = []
    seen: set[str] = set()
    lockups: list = []
    _walk(data, "shortsLockupViewModel", lockups)
    for it in lockups:
        try:
            vid = it["onTap"]["innertubeCommand"]["reelWatchEndpoint"]["videoId"]
        except (KeyError, TypeError):
            vid = (it.get("entityId") or "").removeprefix("shorts-shelf-item-")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        meta = it.get("overlayMetadata") or {}
        title = ((meta.get("primaryText") or {}).get("content") or "").strip()
        views = parse_views((meta.get("secondaryText") or {}).get("content") or "")
        found.append(Video(vid, f"https://www.youtube.com/shorts/{vid}", title or vid, None, "", views, "short"))
    reels: list = []
    _walk(data, "reelItemRenderer", reels)  # older page layout
    for it in reels:
        vid = it.get("videoId")
        if not vid or vid in seen:
            continue
        seen.add(vid)
        title = ((it.get("headline") or {}).get("simpleText") or "").strip()
        views = parse_views(((it.get("viewCountText") or {}).get("simpleText")) or "")
        found.append(Video(vid, f"https://www.youtube.com/shorts/{vid}", title or vid, None, "", views, "short"))
    return found


def _shorts_from_channel(handle: str, words: str, limit: int) -> list[Video]:
    """A channel's Shorts tab, optionally filtered by words in the title."""
    import yt_dlp

    handle = handle if handle.startswith("@") else f"@{handle}"
    try:
        with yt_dlp.YoutubeDL(_ydl_opts(limit * 4 if words else limit)) as ydl:
            info = ydl.extract_info(f"https://www.youtube.com/{handle}/shorts", download=False)
    except Exception as e:
        raise SearchError(f"Не удалось открыть Shorts канала {handle}: {str(e).splitlines()[0][:160]}") from e
    channel = info.get("channel") or info.get("uploader") or handle
    needles = [w.lower() for w in words.split()]
    out = []
    for e in info.get("entries") or []:
        if not e or not e.get("id"):
            continue
        title = e.get("title") or e["id"]
        if needles and not all(n in title.lower() for n in needles):
            continue
        out.append(Video(e["id"], f"https://www.youtube.com/shorts/{e['id']}", title, e.get("duration"), channel,
                         e.get("view_count"), "short"))
    return out


def check_short(video_id: str) -> bool:
    """True if YouTube serves the id as a Short (/shorts/<id> answers 200, a regular video redirects)."""
    import requests

    try:
        r = requests.head(f"https://www.youtube.com/shorts/{video_id}", headers=_HEADERS, allow_redirects=False, timeout=10)
    except requests.RequestException:
        return False
    return r.status_code == 200


def _shorts_fallback(query: str, limit: int) -> list[Video]:
    """ytsearch + per-video check, when the results page can't be read."""
    cands = [v for v in search_videos(query, limit * 2) if (v.duration or 0) <= SHORT_MAX]
    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        flags = list(ex.map(lambda v: check_short(v.id), cands))
    out = []
    for v, ok in zip(cands, flags):
        if ok:
            v.kind, v.url = "short", f"https://www.youtube.com/shorts/{v.id}"
            out.append(v)
    return out


def _enrich(video: Video) -> Video:
    """Duration, channel, views and upload date from the Short's own page."""
    try:
        html = _get(f"https://www.youtube.com/shorts/{video.id}", timeout=12).text
    except Exception as e:
        log.debug("short %s: %s", video.id, e)
        return video

    def grab(pattern: str) -> str | None:
        m = re.search(pattern, html)
        return m.group(1) if m else None

    if video.duration is None and (sec := grab(r'"lengthSeconds":"(\d+)"')):
        video.duration = float(sec)
    if not video.channel and (ch := grab(r'"ownerChannelName":"((?:[^"\\]|\\.)*)"')):
        video.channel = json.loads(f'"{ch}"')
    if (views := grab(r'"viewCount":"(\d+)"')) and not video.views:
        video.views = int(views)
    if not video.uploaded:
        video.uploaded = grab(r'"uploadDate":"([^"]+)"') or grab(r'"publishDate":"([^"]+)"') or ""
    if video.title == video.id and (t := grab(r'<meta name="title" content="([^"]*)"')):
        import html as _html

        video.title = _html.unescape(t)
    return video


def search_shorts(query: str, limit: int = 18, max_duration: float | None = None, sort: str = "relevance") -> list[Video]:
    """YouTube Shorts for a query. "@channel words" searches that channel's Shorts;
    `max_duration` (s) and `sort` ("relevance" | "views" | "date") narrow the list."""
    query = (query or "").strip()
    if not query:
        raise SearchError("Введите, что искать в Shorts: название, фразу, ключевые слова или @канал.")
    if sort not in SORTS:
        sort = "relevance"
    shorts = _remembered(("shorts", query.lower(), limit), lambda: _fetch_shorts(query, limit), f"Shorts «{query}»")
    if max_duration:
        shorts = [v for v in shorts if v.duration is None or v.duration <= max_duration]
    if sort == "views":
        shorts.sort(key=lambda v: -(v.views or 0))
    elif sort == "date":
        shorts.sort(key=lambda v: v.uploaded or "", reverse=True)
    shorts = shorts[:limit]
    log.info("Найдено Shorts: %d", len(shorts))
    return shorts


def _fetch_shorts(query: str, limit: int) -> list[Video]:
    """Up to 2×limit Shorts with their page data, before filtering and sorting."""
    log.info("Ищу Shorts: «%s»…", query)
    m = re.match(r"^(?:https?://(?:www\.)?youtube\.com/)?(@[\w.\-]+)(?:/shorts)?\s*(.*)$", query)
    try:
        if m:
            shorts = _shorts_from_channel(m.group(1), m.group(2).strip(), limit)
        else:
            shorts = []
            for attempt in (1, 2):  # now and then a single response comes without the Shorts shelf
                try:
                    shorts = _shorts_from_results(query)
                except (SearchError, ValueError) as e:
                    log.debug("shorts results page, attempt %d: %s", attempt, e)
                if shorts:
                    break
                time.sleep(1.0)
            if not shorts:
                log.info("Полка Shorts не прочиталась — проверяю обычную выдачу по одному ролику")
                shorts = _shorts_fallback(query, limit)
    except SearchError:
        raise
    except Exception as e:
        raise SearchError(f"Поиск Shorts не удался: {str(e).splitlines()[0][:200]}") from e
    with concurrent.futures.ThreadPoolExecutor(10) as ex:
        return list(ex.map(_enrich, shorts[: limit * 2]))
