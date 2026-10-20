"""Step 5: upload the approved video to TikTok via `tiktok-uploader` (Playwright),
authenticated with a Netscape-format cookies.txt exported from your browser."""
from __future__ import annotations

import logging
import re
from pathlib import Path

import config

log = logging.getLogger(__name__)


class PublishError(RuntimeError):
    pass


def build_description(caption: str, hashtags: str | list[str]) -> str:
    """'caption' + 'chess, #brilliant шахматы' -> 'caption #chess #brilliant #шахматы'.
    Tags already written in the caption are not repeated."""
    if isinstance(hashtags, str):
        hashtags = re.split(r"[\s,;]+", hashtags)
    present = {t.lower() for t in re.findall(r"#([\w\d]+)", caption or "")}
    tags = []
    for t in hashtags:
        t = t.strip().lstrip("#")
        if t and t.lower() not in present and f"#{t}" not in tags:
            tags.append(f"#{t}")
            present.add(t.lower())
    return " ".join(p for p in ((caption or "").strip(), " ".join(tags)) if p)


def check_cookies(path: Path) -> None:
    if not path.exists():
        raise PublishError(
            f"Cookies file not found: {path}\n"
            "Log in to tiktok.com in your browser, export cookies in Netscape format "
            "(e.g. the 'Get cookies.txt LOCALLY' extension) and save them there."
        )
    text = path.read_text(errors="replace")
    if "sessionid" not in text:
        raise PublishError(f"{path} has no 'sessionid' cookie — export it while logged in to tiktok.com")


def publish(
    video: Path,
    description: str,
    cookies: Path = config.COOKIES_FILE,
    headless: bool = config.TIKTOK_HEADLESS,
    browser: str = config.TIKTOK_BROWSER,
    visibility: str = "everyone",
) -> None:
    """Raises PublishError if TikTok rejected the upload."""
    video = Path(video).resolve()
    if not video.exists():
        raise PublishError(f"Video not found: {video}")
    if len(description) > 2200:
        raise PublishError(f"Description is {len(description)} chars; TikTok allows 2200")
    check_cookies(cookies)
    try:
        from tiktok_uploader.upload import upload_video
    except ImportError as e:
        raise PublishError("tiktok-uploader is not installed (pip install tiktok-uploader)") from e

    log.info("▶ Публикация в TikTok: открываю %s и загружаю %s…", browser, video.name)
    try:
        failed = upload_video(
            str(video),
            description=description,
            cookies=str(cookies),
            browser=browser,
            headless=headless,
            visibility=visibility,
        )
    except Exception as e:
        raise PublishError(f"Upload crashed: {e}") from e
    if failed:
        raise PublishError("TikTok upload failed (see the log above; cookies may have expired)")
    log.info("✓ Опубликовано в TikTok")
